# -*- coding: utf-8 -*-
"""
V38 Exit Manager

真实退出管理器：
1. 不做 TP1
2. 分状态追踪止损
3. 保本锁仓
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ExitState:
    stop_price: float
    peak_r: float = 0.0
    locked: bool = False


class ExitManagerV38:

    def update(
        self,
        state: ExitState,
        entry: float,
        current: float,
        risk: float,
        atr: float,
        regime: str,
        impulse_strength: float = 0.5,
    ) -> ExitState:
        pnl_r = (current - entry) / risk if risk > 0 else 0
        state.peak_r = max(state.peak_r, pnl_r)

        # 不做 TP1

        # 保本锁仓：涨到 2.0R 再拉保本，给趋势足够喘息空间
        # 【修复20260625】原 1.5R 在 15M 级别仍然容易被插针扫掉
        if pnl_r >= 1.9 and not state.locked:
            state.stop_price = entry + risk * 0.05
            state.locked = True

                        # 自适应 Trailing
        if regime == "TREND":
            trail_mult = 5.0
            if impulse_strength > 0.75:
                trail_mult = 6.5   # 最终加强
            state.stop_price = max(state.stop_price, current - atr * trail_mult)
        elif regime == "TRANSITION":
            trail = max(
                atr * 3.0,                  # 从 1.8→3.0
                state.peak_r * 0.15 * risk
            )
            state.stop_price = max(state.stop_price, current - trail)
        else:
            trail = atr * 2.0               # 从 1.2→2.0
            state.stop_price = max(state.stop_price, current - trail)

        return state

