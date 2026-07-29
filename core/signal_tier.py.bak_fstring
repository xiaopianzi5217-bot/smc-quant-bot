# -*- coding: utf-8 -*-
"""V38 institutional signal tiering and regime-adaptive entry helpers.

This module keeps the trade-frequency / win-rate / PF trade-off explicit.
It does not create new indicators; it converts the existing SMC, SQZMOM,
scorecard, regime and EV fields into three execution tiers:

Tier 1  HIGH_PRECISION  : fewer trades, highest confidence.
Tier 2  BALANCED        : main book, balanced frequency and quality.
Tier 3  EXPLORATION     : controlled small-size entries to raise coverage.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, Tuple
import math

from utils.safe import safe_float, safe_bool, safe_str





class TierDecision:
    trade_allowed: bool
    tier: int
    tier_name: str
    threshold: float
    rank_score: float
    position_multiplier: float
    confirm_count: int
    confirmation_flags: Dict[str, bool]
    recovery_allowed: bool
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["rank_score"] = round(float(d["rank_score"]), 6)
        d["position_multiplier"] = round(float(d["position_multiplier"]), 6)
        d["threshold"] = round(float(d["threshold"]), 6)
        return d


def dynamic_ev_threshold(regime: Any, vol_state: Any, tier: int) -> float:
    """Regime-aware EV boundary.

    The values are intentionally lower than the old hard EV gate for Tier 2/3
    so the system can increase trade count, while Tier 1 remains strict enough
    to protect win rate and PF.
    """
    regime_u = str(regime or "UNKNOWN").upper()
    vol_u = str(vol_state or "MID_VOL").upper()
    base = {
        "TRANSITION": {1: 0.205, 2: 0.085, 3: 0.020},
        "TREND": {1: 0.235, 2: 0.125, 3: 0.055},
        "CHOP": {1: 0.185, 2: 0.105, 3: 0.045},
        "CRISIS_RISK_OFF": {1: 0.320, 2: 0.220, 3: 0.160},
    }.get(regime_u, {1: 0.220, 2: 0.110, 3: 0.050})[int(tier)]

    if vol_u == "HIGH_VOL":
        base += 0.020 if tier <= 2 else 0.012
    elif vol_u == "LOW_VOL":
        base -= 0.012 if tier >= 2 else 0.006
    return round(max(-0.02, base), 6)


def _confirmation_flags(signal: Dict[str, Any], ctx: Dict[str, Any], regime: Any, vol_state: Any) -> Dict[str, bool]:
    ev = safe_float(signal.get("expected_value"), 0.0)
    score = safe_float(signal.get("score"), 0.0)
    score_raw = safe_float(signal.get("score_raw"), 0.0)
    win_prob = safe_float(signal.get("win_prob"), 0.0)
    rr = safe_float(signal.get("estimated_rr"), 0.0)
    base = signal.get("base_trigger", {}) if isinstance(signal.get("base_trigger"), dict) else {}
    base_strength = safe_float(base.get("strength", signal.get("base_trigger_strength", 0.0)), 0.0)
    scorecard_total = safe_float(signal.get("scorecard_total"), 0.0)

    zone_near_atr = safe_float(ctx.get("zone_near_atr"), 9.99)
    has_valid_zone = safe_bool(ctx.get("has_valid_zone")) and zone_near_atr <= 2.40
    tight_zone = safe_bool(ctx.get("has_valid_zone")) and zone_near_atr <= 1.25
    sweep = safe_bool(ctx.get("liquidity_sweep_confirmed")) or safe_bool(ctx.get("liquidity_sweep"))
    wrong_sweep = safe_bool(ctx.get("liquidity_wrong_side"))
    setup_match = safe_bool(ctx.get("setup_direction_match"))
    has_any_setup = safe_bool(ctx.get("has_any_setup"))
    momentum = safe_bool(ctx.get("momentum_align")) or safe_bool(base.get("momentum_confirm"))
    dmi = safe_bool(ctx.get("sqzmom_dmi_aligned")) or safe_bool(base.get("dmi_aligned"))
    trend_aligned = safe_bool(ctx.get("trend_aligned"))
    base_passed = safe_bool(signal.get("base_trigger_passed"))
    sqz_passed = safe_bool(signal.get("sqz_passed")) or safe_bool(base.get("sqzmom_pass"))
    smc_passed = safe_bool(signal.get("smc_passed")) or safe_bool(base.get("smc_pass"))

    return {
        "base": base_passed,
        "ev_t3": ev >= dynamic_ev_threshold(regime, vol_state, 3),
        "ev_t2": ev >= dynamic_ev_threshold(regime, vol_state, 2),
        "ev_t1": ev >= dynamic_ev_threshold(regime, vol_state, 1),
        "win_prob": win_prob >= 0.47,
        "rr": rr >= 1.05,
        "score": score >= 48.0 or score_raw >= 17.0,
        "score_strong": score >= 68.0 or score_raw >= 24.0,
        "structure": bool(smc_passed or has_valid_zone or tight_zone),
        "structure_tight": bool(tight_zone or (safe_float(ctx.get("smc_quality_100"), 0.0) >= 55.0)),
        "liquidity": bool(sweep),
        "not_wrong_liquidity": not wrong_sweep,
        "momentum": bool(sqz_passed or momentum or dmi),
        "setup": bool(setup_match or has_any_setup),
        "scorecard": scorecard_total >= -0.15,
        "scorecard_positive": scorecard_total >= 0.20,
        "regime_aligned": bool(str(regime).upper() != "TREND" or trend_aligned or sweep),
        "base_strength": base_strength >= 0.55,
    }


def classify_signal_tier(signal: Dict[str, Any], ctx: Dict[str, Any], regime: Any, vol_state: Any) -> TierDecision:
    flags = _confirmation_flags(signal, ctx, regime, vol_state)
    regime_u = str(regime or "UNKNOWN").upper()
    ev = safe_float(signal.get("expected_value"), 0.0)
    win_prob = safe_float(signal.get("win_prob"), 0.0)
    rr = safe_float(signal.get("estimated_rr"), 0.0)
    score = safe_float(signal.get("score"), 0.0)
    score_raw = safe_float(signal.get("score_raw"), 0.0)
    base_strength = safe_float(signal.get("base_trigger_strength"), 0.0)
    scorecard_total = safe_float(signal.get("scorecard_total"), 0.0)

    confirm_core = [
        flags["base"], flags["structure"], flags["momentum"], flags["setup"],
        flags["not_wrong_liquidity"], flags["scorecard"], flags["regime_aligned"],
    ]
    confirm_count = int(sum(1 for x in confirm_core if x))

    recovery_allowed = bool(
        not flags["base"]
        and flags["setup"]
        and flags["structure"]
        and flags["momentum"]
        and flags["not_wrong_liquidity"]
        and ev >= dynamic_ev_threshold(regime, vol_state, 3)
        and (score >= 38.0 or score_raw >= 13.0 or flags["liquidity"])
    )

    # Crisis remains defensive.  It can trade only the best tier.
    if regime_u == "CRISIS_RISK_OFF":
        if flags["base"] and flags["ev_t1"] and flags["structure_tight"] and flags["liquidity"] and flags["momentum"]:
            tier, name, allowed, mult, reason = 1, "HIGH_PRECISION", True, 0.35, "T1_CRISIS_EXCEPTION"
        else:
            tier, name, allowed, mult, reason = 0, "REJECT", False, 0.0, "REJECT_CRISIS_NOT_T1"
    elif (
        flags["base"] and flags["ev_t1"] and flags["win_prob"] and rr >= 1.20
        and flags["structure_tight"] and flags["momentum"] and flags["score_strong"]
        and flags["not_wrong_liquidity"] and confirm_count >= 6
    ):
        tier, name, allowed, mult, reason = 1, "HIGH_PRECISION", True, 1.00, "T1_HIGH_PRECISION"
    elif (
        (flags["base"] or recovery_allowed)
        and flags["ev_t2"] and flags["rr"] and flags["structure"] and flags["momentum"]
        and flags["not_wrong_liquidity"] and confirm_count >= 5
    ):
        tier, name, allowed, mult, reason = 2, "BALANCED", True, 0.62, "T2_BALANCED"
    elif (
        (flags["base"] or recovery_allowed)
        and flags["ev_t3"] and rr >= 0.92 and flags["not_wrong_liquidity"]
        and flags["structure"] and (flags["momentum"] or flags["liquidity"] or flags["setup"])
        and confirm_count >= 4
    ):
        # CHOP exploration must still have liquidity or a tight mitigation zone.
        if regime_u == "CHOP" and not (flags["liquidity"] or flags["structure_tight"]):
            tier, name, allowed, mult, reason = 0, "REJECT", False, 0.0, "REJECT_CHOP_T3_NEEDS_LIQUIDITY_OR_TIGHT_ZONE"
        else:
            tier, name, allowed, mult, reason = 3, "EXPLORATION", True, 0.24, "T3_EXPLORATION_SMALL_SIZE"
    else:
        tier, name, allowed, mult, reason = 0, "REJECT", False, 0.0, "REJECT_NO_V38_TIER"

    # Small adjustments that keep PF stable when expanding trade count.
    if allowed:
        if flags["liquidity"]:
            mult *= 1.08
        if flags["scorecard_positive"]:
            mult *= 1.05
        if regime_u == "TREND" and not flags["regime_aligned"]:
            mult *= 0.55
        if regime_u == "CHOP" and tier >= 2:
            mult *= 0.78
        if str(vol_state).upper() == "HIGH_VOL" and tier >= 2:
            mult *= 0.82
        mult = max(0.02, min(1.20, mult))

    threshold = dynamic_ev_threshold(regime, vol_state, tier if tier in (1, 2, 3) else 3)
    rank_score = (
        ev * 130.0
        + win_prob * 35.0
        + rr * 8.0
        + score * 0.12
        + score_raw * 0.45
        + base_strength * 10.0
        + confirm_count * 3.0
        + scorecard_total * 4.0
        + (6.0 if flags["liquidity"] else 0.0)
        - (0.0 if tier == 1 else 7.0 if tier == 2 else 14.0 if tier == 3 else 40.0)
    )

    return TierDecision(
        trade_allowed=bool(allowed),
        tier=int(tier),
        tier_name=name,
        threshold=threshold,
        rank_score=rank_score,
        position_multiplier=mult,
        confirm_count=confirm_count,
        confirmation_flags=flags,
        recovery_allowed=recovery_allowed,
        reason=reason,
    )


def annotate_signal_with_tier(signal: Dict[str, Any], regime: Any, vol_state: Any) -> Dict[str, Any]:
    ctx = signal.get("entry_meta", {}) if isinstance(signal.get("entry_meta"), dict) else {}
    tier_decision = classify_signal_tier(signal, ctx, regime, vol_state)
    signal["v38_tier"] = tier_decision.tier
    signal["v38_tier_name"] = tier_decision.tier_name
    signal["v38_trade_allowed"] = tier_decision.trade_allowed
    signal["v38_dynamic_ev_threshold"] = round(float(tier_decision.threshold), 6)
    signal["v38_rank_score"] = round(float(tier_decision.rank_score), 6)
    signal["v38_tier_position_multiplier"] = round(float(tier_decision.position_multiplier), 6)
    signal["v38_confirm_count"] = int(tier_decision.confirm_count)
    signal["v38_recovery_allowed"] = bool(tier_decision.recovery_allowed)
    signal["v38_tier_reason"] = tier_decision.reason
    signal["v38_confirmation_flags"] = tier_decision.confirmation_flags
    signal["v38_tier_json"] = str(tier_decision.to_dict())
    signal["size_multiplier"] = round(
        safe_float(signal.get("size_multiplier"), 1.0) * tier_decision.position_multiplier,
        6,
    )
    signal["ev_reasons"] = str(signal.get("ev_reasons", "")) + f";{tier_decision.reason};V38_TIER={tier_decision.tier_name}"
    return signal


def regime_adaptive_entry_params(signal: Dict[str, Any], regime: Any, vol_state: Any, defaults: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Entry waiting/chase parameters per tier and regime.

    Lower tiers are allowed to increase frequency, but only with smaller chase
    tolerance.  This reduces bad fills and protects PF.
    """
    defaults = defaults or {}
    tier = int(safe_float(signal.get("v38_tier"), 2))
    regime_u = str(regime or "UNKNOWN").upper()
    vol_u = str(vol_state or "MID_VOL").upper()

    if tier <= 1:
        max_wait, max_chase = 10, 0.82
        profile = "PATIENT_PRECISION"
    elif tier == 2:
        max_wait, max_chase = 7, 0.58
        profile = "BALANCED_LIMIT"
    else:
        max_wait, max_chase = 3, 0.34
        profile = "FAST_SMALL_SIZE"

    if regime_u == "TREND":
        max_chase *= 1.10 if tier <= 2 else 0.90
        max_wait += 1 if tier <= 2 else 0
    elif regime_u == "CHOP":
        max_chase *= 0.78
        max_wait = max(2, max_wait - 1)
    elif regime_u == "TRANSITION":
        max_chase *= 0.96

    if vol_u == "HIGH_VOL":
        max_chase *= 0.72
        max_wait = max(2, max_wait - 1)
    elif vol_u == "LOW_VOL":
        max_chase *= 1.08

    return {
        "max_wait_bars": int(max(1, min(14, max_wait))),
        "max_chase_atr": round(float(max(0.15, min(1.00, max_chase))), 4),
        "entry_profile": profile,
    }


def tier_exit_profile(signal: Dict[str, Any], regime: Any, base_max_hold_bars: int) -> Dict[str, Any]:
    tier = int(safe_float(signal.get("v38_tier"), 2))
    regime_u = str(regime or "UNKNOWN").upper()
    base_hold = max(12, int(base_max_hold_bars))

    if tier <= 1:
        time_decay = 14 if regime_u == "TREND" else 11 if regime_u == "TRANSITION" else 8
        trail = 2.60 if regime_u == "TREND" else 1.35 if regime_u == "TRANSITION" else 1.05
        tp1_close, tp2_close = 0.34, 0.32
        hold_mult = 1.05
    elif tier == 2:
        time_decay = 10 if regime_u == "TREND" else 8 if regime_u == "TRANSITION" else 6
        trail = 1.95 if regime_u == "TREND" else 1.05 if regime_u == "TRANSITION" else 0.88
        tp1_close, tp2_close = 0.48, 0.32
        hold_mult = 0.82
    else:
        time_decay = 6 if regime_u == "TREND" else 5
        trail = 1.20 if regime_u == "TREND" else 0.78
        tp1_close, tp2_close = 0.66, 0.22
        hold_mult = 0.55

    return {
        "time_drawdown_bars": int(time_decay),
        "trail_atr_mult": round(float(trail), 4),
        "tp1_close_pct": round(float(tp1_close), 4),
        "tp2_close_pct": round(float(tp2_close), 4),
        "max_hold_bars": int(max(8, round(base_hold * hold_mult))),
    }

