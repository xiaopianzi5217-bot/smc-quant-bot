# -*- coding: utf-8 -*-
"""
Scorecard System V1

把高阶判断从“硬过滤器”降级为“旁路评分/审计层”。

设计目标：
1. 第一准入只由 Base Layer 决定：SMC 结构 + SQZMOM 背离/动量确认。
2. HTF / VWAP / DMI / Breakout / Regime 等不再一票否决，只输出分值、EV 修正和仓位修正。
3. 每个 scorer 的输出都可进入 trade log / reject audit，后续可以做 counterfactual 分组统计。
"""
from __future__ import annotations

from typing import Any, Dict, List
import json
import math


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        out = float(value)
        if math.isnan(out) or math.isinf(out):
            return default
        return out
    except Exception:
        return default


def _safe_bool(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "long", "short", "bull", "bear"}
    try:
        if value != value:
            return False
    except Exception:
        pass
    try:
        return bool(value)
    except Exception:
        return False


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _direction_key(direction: str) -> str:
    return "long" if str(direction).lower().startswith("l") else "short"


def _jsonable(value: Any) -> Any:
    """Convert numpy/pandas scalar values to plain JSON-compatible values."""
    try:
        import numpy as np  # type: ignore
        if isinstance(value, (np.bool_,)):
            return bool(value)
        if isinstance(value, (np.integer,)):
            return int(value)
        if isinstance(value, (np.floating,)):
            return float(value)
    except Exception:
        pass
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def dumps_compact(obj: Dict[str, Any]) -> str:
    return json.dumps(_jsonable(obj or {}), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def evaluate_base_trigger(row: Any, direction: str, ctx: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """
    第一道进场许可：只看 SMC + SQZMOM。

    注意：这不是高阶过滤。它只回答“当前 15m 是否存在可交易结构”。
    - SMC：结构质量分 / FVG-OB 区 / liquidity sweep 任一提供结构支撑。
    - SQZMOM：同向背离 + 白柱/动量/DMI/释放等确认。
    """
    ctx = ctx or {}
    direction = str(direction).title()
    dkey = _direction_key(direction)

    if hasattr(row, "get"):
        get = row.get
    else:
        get = lambda k, default=None: default

    smc_quality_col = "smc_quality_score_bull" if dkey == "long" else "smc_quality_score_bear"
    smc_quality = _safe_float(get(smc_quality_col, get("smc_quality_score", ctx.get("smc_quality_100", 0.0))), 0.0)
    has_valid_zone = _safe_bool(ctx.get("has_valid_zone", False))
    liquidity_sweep = _safe_bool(ctx.get("liquidity_sweep_confirmed", ctx.get("liquidity_sweep", False)))
    stop_hunt = _safe_bool(get("bullish_stop_hunt" if dkey == "long" else "bearish_stop_hunt", False))
    smc_pass = bool(smc_quality >= 35.0 or has_valid_zone or liquidity_sweep or stop_hunt)

    div_dir = str(get("sqzmom_divergence_dir", ctx.get("sqzmom_divergence_dir", "None")))
    div_age = _safe_float(get("sqzmom_divergence_age", ctx.get("sqzmom_divergence_age", 999)), 999)
    div_strength = _safe_float(get("sqzmom_divergence_strength", ctx.get("sqzmom_divergence_strength", 0.0)), 0.0)
    same_div_recent = bool(div_dir == direction and div_age <= 18)
    same_div_fresh = bool(div_dir == direction and div_age <= 8)
    # V54 Alpha Expansion: 修复强背离直通断层。
    # 当前回测管线里的 sqzmom_divergence_strength 实际是 0~12 左右，
    # 旧阈值 25 基本不会触发；保留 25+ 兼容未来 0~100 标尺。
    strong_div_threshold = 7.5
    strong_div_confirm = bool(same_div_recent and (div_strength >= strong_div_threshold or div_strength >= 25.0))

    white_confirm = _safe_bool(get("sqzmom_reversal_confirm_long" if dkey == "long" else "sqzmom_reversal_confirm_short", False))
    momentum = _safe_float(get("momentum", ctx.get("momentum", 0.0)), 0.0)
    momentum_slope = _safe_float(get("momentum_slope", ctx.get("momentum_slope", 0.0)), 0.0)
    momentum_strength = _safe_float(get("momentum_strength", ctx.get("momentum_strength", 0.0)), 0.0)
    momentum_strength_slope = _safe_float(get("momentum_strength_slope", ctx.get("momentum_strength_slope", 0.0)), 0.0)
    squeeze_released = _safe_bool(get("squeeze_released", ctx.get("squeeze_released", False)))
    dmi_aligned = _safe_bool(ctx.get("sqzmom_dmi_aligned", False))
    if dkey == "long":
        dmi_aligned = dmi_aligned or _safe_float(get("plus_di", 0.0), 0.0) >= _safe_float(get("minus_di", 0.0), 0.0)
        momentum_confirm = bool((momentum > 0 or momentum_slope > 0) and (momentum_strength >= 0 or momentum_strength_slope >= 0 or squeeze_released))
    else:
        dmi_aligned = dmi_aligned or _safe_float(get("minus_di", 0.0), 0.0) >= _safe_float(get("plus_di", 0.0), 0.0)
        momentum_confirm = bool((momentum < 0 or momentum_slope < 0) and (momentum_strength <= 0 or momentum_strength_slope <= 0 or squeeze_released))

    # V54.5 Alpha Expansion: 放宽 sqz 确认条件
    # 1. 强背离直通（不变）
    # 2. 有背离+动量/DMI/白柱（不变）
    # 3. 新：SMC >= 45（强结构） + 动量确认 + DMI 任意一项 → 不需要背离
    no_div_sqz = bool(
        smc_quality >= 40.0
        and (momentum_confirm or dmi_aligned or white_confirm or squeeze_released)
    )
    sqz_pass = bool(
        (same_div_fresh and white_confirm)
        or strong_div_confirm
        or (same_div_recent and (momentum_confirm or dmi_aligned or squeeze_released or white_confirm))
        or no_div_sqz
    )

    strength = 0.0
    if smc_pass:
        strength += 0.50
    strength += _clip((smc_quality - 30.0) / 45.0, 0.0, 0.35)
    if same_div_fresh:
        strength += 0.40
    elif same_div_recent:
        strength += 0.25
    if strong_div_confirm:
        strength += 0.24
    if white_confirm:
        strength += 0.18
    if momentum_confirm:
        strength += 0.16
    if dmi_aligned:
        strength += 0.10
    if div_strength > 0:
        strength += _clip(div_strength / 60.0, 0.0, 0.12)

    passed = bool(smc_pass and sqz_pass)
    reasons: List[str] = []
    if not smc_pass:
        reasons.append("BASE_SMC_NOT_READY")
    if not sqz_pass:
        reasons.append("BASE_SQZMOM_NOT_READY")
    if passed:
        reasons.append("BASE_SMC_SQZMOM_STRONG_DIV_PASS" if strong_div_confirm else "BASE_SMC_SQZMOM_PASS")

    return {
        "passed": passed,
        "direction": direction,
        "strength": round(_clip(strength, 0.0, 1.35), 4),
        "smc_pass": bool(smc_pass),
        "sqzmom_pass": bool(sqz_pass),
        "smc_quality": round(float(smc_quality), 4),
        "same_div_recent": bool(same_div_recent),
        "same_div_fresh": bool(same_div_fresh),
        "div_age": int(div_age) if div_age < 998 else 999,
        "div_strength": round(float(div_strength), 4),
        "strong_div_threshold": round(float(strong_div_threshold), 4),
        "strong_div_confirm": bool(strong_div_confirm),
        "white_confirm": bool(white_confirm),
        "momentum_confirm": bool(momentum_confirm),
        "dmi_aligned": bool(dmi_aligned),
        "squeeze_released": bool(squeeze_released),
        "reason": ";".join(reasons),
    }


def _scorer(name: str, score: float, ev: float, pos_mult: float, reason: str) -> Dict[str, Any]:
    return {
        "name": name,
        "score": round(_clip(float(score), -2.0, 2.0), 4),
        "ev": round(float(ev), 4),
        "position_mult": round(_clip(float(pos_mult), 0.05, 1.35), 4),
        "reason": reason,
    }


def build_scorecard(signal: Dict[str, Any], ctx: Dict[str, Any], macro_ctx: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """
    高阶模块旁路评分卡。

    返回值只用于 EV/仓位修正和日志审计，不做 reject。
    """
    macro_ctx = macro_ctx or {}
    direction = str(signal.get("direction", ctx.get("direction", ""))).title()
    dkey = _direction_key(direction)
    close = _safe_float(ctx.get("close", 0.0), 0.0)
    atr = max(_safe_float(ctx.get("ATRr_14", ctx.get("atr", 0.0)), 0.0), 1e-12)
    regime = str(ctx.get("regime", "")).upper()
    vol_state = str(ctx.get("vol_state", "")).upper()

    scorers: Dict[str, Dict[str, Any]] = {}

    base = signal.get("base_trigger", {}) if isinstance(signal, dict) else {}
    base_strength = _safe_float(base.get("strength"), 0.0)
    scorers["base"] = _scorer("base", base_strength, 0.035 * base_strength, 0.65 + 0.35 * _clip(base_strength, 0, 1), str(base.get("reason", "BASE_UNKNOWN")))

    # HTF/Macro：只降权，不拦截。
    htf_score = _safe_float(macro_ctx.get("htf_macro_score", ctx.get("htf_macro_score", 0.0)), 0.0)
    if abs(htf_score) < 1e-9:
        htf = _scorer("htf", 0.0, 0.0, 1.0, "HTF_NEUTRAL")
    else:
        aligned = (htf_score > 0 and dkey == "long") or (htf_score < 0 and dkey == "short")
        mag = _clip(abs(htf_score) / 60.0, 0.0, 1.0)
        htf = _scorer("htf", mag if aligned else -mag, (0.035 if aligned else -0.055) * mag, 1.0 + (0.12 * mag if aligned else -0.28 * mag), "HTF_ALIGNED" if aligned else "HTF_CONFLICT_SOFT")
    scorers["htf"] = htf

    # VWAP：距离过远只压仓，不能一票否决。
    vwap = _safe_float(ctx.get("vwap_48", ctx.get("VWAP", ctx.get("vwap", close))), close)
    vwap_dist_atr = abs(close - vwap) / atr if atr > 0 and close > 0 else _safe_float(ctx.get("vwap_dist_atr", 0.0), 0.0)
    directional_side_ok = (close >= vwap and dkey == "long") or (close <= vwap and dkey == "short")
    dist_penalty = _clip((vwap_dist_atr - 1.6) / 2.4, 0.0, 1.0)
    if directional_side_ok:
        vwap_score = 0.35 - 0.55 * dist_penalty
        vwap_ev = 0.015 - 0.035 * dist_penalty
        vwap_mult = 1.03 - 0.22 * dist_penalty
        vwap_reason = "VWAP_SIDE_OK" if dist_penalty <= 0 else "VWAP_EXTENDED_SOFT"
    else:
        vwap_score = -0.20 - 0.55 * dist_penalty
        vwap_ev = -0.020 - 0.035 * dist_penalty
        vwap_mult = 0.88 - 0.25 * dist_penalty
        vwap_reason = "VWAP_SIDE_CONFLICT_SOFT"
    scorers["vwap"] = _scorer("vwap", vwap_score, vwap_ev, vwap_mult, vwap_reason)

    # DMI：趋势一致加分，强趋势反向压仓。
    plus_di = _safe_float(ctx.get("plus_di", 0.0), 0.0)
    minus_di = _safe_float(ctx.get("minus_di", 0.0), 0.0)
    adx = _safe_float(ctx.get("adx", 0.0), 0.0)
    dmi_aligned = (plus_di >= minus_di and dkey == "long") or (minus_di >= plus_di and dkey == "short")
    dmi_strength = _clip(abs(plus_di - minus_di) / 35.0 + max(0.0, adx - 18.0) / 80.0, 0.0, 1.0)
    dmi_score = dmi_strength if dmi_aligned else -dmi_strength
    dmi_ev = (0.025 if dmi_aligned else -0.035) * dmi_strength
    dmi_mult = 1.0 + (0.10 if dmi_aligned else -0.22) * dmi_strength
    scorers["dmi"] = _scorer("dmi", dmi_score, dmi_ev, dmi_mult, "DMI_ALIGNED" if dmi_aligned else "DMI_CONFLICT_SOFT")

    # Breakout：概率评分只作为加分项；低 breakout 不再阻断 reversal/divergence。
    brk = _safe_float(signal.get("breakout", 0.0), 0.0)
    brk_term = _clip(brk / 30.0, 0.0, 1.0)
    if brk_term > 0:
        scorers["breakout"] = _scorer("breakout", brk_term, 0.030 * brk_term, 1.0 + 0.12 * brk_term, "BREAKOUT_SCORE_BONUS")
    else:
        scorers["breakout"] = _scorer("breakout", -0.10, -0.005, 0.95, "NO_BREAKOUT_NO_REJECT")

    # Divergence：背离是状态变量，不是好坏标签。新鲜同向加分；反向新鲜背离压仓。
    div_dir = str(ctx.get("sqzmom_divergence_dir", "None"))
    div_age = _safe_float(ctx.get("sqzmom_divergence_age", 999), 999)
    div_strength = _safe_float(ctx.get("sqzmom_divergence_strength", 0.0), 0.0)
    if div_dir == direction and div_age <= 18:
        freshness = 1.0 if div_age <= 8 else 0.62
        strength_bonus = _clip(div_strength / 25.0, 0.0, 0.35)
        div_score = _clip(freshness + strength_bonus, 0.0, 1.25)
        scorers["divergence"] = _scorer("divergence", div_score, 0.040 * div_score, 1.0 + 0.13 * div_score, "DIVERGENCE_SAME_SIDE")
    elif div_dir in ("Long", "Short") and div_dir != direction and div_age <= 18:
        scorers["divergence"] = _scorer("divergence", -0.75, -0.045, 0.72, "DIVERGENCE_OPPOSITE_SIDE_SOFT")
    else:
        scorers["divergence"] = _scorer("divergence", 0.0, 0.0, 1.0, "DIVERGENCE_NEUTRAL")

    # Regime/volatility：分频段决策。状态机只调仓，不决定开不开。
    if regime == "TREND":
        trend_aligned = _safe_bool(ctx.get("trend_aligned", False))
        reg = _scorer("regime", 0.45 if trend_aligned else -0.45, 0.020 if trend_aligned else -0.035, 1.08 if trend_aligned else 0.75, "TREND_ALIGNED" if trend_aligned else "TREND_COUNTER_SOFT")
    elif regime == "TRANSITION":
        reg = _scorer("regime", 0.25, 0.012, 1.08, "TRANSITION_REVERSAL_FRIENDLY")
    elif regime == "CHOP":
        reg = _scorer("regime", -0.25, -0.018, 0.82, "CHOP_SOFT_SIZE_DOWN")
    elif regime == "CRISIS_RISK_OFF":
        reg = _scorer("regime", -1.0, -0.090, 0.35, "CRISIS_SIZE_DOWN")
    else:
        reg = _scorer("regime", 0.0, 0.0, 1.0, "REGIME_NEUTRAL")
    if vol_state == "HIGH_VOL":
        reg["position_mult"] = round(max(0.05, reg["position_mult"] * 0.86), 4)
        reg["ev"] = round(reg["ev"] - 0.010, 4)
        reg["reason"] += "+HIGH_VOL"
    scorers["regime"] = reg

    total_score = sum(v["score"] for v in scorers.values())
    ev_adjustment = sum(v["ev"] for v in scorers.values())
    position_multiplier = 1.0
    for v in scorers.values():
        position_multiplier *= _safe_float(v.get("position_mult"), 1.0)
    position_multiplier = _clip(position_multiplier, 0.05, 1.35)

    positives = [k for k, v in scorers.items() if v["score"] > 0.20]
    negatives = [k for k, v in scorers.items() if v["score"] < -0.20]
    return {
        "version": "Scorecard_V1_Decoupled_20260616",
        "direction": direction,
        "total_score": round(float(total_score), 4),
        "ev_adjustment": round(float(_clip(ev_adjustment, -0.18, 0.16)), 4),
        "position_multiplier": round(float(position_multiplier), 4),
        "scorers": scorers,
        "positive_modules": positives,
        "negative_modules": negatives,
        "summary": f"score={total_score:.2f};ev_adj={ev_adjustment:.3f};pos_mult={position_multiplier:.2f};+{','.join(positives) or 'none'};-{','.join(negatives) or 'none'}",
    }
