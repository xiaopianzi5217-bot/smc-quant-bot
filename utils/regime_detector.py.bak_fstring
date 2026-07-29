# -*- coding: utf-8 -*-
"""
Regime Transition Detector — 检测市场体制过渡并给出风控建议

用法：
    machine = RegimeStateMachine()
    state = machine.update(regime, adx)
    if state["is_transition"]:
        size_mult *= state["size_mult"]
"""

from datetime import datetime
from typing import Optional


def detect_transition(old_regime: Optional[str], new_regime: str, adx: float) -> tuple:
    """检测 regime 过渡并给出风控建议

    Args:
        old_regime: 上一个体制（None 表示首次）
        new_regime: 当前体制
        adx: 当前 ADX 值

    Returns:
        (is_transition: bool, reason: str, size_mult: float)
    """
    if old_regime is None:
        return False, "", 1.0

    if old_regime == "trend" and new_regime in ("mud", "transition") and adx < 20:
        return True, "TREND_TO_RANGE", 0.6
    if old_regime == "mud" and new_regime == "trend" and adx > 22:
        return True, "MUD_TO_TREND", 1.0
    if old_regime != new_regime and "transition" in (old_regime, new_regime):
        return True, "TRANSITION", 0.7
    return False, "", 1.0


class RegimeStateMachine:
    """Regime 状态跟踪器 — 维护历史并自动 detect transition"""

    def __init__(self):
        self.history: list = []  # [(timestamp, regime, adx), ...]

    def update(self, regime: str, adx: float) -> dict:
        """传入最新 regime，返回过渡检测结果

        Returns:
            {"is_transition": bool, "reason": str, "size_mult": float}
        """
        old = self.history[-1][1] if self.history else None
        is_trans, reason, mult = detect_transition(old, regime, adx)

        self.history.append((datetime.now(), regime, adx))
        if len(self.history) > 100:
            self.history.pop(0)

        return {
            "is_transition": is_trans,
            "reason": reason,
            "size_mult": mult,
        }

    def clear(self) -> None:
        """重置状态"""
        self.history.clear()
