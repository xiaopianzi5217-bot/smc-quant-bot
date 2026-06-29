# -*- coding: utf-8 -*-
"""
SMC V2：三维结构概率评分器（非 gate）

评分公式：
    SMC_TOTAL = Zone_Quality(40%) + Mitigation_Strength(30%) + Structure_Alignment(30%)
    
    输出 0~100 连续分数，不再有 0/49 断崖式 binary 判定。

用法：
    from strategy.smc_module_v2 import calculate_smc_score
    result = calculate_smc_score(ctx)
    smc_score = result["smc_score"]  # 0~100 连续
"""

from __future__ import annotations
from typing import Any, Dict


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _safe_bool(v: Any) -> bool:
    try:
        return bool(v)
    except Exception:
        return False


def _safe_str(v: Any, default: str = "") -> str:
    try:
        if v is None:
            return default
        return str(v)
    except Exception:
        return default


# ============================================================
# 维度 1：Liquidity Zone Quality（流动性结构质量）— 权重 40%
# ============================================================
def _zone_quality(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    评估流动性结构质量：
    - 是否有有效 SMC 区域（OB/FVG）
    - 是否发生了流动性扫除（Sweep）
    - OB 强度
    - 区域接近度（zone_near_atr）
    """
    score = 0.0
    reasons = []

    # 1a. 有效区域基础分（0~25）
    has_valid_zone = _safe_bool(ctx.get("has_valid_zone", False))
    if has_valid_zone:
        score += 25.0
        reasons.append("VALID_ZONE_+25")

    # 1b. 流动性扫除加分（0~15）
    liquidity_sweep = _safe_bool(ctx.get("liquidity_sweep", ctx.get("liquidity_sweep_confirmed", False)))
    if liquidity_sweep:
        score += 15.0
        reasons.append("LIQUIDITY_SWEEP_+15")

    # 1c. OB 强度加分（0~10）
    ob_strength = _safe_float(ctx.get("ob_strength", 0.0))
    if ob_strength > 0.6:
        score += 10.0
        reasons.append(f"OB_STRENGTH_{ob_strength:.1f}_+10")
    elif ob_strength > 0.3:
        score += 5.0
        reasons.append(f"OB_STRENGTH_{ob_strength:.1f}_+5")

    # 1d. 区域接近度加分（zone_near_atr <= 0.7 表示价格靠近区域）
    zone_near = _safe_float(ctx.get("zone_near_atr", 9.99))
    if zone_near <= 0.35:
        score += 8.0
        reasons.append(f"ZONE_NEAR_{zone_near:.2f}ATR_+8")
    elif zone_near <= 0.70:
        score += 5.0
        reasons.append(f"ZONE_NEAR_{zone_near:.2f}ATR_+5")
    elif zone_near <= 1.05:
        score += 2.0
        reasons.append(f"ZONE_NEAR_{zone_near:.2f}ATR_+2")

    final_score = min(score, 40.0)
    return {"score": round(final_score, 2), "raw": round(score, 2), "reasons": reasons}


# ============================================================
# 维度 2：Mitigation Strength（回补强度）— 权重 30%
# ============================================================
def _mitigation_strength(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    评估回补/测试强度：
    - Wick Fill Ratio（影线回补比例）
    - 是否有 Mitigation Source（FVG/OB）
    - 是否发生了 Retest（重新测试）
    """
    score = 0.0
    reasons = []

    # 2a. Wick Fill Ratio（0~15）
    fill_ratio = _safe_float(ctx.get("wick_fill_ratio", ctx.get("fill_ratio", 0.0)))
    if fill_ratio > 0.7:
        score += 15.0
        reasons.append(f"WICK_FILL_{fill_ratio:.2f}_+15")
    elif fill_ratio > 0.5:
        score += 10.0
        reasons.append(f"WICK_FILL_{fill_ratio:.2f}_+10")
    elif fill_ratio > 0.3:
        score += 5.0
        reasons.append(f"WICK_FILL_{fill_ratio:.2f}_+5")

    # 2b. Mitigation Source 存在（0~10）
    mitigation_src = _safe_str(ctx.get("mitigation_src", "NO_FVG_OB"))
    has_mitigation = mitigation_src != "NO_FVG_OB"
    if has_mitigation:
        score += 10.0
        reasons.append(f"MITIGATION_SRC_{mitigation_src}_+10")

    # 2c. Retest 确认（0~5）
    retest_confirmed = _safe_bool(ctx.get("retest_confirmed", False))
    if retest_confirmed:
        score += 5.0
        reasons.append("RETEST_CONFIRMED_+5")

    # 2d. 实体确认加分（body_pct >= 0.36 表示强确认）
    body_pct = _safe_float(ctx.get("body_pct", 0.0))
    if body_pct >= 0.42:
        score += 5.0
        reasons.append(f"BODY_{body_pct:.2f}_+5")
    elif body_pct >= 0.36:
        score += 3.0
        reasons.append(f"BODY_{body_pct:.2f}_+3")

    final_score = min(score, 30.0)
    return {"score": round(final_score, 2), "raw": round(score, 2), "reasons": reasons}


# ============================================================
# 维度 3：Structure Alignment（结构一致性）— 权重 30%
# ============================================================
def _structure_alignment(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    评估结构一致性：
    - HTF 方向与交易方向一致
    - Sweep 方向匹配
    - 动量方向对齐
    - DMI 对齐
    """
    score = 0.0
    reasons = []
    direction = _safe_str(ctx.get("direction", "")).lower()

    # 3a. HTF 方向一致（0~15）
    htf_direction = _safe_str(ctx.get("htf_direction", "")).lower()
    if htf_direction and direction:
        if htf_direction == direction:
            score += 15.0
            reasons.append(f"HTF_{htf_direction.upper()}_ALIGN_+15")
        else:
            score -= 5.0
            reasons.append(f"HTF_{htf_direction.upper()}_CONFLICT_-5")

    # 3b. Sweep 方向匹配（0~10）
    sweep_direction_match = _safe_bool(ctx.get("sweep_direction_match", False))
    if sweep_direction_match:
        score += 10.0
        reasons.append("SWEEP_DIR_MATCH_+10")

    # 3c. 动量方向对齐（0~5）
    momentum_align = _safe_bool(ctx.get("momentum_align", False))
    if momentum_align:
        score += 5.0
        reasons.append("MOMENTUM_ALIGN_+5")

    # 3d. DMI 对齐加分（0~5）
    dmi_aligned = _safe_bool(ctx.get("sqzmom_dmi_aligned", ctx.get("dmi_aligned", False)))
    if dmi_aligned:
        score += 5.0
        reasons.append("DMI_ALIGNED_+5")

    # 3e. 趋势方向一致加分（regime + trend_direction）
    regime = _safe_str(ctx.get("regime", "mud"))
    trend_dir = _safe_str(ctx.get("trend_direction", "None")).lower()
    if regime == "trend" and trend_dir == direction:
        score += 5.0
        reasons.append(f"TREND_{trend_dir.upper()}_ALIGN_+5")

    final_score = min(score, 30.0)
    return {"score": round(final_score, 2), "raw": round(score, 2), "reasons": reasons}


# ============================================================
# 主入口：calculate_smc_score
# ============================================================
def calculate_smc_score(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    SMC V2：结构化评分模块（非 gate）
    
    参数:
        ctx: 包含所有 SMC 相关字段的上下文字典
    
    返回:
        {
            "smc_score": float,       # 0~100 连续分数
            "zone": {...},            # 维度 1 明细
            "mitigation": {...},      # 维度 2 明细
            "alignment": {...},       # 维度 3 明细
            "breakdown": str          # 可读的评分明细
        }
    
    用法:
        result = calculate_smc_score(ctx)
        smc_score = result["smc_score"]  # 0~100
        print(result["breakdown"])
    """
    zone = _zone_quality(ctx)
    mitigation = _mitigation_strength(ctx)
    alignment = _structure_alignment(ctx)

    score = (
        zone["score"] * 0.4 +
        mitigation["score"] * 0.3 +
        alignment["score"] * 0.3
    )

    # 构建可读的 breakdown
    breakdown = (
        f"SMC={score:.1f} | "
        f"Zone({zone['score']:.1f}×0.4={zone['score']*0.4:.1f}) | "
        f"Miti({mitigation['score']:.1f}×0.3={mitigation['score']*0.3:.1f}) | "
        f"Align({alignment['score']:.1f}×0.3={alignment['score']*0.3:.1f})"
    )

    return {
        "smc_score": round(score, 2),
        "zone": zone,
        "mitigation": mitigation,
        "alignment": alignment,
        "breakdown": breakdown,
    }
