# decision/v37_gate.py
from typing import Dict, Tuple

try:
    import numpy as np
    _HAS_NP = True
except ImportError:
    _HAS_NP = False


def v37_final_gate(base_decision: Dict, ctx: Dict) -> Tuple[bool, str, float]:
    """V37 最终决策 Gate — 模糊化连续仓位控制版

    20260706 优化:
    - EV 0.015→0.012, Score 45→43, Gap 4.0→3.5（增加合格信号量）
    - _boost 默认 1.0 而非 0.0（修复组合信号仓位归零 bug）
    - score 分档 → 连续线性插值（消除硬拐点，Soft Gate）
    - 组合信号加成分: 1.15→1.25
    """
    score = base_decision.get("score", 0.0)
    ev = base_decision.get("expected_value", -999.0)
    long_s = ctx.get("long_score", 0.0)
    short_s = ctx.get("short_score", 0.0)
    gap = abs(long_s - short_s)

    if ev < 0.012:
        return False, "EV_TOO_LOW", 0.0
    if score < 43:
        return False, "SCORE_LOW", 0.0
    if gap < 3.5:
        return False, "DIRECTION_AMBIGUOUS", 0.0

    # ===== 强组合信号加权: DIVERGENCE_R + FVG/CHOCH =====
    _boost = 1.0  # 默认 1.0，避免乘 0 归零
    _has_div = bool(ctx.get("has_bot_div") or ctx.get("has_top_div"))
    _has_fvg = bool(ctx.get("bullish_fvg") or ctx.get("bearish_fvg"))
    _has_choch = bool(ctx.get("swing_high", 0) > 0) or bool(ctx.get("swing_low", 0) > 0)

    if _has_div and (_has_fvg or _has_choch):
        _boost = 1.25

    # ===== Soft Gate: 连续线性插值替代硬分档 =====
    # score=43→0.50, 50→0.70, 58→0.90, 70+→1.10
    if _HAS_NP:
        score_nodes = [0.0, 43.0, 50.0, 58.0, 70.0, 100.0]
        size_nodes   = [0.0, 0.50, 0.70, 0.90, 1.10, 1.10]
        size_mult = float(np.interp(score, score_nodes, size_nodes))
    else:
        # 兜底：原逻辑
        if score < 50:
            size_mult = 0.7
        elif score < 58:
            size_mult = 0.9
        else:
            size_mult = 1.0

    size_mult *= _boost
    size_mult = max(0.1, min(1.5, size_mult))  # 软边界约束
    return True, "V37_APPROVED", size_mult