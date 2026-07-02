# -*- coding: utf-8 -*-
""" SMC + SQZMOM alpha layers for V28 semi-rebuild. Design goal: - Do not replace the original institutional_alpha_score. - Add an independent scoring/diagnostic layer that can release a small-size EARLY_REVERSAL path when SQZMOM white confirmation is missing, but SMC location + momentum quality are strong enough. - Add DMI/momentum-strength diagnostics for the second breakout engine. """
from __future__ import annotations

from typing import Any, Dict, Tuple
import numpy as np
import pandas as pd

from utils.safe import safe_float, safe_bool, safe_str


def _linreg_last(series: pd.Series, length: int) -> pd.Series:
    """Rolling linear-regression fitted value at the last point, Pine ta.linreg(..., 0)-like."""
    x = np.arange(length, dtype=float)
    x_mean = x.mean()
    denom = ((x - x_mean) ** 2).sum()

    def calc(y: np.ndarray) -> float:
        if len(y) != length or np.isnan(y).any() or denom == 0:
            return np.nan
        y_mean = y.mean()
        slope = ((x - x_mean) * (y - y_mean)).sum() / denom
        intercept = y_mean - slope * x_mean
        return float(intercept + slope * (length - 1))

    return series.rolling(length, min_periods=length).apply(calc, raw=True)


def add_enhanced_indicator_features(df: pd.DataFrame, length: int = 20, sig_len: int = 5) -> pd.DataFrame:
    """Add source-inspired SQZMOM Pro, DMI and simple liquidity target features. Safe to call multiple times. It only depends on OHLCV + ATRr_14 if present. """
    if df is None or df.empty:
        return df

    out = df.copy()
    close = pd.to_numeric(out.get("close"), errors="coerce")
    high = pd.to_numeric(out.get("high"), errors="coerce")
    low = pd.to_numeric(out.get("low"), errors="coerce")
    open_ = pd.to_numeric(out.get("open", close), errors="coerce")

    src = (open_ + high + low + close) / 4.0
    ma_momentum = close.rolling(length, min_periods=max(5, length // 2)).mean()
    highest = high.rolling(length, min_periods=max(5, length // 2)).max()
    lowest = low.rolling(length, min_periods=max(5, length // 2)).min()
    raw_mom = src - (((highest + lowest) / 2.0 + ma_momentum) / 2.0)
    sz = _linreg_last(raw_mom, length).fillna(out.get("momentum", raw_mom).fillna(0.0))
    sig = sz.rolling(sig_len, min_periods=1).mean()
    strength = sz - sig

    out["sqzmom_sz"] = sz
    out["sqzmom_signal"] = sig
    out["sqzmom_strength"] = strength
    out["sqzmom_strength_slope"] = strength.diff(2)
    out["sqzmom_cross_up"] = (sz >= sig) & (sz.shift(1) < sig.shift(1))
    out["sqzmom_cross_down"] = (sz <= sig) & (sz.shift(1) > sig.shift(1))

    basis = src.rolling(length, min_periods=max(5, length // 2)).mean()
    dev = 2.0 * src.rolling(length, min_periods=max(5, length // 2)).std(ddof=0)
    upper_bb = basis + dev
    lower_bb = basis - dev
    tr = pd.concat([(high - low), (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    range_kc = tr.rolling(length, min_periods=max(5, length // 2)).mean()
    base_kc = basis
    out["sqz_low"] = (lower_bb > base_kc - 2.0 * range_kc) & (upper_bb < base_kc + 2.0 * range_kc)
    out["sqz_mid"] = (lower_bb > base_kc - 1.5 * range_kc) & (upper_bb < base_kc + 1.5 * range_kc)
    out["sqz_high"] = (lower_bb > base_kc - 1.0 * range_kc) & (upper_bb < base_kc + 1.0 * range_kc)
    out["sqz_level"] = np.select([out["sqz_high"], out["sqz_mid"], out["sqz_low"]], [3, 2, 1], default=0).astype(int)
    out["sqz_release_pro"] = (out["sqz_level"].shift(1).fillna(0) > 0) & (out["sqz_level"] == 0)

    atr = pd.to_numeric(out.get("ATRr_14", tr.rolling(14, min_periods=1).mean()), errors="coerce").replace(0, np.nan)
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=out.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=out.index)
    plus_di = 100.0 * plus_dm.ewm(alpha=1 / 14, adjust=False).mean() / atr
    minus_di = 100.0 * minus_dm.ewm(alpha=1 / 14, adjust=False).mean() / atr
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = pd.to_numeric(out.get("adx", dx.ewm(alpha=1 / 14, adjust=False).mean()), errors="coerce").fillna(0.0)
    out["plus_di"] = plus_di.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    out["minus_di"] = minus_di.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    out["adx"] = adx
    out["adx_slope"] = adx.diff(1).fillna(0.0)
    out["dmi_bull"] = (out["plus_di"] >= out["minus_di"]) & (adx >= 23)
    out["dmi_bear"] = (out["plus_di"] < out["minus_di"]) & (adx >= 23)
    out["dmi_weak"] = (adx < 23) & (adx > 17)

    # EQH/EQL-like liquidity pools for targeting and location scoring.
    atr300 = tr.rolling(300, min_periods=20).mean().fillna(atr).replace(0, np.nan)
    eq_th = 0.30 * atr300
    ph = high.rolling(5, center=True, min_periods=5).max() == high
    pl = low.rolling(5, center=True, min_periods=5).min() == low
    prev_ph_price = high.where(ph).ffill().shift(1)
    prev_pl_price = low.where(pl).ffill().shift(1)
    out["eqh_near"] = ph & ((high - prev_ph_price).abs() <= eq_th)
    out["eql_near"] = pl & ((low - prev_pl_price).abs() <= eq_th)
    out["recent_eqh_50"] = out["eqh_near"].rolling(50, min_periods=1).sum() > 0
    out["recent_eql_50"] = out["eql_near"].rolling(50, min_periods=1).sum() > 0
    out["last_eqh_price"] = high.where(out["eqh_near"]).ffill()
    out["last_eql_price"] = low.where(out["eql_near"]).ffill()
    out["dist_to_eqh_atr"] = ((out["last_eqh_price"] - close).abs() / atr).replace([np.inf, -np.inf], np.nan)
    out["dist_to_eql_atr"] = ((close - out["last_eql_price"]).abs() / atr).replace([np.inf, -np.inf], np.nan)

    return out


def smc_location_score(row: Any, direction: str, entry_meta: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Score SMC location quality. Uses generic columns so it works across your prepare_smc_features versions."""
    entry_meta = entry_meta or {}
    direction = str(direction).title()
    score = 0.0
    reasons = []

    src = str(entry_meta.get("mitigation_src", row.get("mitigation_src", "NO_FVG_OB"))).upper()
    if src and src != "NO_FVG_OB":
        score += 28.0; reasons.append("STRUCTURE_PRESENT")
        if "OB" in src:
            score += 10.0; reasons.append("OB")
        if "FVG" in src:
            score += 7.0; reasons.append("FVG")
        if "BREAKER" in src or "BB" in src:
            score += 14.0; reasons.append("BREAKER_BLOCK")

    zone_near = safe_float(entry_meta.get("zone_near_atr", row.get("zone_near_atr", 9.99)), 9.99)
    vwap_dist = safe_float(entry_meta.get("vwap_dist_atr", row.get("vwap_dist_atr", 9.99)), 9.99)
    if zone_near <= 0.35:
        score += 14.0; reasons.append("ZONE_TOUCH")
    elif zone_near <= 0.75:
        score += 9.0; reasons.append("ZONE_NEAR")
    elif zone_near <= 1.20:
        score += 4.0; reasons.append("ZONE_ACCEPTABLE")

    if vwap_dist <= 0.75:
        score += 8.0; reasons.append("VWAP_NEAR")
    elif vwap_dist <= 1.25:
        score += 4.0; reasons.append("VWAP_OK")

    if direction == "Long":
        sweep = any(safe_bool(row.get(c, False)) for c in ["sellside_sweep", "sellside_liquidity_taken", "bullish_stop_hunt", "sweep_low", "liquidity_sweep_long"])
        wrong = any(safe_bool(row.get(c, False)) for c in ["buyside_sweep", "buyside_liquidity_taken", "bearish_stop_hunt", "sweep_high", "liquidity_sweep_short"])
        if safe_bool(row.get("recent_eql_50", False)) or safe_float(row.get("dist_to_eql_atr", 9.99), 9.99) <= 0.85:
            score += 8.0; reasons.append("EQL_POOL_CONTEXT")
    else:
        sweep = any(safe_bool(row.get(c, False)) for c in ["buyside_sweep", "buyside_liquidity_taken", "bearish_stop_hunt", "sweep_high", "liquidity_sweep_short"])
        wrong = any(safe_bool(row.get(c, False)) for c in ["sellside_sweep", "sellside_liquidity_taken", "bullish_stop_hunt", "sweep_low", "liquidity_sweep_long"])
        if safe_bool(row.get("recent_eqh_50", False)) or safe_float(row.get("dist_to_eqh_atr", 9.99), 9.99) <= 0.85:
            score += 8.0; reasons.append("EQH_POOL_CONTEXT")

    if sweep:
        score += 18.0; reasons.append("LIQUIDITY_SWEEP")
    if wrong:
        score -= 18.0; reasons.append("WRONG_SIDE_SWEEP")

    return {"smc_location_score_v2": round(float(max(0.0, min(100.0, score))), 4), "smc_location_reasons_v2": ";".join(reasons)}


def sqzmom_momentum_score(row: Any, direction: str) -> Dict[str, Any]:
    direction = str(direction).title()
    sz = safe_float(row.get("sqzmom_sz", row.get("momentum", 0.0)), 0.0)
    sig = safe_float(row.get("sqzmom_signal", 0.0), 0.0)
    strength = safe_float(row.get("sqzmom_strength", sz - sig), 0.0)
    strength_slope = safe_float(row.get("sqzmom_strength_slope", row.get("momentum_slope", 0.0)), 0.0)
    sqz_level = int(safe_float(row.get("sqz_level", 1 if safe_bool(row.get("squeeze_on", False)) else 0), 0))
    release = safe_bool(row.get("sqz_release_pro", row.get("squeeze_released", False)))
    adx = safe_float(row.get("adx", 0.0), 0.0)
    adx_slope = safe_float(row.get("adx_slope", 0.0), 0.0)
    plus_di = safe_float(row.get("plus_di", 0.0), 0.0)
    minus_di = safe_float(row.get("minus_di", 0.0), 0.0)

    score = 0.0
    reasons = []
    if sqz_level >= 2:
        score += 10.0; reasons.append("MID_HIGH_SQUEEZE")
    elif sqz_level == 1:
        score += 5.0; reasons.append("LOW_SQUEEZE")
    if release:
        score += 14.0; reasons.append("SQUEEZE_RELEASE")

    if direction == "Long":
        if sz > sig:
            score += 13.0; reasons.append("SZ_ABOVE_SIGNAL")
        if strength > 0:
            score += 10.0; reasons.append("MOM_STRENGTH_POS")
        if strength_slope > 0:
            score += 10.0; reasons.append("MOM_STRENGTH_RISING")
        if plus_di >= minus_di:
            score += 8.0; reasons.append("DMI_LONG")
    else:
        if sz < sig:
            score += 13.0; reasons.append("SZ_BELOW_SIGNAL")
        if strength < 0:
            score += 10.0; reasons.append("MOM_STRENGTH_NEG")
        if strength_slope < 0:
            score += 10.0; reasons.append("MOM_STRENGTH_FALLING")
        if minus_di > plus_di:
            score += 8.0; reasons.append("DMI_SHORT")

    if adx >= 23:
        score += 8.0; reasons.append("ADX_TREND")
    if adx_slope > 0:
        score += 5.0; reasons.append("ADX_RISING")

    return {"sqzmom_momentum_score_v2": round(float(max(0.0, min(100.0, score))), 4), "sqzmom_momentum_reasons_v2": ";".join(reasons)}


def evaluate_alpha_layers(row: Any, direction: str, exec_ctx: Dict[str, Any], macro_ctx: Dict[str, Any], entry_meta: Dict[str, Any]) -> Dict[str, Any]:
    """Return a small independent layer decision. This does not force a trade. runner.py decides how to use allow_early_entry. """
    direction = str(direction).title()
    smc = smc_location_score(row, direction, entry_meta)
    mom = sqzmom_momentum_score(row, direction)

    div_age = int(safe_float(row.get("sqzmom_divergence_age", 999), 999))
    div_dir = str(row.get("sqzmom_divergence_dir", "None"))
    div_recent = (div_dir == direction and div_age <= 12)
    white = safe_bool(row.get("sqzmom_reversal_confirm_long" if direction == "Long" else "sqzmom_reversal_confirm_short", False))
    macro_conflict = False
    if macro_ctx:
        mm = safe_float(macro_ctx.get("macro_momentum", 0.0), 0.0)
        ms = safe_float(macro_ctx.get("macro_momentum_slope", 0.0), 0.0)
        if direction == "Long" and mm < 0 and ms < 0:
            macro_conflict = True
        if direction == "Short" and mm > 0 and ms > 0:
            macro_conflict = True

    location = safe_float(smc["smc_location_score_v2"], 0.0)
    momentum = safe_float(mom["sqzmom_momentum_score_v2"], 0.0)
    blend = round(location * 0.52 + momentum * 0.48, 4)

    allow_early = bool(div_recent and (not white) and (not macro_conflict) and location >= 58.0 and momentum >= 54.0 and blend >= 60.0)
    alpha_path = "CORE_REVERSAL" if div_recent and white else ("EARLY_REVERSAL" if allow_early else "NO_ALPHA_LAYER")
    size = 0.18
    if blend >= 78:
        size = 0.30
    elif blend >= 68:
        size = 0.24

    out: Dict[str, Any] = {}
    out.update(smc)
    out.update(mom)
    out.update({
        "alpha_layer_score": blend,
        "alpha_path": alpha_path,
        "allow_early_entry": allow_early,
        "alpha_layer_size_mult": size,
        "alpha_layer_div_recent_12": bool(div_recent),
        "alpha_layer_white_confirm": bool(white),
        "alpha_layer_macro_conflict": bool(macro_conflict),
    })
    return out


def dmi_breakout_filter(row: Any, direction: str) -> Tuple[bool, str]:
    direction = str(direction).title()
    adx = safe_float(row.get("adx", 0.0), 0.0)
    adx_slope = safe_float(row.get("adx_slope", 0.0), 0.0)
    strength = safe_float(row.get("sqzmom_strength", 0.0), 0.0)
    plus_di = safe_float(row.get("plus_di", 0.0), 0.0)
    minus_di = safe_float(row.get("minus_di", 0.0), 0.0)
    if direction == "Long":
        ok = plus_di >= minus_di and adx >= 20.0 and adx_slope >= -0.25 and strength >= 0.0
    else:
        ok = minus_di > plus_di and adx >= 20.0 and adx_slope >= -0.25 and strength <= 0.0
    return bool(ok), "DMI_BREAKOUT_OK" if ok else "DMI_BREAKOUT_REJECT"
