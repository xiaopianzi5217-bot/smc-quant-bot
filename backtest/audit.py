# -*- coding: utf-8 -*-
"""Backtest audit funnel for diagnosing why trades == 0. 把这个文件放到项目：backtest/audit.py 然后在你的回测主循环里接入 BacktestAudit。 它不会改变策略开单逻辑，只统计每一层过滤剩多少、最大分数、失败原因。 """
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import json
import math


_BLOCK_VALUES = {None, "", "N/A", "None", "nan", "NaN"}


def _valid(v: Any) -> bool:
    if v in _BLOCK_VALUES:
        return False
    try:
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return False
    except Exception:
        pass
    return True


def _f(v: Any, default: float = 0.0) -> float:
    if not _valid(v):
        return default
    try:
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return default
        return x
    except Exception:
        return default


def _b(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in {"true", "yes", "y", "1", "pass", "allow", "allowed", "ok"}
    return bool(v)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    if hasattr(obj, key):
        return getattr(obj, key)
    if hasattr(obj, "to_dict"):
        try:
            return obj.to_dict().get(key, default)
        except Exception:
            return default
    return default


@dataclass
class BacktestAudit:
    """Filter funnel recorder for a backtest run."""

    sample_limit: int = 30
    bars: int = 0
    counters: Counter = field(default_factory=Counter)
    fail_reasons: Counter = field(default_factory=Counter)
    symbol_counters: Dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    max_values: Dict[str, float] = field(default_factory=lambda: defaultdict(float))
    samples: List[Dict[str, Any]] = field(default_factory=list)

    def mark(self, key: str, symbol: str = "UNKNOWN", inc: int = 1) -> None:
        self.counters[key] += inc
        self.symbol_counters[str(symbol)][key] += inc

    def fail(self, reason: str, symbol: str = "UNKNOWN", ctx: Optional[Dict[str, Any]] = None) -> None:
        reason = str(reason or "unknown")
        self.fail_reasons[reason] += 1
        self.symbol_counters[str(symbol)][f"fail:{reason}"] += 1
        if ctx and len(self.samples) < self.sample_limit:
            row = {"reason": reason}
            row.update(ctx)
            self.samples.append(row)

    def update_max(self, key: str, value: Any) -> None:
        self.max_values[key] = max(float(self.max_values.get(key, 0.0)), _f(value, 0.0))

    def inspect_bar( self, *, symbol: str, i: int, price: Any = None, long_score: Any = 0, short_score: Any = 0, threshold_long: Any = 1, threshold_short: Any = 1, direction: Any = None, funding_status: Any = None, funding_allowed: Any = None, rr: Any = None, entry_signal: Any = None, in_position: bool = False, extra: Optional[Dict[str, Any]] = None, ) -> Dict[str, Any]:
        """Call once per candle after all indicators/scores/filters are calculated. Returns a normalized audit context you can also print when needed. """
        self.bars += 1
        symbol = str(symbol or "UNKNOWN")
        self.mark("bars", symbol)

        ls = _f(long_score)
        ss = _f(short_score)
        tl = max(_f(threshold_long, 1.0), 1e-9)
        ts = max(_f(threshold_short, 1.0), 1e-9)
        rr_v = _f(rr)

        self.update_max("long_score_max", ls)
        self.update_max("short_score_max", ss)
        self.update_max("long_score_pct_max", ls / tl * 100.0)
        self.update_max("short_score_pct_max", ss / ts * 100.0)
        self.update_max("rr_max", rr_v)

        ctx: Dict[str, Any] = {
            "symbol": symbol,
            "i": i,
            "price": price,
            "long_score": ls,
            "short_score": ss,
            "threshold_long": tl,
            "threshold_short": ts,
            "long_pct": round(ls / tl * 100.0, 2),
            "short_pct": round(ss / ts * 100.0, 2),
            "direction": direction,
            "funding_status": funding_status,
            "funding_allowed": funding_allowed,
            "rr": rr,
            "entry_signal": entry_signal,
            "in_position": in_position,
        }
        if extra:
            ctx.update(extra)

        if in_position:
            self.mark("blocked_in_position", symbol)
            self.fail("already_in_position", symbol, ctx)
            return ctx

        long_pass = ls >= tl
        short_pass = ss >= ts
        if long_pass:
            self.mark("long_score_pass", symbol)
        if short_pass:
            self.mark("short_score_pass", symbol)
        if not long_pass and not short_pass:
            self.fail("score_below_threshold", symbol, ctx)
            return ctx

        self.mark("score_pass_any", symbol)

        d = str(direction or "").lower()
        direction_pass = (
            (long_pass and d in {"long", "buy", "bull", "bullish", "多", "看多", "偏多"})
            or (short_pass and d in {"short", "sell", "bear", "bearish", "空", "看空", "偏空"})
            or d in {"", "none", "n/a", "neutral"}
        )
        if direction_pass:
            self.mark("direction_pass", symbol)
        else:
            self.fail("direction_mismatch", symbol, ctx)
            return ctx

        # funding_allowed=None 表示当前回测没有启用/没有提供该过滤，不当作失败。
        if funding_allowed is not None and not _b(funding_allowed):
            self.fail("funding_block", symbol, ctx)
            return ctx
        self.mark("funding_pass_or_unused", symbol)

        # rr=None / N/A 表示当前回测没有提供，不当作失败；rr<=0 才视为异常。
        if _valid(rr) and rr_v <= 0:
            self.fail("rr_invalid", symbol, ctx)
            return ctx
        if _valid(rr):
            self.mark("rr_pass", symbol)
        else:
            self.mark("rr_unused", symbol)

        if entry_signal is not None and not _b(entry_signal):
            self.fail("entry_signal_false", symbol, ctx)
            return ctx

        self.mark("final_entry_candidate", symbol)
        return ctx

    def mark_trade_opened(self, symbol: str = "UNKNOWN") -> None:
        self.mark("trade_opened", str(symbol or "UNKNOWN"))

    def summary(self) -> Dict[str, Any]:
        return {
            "bars": self.bars,
            "funnel": dict(self.counters),
            "fail_reasons_top": dict(self.fail_reasons.most_common(20)),
            "max_values": {k: round(v, 4) for k, v in self.max_values.items()},
            "by_symbol": {k: dict(v) for k, v in self.symbol_counters.items()},
            "samples": self.samples,
        }

    def print_summary(self) -> None:
        print("\n========== BACKTEST AUDIT / 开单漏斗诊断 ==========")
        print(json.dumps(self.summary(), ensure_ascii=False, indent=2))
        print("==================================================\n")