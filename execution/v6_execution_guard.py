# -*- coding: utf-8 -*-
"""V6 execution guard — lightweight pre-trade checks.

主拦截仍在 PortfolioManager.can_open；这里做执行层双保险。
兼容旧调用签名；当传入 symbol + open_positions（或可从 recent_trades 推断）时启用高相关互斥。
"""
from __future__ import annotations

from typing import Any, Iterable, List, Optional


class V6ExecutionGuard:
    def __init__(self, params=None):
        self.params = params or {}
        self.major_correlated = set(self.params.get("major_correlated", ["BTC", "ETH"]))

    def _base(self, symbol: str) -> str:
        s = str(symbol or "").upper().replace(":USDT", "").replace("/USDT", "").replace("-USDT", "")
        for sep in ("/", ":", "-"):
            if sep in s:
                s = s.split(sep)[0]
                break
        return s

    def _normalize_positions(self, open_positions: Optional[Iterable[Any]], recent_trades: Optional[list]) -> List[Any]:
        """优先用显式 open_positions；否则尝试从 recent_trades 里抽仍 OPEN 的记录。"""
        if open_positions:
            return list(open_positions)

        inferred = []
        for t in (recent_trades or []):
            if not isinstance(t, dict):
                # 支持 Position 对象
                state = getattr(t, "state", None)
                if state and str(state).upper() not in ("OPEN",):
                    continue
                if getattr(t, "symbol", None) and getattr(t, "direction", None):
                    inferred.append(t)
                continue
            state = str(t.get("state") or t.get("status") or "").upper()
            # 若明确已平仓则跳过；无 state 字段时保守当作可能持仓
            if state in ("CLOSED", "FILLED_CLOSED", "DONE"):
                continue
            if t.get("symbol") and t.get("direction"):
                # 若带 remaining_size / size，要求 > 0
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

        # 高相关主流币同向互斥（执行层双保险）
        if symbol and positions:
            base = self._base(symbol)
            if base in self.major_correlated:
                for p in positions:
                    p_dir = getattr(p, "direction", None) if not isinstance(p, dict) else p.get("direction")
                    if str(p_dir) != str(direction):
                        continue
                    p_sym = getattr(p, "symbol", None) if not isinstance(p, dict) else p.get("symbol")
                    p_base = self._base(p_sym or "")
                    if p_base in self.major_correlated and p_base != base:
                        return {
                            "allowed": False,
                            "reason_cn": f"执行卫士拦截：高相关币同向已有 {p_sym}",
                            "direction": direction,
                        }

        return {"allowed": True, "reason_cn": "执行检查通过", "direction": direction}
