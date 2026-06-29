"""
TradeDependencyGraph — 交易短期记忆库

用于记录近期交易结果，计算当前信号的惩罚乘数。
避免在连续亏损后继续加码，实现贝叶斯惩罚阶梯。
"""
from typing import List, Dict, Optional


class TradeDependencyGraph:
    """
    交易短期记忆库

    实盘/回测中，平仓后必须调用 update_history()，
    将盈亏结果喂给系统，系统会自动追踪同方向的连续亏损次数，
    并计算惩罚乘数。
    """

    def __init__(self, max_memory: int = 10):
        """
        初始化交易短期记忆库

        Args:
            max_memory: 记住最近 N 笔交易（超过则遗忘，防止过度拟合）
        """
        self.trade_history: List[Dict] = []
        self.max_memory = max_memory

    def update_history(self, trade_result: Dict) -> None:
        """
        实盘/回测中，平仓后必须调用此方法，将盈亏结果喂给系统

        Args:
            trade_result: {
                "direction": "Long" | "Short",
                "pnl": float,         # 盈亏比例 (如 -0.05 表示 -5%)
                "cluster": str,        # 簇标识 (可选)
            }
        """
        self.trade_history.append(trade_result)

        # 维持记忆容量
        if len(self.trade_history) > self.max_memory:
            self.trade_history.pop(0)

    def compute_penalty(self, current_signal: Dict) -> float:
        """
        计算当前信号的惩罚乘数 (0.0 ~ 1.0)

        贝叶斯惩罚阶梯:
            - 0 次连续亏损 → 1.0  (满血输出)
            - 1 次连续亏损 → 0.7  (打7折)
            - 2 次连续亏损 → 0.3  (打3折)
            - 3 次及以上   → 0.0  (熔断，拒绝交易)

        Args:
            current_signal: {
                "direction": "Long" | "Short",
                "cluster": str,  # 可选
            }

        Returns:
            float: 惩罚乘数 (0.0 ~ 1.0)
        """
        if not self.trade_history:
            return 1.0  # 没有任何历史包袱，满血输出

        consecutive_losses = 0

        # 倒序遍历（从最近的一笔交易开始看）
        for trade in reversed(self.trade_history):
            # 核心：只统计【相同方向】的连续亏损
            # (如果刚刚做多连亏2次，现在出做空信号，不应被惩罚)
            if trade.get("direction") == current_signal.get("direction"):
                if trade.get("pnl", 0) < 0:
                    consecutive_losses += 1
                else:
                    # 遇到同方向的盈利单，说明猎杀期结束，逻辑重置
                    break
            # 不同方向的交易，不计数也不中断（继续向前看）

        # 贝叶斯惩罚阶梯 (Bayesian Penalty Escalation)
        if consecutive_losses == 0:
            return 1.0   # 状态火热，不降权
        elif consecutive_losses == 1:
            return 0.7   # 亏1次，仓位打 7 折 (保留实力)
        elif consecutive_losses == 2:
            return 0.3   # 亏2次，仓位打 3 折 (试探性观察仓)
        else:
            return 0.0   # 亏3次及以上，直接熔断！(拒绝交易)