# -*- coding: utf-8 -*-
"""
Scorecard System V2

鎶婇珮闃跺垽鏂粠鈥滅‖杩囨护鍣ㄢ€濋檷绾т负鈥滄梺璺瘎鍒?瀹¤灞傗€濄€?
V2: 寮曞叆 HTF 1H 绾у埆鑳岀鍙嶅悜鎸″仠锛屼互鍙?HTF+LTF 澶氬懆鏈熻儗绂诲叡鎸€昏緫銆?

璁捐鐩爣锛?
1. 绗竴鍑嗗叆鍙敱 Base Layer 鍐冲畾锛歋MC 缁撴瀯 + SQZMOM 鑳岀/鍔ㄩ噺纭銆?
2. HTF / VWAP / DMI / Breakout / Regime 绛変笉鍐嶄竴绁ㄥ惁鍐筹紝鍙緭鍑哄垎鍊笺€丒V 淇鍜屼粨浣嶄慨姝ｃ€?
3. 閽堝 1H HTF 寮哄弽鍚戣儗绂昏缃‖闃绘柇 (block)锛屽己鍒跺綊闆朵粨浣嶃€?
4. 姣忎釜 scorer 鐨勮緭鍑洪兘鍙繘鍏?trade log / reject audit锛屽悗缁彲浠ュ仛 counterfactual 鍒嗙粍缁熻銆?
"""
from __future__ import annotations

from typing import Any, Dict, List
import json
import math

from utils.safe import safe_float, safe_bool, safe_str







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
    绗竴閬撹繘鍦鸿鍙細鍙湅 SMC + SQZMOM銆?

    娉ㄦ剰锛氳繖涓嶆槸楂橀樁杩囨护銆傚畠鍙洖绛斺€滃綋鍓?15m 鏄惁瀛樺湪鍙氦鏄撶粨鏋勨€濄€?
    - SMC锛氱粨鏋勮川閲忓垎 / FVG-OB 鍖?/ liquidity sweep 浠讳竴鎻愪緵缁撴瀯鏀拺銆?
    - SQZMOM锛氬悓鍚戣儗绂?+ 鐧芥煴/鍔ㄩ噺/DMI/閲婃斁绛夌‘璁ゃ€?
    """
    ctx = ctx or {}
    direction = str(direction).title()
    dkey = _direction_key(direction)

    if hasattr(row, "get"):
        get = row.get
    else:
        get = lambda k, default=None: default

    smc_quality_col = "smc_quality_score_bull" if dkey == "long" else "smc_quality_score_bear"
    smc_quality = safe_float(get(smc_quality_col, get("smc_quality_score", ctx.get("smc_quality_100", 0.0))), 0.0)
    has_valid_zone = safe_bool(ctx.get("has_valid_zone", False))
    liquidity_sweep = safe_bool(ctx.get("liquidity_sweep_confirmed", ctx.get("liquidity_sweep", False)))
    stop_hunt = safe_bool(get("bullish_stop_hunt" if dkey == "long" else "bearish_stop_hunt", False))
    smc_pass = bool(smc_quality >= 35.0 or has_valid_zone or liquidity_sweep or stop_hunt)

    div_dir = str(get("sqzmom_divergence_dir", ctx.get("sqzmom_divergence_dir", "None")))
    div_age = safe_float(get("sqzmom_divergence_age", ctx.get("sqzmom_divergence_age", 999)), 999)
    div_strength = safe_float(get("sqzmom_divergence_strength", ctx.get("sqzmom_divergence_strength", 0.0)), 0.0)
    same_div_recent = bool(div_dir == direction and div_age <= 18)
    same_div_fresh = bool(div_dir == direction and div_age <= 8)
    
    strong_div_threshold = 7.5
    strong_div_confirm = bool(same_div_recent and (div_strength >= strong_div_threshold or div_strength >= 25.0))

    white_confirm = safe_bool(get("sqzmom_reversal_confirm_long" if dkey == "long" else "sqzmom_reversal_confirm_short", False))
    momentum = safe_float(get("momentum", ctx.get("momentum", 0.0)), 0.0)
    momentum_slope = safe_float(get("momentum_slope", ctx.get("momentum_slope", 0.0)), 0.0)
    momentum_strength = safe_float(get("momentum_strength", ctx.get("momentum_strength", 0.0)), 0.0)
    momentum_strength_slope = safe_float(get("momentum_strength_slope", ctx.get("momentum_strength_slope", 0.0)), 0.0)
    squeeze_released = safe_bool(get("squeeze_released", ctx.get("squeeze_released", False)))
    dmi_aligned = safe_bool(ctx.get("sqzmom_dmi_aligned", False))
    
    if dkey == "long":
        dmi_aligned = dmi_aligned or safe_float(get("plus_di", 0.0), 0.0) >= safe_float(get("minus_di", 0.0), 0.0)
        momentum_confirm = bool((momentum > 0 or momentum_slope > 0) and (momentum_strength >= 0 or momentum_strength_slope >= 0 or squeeze_released))
    else:
        dmi_aligned = dmi_aligned or safe_float(get("minus_di", 0.0), 0.0) >= safe_float(get("plus_di", 0.0), 0.0)
        momentum_confirm = bool((momentum < 0 or momentum_slope < 0) and (momentum_strength <= 0 or momentum_strength_slope <= 0 or squeeze_released))

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
    楂橀樁妯″潡鏃佽矾璇勫垎鍗?(V2).
    
    淇敼锛氬鍔?1H(HTF) 鑳岀鍙嶅悜鎸″仠鏈哄埗锛屼互鍙?HTF+LTF 澶氬懆鏈熻儗绂诲叡鎸姞鍒嗐€?
    濡傛灉瑙﹀彂鎸″仠锛屽皢鏃犺鏃佽矾鍒嗘暟锛屽己鍒?position_multiplier = 0.0銆?
    """
    macro_ctx = macro_ctx or {}
    direction = str(signal.get("direction", ctx.get("direction", ""))).title()
    dkey = _direction_key(direction)
    close = safe_float(ctx.get("close", 0.0), 0.0)
    atr = max(safe_float(ctx.get("ATRr_14", ctx.get("atr", 0.0)), 0.0), 1e-12)
    regime = str(ctx.get("regime", "")).upper()
    vol_state = str(ctx.get("vol_state", "")).upper()

    scorers: Dict[str, Dict[str, Any]] = {}
    is_blocked = False
    block_reason = ""

    base = signal.get("base_trigger", {}) if isinstance(signal, dict) else {}
    base_strength = safe_float(base.get("strength"), 0.0)
    scorers["base"] = _scorer("base", base_strength, 0.035 * base_strength, 0.65 + 0.35 * _clip(base_strength, 0, 1), str(base.get("reason", "BASE_UNKNOWN")))

    # HTF/Macro
    htf_score = safe_float(macro_ctx.get("htf_macro_score", ctx.get("htf_macro_score", 0.0)), 0.0)
    if abs(htf_score) < 1e-9:
        htf = _scorer("htf", 0.0, 0.0, 1.0, "HTF_NEUTRAL")
    else:
        aligned = (htf_score > 0 and dkey == "long") or (htf_score < 0 and dkey == "short")
        mag = _clip(abs(htf_score) / 60.0, 0.0, 1.0)
        htf = _scorer("htf", mag if aligned else -mag, (0.035 if aligned else -0.055) * mag, 1.0 + (0.12 * mag if aligned else -0.28 * mag), "HTF_ALIGNED" if aligned else "HTF_CONFLICT_SOFT")
    scorers["htf"] = htf

    # VWAP
    vwap = safe_float(ctx.get("vwap_48", ctx.get("VWAP", ctx.get("vwap", close))), close)
    vwap_dist_atr = abs(close - vwap) / atr if atr > 0 and close > 0 else safe_float(ctx.get("vwap_dist_atr", 0.0), 0.0)
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

    # DMI
    plus_di = safe_float(ctx.get("plus_di", 0.0), 0.0)
    minus_di = safe_float(ctx.get("minus_di", 0.0), 0.0)
    adx = safe_float(ctx.get("adx", 0.0), 0.0)
    dmi_aligned = (plus_di >= minus_di and dkey == "long") or (minus_di >= plus_di and dkey == "short")
    dmi_strength = _clip(abs(plus_di - minus_di) / 35.0 + max(0.0, adx - 18.0) / 80.0, 0.0, 1.0)
    dmi_score = dmi_strength if dmi_aligned else -dmi_strength
    dmi_ev = (0.025 if dmi_aligned else -0.035) * dmi_strength
    dmi_mult = 1.0 + (0.10 if dmi_aligned else -0.22) * dmi_strength
    scorers["dmi"] = _scorer("dmi", dmi_score, dmi_ev, dmi_mult, "DMI_ALIGNED" if dmi_aligned else "DMI_CONFLICT_SOFT")

    # Breakout
    brk = safe_float(signal.get("breakout", 0.0), 0.0)
    brk_term = _clip(brk / 30.0, 0.0, 1.0)
    if brk_term > 0:
        scorers["breakout"] = _scorer("breakout", brk_term, 0.030 * brk_term, 1.0 + 0.12 * brk_term, "BREAKOUT_SCORE_BONUS")
    else:
        scorers["breakout"] = _scorer("breakout", -0.10, -0.005, 0.95, "NO_BREAKOUT_NO_REJECT")

    # ==========================
    # 澶氬懆鏈熻儗绂讳笌鍏辨尟閫昏緫 (15M & 1H)
    # ==========================
    
    # 15M (LTF) 鑳岀鐘舵€?
    div_dir = str(ctx.get("sqzmom_divergence_dir", "None"))
    div_age = safe_float(ctx.get("sqzmom_divergence_age", 999), 999)
    div_strength = safe_float(ctx.get("sqzmom_divergence_strength", 0.0), 0.0)
    
    # 1H (HTF) 鑳岀鐘舵€?
    htf_div_dir = str(macro_ctx.get("sqzmom_divergence_dir", "None"))
    htf_div_age = safe_float(macro_ctx.get("sqzmom_divergence_age", 999), 999)
    
    # 娲昏穬鑳岀鍒ゅ畾鏍囧噯 (18鍛ㄦ湡鍐?
    ltf_active_same = (div_dir == direction and div_age <= 18)
    htf_active_opp = (htf_div_dir in ("Long", "Short") and htf_div_dir != direction and htf_div_age <= 18)
    htf_active_same = (htf_div_dir == direction and htf_div_age <= 18)

    # 1. 1H HTF 鑳岀澶勭悊 (鎸″仠涓庡叡鎸?
    if htf_active_opp:
        is_blocked = True
        block_reason = f"HTF_OPPOSITE_DIV_BLOCK_{htf_div_dir.upper()}"
        scorers["htf_divergence"] = _scorer("htf_divergence", -2.0, -0.15, 0.05, block_reason)
    elif htf_active_same and ltf_active_same:
        scorers["htf_divergence"] = _scorer("htf_divergence", 1.25, 0.050, 1.35, "HTF_LTF_RESONANCE_BONUS")
    elif htf_active_same:
        scorers["htf_divergence"] = _scorer("htf_divergence", 0.60, 0.025, 1.15, "HTF_DIVERGENCE_SAME_SIDE")
    else:
        scorers["htf_divergence"] = _scorer("htf_divergence", 0.0, 0.0, 1.0, "HTF_DIVERGENCE_NEUTRAL")

    # 2. 15M LTF 鍘熸湁鑳岀閫昏緫
    if ltf_active_same:
        freshness = 1.0 if div_age <= 8 else 0.62
        strength_bonus = _clip(div_strength / 25.0, 0.0, 0.35)
        div_score = _clip(freshness + strength_bonus, 0.0, 1.25)
        scorers["ltf_divergence"] = _scorer("ltf_divergence", div_score, 0.040 * div_score, 1.0 + 0.13 * div_score, "LTF_DIVERGENCE_SAME_SIDE")
    elif div_dir in ("Long", "Short") and div_dir != direction and div_age <= 18:
        scorers["ltf_divergence"] = _scorer("ltf_divergence", -0.75, -0.045, 0.72, "LTF_DIVERGENCE_OPPOSITE_SIDE_SOFT")
    else:
        scorers["ltf_divergence"] = _scorer("ltf_divergence", 0.0, 0.0, 1.0, "LTF_DIVERGENCE_NEUTRAL")

    # Regime/Volatility
    if regime == "TREND":
        trend_aligned = safe_bool(ctx.get("trend_aligned", False))
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

    # 姹囨€昏绠?
    total_score = sum(v["score"] for v in scorers.values())
    ev_adjustment = sum(v["ev"] for v in scorers.values())
    
    position_multiplier = 1.0
    for v in scorers.values():
        position_multiplier *= safe_float(v.get("position_mult"), 1.0)
    
    # 鎸″仠閫昏緫锛氬鏋滄槸 Block 鐘舵€侊紝褰诲簳鍓ュず浠撲綅銆?
    if is_blocked:
        position_multiplier = 0.0
    else:
        position_multiplier = _clip(position_multiplier, 0.05, 1.35)

    positives = [k for k, v in scorers.items() if v["score"] > 0.20]
    negatives = [k for k, v in scorers.items() if v["score"] < -0.20]
    
    summary_str = f"blk={int(is_blocked)};score={total_score:.2f};ev_adj={ev_adjustment:.3f};pos_mult={position_multiplier:.2f};+{','.join(positives) or 'none'};-{','.join(negatives) or 'none'}"
    if is_blocked:
        summary_str += f";REJECT={block_reason}"

    return {
        "version": "Scorecard_V2_HTF_Div_Resonance",
        "direction": direction,
        "is_blocked": is_blocked,
        "block_reason": block_reason,
        "total_score": round(float(total_score), 4),
        "ev_adjustment": round(float(_clip(ev_adjustment, -0.18, 0.16)), 4),
        "position_multiplier": round(float(position_multiplier), 4),
        "scorers": scorers,
        "positive_modules": positives,
        "negative_modules": negatives,
        "summary": summary_str,
    }
