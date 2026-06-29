# -*- coding: utf-8 -*-
"""V36 multi-engine signal layer for SQZMOM divergence."""
from __future__ import annotations
from typing import Tuple
import os
import numpy as np
import pandas as pd
ENGINE_VERSION = "V36_MULTI_ENGINE_SIGNALS_20260612"

def _bool_series(df: pd.DataFrame, name: str, default: bool = False) -> pd.Series:
    if name not in df.columns:
        return pd.Series(default, index=df.index, dtype=bool)
    return df[name].fillna(default).astype(bool)

def _continuous_true_streak(s: pd.Series) -> pd.Series:
    b = s.fillna(False).astype(bool)
    groups = (b != b.shift()).cumsum()
    return b.astype(int).groupby(groups).cumsum().where(b, 0).astype(int)

def _atr_ratio(df: pd.DataFrame) -> pd.Series:
    atr14 = pd.to_numeric(df.get("ATRr_14", pd.Series(np.nan, index=df.index)), errors="coerce")
    atr50 = pd.to_numeric(df.get("ATRr_50", pd.Series(np.nan, index=df.index)), errors="coerce")
    high = pd.to_numeric(df.get("high", pd.Series(0, index=df.index)), errors="coerce").values
    low = pd.to_numeric(df.get("low", pd.Series(0, index=df.index)), errors="coerce").values
    close = pd.to_numeric(df.get("close", pd.Series(0, index=df.index)), errors="coerce")
    prev_close = close.shift(1).bfill().values
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    fallback_atr50 = pd.Series(tr, index=df.index).rolling(50, min_periods=10).mean()
    final_atr50 = atr50.fillna(fallback_atr50)
    return (atr14 / final_atr50.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan).fillna(0.0)

def get_breakout_after_div_signals(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    if df is None or df.empty:
        empty = pd.Series(False, dtype=bool)
        num = pd.Series(dtype=float)
        return empty, empty, num, num
    DIV_CONTEXT_BARS = 18
    BREAKOUT_LOOKBACK = 12
    MIN_VOL_Z = 0.45
    MIN_ATR_RATIO = 0.88
    MIN_SQUEEZE_STREAK = 3
    close = pd.to_numeric(df["close"], errors="coerce")
    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    volume = pd.to_numeric(df.get("volume", pd.Series(0.0, index=df.index)), errors="coerce").fillna(0.0)
    div_dir = df.get("sqzmom_divergence_dir", pd.Series("None", index=df.index)).fillna("None").astype(str)
    div_age = pd.to_numeric(df.get("sqzmom_divergence_age", pd.Series(999, index=df.index)), errors="coerce").fillna(999)
    bull_div_recent = ((div_dir == "Long") & (div_age <= DIV_CONTEXT_BARS)) | (_bool_series(df, "bullish_divergence").rolling(DIV_CONTEXT_BARS, min_periods=1).sum() > 0)
    bear_div_recent = ((div_dir == "Short") & (div_age <= DIV_CONTEXT_BARS)) | (_bool_series(df, "bearish_divergence").rolling(DIV_CONTEXT_BARS, min_periods=1).sum() > 0)
    squeeze_on = _bool_series(df, "squeeze_on")
    squeeze_streak = _continuous_true_streak(squeeze_on)
    squeeze_context = (squeeze_streak >= MIN_SQUEEZE_STREAK) | _bool_series(df, "squeeze_released")
    vol_std = volume.rolling(50, min_periods=10).std(ddof=0).replace(0, np.nan)
    vol_z = ((volume - volume.rolling(50, min_periods=10).mean()) / vol_std).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    volume_ratio = pd.to_numeric(df.get("volume_ratio", pd.Series(1.0, index=df.index)), errors="coerce").fillna(1.0)
    atr_expansion = _atr_ratio(df)
    prior_high = high.rolling(BREAKOUT_LOOKBACK, min_periods=4).max().shift(1)
    prior_low = low.rolling(BREAKOUT_LOOKBACK, min_periods=4).min().shift(1)
    momentum = pd.to_numeric(df.get("momentum", close - close.rolling(20, min_periods=5).mean()), errors="coerce").fillna(0.0)
    momentum_signal = pd.to_numeric(df.get("momentum_signal", momentum.rolling(5, min_periods=1).mean()), errors="coerce").fillna(0.0)
    momentum_strength = pd.to_numeric(df.get("momentum_strength", momentum - momentum_signal), errors="coerce").fillna(0.0)
    momentum_strength_slope = pd.to_numeric(df.get("momentum_strength_slope", momentum_strength.diff(3)), errors="coerce").fillna(0.0)
    plus_di = pd.to_numeric(df.get("plus_di", pd.Series(0.0, index=df.index)), errors="coerce").fillna(0.0)
    minus_di = pd.to_numeric(df.get("minus_di", pd.Series(0.0, index=df.index)), errors="coerce").fillna(0.0)
    adx = pd.to_numeric(df.get("adx", pd.Series(0.0, index=df.index)), errors="coerce").fillna(0.0)
    mom_long_ok = (momentum_strength >= 0) | (momentum_strength_slope > 0)
    mom_short_ok = (momentum_strength <= 0) | (momentum_strength_slope < 0)
    dmi_long_ok = (plus_di >= minus_di) | ((adx >= 15) & (adx < 28))
    dmi_short_ok = (minus_di >= plus_di) | ((adx >= 15) & (adx < 28))
    vol_ok = (vol_z > MIN_VOL_Z) | (volume_ratio > 1.08)
    # If ATR columns are missing, atr_expansion will be all 0.0 -> atr_ok all False.
    # Fallback: if ATR data is unavailable (all zeros), default atr_ok to True
    # so that missing data does not silently kill all breakout signals.
    atr_data_available = atr_expansion.max() > 0.01
    atr_ok = (atr_expansion > MIN_ATR_RATIO) if atr_data_available else pd.Series(True, index=df.index)
    long_breakout = bull_div_recent & (close > prior_high) & vol_ok & atr_ok & mom_long_ok & dmi_long_ok
    short_breakout = bear_div_recent & (close < prior_low) & vol_ok & atr_ok & mom_short_ok & dmi_short_ok
    # Squeeze path: still requires vol_ok and atr_ok to ensure quality
    long_breakout = long_breakout | (bull_div_recent & squeeze_context & (close > prior_high) & vol_ok & atr_ok & mom_long_ok)
    short_breakout = short_breakout | (bear_div_recent & squeeze_context & (close < prior_low) & vol_ok & atr_ok & mom_short_ok)
    if os.environ.get("SMC_DEBUG_SIGNALS", "0") == "1":
        print("\n" + "=" * 55)
        print("🕵️‍♂️ [V36 Breakout Engine Diagnostic / 突破引擎漏斗分析]")
        print(f" K线总数: {len(df)}")
        print(f" 1. 背离上下文数 (Bull/Bear <= {DIV_CONTEXT_BARS}): {int(bull_div_recent.sum())} / {int(bear_div_recent.sum())}")
        print(f" 2. Squeeze上下文数: {int(squeeze_context.sum())}")
        print(f" 3. 价格突破{BREAKOUT_LOOKBACK}前高/前低数: {int((close > prior_high).sum())} / {int((close < prior_low).sum())}")
        print(f" 4. 量能满足数 (Vol Z > {MIN_VOL_Z} 或 VR>1.08): {int(vol_ok.sum())}")
        print(f" 5. 波动扩张满足数 (ATR > {MIN_ATR_RATIO}): {int(atr_ok.sum())}")
        print(f" 6. Momentum+DMI满足数: {int((mom_long_ok & dmi_long_ok).sum())} / {int((mom_short_ok & dmi_short_ok).sum())}")
        print(f" => 最终突破触发 (Long/Short): {int(long_breakout.sum())} / {int(short_breakout.sum())}")
        print("=" * 55 + "\n")
    return long_breakout.fillna(False), short_breakout.fillna(False), vol_z, atr_expansion

def get_reversal_signals(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    if df is None or df.empty:
        empty = pd.Series(False, dtype=bool)
        return empty, empty
    bull_div = _bool_series(df, "bullish_divergence")
    bear_div = _bool_series(df, "bearish_divergence")
    bull_confirm = _bool_series(df, "sqzmom_reversal_confirm_long", False)
    bear_confirm = _bool_series(df, "sqzmom_reversal_confirm_short", False)
    div_dir = df.get("sqzmom_divergence_dir", pd.Series("None", index=df.index)).fillna("None").astype(str)
    div_age = pd.to_numeric(df.get("sqzmom_divergence_age", pd.Series(999, index=df.index)), errors="coerce").fillna(999)
    fresh_long = ((div_dir == "Long") & (div_age <= 8)) | bull_div
    fresh_short = ((div_dir == "Short") & (div_age <= 8)) | bear_div
    reversal_long = fresh_long & bull_confirm
    reversal_short = fresh_short & bear_confirm
    return reversal_long.fillna(False), reversal_short.fillna(False)

def get_setup_signals(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    breakout_long, breakout_short, vol_z, atr_ratio = get_breakout_after_div_signals(df)
    reversal_long, reversal_short = get_reversal_signals(df)
    out = pd.DataFrame(index=df.index)
    out["reversal_long"] = reversal_long.astype(bool)
    out["reversal_short"] = reversal_short.astype(bool)
    out["breakout_long"] = breakout_long.astype(bool)
    out["breakout_short"] = breakout_short.astype(bool)
    out["combo_long"] = out["reversal_long"] & out["breakout_long"]
    out["combo_short"] = out["reversal_short"] & out["breakout_short"]
    out["breakout_vol_z"] = vol_z
    out["breakout_atr_ratio"] = atr_ratio
    out["setup_signal_count"] = out[["reversal_long", "reversal_short", "breakout_long", "breakout_short", "combo_long", "combo_short"]].astype(int).sum(axis=1)
    out["has_any_setup"] = out["setup_signal_count"] > 0
    return out