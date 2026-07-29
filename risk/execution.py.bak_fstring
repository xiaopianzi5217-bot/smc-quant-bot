"""
Execution Engine — 交易执行模块

处理最终的订单执行、滑点、手续费等。
"""
from typing import Dict, Optional


class ExecutionEngine:
    """
    执行引擎

    将仓位画像转换为实际可执行的交易指令。
    """

    def __init__(self):
        self.slippage_pct = 0.001   # 0.1% 滑点
        self.commission_pct = 0.001  # 0.1% 手续费

    def execute(self, profile: Dict, market_price: float, direction: str = "LONG") -> Dict:
        """
        执行交易。

        Args:
            profile: 仓位画像 (来自 ProfileEngine)
            market_price: 当前市场价格
            direction: "LONG" | "SHORT"

        Returns:
            dict: {
                "executed": bool,
                "order": dict,      # 订单详情
                "cost": float,      # 总成本
            }
        """
        size = profile.get("position_size", 0.0)
        confidence = profile.get("confidence", 0.0)

        if size <= 0 or confidence <= 0:
            return {
                "executed": False,
                "reason": "Size or confidence is zero",
            }

        # 计算执行价格（含滑点）
        slip = market_price * self.slippage_pct
        if direction == "LONG":
            exec_price = market_price + slip
        else:
            exec_price = market_price - slip

        # 计算成本
        cost = exec_price * size * (1 + self.commission_pct)

        order = {
            "direction": direction,
            "size_pct": size,
            "exec_price": exec_price,
            "market_price": market_price,
            "slippage": slip,
            "commission": exec_price * size * self.commission_pct,
        }

        return {
            "executed": True,
            "order": order,
            "cost": float(cost),
        }