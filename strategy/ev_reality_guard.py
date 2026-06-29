# -*- coding: utf-8 -*-
"""
V37.5 EV Reality Guard

实时校准器：对比 EV 预测值与实际盈亏，输出校准系数。
防止引擎持续高估/低估，动态修正后续信号。
"""
from __future__ import annotations

from typing import Any


def ev_calibration_factor(realized_r: float, expected_ev: float) -> float:
    """
    防止系统长期偏乐观

    参数：
        realized_r: 实际累计盈亏
        expected_ev: 累计 EV 预测值

    返回：
        校准系数（0.6~1.4）
    """
    if expected_ev == 0:
        return 1.0
    ratio = realized_r / (expected_ev + 1e-9)
    return max(0.6, min(1.4, ratio))


class EVRealityGuard:
    """EV 现实校准器"""

    def __init__(self) -> None:
        self.ev_sum = 0.0
        self.realized_sum = 0.0
        self.trade_count = 0

    def update(self, ev: float, realized_r: float) -> None:
        """每笔交易结束后调用，记录 EV 预测 vs 实际盈亏"""
        self.ev_sum += float(ev)
        self.realized_sum += float(realized_r)
        self.trade_count += 1

    def calibration_factor(self) -> float:
        """返回校准系数：实际/预测比值，用于修正后续 EV"""
        return ev_calibration_factor(self.realized_sum, self.ev_sum)

    def status(self) -> str:
        """打印当前校准状态"""
        if self.trade_count == 0:
            return "EVRealityGuard: 无交易数据"
        cf = self.calibration_factor()
        return (
            f"EVRealityGuard: {self.trade_count}笔 | "
            f"EV累计={self.ev_sum:.4f} | "
            f"实际累计={self.realized_sum:.4f} | "
            f"校准系数={cf:.4f}"
        )
