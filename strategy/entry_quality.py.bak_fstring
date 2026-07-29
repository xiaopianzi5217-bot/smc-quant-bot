# -*- coding: utf-8 -*-
""" strategy/entry_quality.py V3 Unified Scoring Version 统一评分系统： - 合并旧版 grade_entry_quality 的上下文评分 + V37 institutional_alpha_score 的 divergence/momentum/SMC/liquidity 维度 - 回测和实盘使用同一套评分逻辑 - 保留全部旧接口兼容性 """

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple


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


def _lower(v: Any) -> str:
    return _safe_str(v).strip().lower()


def _get(ctx: Optional[Dict[str, Any]], *names: str, default: Any = None) -> Any:
    if not isinstance(ctx, dict):
        return default
    for n in names:
        if n in ctx:
            return ctx.get(n)
    return default


@dataclass
class EntryGrade:
    grade: str = "B"
    score: float = 60.0
    size_mult: float = 0.70
    allowed: bool = True
    reason: str = "DEFAULT_B"
    rr_min: float = 1.10
    be_trigger_r: float = 0.80
    trail_trigger_r: float = 1.25
    partial_tp1_r: float = 1.00
    partial_tp1_pct: float = 0.30


def _directional_bool(ctx: Dict[str, Any], direction: str, long_names: List[str], short_names: List[str]) -> bool:
    names = long_names if _lower(direction) == "long" else short_names
    for n in names:
        if _safe_bool(ctx.get(n, False)):
            return True
    return False


def grade_entry_quality(context: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
    """ V3 统一评分：合并上下文评分 + 技术指标评分（divergence/momentum/SMC/liquidity）。
    
    评分公式：base_score(50) + context_bonus + technical_bonus - risk_penalty
    输出：0-100 分，S/A/B/C/REJECT 等级
    
    兼容旧接口：grade_entry_quality(context) 或 grade_entry_quality(regime="trend", ...)
    """
    ctx: Dict[str, Any] = {}
    if isinstance(context, dict):
        ctx.update(context)
    ctx.update(kwargs)

    # ===== 1. 上下文参数提取 =====
    regime = _lower(_get(ctx, "regime", "market_regime", "trend_state", default=""))
    volatility = _lower(_get(ctx, "volatility", "volatility_state", "vol_state", default="normal"))
    squeeze = _lower(_get(ctx, "squeeze", "squeeze_state", default="none"))
    direction = _lower(_get(ctx, "direction", "side", default=""))
    htf_direction = _lower(_get(ctx, "htf_direction", "higher_tf_direction", "htf_bias", default=""))
    setup_type = _lower(_get(ctx, "setup_type", "entry_type", "poi_type", "zone_type", default=""))
    rr = _safe_float(_get(ctx, "rr", "risk_reward", "expected_rr", "rr_est", default=1.0), 1.0)
    distance_atr = _safe_float(_get(ctx, "distance_atr", "entry_distance_atr", default=0.0), 0.0)
    ob_strength = _safe_float(_get(ctx, "ob_strength", "zone_strength", "poi_strength", default=0.0), 0.0)
    fvg_quality = _safe_float(_get(ctx, "fvg_quality", "gap_quality", default=0.0), 0.0)
    displacement = _safe_float(_get(ctx, "displacement", "impulse_score", default=0.0), 0.0)
    liquidity = _safe_float(_get(ctx, "liquidity", "liq_score", "liquidity_score", default=0.0), 0.0)
    vwap_align = _get(ctx, "vwap_align", "vwap_aligned", default=None)

    # ===== 2. 技术指标参数提取（来自 V37 institutional_alpha_score） =====
    divergence_confirmed = _safe_bool(ctx.get("divergence_confirmed", False))
    div_age = int(_safe_float(ctx.get("sqzmom_divergence_age", 999), 999))
    div_strength = _safe_float(ctx.get("sqzmom_divergence_strength", 0.0), 0.0)
    white_confirm = _safe_bool(ctx.get("sqzmom_white_confirm", False))
    momentum_confirm = _safe_bool(ctx.get("sqzmom_momentum_confirm", False))
    dmi_aligned = _safe_bool(ctx.get("sqzmom_dmi_aligned", False))
    trigger_ok = _safe_bool(ctx.get("sqzmom_trigger_ok", False))
    squeeze_released = _safe_bool(ctx.get("squeeze_released", False))
    sqzmom_score = _safe_float(ctx.get("sqzmom_score", 0.0), 0.0)
    smc_zone_score = _safe_float(ctx.get("smc_zone_score", 0.0), 0.0)
    has_valid_zone = _safe_bool(ctx.get("has_valid_zone", False))
    liquidity_sweep_confirmed = _safe_bool(ctx.get("liquidity_sweep_confirmed", False))
    liquidity_wrong_side = _safe_bool(ctx.get("liquidity_wrong_side", False))
    volume_ratio = _safe_float(ctx.get("volume_ratio", 1.0), 1.0)
    body_pct = _safe_float(ctx.get("body_pct", 0.0), 0.0)
    adx = _safe_float(ctx.get("adx", 0.0), 0.0)
    same_div = _safe_float(ctx.get("same_side_div_count_12", 0.0), 0.0)
    macro_conflict = _safe_bool(ctx.get("macro_conflict", False))
    too_extended = _safe_bool(ctx.get("too_extended", False))
    fe_bottom = _safe_bool(ctx.get("fe_bottom", False))
    fe_top = _safe_bool(ctx.get("fe_top", False))

    # ===== 3. 基础分 =====
    # 基础分 10~15（非固定），给 sqzmom_score 和 smc_zone_score 的线性贡献留出空间
    # 好信号最终应在 60~85 分，优秀信号 85~100 分
    score = 12.0
    reasons: List[str] = []

    # ===== 4. 上下文加分（Context Bonus） =====
    if regime == "trend":
        score += 12
        reasons.append("REGIME_TREND")
    elif regime == "transition":
        score += 3
        reasons.append("REGIME_TRANSITION")
    elif regime in ("range", "chop", "sideways"):
        score -= 4
        reasons.append("REGIME_CHOP")
    else:
        reasons.append("REGIME_UNKNOWN")

    if volatility == "low":
        score -= 6
        reasons.append("LOW_VOL_DOWNGRADE")
    elif volatility in ("normal", "medium"):
        score += 3
        reasons.append("VOL_NORMAL")
    elif volatility == "high":
        score += 1
        reasons.append("VOL_HIGH")

    if squeeze in ("release", "squeeze_release", "released") or squeeze_released:
        score += 6
        reasons.append("SQUEEZE_RELEASE")
    elif squeeze in ("building", "build"):
        score -= 2
        reasons.append("SQUEEZE_BUILDING")

    if direction and htf_direction:
        if direction == htf_direction:
            score += 8
            reasons.append("HTF_ALIGNED")
        else:
            score -= 10
            reasons.append("HTF_CONFLICT_DOWNGRADE")

    if "ob" in setup_type:
        score += 4
        reasons.append("OB_SETUP")
    if "fvg" in setup_type:
        score += 2
        reasons.append("FVG_SETUP")

    if rr >= 1.8:
        score += 7
        reasons.append("RR_STRONG")
    elif rr >= 1.2:
        score += 4
        reasons.append("RR_OK")
    elif rr < 0.9:
        score -= 8
        reasons.append("RR_TOO_LOW")

    if ob_strength:
        score += min(max(ob_strength, 0.0), 3.0) * 2.0
    if fvg_quality:
        score += min(max(fvg_quality, 0.0), 3.0) * 1.5
    if displacement:
        score += min(max(displacement, 0.0), 3.0) * 3.0
    if liquidity:
        score += min(max(liquidity, 0.0), 3.0) * 1.5

    if vwap_align is True:
        score += 4
        reasons.append("VWAP_ALIGNED")
    elif vwap_align is False:
        score -= 4
        reasons.append("VWAP_NOT_ALIGNED")

    if distance_atr > 1.2:
        score -= 6
        reasons.append("ENTRY_TOO_FAR")

    # ===== 5. 技术指标加分（Technical Bonus - 来自 V37） =====
    # 5a. Divergence bonus (上限 15) — 需要背离方向与交易方向一致
    if divergence_confirmed:
        div_bonus = 8.0
        # 检查背离方向是否与交易方向一致
        div_dir = _lower(_get(ctx, "sqzmom_divergence_dir", "divergence_dir", default=""))
        if div_dir and div_dir != direction:
            div_bonus = 2.0  # 方向不一致，大幅降分
            reasons.append("DIVERGENCE_WRONG_DIR")
        else:
            if div_age <= 3:
                div_bonus += 5.0
            elif div_age <= 6:
                div_bonus += 3.0
            elif div_age <= 10:
                div_bonus += 1.0
            div_bonus += min(4.0, max(0.0, div_strength - 4.0) * 0.5)
        div_bonus = min(15.0, max(0.0, div_bonus))
        score += div_bonus
        reasons.append(f"DIVERGENCE_BONUS_{div_bonus:.0f}")

    # 5b. Momentum bonus (上限 10) — 需要动量方向与交易方向一致
    mom_bonus = 0.0
    # 获取动量方向
    momentum_val = _safe_float(ctx.get("momentum", 0.0), 0.0)
    mom_direction_match = (direction == "long" and momentum_val > 0) or (direction == "short" and momentum_val < 0)
    
    if white_confirm:
        # white_confirm 本身是方向相关的
        white_dir_match = (direction == "long" and _safe_bool(ctx.get("sqzmom_reversal_confirm_long", False))) or \
                          (direction == "short" and _safe_bool(ctx.get("sqzmom_reversal_confirm_short", False)))
        if white_dir_match:
            mom_bonus += 3.0
        else:
            mom_bonus += 1.0  # 方向不匹配也给一点
    if momentum_confirm and mom_direction_match:
        mom_bonus += 4.0
    elif momentum_confirm:
        mom_bonus += 1.0  # 动量确认但方向不匹配
    if dmi_aligned:
        # DMI 方向检查
        dmi_bull = _safe_bool(ctx.get("dmi_bull", False))
        dmi_bear = _safe_bool(ctx.get("dmi_bear", False))
        dmi_dir_match = (direction == "long" and dmi_bull) or (direction == "short" and dmi_bear)
        if dmi_dir_match:
            mom_bonus += 2.0
    if trigger_ok:
        mom_bonus += 2.0
    if squeeze_released:
        mom_bonus += 2.0
    mom_bonus = min(10.0, max(0.0, mom_bonus))
    if mom_bonus > 0:
        score += mom_bonus
        reasons.append(f"MOMENTUM_BONUS_{mom_bonus:.0f}")

    # 5c. SQZMOM 动量线性贡献（无上限）
    # sqzmom_score 范围 0~50，乘以 0.6 后贡献 0~30 分
    sqzmom_contrib = sqzmom_score * 0.6
    if sqzmom_contrib > 0:
        score += sqzmom_contrib
        reasons.append(f"SQZMOM_CONTRIB_{sqzmom_contrib:.0f}")

    # 5d. SMC zone 线性贡献（无上限）
    # smc_zone_score 范围 0~50，乘以 0.5 后贡献 0~25 分
    smc_contrib = smc_zone_score * 0.5
    if smc_contrib > 0:
        score += smc_contrib
        reasons.append(f"SMC_ZONE_CONTRIB_{smc_contrib:.0f}")

    # 5d. Liquidity sweep bonus (上限 8)
    liq_bonus = 0.0
    if liquidity_sweep_confirmed:
        liq_bonus += 8.0
    liq_bonus = min(8.0, max(0.0, liq_bonus))
    if liq_bonus > 0:
        score += liq_bonus
        reasons.append(f"LIQUIDITY_BONUS_{liq_bonus:.0f}")

    # 5e. Volume bonus (上限 5)
    vol_bonus = 0.0
    if 0.45 <= volume_ratio <= 1.6:
        vol_bonus += 2.0
    if volume_ratio > 1.15:
        vol_bonus += 1.5
    if fe_bottom and direction == "long":
        vol_bonus += 2.0
    if fe_top and direction == "short":
        vol_bonus += 2.0
    vol_bonus = min(5.0, max(0.0, vol_bonus))
    if vol_bonus > 0:
        score += vol_bonus
        reasons.append(f"VOLUME_BONUS_{vol_bonus:.0f}")

    # 5f. Execution bonus (上限 6，可负)
    exec_bonus = 0.0
    if body_pct >= 0.42:
        exec_bonus += 3.0
    elif body_pct < 0.16:
        exec_bonus -= 2.0
    if volume_ratio > 3.2:
        exec_bonus -= 3.0
    exec_bonus = min(6.0, max(-6.0, exec_bonus))
    if exec_bonus != 0:
        score += exec_bonus
        reasons.append(f"EXECUTION_BONUS_{exec_bonus:+.0f}")

    # ===== 6. 风险惩罚（Risk Penalty） =====
    risk_penalty = 0.0
    if macro_conflict:
        risk_penalty += 15.0
        reasons.append("MACRO_CONFLICT_PENALTY_-15")
    if liquidity_wrong_side:
        risk_penalty += 8.0
        reasons.append("WRONG_SIDE_LIQUIDITY_PENALTY_-8")
    if same_div >= 3 and adx >= 42:
        risk_penalty += 8.0
        reasons.append("REPEAT_DIVERGENCE_PENALTY_-8")
    if too_extended:
        risk_penalty += 10.0
        reasons.append("TOO_EXTENDED_PENALTY_-10")
    # SMC V2：不再使用 has_valid_zone binary gate 扣分，
    # 改为在 smc_module_v2.py 中通过三维连续评分（Zone_Quality 40% + Mitigation_Strength 30% + Structure_Alignment 30%）
    # 输出 0~100 连续分数，由 institutional_alpha_score 中的 SMC 维度（权重 30%）统一处理。
    if risk_penalty > 0:
        score -= risk_penalty

    # ===== 7. 最终分数裁剪 =====
    score = max(0.0, min(100.0, score))

    # ===== 8. 等级判定 =====
    reject = False
    if rr < 0.6:
        reject = True
        reasons.append("REJECT_RR")
    if regime in ("range", "chop", "sideways") and volatility == "low" and rr < 1.1:
        reject = True
        reasons.append("REJECT_CHOP_LOW_VOL")
    if "HTF_CONFLICT_DOWNGRADE" in reasons and rr < 1.05:
        reject = True
        reasons.append("REJECT_HTF_CONFLICT_LOW_RR")

    if reject:
        grade = EntryGrade("REJECT", score, 0.0, False, "|".join(reasons), 1.20, 0.70, 1.10, 0.80, 0.50)
    elif score >= 85:
        grade = EntryGrade("S", score, 1.00, True, "|".join(reasons), 1.30, 0.90, 1.60, 1.00, 0.25)
    elif score >= 70:
        grade = EntryGrade("A", score, 0.85, True, "|".join(reasons), 1.20, 0.85, 1.50, 1.00, 0.25)
    elif score >= 55:
        grade = EntryGrade("B", score, 0.70, True, "|".join(reasons), 1.05, 0.75, 1.20, 0.90, 0.35)
    elif score >= 40:
        grade = EntryGrade("C", score, 0.40, True, "|".join(reasons), 0.95, 0.60, 1.00, 0.75, 0.50)
    else:
        grade = EntryGrade("D", score, 0.20, True, "|".join(reasons), 0.85, 0.50, 0.80, 0.60, 0.50)

    out = asdict(grade)
    out["entry_grade"] = out["grade"]
    out["quality_score"] = out["score"]
    out["approved"] = out["allowed"]
    out["allow_entry"] = out["allowed"]
    out["reasons"] = reasons
    # 附加技术指标明细
    out["divergence_bonus"] = round(div_bonus if divergence_confirmed else 0.0, 4)
    out["momentum_bonus"] = round(mom_bonus, 4)
    out["sqzmom_contrib"] = round(sqzmom_contrib, 4)
    out["smc_contrib"] = round(smc_contrib, 4)
    out["liquidity_bonus"] = round(liq_bonus, 4)
    out["volume_bonus"] = round(vol_bonus, 4)
    out["execution_bonus"] = round(exec_bonus, 4)
    out["risk_penalty"] = round(risk_penalty, 4)
    return out


def score_entry(*args: Any, **kwargs: Any) -> float:
    """ 旧系统可能只需要一个数值分数。 """
    ctx = parse_score_args(*args, **kwargs)
    # 确保 direction 参数被合并到 ctx 中（字符串参数不是 dict，parse_score_args 不会自动合并）
    for arg in args:
        if isinstance(arg, str) and arg.lower() in ("long", "short", "buy", "sell", "bull", "bear"):
            ctx["direction"] = arg
    if "direction" in kwargs and kwargs["direction"] is not None:
        ctx["direction"] = kwargs["direction"]
    return float(grade_entry_quality(ctx).get("score", 0.0))


def parse_score_args(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """ 兼容旧 scoring.py 的参数解析。 """
    ctx: Dict[str, Any] = {}
    for arg in args:
        if isinstance(arg, dict):
            ctx.update(arg)
    ctx.update(kwargs)
    return ctx


def build_entry_decision(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """ 旧接口：返回入场决策。 """
    ctx = parse_score_args(*args, **kwargs)
    grade = grade_entry_quality(ctx)

    return {
        "approved": bool(grade.get("allowed", True)),
        "allow_entry": bool(grade.get("allowed", True)),
        "entry_ok": bool(grade.get("allowed", True)),
        "grade": grade.get("grade", "B"),
        "entry_grade": grade.get("grade", "B"),
        "score": float(grade.get("score", 60.0)),
        "quality_score": float(grade.get("score", 60.0)),
        "size_mult": float(grade.get("size_mult", 0.7)),
        "reason": grade.get("reason", ""),
        "reasons": grade.get("reasons", []),
        "rr_min": grade.get("rr_min", 1.1),
        "be_trigger_r": grade.get("be_trigger_r", 0.8),
        "trail_trigger_r": grade.get("trail_trigger_r", 1.25),
        "partial_tp1_r": grade.get("partial_tp1_r", 1.0),
        "partial_tp1_pct": grade.get("partial_tp1_pct", 0.3),
    }


def enrich_exec_context(context: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
    """ 旧接口：给执行上下文追加 v2 入场质量字段。 """
    ctx: Dict[str, Any] = {}
    if isinstance(context, dict):
        ctx.update(context)
    ctx.update(kwargs)
    decision = build_entry_decision(ctx)
    ctx.update({
        "entry_grade": decision["entry_grade"],
        "entry_quality_score": decision["quality_score"],
        "entry_size_mult": decision["size_mult"],
        "entry_reason": decision["reason"],
        "entry_allowed": decision["approved"],
        "v2_entry_decision": decision,
    })
    return ctx


def explain_entry(*args: Any, **kwargs: Any) -> str:
    """ 旧接口：返回可读解释。 """
    d = build_entry_decision(*args, **kwargs)
    return (
        f"grade={d.get('entry_grade')} "
        f"score={d.get('quality_score'):.1f} "
        f"size_mult={d.get('size_mult')} "
        f"approved={d.get('approved')} "
        f"reason={d.get('reason')}"
    )