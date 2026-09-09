# -*- coding: utf-8 -*-
"""V6 execution guard — pre-trade checks.

功能:
1) 高相关主流币互斥（BTC/ETH）
   - mutex_mode=same_dir: 仅同向互斥（默认旧行为）
   - mutex_mode=any:      组内同时只允许一笔，不论方向（防对冲磨损）
2) 兼容旧调用签名；传 symbol + open_positions 时生效。
"""
from __future__ import annotations

import os
from typing import Any, Iterable, List, Optional


class V6ExecutionGuard:
    def __init__(self, params=None):
        self.params = params or {}
        self.major_correlated = set(
            self.params.get("major_correlated")
            or [x.strip().upper() for x in os.getenv("V6_MAJOR_CORRELATED", "BTC,ETH").split(",") if x.strip()]
        )
        # same_dir | any
        self.mutex_mode = str(
            self.params.get("mutex_mode") or os.getenv("V6_CORRELATED_MUTEX", "any")
        ).strip().lower()
        if self.mutex_mode not in ("same_dir", "any"):
            self.mutex_mode = "any"

    def _base(self, symbol: str) -> str:
        s = str(symbol or "").upper().replace(":USDT", "").replace("/USDT", "").replace("-USDT", "")
        for sep in ("/", ":", "-"):
            if sep in s:
                s = s.split(sep)[0]
                break
        return s

    def _normalize_positions(self, open_positions: Optional[Iterable[Any]], recent_trades: Optional[list]) -> List[Any]:
        if open_positions:
            return list(open_positions)

        inferred = []
        for t in (recent_trades or []):
            if not isinstance(t, dict):
                state = getattr(t, "state", None)
                if state and str(state).upper() not in ("OPEN",):
                    continue
                if getattr(t, "symbol", None) and getattr(t, "direction", None):
                    inferred.append(t)
                continue
            state = str(t.get("state") or t.get("status") or "").upper()
            if state in ("CLOSED", "FILLED_CLOSED", "DONE"):
                continue
            if t.get("symbol") and t.get("direction"):
                rem = t.get("remaining_size", t.get("size", t.get("qty")))
                if rem is not None:
                    try:
                        if float(rem) <= 0:
                            continue
                    except Exception:
                        pass
                inferred.append(t)
        return inferred

    def check(
        self,
        curr,
        direction,
        recent_trades=None,
        bar_index=None,
        symbol=None,
        open_positions=None,
    ):
        """兼容旧调用：decide() 可只传 curr/direction/recent_trades/bar_index。"""
        positions = self._normalize_positions(open_positions, recent_trades)

        if not symbol or not positions:
            return {"allowed": True, "reason_cn": "执行检查通过", "direction": direction}

        base = self._base(symbol)
        if base not in self.major_correlated:
            return {"allowed": True, "reason_cn": "执行检查通过", "direction": direction}

        dir_norm = str(direction or "").strip().lower()
        for p in positions:
            p_dir = getattr(p, "direction", None) if not isinstance(p, dict) else p.get("direction")
            p_sym = getattr(p, "symbol", None) if not isinstance(p, dict) else p.get("symbol")
            p_base = self._base(p_sym or "")
            if p_base not in self.major_correlated:
                continue
            if p_base == base:
                # 同品种由上层 OPEN 拦截处理
                continue

            if self.mutex_mode == "any":
                return {
                    "allowed": False,
                    "reason_cn": f"执行卫士拦截：高相关币组已有持仓 {p_sym} {p_dir}（mutex=any）",
                    "direction": direction,
                }

            # same_dir
            if str(p_dir or "").strip().lower() == dir_norm and dir_norm:
                return {
                    "allowed": False,
                    "reason_cn": f"执行卫士拦截：高相关币同向已有 {p_sym}",
                    "direction": direction,
                }

        return {"allowed": True, "reason_cn": "执行检查通过", "direction": direction}
