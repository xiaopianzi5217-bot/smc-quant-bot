# -*- coding: utf-8 -*-
"""Centralized pre-trade risk helpers used by backtest/execution."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import math

from utils.safe import safe_float, safe_bool, safe_str


class CostRiskDecision:
    allow: bool
    reason: str
    position_multiplier: float
    cost_r_pretrade: float
    adaptive_cost_ceiling_r: float
    hard_cost_ceiling_r: float
    would_have_been_rejected_by_fixed_firewall: bool


class PreTradeRiskEngine:
    """Single place for deterministic execution-cost risk compression.

    The old runner embedded this logic inline, which made backtest another
    decision engine.  Keeping the same math here preserves behavior while making
    decision flow easier to audit.
    """

    def __init__(self, base_cost_ceiling_r: float = 0.38, hard_cost_ceiling_r: float = 1.30) -> None:
        self.base_cost_ceiling_r = float(base_cost_ceiling_r)
        self.hard_cost_ceiling_r = float(hard_cost_ceiling_r)

    def evaluate_transaction_cost(self, cost_r: float, atr_now: float, avg_atr: float) -> CostRiskDecision:
        cost_r = safe_float(cost_r, 0.0)
        atr_now = max(safe_float(atr_now, 0.0), 1e-12)
        avg_atr = max(safe_float(avg_atr, atr_now), 1e-12)

        atr_regime_ratio = max(0.35, min(2.50, atr_now / avg_atr))
        adaptive_ceiling = max(0.22, min(1.35, self.base_cost_ceiling_r * atr_regime_ratio))
        fixed_firewall_reject = bool(cost_r > self.base_cost_ceiling_r)

        if cost_r > self.hard_cost_ceiling_r:
            return CostRiskDecision(
                allow=False,
                reason=f"REJECT_EXTREME_COST_{round(cost_r, 3)}",
                position_multiplier=0.0,
                cost_r_pretrade=round(float(cost_r), 4),
                adaptive_cost_ceiling_r=round(float(adaptive_ceiling), 4),
                hard_cost_ceiling_r=round(float(self.hard_cost_ceiling_r), 4),
                would_have_been_rejected_by_fixed_firewall=fixed_firewall_reject,
            )

        multiplier = 1.0
        reason = "COST_OK"
        if cost_r > adaptive_ceiling:
            ratio = adaptive_ceiling / max(cost_r, 1e-12)
            multiplier = max(0.05, min(1.0, ratio ** 1.25))
            if cost_r > 0.55:
                multiplier *= 0.70
            if cost_r > 0.80:
                multiplier *= 0.60
            multiplier = max(0.0, min(1.0, multiplier))
            reason = f"CONVEX_COST_COMPRESS_{round(multiplier, 3)}"

        return CostRiskDecision(
            allow=True,
            reason=reason,
            position_multiplier=round(float(multiplier), 6),
            cost_r_pretrade=round(float(cost_r), 4),
            adaptive_cost_ceiling_r=round(float(adaptive_ceiling), 4),
            hard_cost_ceiling_r=round(float(self.hard_cost_ceiling_r), 4),
            would_have_been_rejected_by_fixed_firewall=fixed_firewall_reject,
        )

