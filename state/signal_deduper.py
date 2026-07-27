# -*- coding: utf-8 -*-
"""
P0-3: 统一信号去重服务 SignalDeduper

职责：
  消除分散在多处的冷却/去重逻辑（SIGNAL_COOLDOWN、processed_signals TTL、止损冷却），
  统一管理已处理信号的 TTL 过期和持久化。

用法：
  from state.signal_deduper import signal_deduper

  # 判断是否已处理
  if not signal_deduper.should_process("BTC/USDT:USDT", "Long", signal_id):
      print("信号已被处理或冷却中")

  # 标记为已处理（自动记录时间戳，自动清理过期）
  signal_deduper.mark_processed("BTC/USDT:USDT", "Long", signal_id)

  # 检查同品种同方向冷却（避免重复开仓）
  if not signal_deduper.is_symbol_cooled("BTC/USDT:USDT", "Long"):
      print("该品种方向冷却中")

设计：
  - 内存使用 OrderedDict，LRU 风格清理
  - 持久化到 state/signal_deduper.json
  - 默认 TTL: 24 小时（可配置）
  - 线程安全（threading.Lock）
"""

import json
import os
import threading
import time
from collections import OrderedDict
from typing import Optional

DEFAULT_TTL_SEC = 86400  # 24 小时
PERSIST_PATH = "state/signal_deduper.json"


class SignalDeduper:
    """统一信号去重服务。"""

    def __init__(self, ttl_sec: int = DEFAULT_TTL_SEC):
        self._ttl = ttl_sec
        self._lock = threading.Lock()
        self._signals: OrderedDict[str, float] = OrderedDict()
        self._loaded = False

    # ── 持久化 ──────────────────────────────────────────────

    def _load(self):
        if self._loaded:
            return
        self._loaded = True
        if not os.path.exists(PERSIST_PATH):
            return
        try:
            with open(PERSIST_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return
            now = time.time()
            cutoff = now - self._ttl
            for k, v in data.items():
                if isinstance(k, str) and isinstance(v, (int, float)) and float(v) >= cutoff:
                    self._signals[k] = float(v)
        except Exception:
            pass

    def _save(self):
        try:
            os.makedirs(os.path.dirname(PERSIST_PATH) or ".", exist_ok=True)
            serialized = json.dumps(self._signals, ensure_ascii=False, default=str)
            with open(PERSIST_PATH + ".tmp", "w", encoding="utf-8") as f:
                f.write(serialized)
            os.replace(PERSIST_PATH + ".tmp", PERSIST_PATH)
        except Exception:
            pass

    # ── 核心方法 ────────────────────────────────────────────

    def _make_key(self, symbol: str, direction: str, signal_id: str = "") -> str:
        """生成唯一键，区分精确信号和品种冷却。"""
        return f"{symbol}|{direction}|{signal_id}"

    def _prune_expired(self):
        now = time.time()
        cutoff = now - self._ttl
        expired = [k for k, v in self._signals.items() if v < cutoff]
        for k in expired:
            del self._signals[k]

    def should_process(self, symbol: str, direction: str, signal_id: str = "") -> bool:
        """
        判断该信号是否应该处理。
        返回 True 表示「未被处理过，可以处理」
        """
        with self._lock:
            self._load()
            key = self._make_key(symbol, direction, signal_id)
            return key not in self._signals

    def mark_processed(self, symbol: str, direction: str, signal_id: str = "") -> None:
        """标记信号为已处理。"""
        with self._lock:
            self._load()
            key = self._make_key(symbol, direction, signal_id)
            self._signals[key] = time.time()
            self._prune_expired()
        self._save()

    def is_symbol_cooled(self, symbol: str, direction: str) -> bool:
        """
        判断品种+方向是否在冷却中。
        冷却条件：3 分钟（180秒）内有同方向任意信号被处理过。
        返回 True = 正在冷却，不应当开仓
        """
        with self._lock:
            self._load()
            now = time.time()
            for k, ts in list(self._signals.items()):
                # 匹配品种+方向，忽略具体 signal_id
                prefix = self._make_key(symbol, direction, "")
                if k.startswith(prefix) and (now - ts) < 180:
                    return True
            return False

    def clear_symbol(self, symbol: str) -> int:
        """清除指定品种的所有记录。返回清除数。"""
        with self._lock:
            self._load()
            before = len(self._signals)
            self._signals = OrderedDict(
                (k, v) for k, v in self._signals.items() if not k.startswith(f"{symbol}|")
            )
            cleared = before - len(self._signals)
        if cleared:
            self._save()
        return cleared

    def clear_all(self) -> int:
        """清除所有记录。"""
        with self._lock:
            self._load()
            count = len(self._signals)
            self._signals.clear()
        if count:
            self._save()
        return count

    def get_stats(self) -> dict:
        """返回当前去重器统计。"""
        with self._lock:
            self._load()
            self._prune_expired()
            return {
                "total_records": len(self._signals),
                "ttl_sec": self._ttl,
            }


# 单例
signal_deduper = SignalDeduper()
