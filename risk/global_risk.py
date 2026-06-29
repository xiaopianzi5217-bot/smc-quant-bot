# -*- coding: utf-8 -*-
"""Portfolio-level risk checks that do not alter V9 signal logic.

These checks are intended to sit after decision approval and before paper/live
execution. They protect capital without changing the strategy scoring pipeline.
"""
from dataclasses import dataclass, asdict


@dataclass
class GlobalRiskState:
    equity: float = 10000.0
    peak_equity: float = 10000.0
    daily_pnl_r: float = 0.0
    open_positions: int = 0
    same_direction_positions: int = 0
    consecutive_losses: int = 0


class GlobalRiskGuard:
    def __init__(self, cfg=None):
        cfg = cfg or {}
        r = cfg.get("risk", cfg)
        self.max_open_positions = int(r.get("max_open_positions", 3))
        self.max_same_direction_positions = int(r.get("max_same_direction_positions", 2))
        self.max_daily_loss_r = float(r.get("max_daily_loss_r", 3.0))
        self.max_drawdown_pct = float(r.get("max_drawdown_pct", 0.12))
        self.max_consecutive_losses = int(r.get("max_consecutive_losses", 4))

    def check(self, state=None):
        state = state or GlobalRiskState()
        reasons = []
        if state.open_positions >= self.max_open_positions:
            reasons.append("open position limit reached")
        if state.same_direction_positions >= self.max_same_direction_positions:
            reasons.append("same direction position limit reached")
        if state.daily_pnl_r <= -abs(self.max_daily_loss_r):
            reasons.append("daily R loss limit reached")
        if state.peak_equity > 0:
            dd = (state.peak_equity - state.equity) / state.peak_equity
            if dd >= self.max_drawdown_pct:
                reasons.append("max drawdown limit reached")
        if state.consecutive_losses >= self.max_consecutive_losses:
            reasons.append("consecutive loss limit reached")
        return {"allowed": not reasons, "reasons": reasons, "state": asdict(state)}
