# -*- coding: utf-8 -*-
"""
TRANSITION Engine (主 Alpha).

regime=TRANSITION (15 < ADX < 23).
当前最强 regime，只接受 A_EV 以上信号。
"""
from __future__ import annotations

from typing import Any, Dict, Tuple
from dataclasses import dataclass, asdict


@dataclass
class TransitionAccountState:
    equity_r: float = 0.0
    peak_equity_r: float = 0.0
    drawdown_r: float = 0.0
    drawdown_pct_proxy: float = 0.0
    loss_streak: int = 0
    trade_count: int = 0

    def update(self, pnl_r: float) -> None:
        self.trade_count += 1
        self.equity_r += pnl_r
        self.peak_equity_r = max(self.peak_equity_r, self.equity_r)
        self.drawdown_r = max(0.0, self.peak_equity_r - self.equity_r)
        self.drawdown_pct_proxy = min(1.0, self.drawdown_r / 10.0)
        self.loss_streak = self.loss_streak + 1 if pnl_r < 0 else 0


class TransitionEngine:
    """
    TRANSITION regime 专用引擎。
    process() 返回 pnl_r。
    """

    def __init__(self, base_risk: float = 0.15, min_expected_value: float = 0.10):
        self.base_risk = base_risk
        self.min_expected_value = min_expected_value
        self.account = TransitionAccountState()

    def process(self, row: Any) -> float:
        ev = float(row.get("pnl_r", row.get("ev", 0)))
        grade = str(row.get("grade", "D_NEG_EV"))
        if ev > 0 and grade == "A_EV":
            return ev
        return 0.0

    def risk_budget(self, signal: Dict[str, Any], vol_state: str) -> float:
        ev = signal.get("expected_value", 0.0)
        confidence = signal.get("confidence", 0.0)
        edge_term = max(0.0, min(1.0, (ev - self.min_expected_value) / max(1e-9, 0.40 - self.min_expected_value)))
        risk = self.base_risk * (0.40 + 0.60 * confidence) * (0.40 + 0.60 * edge_term)
        risk *= 1.30
        if vol_state == "HIGH_VOL":
            risk *= 0.70
        elif vol_state == "LOW_VOL":
            risk *= 1.05
        if self.account.drawdown_pct_proxy > 0.20:
            risk *= 0.35
        elif self.account.drawdown_pct_proxy > 0.10:
            risk *= 0.55
        if self.account.loss_streak >= 6:
            risk *= 0.35
        elif self.account.loss_streak >= 3:
            risk *= 0.60
        return max(0.0, min(risk, 0.40))

    def allocate(self, signal: Dict[str, Any], risk: float) -> Tuple[str, float]:
        ev = signal.get("expected_value", -9.0)
        if ev > 0.25:
            mult = 1.20
        elif ev > 0.15:
            mult = 0.90
        elif ev > 0.07:
            mult = 0.60
        else:
            mult = 0.10
        grade = signal.get("ev_grade", "D_NEG_EV")
        if grade == "S_EV_HOT":
            mult *= 1.08
        return "CORE" if ev > 0.25 else "PROBE", max(0.0, min(0.40, risk * mult))

    def update_account(self, pnl_r: float) -> None:
        self.account.update(pnl_r)

    def state_dict(self) -> Dict[str, Any]:
        return asdict(self.account)
