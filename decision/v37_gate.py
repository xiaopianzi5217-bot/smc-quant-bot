# decision/v37_gate.py
from typing import Dict, Tuple

def v37_final_gate(base_decision: Dict, ctx: Dict) -> Tuple[bool, str, float]:
    """V37 轻量最终决策 Gate"""
    score = base_decision.get("score", 0.0)
    ev = base_decision.get("expected_value", -999.0)
    long_s = ctx.get("long_score", 0.0)
    short_s = ctx.get("short_score", 0.0)
    gap = abs(long_s - short_s)

    if ev < 0.028:
        return False, "EV_TOO_LOW", 0.0
    if score < 50:
        return False, "SCORE_LOW", 0.0
    if gap < 5.0:
        return False, "DIRECTION_AMBIGUOUS", 0.0

    size_mult = 1.0
    if score < 56: size_mult = 0.65
    elif score < 63: size_mult = 0.85

    return True, "V37_APPROVED", size_mult