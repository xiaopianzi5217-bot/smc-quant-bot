# -*- coding: utf-8 -*-
"""
TREND Engine: regime=TREND (ADX >= 23).
低确信，只采样不贡献 PnL。
"""
from __future__ import annotations

from typing import Any, Dict, Tuple
from dataclasses import dataclass, asdict


@dataclass
class TrendAccountState:
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


class TrendEngine:
    """
    TREND regime 专用引擎。
    仓位压缩 90%，纯采样本。
    """

    def __init__(self, base_risk: float = 0.12, min_expected_value: float = 0.10):
        self.base_risk = base_risk
        self.min_expected_value = min_expected_value
        self.account = TrendAccountState()

    def process(self, row: Any) -> float:
        ev = float(row.get("ev", row.get("pnl_r", 0)))
        # TREND 降权处理（避免反向 alpha 问题）
        return ev * 0.25

    def risk_budget(self, signal: Dict[str, Any], vol_state: str) -> float:
        ev = signal.get("expected_value", 0.0)
        confidence = signal.get("confidence", 0.0)
        edge_term = max(0.0, min(1.0, (ev - self.min_expected_value) / max(1e-9, 0.35 - self.min_expected_value)))
        risk = self.base_risk * (0.30 + 0.70 * confidence) * (0.30 + 0.70 * edge_term)
        risk *= 0.10
        if vol_state == "HIGH_VOL":
            risk *= 0.70
        elif vol_state == "LOW_VOL":
            risk *= 1.05
        if self.account.drawdown_pct_proxy > 0.15:
            risk *= 0.50
        if self.account.loss_streak >= 6:
            risk *= 0.50
        return max(0.0, min(risk, 0.40))

    def allocate(self, signal: Dict[str, Any], risk: float) -> Tuple[str, float]:
        ev = signal.get("expected_value", -9.0)
        if ev > 0.25:
            mult = 0.50
        elif ev > 0.15:
            mult = 0.30
        elif ev > 0.07:
            mult = 0.15
        else:
            mult = 0.05
        return "PROBE", max(0.0, min(0.40, risk * mult))

    def update_account(self, pnl_r: float) -> None:
        self.account.update(pnl_r)

    def state_dict(self) -> Dict[str, Any]:
        return asdict(self.account)
