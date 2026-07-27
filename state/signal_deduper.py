# -*- coding: utf-8 -*-
"""signal_deduper.py — 统一信号去重 + 冷却

功能:
- should_process(signal_id)  : 未处理过则返回 True，并标记已处理
- is_processed(signal_id)    : 只查询，不标记
- mark_processed(signal_id)  : 强制标记
- is_symbol_cooled(symbol, direction=None, reason=None) : 同类信号冷却检查
- mark_symbol_fired(...)     : 记录开仓时刻，启动冷却
- is_sl_cooled(symbol)       : 止损后冷却
- mark_sl_hit(symbol)        : 记录止损时刻

线程安全 + JSON 持久化 + TTL 自动清理。
"""
from __future__ import annotations
import json
import os
import threading
import time
from pathlib import Path
from typing import Optional


DEFAULT_SIGNAL_TTL_SEC = int(os.getenv("SIGNAL_DEDUP_TTL_SEC", str(86400 * 7)))
DEFAULT_SYMBOL_COOLDOWN_SEC = int(os.getenv("SIGNAL_SYMBOL_COOLDOWN_SEC", "900"))
DEFAULT_SAME_SETUP_COOLDOWN_SEC = int(os.getenv("SIGNAL_SAME_SETUP_COOLDOWN_SEC", str(5 * 75)))
DEFAULT_SL_COOLDOWN_SEC = int(os.getenv("SIGNAL_SL_COOLDOWN_SEC", "300"))
STATE_DIR = Path(os.getenv("SMC_STATE_DIR", "state"))
DEDUP_FILE = STATE_DIR / "signal_deduper.json"


class SignalDeduper:
    def __init__(
        self,
        persist_path: Optional[str | Path] = None,
        signal_ttl_sec: int = DEFAULT_SIGNAL_TTL_SEC,
        symbol_cooldown_sec: int = DEFAULT_SYMBOL_COOLDOWN_SEC,
        same_setup_cooldown_sec: int = DEFAULT_SAME_SETUP_COOLDOWN_SEC,
        sl_cooldown_sec: int = DEFAULT_SL_COOLDOWN_SEC,
    ):
        self._lock = threading.RLock()
        self._path = Path(persist_path) if persist_path else DEDUP_FILE
        self.signal_ttl_sec = int(signal_ttl_sec)
        self.symbol_cooldown_sec = int(symbol_cooldown_sec)
        self.same_setup_cooldown_sec = int(same_setup_cooldown_sec)
        self.sl_cooldown_sec = int(sl_cooldown_sec)
        self._processed: dict[str, float] = {}
        self._cooldowns: dict[str, float] = {}
        self._sl_times: dict[str, float] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._processed = {k: float(v) for k, v in (data.get("processed") or {}).items()}
            self._cooldowns = {k: float(v) for k, v in (data.get("cooldowns") or {}).items()}
            self._sl_times = {k: float(v) for k, v in (data.get("sl_times") or {}).items()}
            self._cleanup_unlocked()
        except Exception as exc:
            print(f"[SignalDeduper] load failed: {exc}")

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "processed": self._processed,
                "cooldowns": self._cooldowns,
                "sl_times": self._sl_times,
                "updated_at": time.time(),
            }
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self._path)
        except Exception as exc:
            print(f"[SignalDeduper] save failed: {exc}")

    def _cleanup_unlocked(self) -> None:
        now = time.time()
        cutoff = now - self.signal_ttl_sec
        for k in [k for k, ts in self._processed.items() if ts < cutoff]:
            self._processed.pop(k, None)
        cool_cutoff = now - max(self.same_setup_cooldown_sec, self.symbol_cooldown_sec) * 3
        for k in [k for k, ts in self._cooldowns.items() if ts < cool_cutoff]:
            self._cooldowns.pop(k, None)
        sl_cutoff = now - self.sl_cooldown_sec * 3
        for k in [k for k, ts in self._sl_times.items() if ts < sl_cutoff]:
            self._sl_times.pop(k, None)

    def is_processed(self, signal_id: str) -> bool:
        if not signal_id:
            return False
        with self._lock:
            ts = self._processed.get(signal_id)
            if ts is None:
                return False
            if time.time() - ts > self.signal_ttl_sec:
                self._processed.pop(signal_id, None)
                return False
            return True

    def mark_processed(self, signal_id: str) -> None:
        if not signal_id:
            return
        with self._lock:
            self._processed[signal_id] = time.time()
            self._cleanup_unlocked()
            self._save()

    def should_process(self, signal_id: str) -> bool:
        """True=首次可处理并已标记；False=已处理过应跳过。"""
        if not signal_id:
            return True
        with self._lock:
            ts = self._processed.get(signal_id)
            now = time.time()
            if ts is not None and now - ts <= self.signal_ttl_sec:
                return False
            self._processed[signal_id] = now
            self._cleanup_unlocked()
            self._save()
            return True

    @staticmethod
    def _cooldown_key(symbol: str, direction: Optional[str] = None, reason: Optional[str] = None) -> str:
        parts = [symbol or "?"]
        if direction:
            parts.append(str(direction))
        if reason:
            parts.append(str(reason))
        return "_".join(parts)

    def is_symbol_cooled(
        self,
        symbol: str,
        direction: Optional[str] = None,
        reason: Optional[str] = None,
        cooldown_sec: Optional[int] = None,
    ) -> bool:
        """True = 仍在冷却中（应跳过）。"""
        with self._lock:
            now = time.time()
            keys = []
            if direction and reason:
                keys.append(self._cooldown_key(symbol, direction, reason))
            if direction:
                keys.append(self._cooldown_key(symbol, direction))
            keys.append(self._cooldown_key(symbol))
            for key in keys:
                ts = self._cooldowns.get(key)
                if ts is None:
                    continue
                window = cooldown_sec
                if window is None:
                    window = (
                        self.same_setup_cooldown_sec
                        if key.count("_") >= 2
                        else self.symbol_cooldown_sec
                    )
                if now - ts < window:
                    return True
            return False

    def mark_symbol_fired(
        self,
        symbol: str,
        direction: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        with self._lock:
            now = time.time()
            self._cooldowns[self._cooldown_key(symbol)] = now
            if direction:
                self._cooldowns[self._cooldown_key(symbol, direction)] = now
            if direction and reason:
                self._cooldowns[self._cooldown_key(symbol, direction, reason)] = now
            self._save()

    def is_sl_cooled(self, symbol: str) -> bool:
        with self._lock:
            ts = self._sl_times.get(symbol)
            if ts is None:
                return False
            return time.time() - ts < self.sl_cooldown_sec

    def mark_sl_hit(self, symbol: str) -> None:
        with self._lock:
            self._sl_times[symbol] = time.time()
            self._save()

    def stats(self) -> dict:
        with self._lock:
            return {
                "processed_count": len(self._processed),
                "cooldown_count": len(self._cooldowns),
                "sl_count": len(self._sl_times),
                "signal_ttl_sec": self.signal_ttl_sec,
                "symbol_cooldown_sec": self.symbol_cooldown_sec,
                "same_setup_cooldown_sec": self.same_setup_cooldown_sec,
                "sl_cooldown_sec": self.sl_cooldown_sec,
            }


signal_deduper = SignalDeduper()
