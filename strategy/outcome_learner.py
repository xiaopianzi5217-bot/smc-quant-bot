# -*- coding: utf-8 -*-
"""
SimpleOutcomeLearner — 基于历史交易结果的 EV 预测器

记录每笔交易的特征与实际盈亏，用最近 N 笔均值预测新信号的 EV。
不引入外部依赖，不侵入现有评分/过滤逻辑。
"""

from __future__ import annotations
from typing import Any, Dict, List, Tuple


class SimpleOutcomeLearner:
    """轻量级 outcome learner。

    - update(features, realized_r): 记录一笔已平仓交易
    - predict_ev(features) -> float: 返回最近窗口的平均盈亏
    """

    def __init__(self, max_history: int = 300):
        self.history: List[Tuple[Dict[str, Any], float]] = []
        self.max_history = max_history

    def update(self, features: Dict[str, Any], realized_r: float) -> None:
        """记录一笔已平仓交易。"""
        self.history.append((features, realized_r))
        if len(self.history) > self.max_history:
            self.history.pop(0)

    def predict_ev(self, features: Dict[str, Any]) -> float:
        """用最近窗口（默认 80 笔）的平均 R 作为 EV 估计。"""
        if not self.history:
            return 0.0
        window = self.history[-80:]
        avg_r = sum(r for _, r in window) / len(window)
        return round(avg_r, 4)

    @property
    def size(self) -> int:
        return len(self.history)
