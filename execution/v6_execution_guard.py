# -*- coding: utf-8 -*-
"""V6 execution guard — lightweight pre-trade checks.

主拦截仍在 PortfolioManager.can_open；这里做兼容增强（可选 symbol/open_positions）。
旧调用签名保持不变，不会破坏现有 decide()。
"""


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

    def check(self, curr, direction, recent_trades=None, bar_index=None, symbol=None, open_positions=None):
        """兼容旧调用：decide() 只传 curr/direction/recent_trades/bar_index。"""
        recent_trades = recent_trades or []
        open_positions = open_positions or []

        # 可选双保险：高相关主流币同向互斥
        if symbol and open_positions:
            base = self._base(symbol)
            if base in self.major_correlated:
                for p in open_positions:
                    p_dir = getattr(p, "direction", None) if not isinstance(p, dict) else p.get("direction")
                    if p_dir != direction:
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
