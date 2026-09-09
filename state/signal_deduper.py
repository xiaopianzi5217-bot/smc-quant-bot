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
from utils.structured_logger import slog


DEFAULT_SIGNAL_TTL_SEC = int(os.getenv("SIGNAL_DEDUP_TTL_SEC", str(6 * 3600)))  # 6 小时
# 2026-09-07: 拉长冷却，防止同一 LIQUIDITY_SWEEP 等结构在短时间内反复开单
DEFAULT_SYMBOL_COOLDOWN_SEC = int(os.getenv("SIGNAL_SYMBOL_COOLDOWN_SEC", "3600"))       # 同品种 1h
DEFAULT_SAME_SETUP_COOLDOWN_SEC = int(os.getenv("SIGNAL_SAME_SETUP_COOLDOWN_SEC", "7200"))  # 同方向+同setup 2h
DEFAULT_SL_COOLDOWN_SEC = int(os.getenv("SIGNAL_SL_COOLDOWN_SEC", "900"))                 # 止损后 15min
DEFAULT_LOSS_STREAK_MAX = int(os.getenv("V6_LOSS_STREAK_MAX", "2"))                      # 连续止损次数阈值
DEFAULT_LOSS_STREAK_FREEZE_SEC = int(os.getenv("V6_LOSS_STREAK_FREEZE_SEC", "7200"))      # 连败后方向冻结秒数(默认2h)
DEFAULT_LOSS_DEADBAND_R = float(os.getenv("V6_LOSS_DEADBAND_R", "0.15"))                  # |pnl_r|<此值视为保本，不计入连胜/连败
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
        self.loss_streak_max = int(os.getenv("V6_LOSS_STREAK_MAX", str(DEFAULT_LOSS_STREAK_MAX)))
        self.loss_streak_freeze_sec = int(os.getenv("V6_LOSS_STREAK_FREEZE_SEC", str(DEFAULT_LOSS_STREAK_FREEZE_SEC)))
        self.loss_deadband_r = float(os.getenv("V6_LOSS_DEADBAND_R", str(DEFAULT_LOSS_DEADBAND_R)))
        self._loss_streaks: dict[str, int] = {}  # symbol_direction -> consecutive SL count
        self._dir_freeze_until: dict[str, float] = {}  # symbol_direction -> unix ts
        self._processed: dict[str, float] = {}
        self._cooldowns: dict[str, float] = {}
        self._sl_times: dict[str, float] = {}
        self._load()
        try:
            self.restore_streaks_from_research_db()
        except Exception as _rs_e:
            slog.error(f"[SignalDeduper] research.db 连败冷启动失败: {_rs_e}")

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._processed = {k: float(v) for k, v in (data.get("processed") or {}).items()}
            self._cooldowns = {k: float(v) for k, v in (data.get("cooldowns") or {}).items()}
            self._loss_streaks = {k: int(v) for k, v in (data.get("loss_streaks") or {}).items()}
            self._dir_freeze_until = {k: float(v) for k, v in (data.get("dir_freeze_until") or {}).items()}
            self._sl_times = {k: float(v) for k, v in (data.get("sl_times") or {}).items()}
            self._cleanup_unlocked()
        except Exception as exc:
            slog.error(f"[SignalDeduper] load failed: {exc}")

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "processed": self._processed,
                "cooldowns": self._cooldowns,
                "loss_streaks": self._loss_streaks,
                "dir_freeze_until": self._dir_freeze_until,
                "sl_times": self._sl_times,
                "updated_at": time.time(),
            }
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self._path)
        except Exception as exc:
            slog.error(f"[SignalDeduper] save failed: {exc}")

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

    def unmark_processed(self, signal_id: str) -> None:
        """信号已平仓/止损离场后，从已处理记录中移除，允许同形态信号重新触发。"""
        if not signal_id:
            return
        with self._lock:
            popped = self._processed.pop(signal_id, None)
            if popped is not None:
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

    def mark_sl_hit(self, symbol: str, direction: str | None = None, is_loss: bool = True) -> None:
        """记录止损。

        - 品种级短冷却（sl_cooldown_sec）始终写入
        - 若 is_loss 且给出 direction：累加连败；达到阈值则冻结该方向 loss_streak_freeze_sec
        """
        with self._lock:
            now = time.time()
            self._sl_times[symbol] = now
            if direction and is_loss:
                key = self._cooldown_key(symbol, str(direction))
                streak = int(self._loss_streaks.get(key, 0)) + 1
                self._loss_streaks[key] = streak
                if streak >= self.loss_streak_max:
                    self._dir_freeze_until[key] = now + self.loss_streak_freeze_sec
            self._save()

    def mark_trade_outcome(self, symbol: str, direction: str | None, pnl_r: float) -> None:
        """平仓后更新连败计数。

        判定规则（死区）:
        - pnl_r <= -loss_deadband_r  → 有效亏损，连败 +1
        - pnl_r >= +loss_deadband_r  → 有效盈利，连败清零并解冻
        - 否则（保本/微亏微盈）      → 不改变连败状态（避免摩擦打断）
        """
        if not direction:
            return
        try:
            pr = float(pnl_r if pnl_r is not None else 0.0)
        except (TypeError, ValueError):
            pr = 0.0
        band = float(self.loss_deadband_r or 0.15)
        with self._lock:
            key = self._cooldown_key(symbol, str(direction))
            if pr <= -band:
                streak = int(self._loss_streaks.get(key, 0)) + 1
                self._loss_streaks[key] = streak
                self._sl_times[symbol] = time.time()
                if streak >= self.loss_streak_max:
                    self._dir_freeze_until[key] = time.time() + self.loss_streak_freeze_sec
                    slog.info(
                        f"[SignalDeduper] 连败冻结 {key} streak={streak} "
                        f"freeze={self.loss_streak_freeze_sec}s pnl_r={pr:+.3f}"
                    )
            elif pr >= band:
                self._loss_streaks[key] = 0
                self._dir_freeze_until.pop(key, None)
            # else: 保本死区内，保持原 streak / freeze
            self._save()

    def restore_streaks_from_research_db(self) -> int:
        """从 v6_research.db 最近已平仓记录冷启动连败/冻结（抗 HF 重启丢 JSON）。

        对每个 (symbol, direction) 取最近若干笔已结算单，自新向旧扫描连续有效亏损。
        返回写入的 key 数量。
        """
        import sqlite3
        from pathlib import Path as _Path

        candidates = [
            _Path("data/v6_research.db"),
            _Path("/app/data/v6_research.db"),
            _Path(__file__).resolve().parents[1] / "data" / "v6_research.db",
        ]
        db_path = next((p for p in candidates if p.exists()), None)
        if db_path is None:
            return 0

        band = float(self.loss_deadband_r or 0.15)
        lookback = int(os.getenv("V6_STREAK_RESTORE_LOOKBACK", "8"))
        restored = 0
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                SELECT symbol, direction, pnl_r, exit_timestamp, exit_reason
                FROM trade_snapshots
                WHERE exit_reason IS NOT NULL
                  AND exit_reason NOT IN ('OPEN', 'MANUAL_CLEANUP_DEPRECATED', 'STALE_OPEN_TIMEOUT', '')
                  AND pnl_r IS NOT NULL
                ORDER BY COALESCE(exit_timestamp, timestamp) DESC
                LIMIT 200
                """
            )
            rows = cur.fetchall()
            conn.close()
        except Exception as e:
            slog.error(f"[SignalDeduper] restore query failed: {e}")
            return 0

        # group by symbol_direction, keep chronological newest-first already
        from collections import defaultdict
        groups = defaultdict(list)
        for r in rows:
            sym = str(r["symbol"] or "")
            d = str(r["direction"] or "")
            if not sym or not d:
                continue
            groups[(sym, d)].append(r)

        now = time.time()
        with self._lock:
            for (sym, d), items in groups.items():
                key = self._cooldown_key(sym, d)
                # 若 JSON 已有未过期冻结，不覆盖
                existing_until = float(self._dir_freeze_until.get(key, 0) or 0)
                if existing_until > now:
                    continue
                streak = 0
                last_exit_ts = 0.0
                for r in items[:lookback]:
                    try:
                        pr = float(r["pnl_r"])
                    except Exception:
                        break
                    if pr <= -band:
                        streak += 1
                        try:
                            last_exit_ts = max(last_exit_ts, float(r["exit_timestamp"] or 0))
                        except Exception:
                            pass
                    elif pr >= band:
                        break  # 遇到有效盈利，连败中断
                    else:
                        continue  # 保本跳过，不中断也不累加
                if streak <= 0:
                    continue
                self._loss_streaks[key] = max(int(self._loss_streaks.get(key, 0)), streak)
                if streak >= self.loss_streak_max:
                    # 冻结起点用最后一笔亏损时间，避免重启后重新给满 2h
                    base_ts = last_exit_ts if last_exit_ts > 0 else now
                    until = base_ts + self.loss_streak_freeze_sec
                    if until > now:
                        self._dir_freeze_until[key] = until
                        restored += 1
                        slog.info(
                            f"[SignalDeduper] 冷启动连败冻结 {key} streak={streak} "
                            f"剩余={int(until - now)}s (from research.db)"
                        )
                    else:
                        # 冻结期已过，仅保留 streak 供展示，不冻结
                        self._loss_streaks[key] = 0
            self._save()
        return restored

    def is_direction_frozen(self, symbol: str, direction: str | None) -> tuple[bool, str]:
        """True=该品种方向因连败仍在冻结。返回 (frozen, reason)。"""
        if not direction:
            return False, ""
        with self._lock:
            key = self._cooldown_key(symbol, str(direction))
            until = float(self._dir_freeze_until.get(key, 0) or 0)
            now = time.time()
            if until > now:
                left = int(until - now)
                streak = int(self._loss_streaks.get(key, 0))
                return True, f"连败冻结 streak={streak} 剩余{left}s"
            # 过期清理
            if key in self._dir_freeze_until:
                self._dir_freeze_until.pop(key, None)
            return False, ""

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
