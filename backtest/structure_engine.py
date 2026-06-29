import pandas as pd
import numpy as np

from .memory_engine import liquidity_memory_zones, market_memory_score
def _liquidity_raid_score(df: pd.DataFrame) -> pd.Series:
    """Calculate liquidity raid probability per row using available SMC fields."""
    # Use available SMC fields to build a raid probability proxy (0~1)
    smc_bull = _safe_col(df, "smc_quality_score_bull", 0.5)
    smc_bear = _safe_col(df, "smc_quality_score_bear", 0.5)

    raid_score = (smc_bull + smc_bear) / 200.0  # normalize 0~100 to 0~1

    # Boost if sweeps are detected
    long_sweep = _safe_col(df, "sellside_sweep", 0) + _safe_col(df, "liquidity_sweep_long", 0)
    short_sweep = _safe_col(df, "buyside_sweep", 0) + _safe_col(df, "liquidity_sweep_short", 0)
    raid_score += (long_sweep + short_sweep) * 0.15

    # Boost if FVG/OB zones exist
    fvg_high = _safe_col(df, "fvg_high", 0)
    raid_score += (fvg_high > 0).astype(float) * 0.1

    return raid_score.clip(0.0, 1.0)


def _safe_col(df: pd.DataFrame, col: str, default: float = 0.0) -> pd.Series:
    """Get a column from DataFrame, returning a Series filled with default if missing."""
    if col in df.columns:
        return df[col].fillna(default).astype(float)
    return pd.Series(float(default), index=df.index)


def sweep_probability(df: pd.DataFrame) -> pd.Series:
    body = (df["close"] - df["open"]).abs()
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    wick = ((df["high"] - df[["open","close"]].max(axis=1)) +
            (df[["open","close"]].min(axis=1) - df["low"])) / rng

    vol = df.get("volume_ratio", 1.0)

    score = (body/rng).fillna(0)*0.3 + wick.fillna(0)*0.4 + vol*0.3
    return score.clip(0,1)


def detect_sweep(df: pd.DataFrame) -> pd.Series:
    # 缺失时用 inf/-inf，使扫单判断恒为 False（不触发误报）
    eq_high = df.get("eq_high", pd.Series(float('inf'), index=df.index))
    eq_low = df.get("eq_low", pd.Series(float('-inf'), index=df.index))
    return (df["high"] > eq_high) | (df["low"] < eq_low)


def detect_mss(df: pd.DataFrame) -> pd.Series:
    # 缺失时用 inf/-inf，使 MSS 判断恒为 False（不触发误报）
    last_lower_high = df.get("last_lower_high", pd.Series(float('inf'), index=df.index))
    last_higher_low = df.get("last_higher_low", pd.Series(float('-inf'), index=df.index))
    return (df["close"] > last_lower_high) | (df["close"] < last_higher_low)


def liquidity_target(df: pd.DataFrame):
    hi, lo = liquidity_memory_zones(df)
    return (hi + lo) / 2


def structure_signal(df: pd.DataFrame) -> pd.Series:
    # 基础 sweep 概率
    base_sweep = sweep_probability(df)
    # 流动性掠夺概率作为权重
    raid_weight = _liquidity_raid_score(df)
    # 加权后的 sweep 得分
    weighted_sweep = base_sweep * (0.5 + 0.5 * raid_weight)
    
    # 市场记忆得分：价格处于历史流动性密集区时降低门槛
    mem_score = market_memory_score(df)
    # 动态门槛：基础 0.5，记忆得分高时最低降到 0.3
    dynamic_threshold = 0.5 - 0.2 * mem_score
    
    sweep_p = weighted_sweep >= dynamic_threshold
    mss = detect_mss(df)
    return sweep_p & mss

