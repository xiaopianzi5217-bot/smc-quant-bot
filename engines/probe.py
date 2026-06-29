# -*- coding: utf-8 -*-
"""
PROBE Engine: 完全隔离的尝鲜仓。
不与其他引擎共享账户状态。
仓位上限 0.32。
"""
from __future__ import annotations

from typing import Any, Dict, Tuple
from dataclasses import dataclass, asdict


@dataclass
class ProbeAccountState:
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


class ProbeEngine:
    """
    PROBE book 专用引擎。完全隔离。
    只接受 pnl_r > 0 的交易。
    """

    def __init__(self, base_risk: float = 0.08, min_expected_value: float = 0.0):
        self.base_risk = base_risk
        self.min_expected_value = min_expected_value
        self.account = ProbeAccountState()

    def process(self, row: Any) -> float:
        # ❌ 不参与任何 PnL / AVS
        return 0.0

    def should_trade(self, signal: Dict[str, Any]) -> Tuple[bool, str]:
        ev = signal.get("expected_value", -9.0)
        if ev < self.min_expected_value:
            return False, f"PROBE_SKIP_NEG_EV_{round(ev, 4)}"
        win_prob = signal.get("win_prob", 0.0)
        if win_prob < 0.35:
            return False, f"PROBE_SKIP_LOW_WIN_PROB_{round(win_prob, 4)}"
        return True, "PROBE_ALLOW"

    def risk_budget(self, signal: Dict[str, Any], regime: str, vol_state: str) -> float:
        ev = signal.get("expected_value", 0.0)
        confidence = signal.get("confidence", 0.0)
        edge_term = max(0.0, min(1.0, ev / max(1e-9, 0.30)))
        risk = self.base_risk * (0.30 + 0.70 * confidence) * (0.30 + 0.70 * edge_term)
        if regime == "TREND":
            risk *= 0.10
        elif regime == "TRANSITION":
            risk *= 1.30
        elif regime == "CHOP":
            risk *= 0.60
        if vol_state == "HIGH_VOL":
            risk *= 0.70
        if self.account.loss_streak >= 6:
            risk *= 0.50
        elif self.account.loss_streak >= 3:
            risk *= 0.75
        return max(0.0, min(risk, 0.32))

    def allocate(self, signal: Dict[str, Any], risk: float) -> Tuple[str, float]:
        ev = signal.get("expected_value", -9.0)
        if ev > 0.25:
            mult = 0.50
        elif ev > 0.15:
            mult = 0.40
        elif ev > 0.07:
            mult = 0.32
        else:
            mult = 0.15
        return "PROBE", max(0.0, min(0.32, risk * mult))

    def update_account(self, pnl_r: float) -> None:
        self.account.update(pnl_r)

    def state_dict(self) -> Dict[str, Any]:
        return asdict(self.account)
