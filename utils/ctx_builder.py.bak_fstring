# -*- coding: utf-8 -*-
"""
ctx_builder.py — 评分上下文构建器（消除 v11_institutional_runner.py 和 app.py 之间的重复代码）

职责：
    1. _enrich_common_fields(exec_ctx, curr, macro_ctx) — 补充共享评分字段
    2. build_directional_contexts(exec_ctx, curr) — 构建多空评分上下文 (long_ctx, short_ctx)
    3. _calc_sqzmom_score(curr, direction) — 计算 sqzmom_score (0~44)
"""

from __future__ import annotations
from typing import Any, Dict, Tuple


def _safe_float(v, default=0.0):
    try:
        if v is None:
            return default
        x = float(v)
        return x if x == x else default
    except (ValueError, TypeError):
        return default


def _safe_bool(v, default=False):
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.lower() in ("1", "true", "yes", "y")
    return bool(v)


def _calc_body_pct(curr) -> float:
    """计算 K 线实体占比"""
    close = _safe_float(curr.get("close", 0))
    open_ = _safe_float(curr.get("open", 0))
    high = _safe_float(curr.get("high", 0))
    low = _safe_float(curr.get("low", 0))
    body = abs(close - open_)
    hilo = high - low
    return body / hilo if hilo > 0 else 0.0


def _calc_sqzmom_score(curr, direction: str) -> float:
    """
    计算 sqzmom_score (0~44)
    
    公式：
    - momentum 方向: ±7
    - momentum_slope 方向: ±6
    - white reversal: ±8
    - DMI 对齐: ±7
    - squeeze_released: ±6
    - divergence + age: ±10
    
    参数:
        curr: 最新 K 线 (dict-like)
        direction: "Long" 或 "Short"
    
    返回:
        0~44 的 sqzmom_score
    """
    is_long = direction.lower() == "long"
    score = 0.0
    
    momentum = _safe_float(curr.get("momentum", 0))
    momentum_slope = _safe_float(curr.get("momentum_slope", 0))
    
    # Momentum 方向
    if is_long and momentum > 0:
        score += 7.0
    elif not is_long and momentum < 0:
        score += 7.0
    
    # Momentum slope
    if is_long and momentum_slope > 0:
        score += 6.0
    elif not is_long and momentum_slope < 0:
        score += 6.0
    
    # White reversal
    if is_long and _safe_bool(curr.get("sqzmom_white_reversal_long", False)):
        score += 8.0
    elif not is_long and _safe_bool(curr.get("sqzmom_white_reversal_short", False)):
        score += 8.0
    
    # DMI 对齐
    plus_di = _safe_float(curr.get("plus_di", 0))
    minus_di = _safe_float(curr.get("minus_di", 0))
    dmi_bull = _safe_bool(curr.get("dmi_bull", False))
    dmi_bear = _safe_bool(curr.get("dmi_bear", False))
    
    if is_long and (dmi_bull or plus_di >= minus_di):
        score += 7.0
    elif not is_long and (dmi_bear or minus_di > plus_di):
        score += 7.0
    
    # Squeeze released
    if _safe_bool(curr.get("squeeze_released", False)):
        score += 6.0
    
    # Divergence + age
    if is_long:
        div_dir = str(curr.get("sqzmom_divergence_dir", "None"))
        bot_age = int(_safe_float(curr.get("bot_div_age", 999)))
        if div_dir == "Long" and bot_age <= 18:
            score += 10.0
    else:
        div_dir = str(curr.get("sqzmom_divergence_dir", "None"))
        top_age = int(_safe_float(curr.get("top_div_age", 999)))
        if div_dir == "Short" and top_age <= 18:
            score += 10.0
    
    return max(0.0, min(44.0, score))


def _enrich_common_fields(exec_ctx: Dict[str, Any], curr, macro_ctx: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    补充评分系统所需的共享字段（方向无关）。
    修改 exec_ctx in-place 并返回。
    """
    # 方向相关
    if macro_ctx:
        exec_ctx["htf_direction"] = macro_ctx.get("allowed_direction", "")
    
    exec_ctx["setup_type"] = (
        "ob" if exec_ctx.get("ob_valid")
        else ("fvg" if exec_ctx.get("bearish_fvg") or exec_ctx.get("bullish_fvg") else "")
    )
    
    # SMC 区域
    exec_ctx["smc_zone_score"] = (
        _safe_float(exec_ctx.get("pivot_strength_high", 0))
        + _safe_float(exec_ctx.get("pivot_strength_low", 0))
    )
    exec_ctx["has_valid_zone"] = bool(
        exec_ctx.get("ob_valid")
        or exec_ctx.get("bullish_fvg")
        or exec_ctx.get("bearish_fvg")
    )
    
    # K 线形态
    exec_ctx["body_pct"] = _calc_body_pct(curr)
    
    # 其他默认字段
    exec_ctx.setdefault("macro_conflict", False)
    exec_ctx.setdefault("too_extended", False)
    exec_ctx["fe_bottom"] = _safe_bool(curr.get("is_FE", False))
    exec_ctx["fe_top"] = _safe_bool(curr.get("is_Inv_FE", False))
    exec_ctx["same_side_div_count_12"] = 0.0
    exec_ctx["vwap_align"] = None
    exec_ctx["rr"] = 1.0  # 占位，后续 calculate_dynamic_tp_sl 覆盖
    exec_ctx["distance_atr"] = 0.0
    exec_ctx["ob_strength"] = _safe_float(exec_ctx.get("pivot_strength_high", 0))
    exec_ctx["fvg_quality"] = 1.0 if (exec_ctx.get("bearish_fvg") or exec_ctx.get("bullish_fvg")) else 0.0
    exec_ctx["displacement"] = _safe_float(exec_ctx.get("pivot_strength_low", 0))
    exec_ctx["liquidity"] = 1.0 if (exec_ctx.get("is_bsl_swept") or exec_ctx.get("is_ssl_swept")) else 0.0
    
    return exec_ctx


def build_directional_contexts(exec_ctx: Dict[str, Any], curr) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    构建多头和空头各自的评分上下文。
    
    返回 (long_ctx, short_ctx)，两者都是独立的 dict 副本。
    """
    direction_defaults = _get_direction_defaults(curr)
    
    long_ctx = dict(exec_ctx)
    short_ctx = dict(exec_ctx)
    
    # ── 多头 ──
    long_ctx["direction"] = "Long"
    long_ctx["divergence_confirmed"] = _safe_bool(curr.get("has_bot_div", False))
    long_ctx["sqzmom_divergence_dir"] = "Long" if _safe_bool(curr.get("has_bot_div", False)) else ""
    long_ctx["sqzmom_divergence_age"] = int(_safe_float(curr.get("bot_div_age", 999)))
    long_ctx["sqzmom_divergence_strength"] = _safe_float(curr.get("bot_div_strength", 0))
    long_ctx["sqzmom_white_confirm"] = _safe_bool(curr.get("sqzmom_white_reversal_long", False))
    long_ctx["sqzmom_momentum_confirm"] = _safe_bool(curr.get("sqzmom_white_reversal_long", False))
    long_ctx["sqzmom_reversal_confirm_long"] = _safe_bool(curr.get("sqzmom_white_reversal_long", False))
    long_ctx["sqzmom_reversal_confirm_short"] = False
    long_ctx["sqzmom_dmi_aligned"] = _safe_bool(curr.get("dmi_bull", False))
    long_ctx["sqzmom_trigger_ok"] = _safe_bool(curr.get("dmi_bull", False))
    long_ctx["dmi_bull"] = _safe_bool(curr.get("dmi_bull", False))
    long_ctx["dmi_bear"] = False
    long_ctx["momentum"] = _safe_float(curr.get("momentum", 0))
    long_ctx["liquidity_sweep_confirmed"] = _safe_bool(curr.get("is_ssl_swept", False))
    long_ctx["liquidity_wrong_side"] = _safe_bool(curr.get("is_bsl_swept", False))
    long_ctx["sqzmom_score"] = _calc_sqzmom_score(curr, "Long")
    
    # ── 空头 ──
    short_ctx["direction"] = "Short"
    short_ctx["divergence_confirmed"] = _safe_bool(curr.get("has_top_div", False))
    short_ctx["sqzmom_divergence_dir"] = "Short" if _safe_bool(curr.get("has_top_div", False)) else ""
    short_ctx["sqzmom_divergence_age"] = int(_safe_float(curr.get("top_div_age", 999)))
    short_ctx["sqzmom_divergence_strength"] = _safe_float(curr.get("top_div_strength", 0))
    short_ctx["sqzmom_white_confirm"] = _safe_bool(curr.get("sqzmom_white_reversal_short", False))
    short_ctx["sqzmom_momentum_confirm"] = _safe_bool(curr.get("sqzmom_white_reversal_short", False))
    short_ctx["sqzmom_reversal_confirm_long"] = False
    short_ctx["sqzmom_reversal_confirm_short"] = _safe_bool(curr.get("sqzmom_white_reversal_short", False))
    short_ctx["sqzmom_dmi_aligned"] = _safe_bool(curr.get("dmi_bear", False))
    short_ctx["sqzmom_trigger_ok"] = _safe_bool(curr.get("dmi_bear", False))
    short_ctx["dmi_bull"] = False
    short_ctx["dmi_bear"] = _safe_bool(curr.get("dmi_bear", False))
    short_ctx["momentum"] = _safe_float(curr.get("momentum", 0))
    short_ctx["liquidity_sweep_confirmed"] = _safe_bool(curr.get("is_bsl_swept", False))
    short_ctx["liquidity_wrong_side"] = _safe_bool(curr.get("is_ssl_swept", False))
    short_ctx["sqzmom_score"] = _calc_sqzmom_score(curr, "Short")
    
    return long_ctx, short_ctx


def _get_direction_defaults(curr) -> Dict[str, Any]:
    """提取当前 K 线中的方向相关原始值（供内部构建用）"""
    return {
        "has_bot_div": curr.get("has_bot_div", False),
        "has_top_div": curr.get("has_top_div", False),
        "bot_div_age": curr.get("bot_div_age", 999),
        "top_div_age": curr.get("top_div_age", 999),
        "bot_div_strength": curr.get("bot_div_strength", 0),
        "top_div_strength": curr.get("top_div_strength", 0),
        "sqzmom_white_reversal_long": curr.get("sqzmom_white_reversal_long", False),
        "sqzmom_white_reversal_short": curr.get("sqzmom_white_reversal_short", False),
        "dmi_bull": curr.get("dmi_bull", False),
        "dmi_bear": curr.get("dmi_bear", False),
        "momentum": curr.get("momentum", 0),
        "momentum_slope": curr.get("momentum_slope", 0),
        "is_bsl_swept": curr.get("is_bsl_swept", False),
        "is_ssl_swept": curr.get("is_ssl_swept", False),
        "plus_di": curr.get("plus_di", 0),
        "minus_di": curr.get("minus_di", 0),
        "squeeze_released": curr.get("squeeze_released", False),
        "sqzmom_divergence_dir": curr.get("sqzmom_divergence_dir", "None"),
    }
