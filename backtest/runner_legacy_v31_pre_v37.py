# === V30 修改版: 增加动量过滤 + 高级OB筛选 + Sweep->MSS->Retest ===
# ==== V29.8 Patch Applied: 动能+SMC入场 & EQH/EQL TP ====
# Optimized patch applied: final cleanup
# -*- coding: utf-8 -*-
"""Integrated SMC Right-Side Entry Backtest Runner V30 SMART MONEY TP - direct overwrite file for backtest/runner.py 核心升级： - 完整 runner.py，直接覆盖 backtest/runner.py - V28 引入智能止盈：扫描对立面流动性池(前高/前低)作为靶点，实施85%抢跑截胡止盈 - 增加 1R 无风险护盾：浮盈达到 1R 强制上推止损至保本(Break-Even) - 放宽入场等待 K 线数与追价空间，拦截流失的高质信号，增加开单频次 - V30 新增：SQZMOM动量斜率严格过滤、Premium/Discount OB过滤、Sweep->MSS->Retest严格认证 """
from __future__ import annotations

import logging
from backtest.diagnostic_tracker import DiagnosticTracker
from backtest.v34_regime_engine import (
    classify_regime,
    select_portfolio,
    score_for_regime,
    check_entry_allowed,
    calc_position_size,
    v34_regime_decision,
    REGIME_MULTIPLIER,
)

# ============================================================
# 调试日志：使用标准 logging 模块
# 全局日志级别由 run_backtest.py 中的 logging.basicConfig 控制
# 设为 logging.INFO 可屏蔽 DEBUG 级别的刷屏代码
# 设为 logging.DEBUG 可查看详细漏斗打印
# ============================================================
logger = logging.getLogger(__name__)


def _liquidity_sweep_context(row, direction):
    """Compatibility helper for V29.7 breakout filter. The original optimized patch referenced this hook before defining it. Keep the default conservative: only confirm liquidity sweep when one of the known row columns/flags is truthy. """
    try:
        if row is None:
            return {"liquidity_sweep_confirmed": False}
        direction = str(direction).title()
        base_keys = [
            "liquidity_sweep_confirmed",
            "liq_sweep",
            "liquidity_sweep",
            "sweep_confirmed",
            "stop_hunt",
            "stop_hunt_confirmed",
        ]
        long_keys = ["bull_liquidity_sweep", "long_liquidity_sweep", "sweep_low", "liquidity_sweep_long"]
        short_keys = ["bear_liquidity_sweep", "short_liquidity_sweep", "sweep_high", "liquidity_sweep_short"]
        keys = base_keys + (long_keys if direction == "Long" else short_keys if direction == "Short" else [])
        confirmed = any(_safe_bool(row.get(k, False)) for k in keys if hasattr(row, "get"))
        return {"liquidity_sweep_confirmed": bool(confirmed)}
    except Exception:
        return {"liquidity_sweep_confirmed": False}


VERSION = "V31_INSTITUTIONAL_SCORING_NO_BREAKOUT_20260610"

from typing import Any, Dict, List, Optional, Tuple
import os

import numpy as np
import pandas as pd

try:
    from analysis.fvg_stop_hunt import prepare_smc_features, nearest_mitigation_price
except Exception:
    from ..analysis.fvg_stop_hunt import prepare_smc_features, nearest_mitigation_price
try:
    from strategy.risk import calculate_dynamic_tp_sl, risk_is_acceptable
except Exception:
    from ..strategy.risk import calculate_dynamic_tp_sl, risk_is_acceptable
try:
    from . import signals_engine
except Exception:
    import signals_engine


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        v = float(value)
        return default if np.isnan(v) or np.isinf(v) else v
    except Exception:
        return default


def _safe_bool(value: Any) -> bool:
    try:
        return bool(value)
    except Exception:
        return False


def _col_any(row: pd.Series, names: List[str], default: Any = None) -> Any:
    for name in names:
        if name in row.index:
            return row.get(name, default)
    return default


def _directional_bool(row: pd.Series, direction: str, long_names: List[str], short_names: List[str]) -> bool:
    direction = str(direction).title()
    names = long_names if direction == "Long" else short_names
    return any(_safe_bool(_col_any(row, [n], False)) for n in names)


# ==========================================
# Module 1: robust CSV loading and cleaning
# ==========================================
def load_ohlcv_csv(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"❌ 找不到文件：{path}，请检查路径！")

    df = pd.read_csv(path, low_memory=False)
    print(f"\n📂 成功读取 CSV 文件: {path} | 初始行数: {len(df)}")

    df.columns = [str(c).lower().strip() for c in df.columns]

    for col in ["ts", "date", "timestamp", "time", "open_time", "datetime"]:
        if col in df.columns:
            df = df.rename(columns={col: "datetime"})
            break

    df = df.loc[:, ~df.columns.duplicated()]

    if "datetime" not in df.columns:
        raise ValueError(f"❌ 找不到时间列！当前 CSV 包含的列有：{list(df.columns)}")

    time_series = df["datetime"].astype(str).str.strip()
    is_numeric = time_series.str.replace(r"\.", "", regex=True).str.isdigit().all()

    if is_numeric:
        print("🕒 检测到纯数字时间戳，启动智能转换...")
        df["datetime"] = pd.to_numeric(df["datetime"], errors="coerce")
        if df["datetime"].max() > 1e11:
            df["datetime"] = pd.to_datetime(df["datetime"], unit="ms", errors="coerce")
        else:
            df["datetime"] = pd.to_datetime(df["datetime"], unit="s", errors="coerce")
    else:
        print("🕒 检测到字符串日期，启动文本解析...")
        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")

    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    before_drop = len(df)
    df = df.dropna(subset=["datetime", "open", "high", "low", "close"])
    after_drop = len(df)

    print(
        f"📊 数据清洗报告：原始 {before_drop} 行 -> 剔除错误/空数据 "
        f"{before_drop - after_drop} 行 -> 【最终有效 K 线: {after_drop} 根】\n"
    )

    return df.sort_values("datetime").reset_index(drop=True)


# ==========================================
# Module 2: Williams VIX Fix, Momentum & Divergence R
# ==========================================

def _local_pivot_low(s: pd.Series, left: int = 2, right: int = 1) -> pd.Series:
    """严格匹配 TV 源码的 Pivot Low: 左侧至少看 lbL=2，右侧要求 lbR=1"""
    out = pd.Series(False, index=s.index)
    for k in range(left, len(s) - right):
        win = s.iloc[k - left:k + right + 1]
        out.iloc[k] = bool(s.iloc[k] == win.min())
    return out

def _local_pivot_high(s: pd.Series, left: int = 2, right: int = 1) -> pd.Series:
    """严格匹配 TV 源码的 Pivot High"""
    out = pd.Series(False, index=s.index)
    for k in range(left, len(s) - right):
        win = s.iloc[k - left:k + right + 1]
        out.iloc[k] = bool(s.iloc[k] == win.max())
    return out

def _add_vix_fix_fe_signals(out: pd.DataFrame) -> pd.DataFrame:
    """提取自 CM Williams Vix Fix V3 的 FE 止跌/止涨逻辑"""
    pd_len, bbl, mult_vix, lb, ph = 22, 20, 2.0, 50, 0.85
    ltLB, mtLB, str_len = 40, 14, 3

    close = out["close"]
    low = out["low"]
    high = out["high"]

    # --- FE Bottom (Bullish) ---
    hc = close.rolling(pd_len, min_periods=1).max()
    wvf = ((hc - low) / hc) * 100
    midLine = wvf.rolling(bbl, min_periods=1).mean()
    sDev = mult_vix * wvf.rolling(bbl, min_periods=1).std(ddof=0)
    upperBand = midLine + sDev
    rangeHigh = wvf.rolling(lb, min_periods=1).max() * ph

    filtered = ((wvf.shift(1) >= upperBand.shift(1)) | (wvf.shift(1) >= rangeHigh.shift(1))) & (wvf < upperBand) & (wvf < rangeHigh)
    upRange = (low > low.shift(1)) & (close > high.shift(1))
    
    cond_FE_bull = upRange & (close > close.shift(str_len)) & ((close < close.shift(ltLB)) | (close < close.shift(mtLB))) & filtered
    out["fe_bottom"] = cond_FE_bull

    # --- FE Top (Bearish - Inverse Logic) ---
    lc = close.rolling(pd_len, min_periods=1).min()
    wvf_inv = ((high - lc) / lc.replace(0, np.nan)) * 100
    midLine_inv = wvf_inv.rolling(bbl, min_periods=1).mean()
    sDev_inv = mult_vix * wvf_inv.rolling(bbl, min_periods=1).std(ddof=0)
    upperBand_inv = midLine_inv + sDev_inv
    rangeHigh_inv = wvf_inv.rolling(lb, min_periods=1).max() * ph

    filtered_inv = ((wvf_inv.shift(1) >= upperBand_inv.shift(1)) | (wvf_inv.shift(1) >= rangeHigh_inv.shift(1))) & (wvf_inv < upperBand_inv) & (wvf_inv < rangeHigh_inv)
    downRange = (high < high.shift(1)) & (close < low.shift(1))
    
    cond_FE_bear = downRange & (close < close.shift(str_len)) & ((close > close.shift(ltLB)) | (close > close.shift(mtLB))) & filtered_inv
    out["fe_top"] = cond_FE_bear

    return out

def _add_sqzmom_divergence_features(out: pd.DataFrame) -> pd.DataFrame:
    """提取 DoctaBot 级别的严苛波段 Pivot R 背离"""
    close = out["close"].astype(float)
    high = out["high"].astype(float)
    low = out["low"].astype(float)
    mom = out["momentum"].astype(float).fillna(0.0)
    
    atr = out["ATRr_14"].replace(0, np.nan).bfill().fillna(close * 0.006)

    piv_l = _local_pivot_low(mom, 2, 1)
    piv_h = _local_pivot_high(mom, 2, 1)

    bull_div = pd.Series(False, index=out.index)
    bear_div = pd.Series(False, index=out.index)
    div_age = pd.Series(999, index=out.index, dtype="int64")
    div_dir = pd.Series("None", index=out.index, dtype="object")
    div_strength = pd.Series(0.0, index=out.index)

    low_pivots: List[int] = []
    high_pivots: List[int] = []
    last_div_i: Optional[int] = None

    for i in range(len(out)):
        if piv_l.iloc[i]:
            low_pivots.append(i)
            if len(low_pivots) >= 2:
                a, b = low_pivots[-2], low_pivots[-1]
                if mom.iloc[b] < 0:
                    price_lower_low = low.iloc[b] < low.iloc[a] - 0.03 * atr.iloc[b]
                    mom_higher_low = mom.iloc[b] > mom.iloc[a]
                    if price_lower_low and mom_higher_low:
                        bull_div.iloc[i] = True
                        div_dir.iloc[i] = "Long"
                        last_div_i = i
                        div_strength.iloc[i] = min(12.0, abs((mom.iloc[b] - mom.iloc[a]) / max(abs(mom.iloc[a]), 1e-9)) * 8.0 + 4.0)

        if piv_h.iloc[i]:
            high_pivots.append(i)
            if len(high_pivots) >= 2:
                a, b = high_pivots[-2], high_pivots[-1]
                if mom.iloc[b] > 0:
                    price_higher_high = high.iloc[b] > high.iloc[a] + 0.03 * atr.iloc[b]
                    mom_lower_high = mom.iloc[b] < mom.iloc[a]
                    if price_higher_high and mom_lower_high:
                        bear_div.iloc[i] = True
                        div_dir.iloc[i] = "Short"
                        last_div_i = i
                        div_strength.iloc[i] = min(12.0, abs((mom.iloc[a] - mom.iloc[b]) / max(abs(mom.iloc[a]), 1e-9)) * 8.0 + 4.0)

        if last_div_i is not None:
            div_age.iloc[i] = i - last_div_i
            if div_dir.iloc[i] == "None" and div_age.iloc[i] <= 20:
                div_dir.iloc[i] = div_dir.iloc[last_div_i]
                div_strength.iloc[i] = max(0.0, div_strength.iloc[last_div_i] - 0.5 * div_age.iloc[i])

    # V30_REALISTIC_BACKTEST_FIX_20260610:
    # Pivot uses right=1, so the pivot/divergence is only known after the next bar has closed.
    # Shift divergence features forward by one bar to avoid using future information for next-open entry.
    bull_div_confirmed = bull_div.shift(1).astype("boolean").fillna(False).astype(bool)
    bear_div_confirmed = bear_div.shift(1).astype("boolean").fillna(False).astype(bool)
    div_dir_confirmed = div_dir.shift(1).fillna("None")
    div_age_confirmed = div_age.shift(1).fillna(999).astype("int64")
    div_strength_confirmed = div_strength.shift(1).fillna(0.0)

    out["bullish_divergence"] = out.get("bullish_divergence", False) | bull_div_confirmed
    out["bearish_divergence"] = out.get("bearish_divergence", False) | bear_div_confirmed
    out["sqzmom_divergence_dir"] = div_dir_confirmed
    out["sqzmom_divergence_age"] = div_age_confirmed
    out["sqzmom_divergence_strength"] = div_strength_confirmed.round(4)

    sc2 = mom < 0
    sc3 = mom >= mom.shift(1)
    sqzmom_white_bull = sc2 & sc3 

    sc1 = mom >= 0
    sc4 = mom < mom.shift(1)
    sqzmom_white_bear = sc1 & sc4 

    body = (close - out["open"].astype(float))
    body_pct = out.get("body_pct", pd.Series(0.0, index=out.index)).fillna(0.0)
    
    strong_reject_long = out.get("strong_reject_long", pd.Series(False, index=out.index))
    strong_reject_short = out.get("strong_reject_short", pd.Series(False, index=out.index))
    enr_long = out.get("effort_no_result_long", pd.Series(False, index=out.index))
    enr_short = out.get("effort_no_result_short", pd.Series(False, index=out.index))
    
    fe_bull = out.get("fe_bottom", pd.Series(False, index=out.index))
    fe_bear = out.get("fe_top", pd.Series(False, index=out.index))

    early_long_triggers = strong_reject_long | enr_long | fe_bull
    early_short_triggers = strong_reject_short | enr_short | fe_bear

    long_confirm = sqzmom_white_bull | ((body > 0) & (body_pct >= 0.28)) | early_long_triggers
    short_confirm = sqzmom_white_bear | ((body < 0) & (body_pct >= 0.28)) | early_short_triggers
    
    out["sqzmom_reversal_confirm_long"] = long_confirm
    out["sqzmom_reversal_confirm_short"] = short_confirm

    out["bull_div_count_12"] = out["bullish_divergence"].rolling(12, min_periods=1).sum()
    out["bear_div_count_12"] = out["bearish_divergence"].rolling(12, min_periods=1).sum()
    return out


def add_basic_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = prepare_smc_features(df)
    close = out["close"].astype(float)
    high = out["high"].astype(float)
    low = out["low"].astype(float)
    open_ = out["open"].astype(float)
    vol = out["volume"].astype(float) if "volume" in out.columns else pd.Series(0.0, index=out.index)

    out["ema_20"] = close.ewm(span=20, adjust=False).mean()
    out["ema_50"] = close.ewm(span=50, adjust=False).mean()
    out["ema_200"] = close.ewm(span=200, adjust=False).mean()
    out["ema_slope_20"] = out["ema_20"].diff(5)
    out["ema_slope_50"] = out["ema_50"].diff(8)

    rng = (high - low).replace(0, np.nan)
    out["body_pct"] = (close - open_).abs() / rng
    out["bar_range_atr"] = rng / out["ATRr_14"].replace(0, np.nan)
    
    out["upper_wick_pct"] = (high - out[["open", "close"]].max(axis=1)) / rng
    out["lower_wick_pct"] = (out[["open", "close"]].min(axis=1) - low) / rng
    
    vol_ma = vol.rolling(20).mean().bfill().replace(0, np.nan)
    out["volume_ratio"] = (vol / vol_ma).replace([np.inf, -np.inf], np.nan).fillna(1.0)
    out["high_vol_anomaly"] = out["volume_ratio"] > 1.5
    out["volume_contracting"] = out["volume_ratio"] <= 0.82

    out = _add_vix_fix_fe_signals(out)

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    atr = out["ATRr_14"].replace(0, np.nan)

    plus_di = 100 * pd.Series(plus_dm, index=out.index).ewm(alpha=1 / 14, adjust=False).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=out.index).ewm(alpha=1 / 14, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)

    if "adx" not in out.columns:
        out["adx"] = dx.ewm(alpha=1 / 14, adjust=False).mean().fillna(0.0)

    bb_mid = close.rolling(20, min_periods=10).mean()
    bb_std = close.rolling(20, min_periods=10).std()
    kc_range = out["ATRr_14"].rolling(20, min_periods=10).mean()
    
    # [V30 Add]: 50-bar Premium/Discount Equilibrium Price
    recent_high_50 = high.rolling(50, min_periods=10).max()
    recent_low_50 = low.rolling(50, min_periods=10).min()
    out["eq_price"] = (recent_high_50 + recent_low_50) / 2.0

    bb_upper = bb_mid + 2.0 * bb_std
    bb_lower = bb_mid - 2.0 * bb_std
    kc_mid = bb_mid
    kc_low_upper = kc_mid + kc_range * 2.0
    kc_low_lower = kc_mid - kc_range * 2.0
    kc_mid_upper = kc_mid + kc_range * 1.5
    kc_mid_lower = kc_mid - kc_range * 1.5
    kc_high_upper = kc_mid + kc_range * 1.0
    kc_high_lower = kc_mid - kc_range * 1.0

    out["squeeze_low"] = ((bb_lower > kc_low_lower) & (bb_upper < kc_low_upper)).fillna(False)
    out["squeeze_mid"] = ((bb_lower > kc_mid_lower) & (bb_upper < kc_mid_upper)).fillna(False)
    out["squeeze_high"] = ((bb_lower > kc_high_lower) & (bb_upper < kc_high_upper)).fillna(False)
    out["squeeze_on"] = out["squeeze_low"]
    prev_squeeze_on = out["squeeze_on"].shift(1).astype("boolean").fillna(False).astype(bool)
    out["squeeze_released"] = prev_squeeze_on & (~out["squeeze_on"].astype(bool))
    out["squeeze_level"] = np.select(
        [out["squeeze_high"], out["squeeze_mid"], out["squeeze_low"]],
        [3, 2, 1],
        default=0,
    )

    out["momentum"] = close - bb_mid
    # [V30 Add]: 1-bar strict momentum slope for sz > sz[1]
    out["momentum_slope_1"] = out["momentum"].diff(1).fillna(0.0)
    out["momentum_signal"] = out["momentum"].rolling(5, min_periods=1).mean()
    out["momentum_strength"] = (out["momentum"] - out["momentum_signal"]).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    out["momentum_slope"] = out["momentum"].diff(3).fillna(0.0)
    out["momentum_strength_slope"] = out["momentum_strength"].diff(3).fillna(0.0)
    out["momentum_strength_rising_3"] = out["momentum_strength"].diff().rolling(3, min_periods=1).sum().fillna(0.0)

    out["plus_di"] = plus_di.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    out["minus_di"] = minus_di.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    out["adx_rising"] = out["adx"].diff(1).fillna(0.0) > 0
    out["dmi_bull"] = (out["plus_di"] >= out["minus_di"]) & (out["adx"] >= 23.0)
    out["dmi_bear"] = (out["plus_di"] < out["minus_di"]) & (out["adx"] >= 23.0)
    out["dmi_weak"] = (out["adx"] < 23.0) & (out["adx"] > 17.0)

    atr_14 = (high - low).rolling(14).mean().bfill()
    deviation = (close - out["ema_20"]) / out["ema_20"].replace(0, np.nan) * 500

    # 方向相关评分：deviation 的正负代表价格在 EMA20 上方（多头有利）或下方（空头有利）
    # 多头：正 deviation 加分，负 deviation 减分
    # 空头：负 deviation 加分，正 deviation 减分
    # 这里先计算一个基础分，然后在主循环中根据方向选择对应的分数
    bull_score = pd.Series(36.0, index=out.index)
    bear_score = pd.Series(36.0, index=out.index)
    
    # 多头：正偏离加分，负偏离减分
    bull_dev = deviation.clip(0, 20) * 0.42  # 只加正偏离
    bear_dev = (-deviation).clip(0, 20) * 0.42  # 只加负偏离
    bull_score += bull_dev
    bear_score += bear_dev
    
    # 波动率（方向无关）
    vol_score = (((high - low) / atr_14.replace(0, np.nan) * 10).clip(0, 15) * 0.38)
    bull_score += vol_score
    bear_score += vol_score
    
    # 成交量（方向无关）
    vol_ratio_score = ((out["volume_ratio"] * 10).clip(0, 15) * 0.35)
    bull_score += vol_ratio_score
    bear_score += vol_ratio_score
    
    # 实体比例（方向无关）
    body_score = (out["body_pct"].fillna(0.0).clip(0, 1.0) * 6.0)
    bull_score += body_score
    bear_score += body_score
    
    # Squeeze release（方向无关）
    squeeze_bonus = np.where(out["squeeze_released"], 4.0, 0.0)
    bull_score += squeeze_bonus
    bear_score += squeeze_bonus

    out["smc_quality_score_bull"] = pd.Series(bull_score, index=out.index).replace([np.inf, -np.inf], np.nan).fillna(36.0).clip(0, 100)
    out["smc_quality_score_bear"] = pd.Series(bear_score, index=out.index).replace([np.inf, -np.inf], np.nan).fillna(36.0).clip(0, 100)
    # 保留旧字段兼容
    out["smc_quality_score"] = out["smc_quality_score_bull"]
    out = _add_sqzmom_divergence_features(out)
    return out


def build_exec_context(row: pd.Series) -> Dict[str, Any]:
    adx = _safe_float(row.get("adx"), 0.0)
    atr = _safe_float(row.get("ATRr_14"), 0.0)
    close = _safe_float(row.get("close"), 0.0)
    atr_pct = atr / close if close > 0 else 0.0
    ema_slope = _safe_float(row.get("ema_slope_20"), 0.0)
    ema50_slope = _safe_float(row.get("ema_slope_50"), 0.0)

    slope_vote = ema_slope + 0.6 * ema50_slope
    trend_dir = "Long" if slope_vote > 0 else ("Short" if slope_vote < 0 else "None")

    # 使用 V34 4-Regime 分类器
    v34_regime = classify_regime(row, {
        "adx": adx,
        "atr": atr,
        "atr_pct": atr_pct,
        "trend_direction": trend_dir,
    })
    
    return {
        "adx": adx,
        "atr": atr,
        "atr_pct": atr_pct,
        "regime": v34_regime,
        "trend_direction": trend_dir,
        "volatility": "high" if atr_pct > 0.012 else "low" if atr_pct < 0.004 else "normal",
        "squeeze": "released" if _safe_bool(row.get("squeeze_released", False)) else "building" if _safe_bool(row.get("squeeze_on", False)) else "none",
    }


def build_macro_context(df_macro: pd.DataFrame, ts: Any) -> Dict[str, Any]:
    if df_macro is None or df_macro.empty:
        return {}
    m = df_macro[df_macro["datetime"] <= ts] if "datetime" in df_macro.columns and pd.notna(ts) else pd.DataFrame()
    row = df_macro.iloc[0] if m.empty else m.iloc[-1]
    vwap = _safe_float(row.get("vwap_48", row.get("VWAP", row.get("vwap", 0.0))), 0.0)
    close = _safe_float(row.get("close"), 0.0)
    mom = _safe_float(row.get("momentum", 0.0), 0.0)
    mom_slope = _safe_float(row.get("momentum_slope", 0.0), 0.0)
    ema_slope = _safe_float(row.get("ema_slope_20", 0.0), 0.0)

    # 统一给 V37/旧引擎提供 HTF 方向。之前只返回 macro_vwap_bias，
    # V37 读取 allowed_direction/macro_direction 时拿不到值，导致 HTF 对齐评分长期失效。
    if (mom > 0 and mom_slope >= 0) or (close >= vwap and ema_slope > 0):
        macro_direction = "Long"
    elif (mom < 0 and mom_slope <= 0) or (close < vwap and ema_slope < 0):
        macro_direction = "Short"
    else:
        macro_direction = "None"

    return {
        "macro_close": close,
        "macro_vwap": vwap,
        "macro_vwap_bias": "bull" if close >= vwap else "bear",
        "macro_slope": ema_slope,
        "macro_momentum": mom,
        "macro_momentum_slope": mom_slope,
        "macro_direction": macro_direction,
        "allowed_direction": macro_direction,
        "macro_bull_divergence": _safe_bool(row.get("bullish_divergence", False)),
        "macro_bear_divergence": _safe_bool(row.get("bearish_divergence", False)),
        "macro_divergence_dir": row.get("sqzmom_divergence_dir", "None"),
    }


def fallback_signal_score(row: pd.Series, direction: str) -> Tuple[float, float, List[str]]:
    direction = str(direction).title()
    close = _safe_float(row.get("close"))
    ema20 = _safe_float(row.get("ema_20"))
    ema50 = _safe_float(row.get("ema_50"))
    ema200 = _safe_float(row.get("ema_200"))
    momentum = _safe_float(row.get("momentum"))
    momentum_slope = _safe_float(row.get("momentum_slope"))
    stop_hunt_dir = str(row.get("stop_hunt_direction") or "")
    volume_ratio = _safe_float(row.get("volume_ratio"), 1.0)
    body_pct = _safe_float(row.get("body_pct"), 0.0)

    score = 0.0
    reasons: List[str] = []

    if direction == "Long":
        if ema20 >= ema50:
            score += 1.8; reasons.append("EMA20_ABOVE_50")
        if close >= ema200:
            score += 0.8; reasons.append("PRICE_ABOVE_EMA200")
        if momentum > 0:
            score += 1.3; reasons.append("MOM_BULL")
        if momentum_slope > 0:
            score += 0.6; reasons.append("MOM_ACCEL")
        if close >= ema20:
            score += 0.8; reasons.append("PRICE_ABOVE_EMA20")
        if stop_hunt_dir == "Long" or _safe_bool(row.get("bullish_stop_hunt", False)):
            score += 2.0; reasons.append("BULLISH_STOP_HUNT")
    else:
        if ema20 <= ema50:
            score += 1.8; reasons.append("EMA20_BELOW_50")
        if close <= ema200:
            score += 0.8; reasons.append("PRICE_BELOW_EMA200")
        if momentum < 0:
            score += 1.3; reasons.append("MOM_BEAR")
        if momentum_slope < 0:
            score += 0.6; reasons.append("MOM_ACCEL")
        if close <= ema20:
            score += 0.8; reasons.append("PRICE_BELOW_EMA20")
        if stop_hunt_dir == "Short" or _safe_bool(row.get("bearish_stop_hunt", False)):
            score += 2.0; reasons.append("BEARISH_STOP_HUNT")

    if volume_ratio >= 1.15:
        score += 0.5; reasons.append("VOL_EXPANSION")
    if body_pct >= 0.45:
        score += 0.4; reasons.append("BODY_COMMITMENT")
    if _safe_bool(row.get("squeeze_released", False)):
        score += 0.7; reasons.append("SQUEEZE_RELEASE")

    return float(score), 4.0, reasons


def _validate_smc_zone(row: pd.Series, direction: str, entry_meta: Dict[str, Any], exec_ctx: Dict[str, Any]) -> Dict[str, Any]:
    direction = str(direction).title()
    atr = max(_safe_float(row.get("ATRr_14", exec_ctx.get("atr", 0.0)), 0.0), 1e-12)
    price = _safe_float(row.get("close"), 0.0)
    mitigation_src = str(entry_meta.get("mitigation_src", "NO_FVG_OB"))
    mitigation_price = entry_meta.get("mitigation_price")
    has_valid_zone = bool(mitigation_price is not None and mitigation_src != "NO_FVG_OB")

    zone_near_atr = 9.99
    if has_valid_zone and price > 0:
        zone_near_atr = abs(price - _safe_float(mitigation_price, price)) / atr

    smc_zone_score = 0.0
    if has_valid_zone:
        smc_zone_score += 26.0
        if "FVG" in mitigation_src.upper():
            smc_zone_score += 7.0
        if "OB" in mitigation_src.upper():
            smc_zone_score += 9.0
        if zone_near_atr <= 0.35:
            smc_zone_score += 10.0
        elif zone_near_atr <= 0.70:
            smc_zone_score += 7.0
        elif zone_near_atr <= 1.05:
            smc_zone_score += 3.0
        else:
            smc_zone_score -= 7.0

    body_pct = _safe_float(row.get("body_pct"), 0.0)
    if body_pct >= 0.36:
        smc_zone_score += 4.0

    return {
        "has_valid_zone": has_valid_zone,
        "smc_zone_score": round(float(max(0.0, min(50.0, smc_zone_score))), 4),
        "zone_near_atr": round(float(zone_near_atr), 4),
    }


def _liquidity_context_score(row: pd.Series, direction: str) -> Dict[str, Any]:
    direction = str(direction).title()
    long_sweep = _directional_bool(
        row,
        "Long",
        ["sellside_sweep", "sellside_liquidity_taken", "bullish_stop_hunt", "sweep_low", "liquidity_sweep_long"],
        ["buyside_sweep", "buyside_liquidity_taken", "bearish_stop_hunt", "sweep_high", "liquidity_sweep_short"],
    )
    short_sweep = _directional_bool(
        row,
        "Short",
        ["sellside_sweep", "sellside_liquidity_taken", "bullish_stop_hunt", "sweep_low", "liquidity_sweep_long"],
        ["buyside_sweep", "buyside_liquidity_taken", "bearish_stop_hunt", "sweep_high", "liquidity_sweep_short"],
    )
    liquidity_sweep_confirmed = long_sweep if direction == "Long" else short_sweep
    wrong_side = short_sweep if direction == "Long" else long_sweep

    score = 0.0
    if liquidity_sweep_confirmed:
        score += 18.0
    if wrong_side:
        score -= 14.0
    stop_hunt_dir = str(row.get("stop_hunt_direction") or "")
    if stop_hunt_dir == direction:
        score += 5.0
    elif stop_hunt_dir in ("Long", "Short") and stop_hunt_dir != direction:
        score -= 5.0

    return {
        "liquidity_score": round(float(max(-18.0, min(24.0, score))), 4),
        "liquidity_sweep_confirmed": bool(liquidity_sweep_confirmed),
        "liquidity_wrong_side": bool(wrong_side),
    }



def _sqzmom_trigger_state(row: pd.Series, direction: str) -> Dict[str, Any]:
    """SQZMOM Plus 触发状态。"""
    direction = str(direction).title()
    divergence = _directional_bool(
        row,
        direction,
        ["bullish_divergence", "bull_div", "divergence_bull", "regular_bullish_divergence", "hidden_bullish_divergence"],
        ["bearish_divergence", "bear_div", "divergence_bear", "regular_bearish_divergence", "hidden_bearish_divergence"],
    )
    div_dir = str(row.get("sqzmom_divergence_dir", "None"))
    if div_dir == direction:
        divergence = True

    age = int(_safe_float(row.get("sqzmom_divergence_age", 999), 999))
    hard_recent_div = bool(divergence and age <= 8)
    soft_recent_div = bool(divergence and age <= 20)

    momentum = _safe_float(row.get("momentum"), 0.0)
    slope = _safe_float(row.get("momentum_slope"), 0.0)
    momentum_slope_1 = _safe_float(row.get("momentum_slope_1"), 0.0) # V30 Strict 1-bar slope
    strength = _safe_float(row.get("momentum_strength"), 0.0)
    strength_slope = _safe_float(row.get("momentum_strength_slope"), 0.0)
    strength_rising_3 = _safe_float(row.get("momentum_strength_rising_3"), 0.0)
    release = _safe_bool(row.get("squeeze_released", False))
    adx_rising = _safe_bool(row.get("adx_rising", False))

    if direction == "Long":
        # [V30 UPDATE]: 动量必须为正且绝对正在加速 (sz > 0 且 sz > sz[1])
        strict_mom_ok = (momentum > 0 or momentum_slope_1 > 0)
        white_confirm = _safe_bool(row.get("sqzmom_reversal_confirm_long", False))
        repeat_count = _safe_float(row.get("bull_div_count_12", 0.0), 0.0)
        dmi_aligned = _safe_bool(row.get("dmi_bull", False)) or (_safe_float(row.get("plus_di"), 0.0) >= _safe_float(row.get("minus_di"), 0.0) and _safe_float(row.get("adx"), 0.0) < 23.0)
        momentum_confirm = bool(strict_mom_ok and ((strength > 0 and strength_slope >= 0) or (slope > 0) or (release and strength_rising_3 > 0)))
    else:
        # [V30 UPDATE]: 动量必须为负且绝对正在加速 (sz < 0 且 sz < sz[1])
        strict_mom_ok = (momentum < 0 or momentum_slope_1 < 0)
        white_confirm = _safe_bool(row.get("sqzmom_reversal_confirm_short", False))
        repeat_count = _safe_float(row.get("bear_div_count_12", 0.0), 0.0)
        dmi_aligned = _safe_bool(row.get("dmi_bear", False)) or (_safe_float(row.get("plus_di"), 0.0) < _safe_float(row.get("minus_di"), 0.0) and _safe_float(row.get("adx"), 0.0) < 23.0)
        momentum_confirm = bool(strict_mom_ok and ((strength < 0 and strength_slope <= 0) or (slope < 0) or (release and strength_rising_3 < 0)))

    trigger_ok = bool((hard_recent_div and white_confirm) or (soft_recent_div and momentum_confirm and (dmi_aligned or release or adx_rising)))

    return {
        "divergence_confirmed": bool(hard_recent_div or (soft_recent_div and momentum_confirm)),
        "sqzmom_white_confirm": bool(white_confirm),
        "sqzmom_momentum_confirm": bool(momentum_confirm),
        "sqzmom_dmi_aligned": bool(dmi_aligned),
        "sqzmom_trigger_ok": bool(trigger_ok),
        "sqzmom_divergence_age": age,
        "sqzmom_divergence_strength": _safe_float(row.get("sqzmom_divergence_strength", 0.0), 0.0),
        "same_side_div_count_12": repeat_count,
        "momentum_strength": round(float(strength), 4),
        "momentum_strength_slope": round(float(strength_slope), 4),
    }


def _sqzmom_context_score(row: pd.Series, direction: str) -> Dict[str, Any]:
    direction = str(direction).title()
    momentum = _safe_float(row.get("momentum"), 0.0)
    slope = _safe_float(row.get("momentum_slope"), 0.0)
    strength = _safe_float(row.get("momentum_strength"), 0.0)
    strength_slope = _safe_float(row.get("momentum_strength_slope"), 0.0)
    squeeze_level = int(_safe_float(row.get("squeeze_level", 0), 0))
    release = _safe_bool(row.get("squeeze_released", False))
    adx = _safe_float(row.get("adx"), 0.0)
    adx_rising = _safe_bool(row.get("adx_rising", False))

    trig = _sqzmom_trigger_state(row, direction)

    hf_confirmed = _directional_bool(
        row,
        direction,
        ["hf_long", "higher_frame_long", "htf_bull", "macro_long", "htf_long"],
        ["hf_short", "higher_frame_short", "htf_bear", "macro_short", "htf_short"],
    )

    score = 0.0
    if squeeze_level >= 3:
        score += 7.0
    elif squeeze_level == 2:
        score += 5.0
    elif squeeze_level == 1:
        score += 3.0
    if release:
        score += 7.0

    if direction == "Long":
        if momentum > 0:
            score += 4.0
        if slope > 0:
            score += 4.0
        if strength > 0:
            score += 7.0
        if strength_slope > 0:
            score += 4.0
    else:
        if momentum < 0:
            score += 4.0
        if slope < 0:
            score += 4.0
        if strength < 0:
            score += 7.0
        if strength_slope < 0:
            score += 4.0

    if trig["divergence_confirmed"]:
        score += 9.0
    if trig["sqzmom_white_confirm"]:
        score += 4.0
    if trig["sqzmom_momentum_confirm"]:
        score += 5.0
    if trig["sqzmom_dmi_aligned"]:
        score += 4.0
    if adx_rising and 17.0 <= adx <= 52.0:
        score += 3.0
    if hf_confirmed:
        score += 7.0

    return {
        "sqzmom_score": round(float(max(0.0, min(44.0, score))), 4),
        "sqzmom_release": bool(release),
        "divergence_confirmed": bool(trig["divergence_confirmed"]),
        "sqzmom_white_confirm": bool(trig["sqzmom_white_confirm"]),
        "sqzmom_momentum_confirm": bool(trig["sqzmom_momentum_confirm"]),
        "sqzmom_dmi_aligned": bool(trig["sqzmom_dmi_aligned"]),
        "sqzmom_trigger_ok": bool(trig["sqzmom_trigger_ok"]),
        "sqzmom_divergence_age": trig["sqzmom_divergence_age"],
        "sqzmom_divergence_strength": round(float(trig["sqzmom_divergence_strength"]), 4),
        "same_side_div_count_12": round(float(trig["same_side_div_count_12"]), 4),
        "momentum_strength": trig["momentum_strength"],
        "momentum_strength_slope": trig["momentum_strength_slope"],
        "squeeze_level": squeeze_level,
        "hf_confirmed": bool(hf_confirmed),
    }


def _macro_conflict(direction: str, macro_ctx: Dict[str, Any]) -> Tuple[bool, str, float]:
    if not macro_ctx:
        return False, "NO_MACRO", 0.0
    direction = str(direction).title()
    macro_mom = _safe_float(macro_ctx.get("macro_momentum"), 0.0)
    macro_slope = _safe_float(macro_ctx.get("macro_momentum_slope"), 0.0)
    macro_div = str(macro_ctx.get("macro_divergence_dir", "None"))
    if direction == "Long":
        if macro_div == "Short" or (macro_mom < 0 and macro_slope < 0):
            return True, "MACRO_1H_BEAR_CONFLICT", -18.0
        if macro_div == "Long" or macro_slope > 0:
            return False, "MACRO_1H_LONG_SUPPORT", 6.0
    else:
        if macro_div == "Long" or (macro_mom > 0 and macro_slope > 0):
            return True, "MACRO_1H_BULL_CONFLICT", -18.0
        if macro_div == "Short" or macro_slope < 0:
            return False, "MACRO_1H_SHORT_SUPPORT", 6.0
    return False, "MACRO_NEUTRAL", 0.0


def _reversal_location_score(row: pd.Series, direction: str, smc: Dict[str, Any], liq: Dict[str, Any], sqz: Dict[str, Any], entry_meta: Dict[str, Any], exec_ctx: Dict[str, Any]) -> Tuple[float, List[str]]:
    direction = str(direction).title()
    reasons: List[str] = []
    add = 0.0
    mitigation_src = str(entry_meta.get("mitigation_src", "NO_FVG_OB")).upper()
    zone_near = _safe_float(smc.get("zone_near_atr"), 9.99)
    adx = _safe_float(row.get("adx"), 0.0)
    vol_ratio = _safe_float(row.get("volume_ratio"), 1.0)
    body_pct = _safe_float(row.get("body_pct"), 0.0)

    if "OB" in mitigation_src and zone_near <= 0.75:
        add += 8.0; reasons.append("OB_NEAR")
    if "FVG" in mitigation_src and zone_near <= 0.75:
        add += 5.0; reasons.append("FVG_NEAR")
    if liq.get("liquidity_sweep_confirmed"):
        add += 8.0; reasons.append("LIQUIDITY_SWEEP")
    if sqz.get("sqzmom_divergence_strength", 0.0) >= 8.0:
        add += 5.0; reasons.append("STRONG_DIVERGENCE")
    if _safe_bool(row.get("volume_contracting", False)) or vol_ratio <= 0.82:
        add += 4.0; reasons.append("VOLUME_CONTRACTING")
    if body_pct >= 0.42:
        add += 3.0; reasons.append("CONFIRM_BODY")
    if direction == "Long" and _safe_bool(row.get("fe_bottom", False)):
        add += 12.0; reasons.append("FE_BOTTOM_WVF")
    if direction == "Short" and _safe_bool(row.get("fe_top", False)):
        add += 12.0; reasons.append("FE_TOP_WVF")

    repeat_count = _safe_float(sqz.get("same_side_div_count_12"), 0.0)
    trend_dir = str(exec_ctx.get("trend_direction", "None"))
    if adx >= 42.0 and trend_dir not in ("None", direction) and repeat_count >= 2:
        add -= 18.0; reasons.append("STRONG_TREND_REPEAT_DIVERGENCE_AGAINST")
    elif adx >= 48.0 and repeat_count >= 3:
        add -= 10.0; reasons.append("REPEAT_DIVERGENCE_EXHAUSTION_RISK")
    if adx > 62.0:
        add -= 10.0; reasons.append("ADX_EXHAUST")
    return add, reasons


def institutional_alpha_score(row, direction, exec_ctx, macro_ctx, entry_meta, long_score, short_score):
    """SMC-Impulse Engine 统一评分（唯一信号源）。 评分流程： 1. 构建 ctx 上下文（包含 row / exec_ctx / macro_ctx / entry_meta 的所有字段） 2. 调用 smc_impulse_score(ctx) 获取融合评分 3. 返回评分结果 + 元数据 设计原则： ✅ SMC = structure probability（结构概率，非 gate） ✅ SQZMOM = momentum pressure（动量压力） ✅ fusion = final score only（融合即最终分数） ❌ 不做 penalty / compression / normalization / clamp ❌ 不做 gate / filter / reject 注意：long_score/short_score 参数保留用于兼容旧接口，但不再使用。 """
    direction = str(direction).title()
    regime = str(exec_ctx.get("regime", "mud"))
    
    # ===== 1. 构建 ctx 上下文 =====
    from strategy.smc_impulse_engine import smc_impulse_score
    
    # 1a. SQZMOM 特征
    sqz = _sqzmom_context_score(row, direction)
    
    # 1b. 流动性特征
    liq = _liquidity_context_score(row, direction)
    
    # 1c. 宏观冲突
    macro_block, macro_reason, _macro_score_val = _macro_conflict(direction, macro_ctx)
    
    # 1d. 合并所有字段到 ctx
    ctx = dict(entry_meta)
    ctx.update(exec_ctx)
    ctx.update(macro_ctx)
    ctx.update(sqz)
    ctx.update(liq)
    ctx["direction"] = direction
    ctx["row"] = row
    # 补充 row 中的技术指标
    for key in ["adx", "body_pct", "volume_ratio", "squeeze_released", "squeeze_level",
                 "fe_bottom", "fe_top", "divergence_confirmed", "sqzmom_divergence_age",
                 "sqzmom_divergence_strength", "sqzmom_white_confirm", "sqzmom_momentum_confirm",
                 "sqzmom_dmi_aligned", "sqzmom_trigger_ok", "same_side_div_count_12",
                 "smc_zone_score", "has_valid_zone", "liquidity_sweep_confirmed",
                 "liquidity_wrong_side", "macro_conflict", "too_extended",
                 "htf_direction", "setup_type", "ob_strength", "fvg_quality",
                 "displacement", "liquidity", "vwap_align",
                 "momentum", "sqzmom_divergence_dir",
                 "dmi_bull", "dmi_bear",
                 "sqzmom_reversal_confirm_long", "sqzmom_reversal_confirm_short"]:
        if key in row.index:
            ctx[key] = row.get(key)
    # 补充缺失字段
    if "htf_direction" not in ctx:
        ctx["htf_direction"] = macro_ctx.get("allowed_direction", "")
    if "setup_type" not in ctx:
        ctx["setup_type"] = "ob" if exec_ctx.get("ob_valid") else ("fvg" if exec_ctx.get("bearish_fvg") or exec_ctx.get("bullish_fvg") else "")
    if "ob_strength" not in ctx:
        ctx["ob_strength"] = float(exec_ctx.get("pivot_strength_high", 0) or 0)
    if "fvg_quality" not in ctx:
        ctx["fvg_quality"] = 1.0 if (exec_ctx.get("bearish_fvg") or exec_ctx.get("bullish_fvg")) else 0.0
    if "displacement" not in ctx:
        ctx["displacement"] = float(exec_ctx.get("pivot_strength_low", 0) or 0)
    if "liquidity" not in ctx:
        ctx["liquidity"] = 1.0 if (exec_ctx.get("is_bsl_swept") or exec_ctx.get("is_ssl_swept")) else 0.0
    if "rr" not in ctx:
        ctx["rr"] = float(entry_meta.get("rr", 1.0))
    if "distance_atr" not in ctx:
        ctx["distance_atr"] = float(entry_meta.get("distance_atr", 0.0))
    
    # ===== 2. 调用 SMC-Impulse Engine 获取融合评分 =====
    result = smc_impulse_score(ctx)
    score = result["final_score"]
    smc_score = result["smc"]
    sqz_score = result["sqzmom"]
    
    # ===== 3. 等级判定 =====
    grade = _grade_from_score(score, regime)
    
    # ===== 4. 元数据 =====
    alpha_trigger_count = int(sqz.get("divergence_confirmed", False)) + int(sqz.get("sqzmom_white_confirm", False)) + int(sqz.get("sqzmom_momentum_confirm", False)) + int(liq.get("liquidity_sweep_confirmed", False)) + int(_safe_bool(row.get("squeeze_released", False)))
    validated_core = bool(sqz.get("divergence_confirmed", False) and sqz.get("sqzmom_momentum_confirm", False) and not liq.get("liquidity_wrong_side", False))
    div_strength = _safe_float(sqz.get("sqzmom_divergence_strength", 0.0), 0.0)
    strong_alpha = bool(score >= 80 and sqz.get("sqzmom_momentum_confirm", False) and (ctx.get("has_valid_zone", False) or liq.get("liquidity_sweep_confirmed", False) or div_strength >= 8.0))
    
    out = {
        "score": round(float(score), 4),
        "raw_score_uncapped": round(float(score), 4),
        "score_cap": 100.0,
        "score_cap_reasons": "SMC_IMPULSE_ENGINE_NO_GATE",
        "grade": grade,
        "validated_core": validated_core,
        "has_alpha_trigger": bool(sqz.get("divergence_confirmed", False)),
        "strong_alpha_trigger": strong_alpha,
        "alpha_trigger_count": alpha_trigger_count,
        # SMC-Impulse Engine 明细
        "smc_score": round(float(smc_score), 4),
        "sqzmom_score": round(float(sqz_score), 4),
        "smc_weight": result["weights"]["smc"],
        "sqzmom_weight": result["weights"]["sqzmom"],
        "breakdown": result["breakdown"],
        "smc_passed": result.get("smc_passed", True),
        "sqz_passed": result.get("sqz_passed", True),
        "execution_adjustment": 0.0,
        "risk_penalty_score": 0.0,
        "risk_penalty_reasons": "",
        "macro_conflict": bool(macro_block),
        "macro_reason": macro_reason,
        "unified_base_grade": grade,
    }
    out.update(liq)
    out.update(sqz)
    out["squeeze_level"] = int(_safe_float(row.get("squeeze_level", 0), 0))
    out["hf_confirmed"] = False
    return out


def _choch_reset_event(row: pd.Series, direction: str) -> Tuple[bool, str]:
    """极端拒绝 / WVF 极值破冰：防止吸收过滤过度锁死历史大底/大顶。"""
    direction = str(direction).title()
    if direction == "Long":
        if _safe_bool(row.get("strong_reject_long", False)):
            return True, "CHOCH_RESET_LONG_WICK_REJECTION"
        if _safe_bool(row.get("effort_no_result_long", False)):
            return True, "CHOCH_RESET_LONG_ENR"
        if _safe_bool(row.get("fe_bottom", False)):
            return True, "CHOCH_RESET_WVF_BOTTOM"
    else:
        if _safe_bool(row.get("strong_reject_short", False)):
            return True, "CHOCH_RESET_SHORT_WICK_REJECTION"
        if _safe_bool(row.get("effort_no_result_short", False)):
            return True, "CHOCH_RESET_SHORT_ENR"
        if _safe_bool(row.get("fe_top", False)):
            return True, "CHOCH_RESET_WVF_TOP"
    return False, "NO_CHOCH_RESET"


def _volume_absorption_firewall(row: pd.Series, direction: str, exec_ctx: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    """成交量吸收防火墙：强趋势中连续逆向背离 + 放量，默认判定为洗盘/换手并锁死。"""
    direction = str(direction).title()
    regime = str(exec_ctx.get("regime", "mud"))
    trend_dir = str(exec_ctx.get("trend_direction", "None"))
    adx = _safe_float(row.get("adx"), 0.0)
    vol_ratio = _safe_float(row.get("volume_ratio"), 1.0)
    same_div_count = _safe_float(row.get("bull_div_count_12" if direction == "Long" else "bear_div_count_12"), 0.0)
    strong_trend_against = regime == "trend" and trend_dir not in ("None", direction) and adx >= 38.0
    absorption_risk = bool(strong_trend_against and same_div_count >= 2 and vol_ratio >= 1.8)
    reset_ok, reset_reason = _choch_reset_event(row, direction)
    meta = {
        "absorption_risk": absorption_risk,
        "absorption_vol_ratio": round(float(vol_ratio), 4),
        "absorption_div_count_12": round(float(same_div_count), 4),
        "choch_reset": bool(reset_ok),
        "choch_reset_reason": reset_reason,
    }
    if absorption_risk and not reset_ok:
        return False, "REJECT_VOLUME_ABSORPTION_FIREWALL", meta
    if absorption_risk and reset_ok:
        return True, "ALLOW_CHOCH_RESET_BREAK_ICE", meta
    return True, "NO_ABSORPTION_RISK", meta

# ==========================================
# Module 3: filters and entry quality
# ==========================================
def _mtf_vwap_filter(direction: str, row: pd.Series, macro_ctx: Dict[str, Any]) -> Tuple[bool, str]:
    if not macro_ctx:
        return True, "NO_MACRO"

    close = _safe_float(row.get("close"), 0.0)
    macro_vwap = _safe_float(macro_ctx.get("macro_vwap"), 0.0)
    macro_close = _safe_float(macro_ctx.get("macro_close"), 0.0)
    macro_slope = _safe_float(macro_ctx.get("macro_slope"), 0.0)

    if macro_vwap <= 0 or macro_close <= 0:
        return True, "NO_MACRO_VWAP"

    direction = str(direction).title()
    if direction == "Long" and macro_close < macro_vwap and macro_slope < 0 and close < macro_vwap:
        return False, "REJECT_MTF_VWAP_BEAR_PRESSURE"
    if direction == "Short" and macro_close > macro_vwap and macro_slope > 0 and close > macro_vwap:
        return False, "REJECT_MTF_VWAP_BULL_PRESSURE"
    return True, "MTF_VWAP_OK"


def _entry_quality_scoring(row: pd.Series, direction: str, long_score: float, short_score: float, long_threshold: float, short_threshold: float, macro_ctx: Dict[str, Any], exec_ctx: Dict[str, Any], mitigation_required: bool = True, final_score_100: float = 0.0, allow_trend_no_structure: bool = False) -> Dict[str, Any]:
    """加权评分版：不再 return False，而是返回结构质量扣分和元数据。 所有过滤条件都变成分数调整值，叠加到 institutional_alpha_score 中。"""
    direction = str(direction).title()
    price = _safe_float(row.get("close"), 0.0)
    atr = _safe_float(row.get("ATRr_14", exec_ctx.get("atr", 0.0)), 0.0)
    vwap = _safe_float(row.get("vwap_48", row.get("VWAP", row.get("vwap", price))), price)
    adx = _safe_float(row.get("adx"), 0.0)
    regime = str(exec_ctx.get("regime", "mud"))
    trend_dir = str(exec_ctx.get("trend_direction", "None"))
    volume_ratio = _safe_float(row.get("volume_ratio"), 1.0)
    body_pct = _safe_float(row.get("body_pct"), 0.0)

    structure_penalty = 0.0
    penalty_reasons = []

    # 基础数据异常（唯一保留的硬性拒绝）
    if price <= 0 or atr <= 0:
        return {"ok": False, "reason": "NO_PRICE_OR_ATR", "structure_penalty": 999.0, "meta": {}}

    vwap_dist = abs(price - vwap) / max(atr, 1e-12)

    # ADX 极端衰竭 → 扣 15 分
    if adx > 72.0:
        structure_penalty += 15.0
        penalty_reasons.append(f"ADX_EXTREME_-15")

    entry_grade = "S"
    size_mult = 1.0
    too_extended = False

    trend_aligned = bool(regime == "trend" and trend_dir == direction)
    vwap_extension_limit = 3.0 if trend_aligned else 1.75

    # 价格远离 VWAP → 按距离扣分
    if direction == "Long":
        if price > vwap + vwap_extension_limit * atr:
            too_extended = True
            dist_over = (price - (vwap + vwap_extension_limit * atr)) / atr
            vwap_penalty = min(10.0, dist_over * 3.0)
            structure_penalty += vwap_penalty
            penalty_reasons.append(f"VWAP_EXTENDED_LONG_-{vwap_penalty:.0f}")
    else:
        if price < vwap - vwap_extension_limit * atr:
            too_extended = True
            dist_over = ((vwap - vwap_extension_limit * atr) - price) / atr
            vwap_penalty = min(10.0, dist_over * 3.0)
            structure_penalty += vwap_penalty
            penalty_reasons.append(f"VWAP_EXTENDED_SHORT_-{vwap_penalty:.0f}")

    # Stop Hunt 方向冲突 → 扣 12 分
    stop_hunt_conflict = False
    if direction == "Long" and _safe_bool(row.get("bearish_stop_hunt", False)) and not (regime == "trend" and trend_dir == "Long"):
        structure_penalty += 12.0
        penalty_reasons.append("STOP_HUNT_CONFLICT_LONG_-12")
        stop_hunt_conflict = True
    if direction == "Short" and _safe_bool(row.get("bullish_stop_hunt", False)) and not (regime == "trend" and trend_dir == "Short"):
        structure_penalty += 12.0
        penalty_reasons.append("STOP_HUNT_CONFLICT_SHORT_-12")
        stop_hunt_conflict = True

    # MTF VWAP 方向冲突 → 扣 10 分
    mtf_res = _mtf_vwap_filter(direction, row, macro_ctx)
    mtf_conflict = isinstance(mtf_res, tuple) and len(mtf_res) > 0 and not mtf_res[0]
    if mtf_conflict:
        structure_penalty += 10.0
        penalty_reasons.append(f"MTF_VWAP_CONFLICT_-10")

    # SMC 结构检查
    miti_res = nearest_mitigation_price(row, direction)
    mitigation_price = miti_res[0] if isinstance(miti_res, tuple) and len(miti_res) > 0 else None
    mitigation_src = miti_res[1] if isinstance(miti_res, tuple) and len(miti_res) > 1 else "NO_FVG_OB"
    has_structure = mitigation_price is not None and mitigation_src != "NO_FVG_OB"

    # Premium/Discount 判定
    eq_price = _safe_float(row.get("eq_price"), price)
    is_discount = price < eq_price
    is_premium = price > eq_price

    # OB 位置不对 → 扣 8 分
    ob_position_bad = False
    if mitigation_required and has_structure:
        if direction == "Long" and not is_discount:
            structure_penalty += 8.0
            penalty_reasons.append("OB_NOT_DISCOUNT_-8")
            ob_position_bad = True
        if direction == "Short" and not is_premium:
            structure_penalty += 8.0
            penalty_reasons.append("OB_NOT_PREMIUM_-8")
            ob_position_bad = True

    # 无 SMC 结构 → 扣 6 分
    if not has_structure:
        structure_penalty += 6.0
        penalty_reasons.append("NO_SMC_STRUCTURE_-6")
        entry_grade = "B" if regime == "transition" else "C"
        size_mult = 0.10 if regime == "transition" else 0.15 if regime == "mud" else 0.20

    # 有结构但价格远离结构 → 降级
    if has_structure:
        if direction == "Long" and price > float(mitigation_price) + 0.75 * atr:
            entry_grade = "B"
            size_mult = min(size_mult, 0.65)
        if direction == "Short" and price < float(mitigation_price) - 0.75 * atr:
            entry_grade = "B"
            size_mult = min(size_mult, 0.65)

    # 市场状态仓位限制
    if regime == "transition":
        size_mult = min(size_mult, 0.45)
    elif regime == "mud":
        size_mult = min(size_mult, 0.20)

    # 结构扣分上限 30 分
    structure_penalty = min(30.0, max(0.0, structure_penalty))

    return {
        "ok": True,
        "reason": "ENTRY_OK",
        "structure_penalty": structure_penalty,
        "penalty_reasons": ";".join(penalty_reasons),
        "meta": {
            "vwap_dist_atr": round(vwap_dist, 4),
            "too_extended": bool(too_extended),
            "mitigation_price": mitigation_price,
            "mitigation_src": mitigation_src,
            "entry_grade": entry_grade,
            "size_mult": size_mult,
            "vwap_extension_limit": vwap_extension_limit,
            "trend_aligned": bool(trend_aligned),
            "stop_hunt_conflict": stop_hunt_conflict,
            "mtf_conflict": mtf_conflict,
            "ob_position_bad": ob_position_bad,
            "has_structure": has_structure,
        }
    }
def _entry_quality_filter( row: pd.Series, direction: str, long_score: float, short_score: float, long_threshold: float, short_threshold: float, macro_ctx: Dict[str, Any], exec_ctx: Dict[str, Any], mitigation_required: bool = True, final_score_100: float = 0.0, allow_trend_no_structure: bool = False, ) -> Tuple[bool, str, Dict[str, Any]]:
    """兼容旧接口：调用 _entry_quality_scoring 并转换为旧格式。"""
    res = _entry_quality_scoring(row, direction, long_score, short_score, long_threshold, short_threshold, macro_ctx, exec_ctx, mitigation_required, final_score_100, allow_trend_no_structure)
    if not res.get("ok", False):
        return False, res.get("reason", "UNKNOWN"), {}
    return True, res.get("reason", "ENTRY_OK"), res.get("meta", {})


def _squeeze_false_breakout_filter(df: pd.DataFrame, i: int, direction: str) -> Tuple[bool, str]:
    if i < 1 or "squeeze_released" not in df.columns or not _safe_bool(df.iloc[i].get("squeeze_released", False)):
        return True, "NO_SQUEEZE_RELEASE"

    prev_high = _safe_float(df.iloc[i - 1].get("high"), 0.0)
    prev_low = _safe_float(df.iloc[i - 1].get("low"), 0.0)
    close = _safe_float(df.iloc[i].get("close"), 0.0)
    atr = _safe_float(df.iloc[i].get("ATRr_14"), close * 0.006)

    if str(direction).title() == "Long" and close <= prev_high - 0.03 * atr:
        return False, "FALSE_BREAKOUT_LONG"
    if str(direction).title() == "Short" and close >= prev_low + 0.03 * atr:
        return False, "FALSE_BREAKOUT_SHORT"
    return True, "SQUEEZE_BREAK_CONFIRMED"


def _adaptive_max_hold_bars(exec_ctx: Dict[str, Any], base: int = 96) -> int:
    hold = int(base)
    if exec_ctx.get("regime") == "trend":
        hold = int(hold * 1.35)
    elif exec_ctx.get("regime") == "mud":
        hold = int(hold * 0.55)
    return max(10, min(hold, 260))


def _min_score_for_regime(regime: str) -> float:
    """加权评分版：大幅降低门槛，让分数说话而不是硬性拒绝。 注意：institutional_alpha_score 的 final_score 实际分布在 6~21 分， 所以门槛必须匹配这个范围，否则所有信号都会被拦截。 """
    regime = str(regime)
    if regime == "trend":
        return 18.0
    if regime == "transition":
        return 20.0
    return 22.0


def _adaptive_min_score_for_entry(row: pd.Series, direction: str, exec_ctx: Dict[str, Any], entry_meta: Dict[str, Any]) -> float:
    regime = str(exec_ctx.get("regime", "mud"))
    trend_dir = str(exec_ctx.get("trend_direction", "None"))
    base = _min_score_for_regime(regime)
    if bool(entry_meta.get("early_reversal_pool", False)):
        return 25.0 if regime == "transition" else 28.0 if regime == "trend" else 35.0

    dmi_ok = bool(entry_meta.get("sqzmom_dmi_aligned", False))
    mom_ok = bool(entry_meta.get("sqzmom_momentum_confirm", False))
    smc_ok = bool(entry_meta.get("has_valid_zone", False)) or _safe_float(entry_meta.get("smc_zone_score"), 0.0) >= 38.0
    trend_aligned = bool(regime == "trend" and trend_dir == str(direction).title())

    if trend_aligned and dmi_ok and mom_ok:
        base -= 6.0
    if smc_ok and mom_ok and _safe_float(entry_meta.get("sqzmom_score"), 0.0) >= 30.0:
        base -= 4.0
    if bool(entry_meta.get("liquidity_sweep_confirmed", False)) and smc_ok:
        base -= 3.0
    return max(18.0, float(base))


def _early_reversal_pool_scoring(row: pd.Series, direction: str, exec_ctx: Dict[str, Any], entry_meta: Dict[str, Any], final_score_100: float) -> Tuple[bool, str, float]:
    """加权评分版：不再硬性拒绝，而是根据条件返回仓位乘数。 不满足条件时给极小仓位（0.02~0.05），满足条件时给正常仓位（0.08~0.10）。"""
    regime = str(exec_ctx.get("regime", "mud"))
    trend_dir = str(exec_ctx.get("trend_direction", "None"))
    direction = str(direction).title()

    # 基础仓位
    base_size = 0.05

    # 非 Transition 状态 → 仓位减半
    if regime != "transition":
        return True, "EARLY_POOL_NOT_TRANSITION_TINY", 0.02

    # 宏观冲突 → 极小仓位
    if bool(entry_meta.get("macro_conflict", False)):
        return True, "EARLY_POOL_MACRO_CONFLICT_TINY", 0.02

    age = int(_safe_float(entry_meta.get("sqzmom_divergence_age", 999), 999))
    if age > 18:
        return True, "EARLY_POOL_DIV_AGE_OLD_TINY", 0.03

    mom_ok = bool(entry_meta.get("sqzmom_momentum_confirm", False))
    dmi_ok = bool(entry_meta.get("sqzmom_dmi_aligned", False))
    smc_score = _safe_float(entry_meta.get("smc_zone_score"), 0.0)
    sqz_score = _safe_float(entry_meta.get("sqzmom_score"), 0.0)
    has_zone = bool(entry_meta.get("has_valid_zone", False))
    liq_ok = bool(entry_meta.get("liquidity_sweep_confirmed", False))
    zone_near = _safe_float(entry_meta.get("zone_near_atr"), 9.99)
    vwap_dist = _safe_float(entry_meta.get("vwap_dist_atr"), 9.99)
    adx = _safe_float(row.get("adx"), 0.0)

    # 逐项检查，每不满足一项就降低仓位
    quality_count = 0
    total_checks = 0

    # 1. 动量和DMI
    total_checks += 1
    if mom_ok and dmi_ok:
        quality_count += 1

    # 2. 有效区域
    total_checks += 1
    if has_zone:
        quality_count += 1

    # 3. SMC和SQZ分数
    total_checks += 1
    if smc_score >= 38.0 and sqz_score >= 36.0:
        quality_count += 1

    # 4. 区域接近度
    total_checks += 1
    if zone_near <= (0.85 if not liq_ok else 1.10):
        quality_count += 1

    # 5. VWAP距离
    total_checks += 1
    if vwap_dist <= 1.45:
        quality_count += 1

    # 6. 趋势方向
    total_checks += 1
    if trend_dir in ("None", direction) or adx < 34.0:
        quality_count += 1

    # 7. 分数
    total_checks += 1
    if final_score_100 >= 78.0:
        quality_count += 1

    # 根据通过率计算仓位乘数
    pass_rate = quality_count / max(total_checks, 1)
    if pass_rate >= 0.85:
        size = 0.10 if liq_ok else 0.08
    elif pass_rate >= 0.60:
        size = 0.06
    elif pass_rate >= 0.40:
        size = 0.04
    else:
        size = 0.02

    return True, f"EARLY_POOL_PASS_{quality_count}/{total_checks}_SIZE_{size:.2f}", size
def _early_reversal_pool_allowed(row: pd.Series, direction: str, exec_ctx: Dict[str, Any], entry_meta: Dict[str, Any], final_score_100: float) -> Tuple[bool, str, float]:
    """兼容旧接口：调用 _early_reversal_pool_scoring 并转换为旧格式。"""
    return _early_reversal_pool_scoring(row, direction, exec_ctx, entry_meta, final_score_100)

def _grade_from_score(score: float, regime: Optional[str] = None) -> str:
    """统一等级映射：与 strategy/entry_quality.py 保持一致。 0~100 分制，S≥85, A≥70, B≥55, C≥40, D<40。 runner.py 不再维护独立的等级映射。"""
    score = _safe_float(score, 0.0)
    if score >= 85:
        return "S"
    if score >= 70:
        return "A"
    if score >= 55:
        return "B"
    if score >= 40:
        return "C"
    return "D"


def _regime_position_multiplier(row: pd.Series, direction: str, final_score_100: float, exec_ctx: Dict[str, Any], entry_meta: Dict[str, Any]) -> Tuple[bool, str, float]:
    """加权仓位乘数版：不再 return False，而是根据市场状态和信号质量返回仓位乘数。 低分信号给极小仓位（5%），高分信号给正常仓位（40%），让分数说话。"""
    regime = str(exec_ctx.get("regime", "mud"))
    trend_dir = str(exec_ctx.get("trend_direction", "None"))
    mitigation_src = str(entry_meta.get("mitigation_src", "NO_FVG_OB"))
    base_size = _safe_float(entry_meta.get("size_mult", 1.0), 1.0)
    grade = str(entry_meta.get("alpha_grade") or _grade_from_score(final_score_100, regime))
    sqzmom_trigger_ok = bool(entry_meta.get("sqzmom_trigger_ok", False))
    early_pool = bool(entry_meta.get("early_reversal_pool", False))
    macro_conflict = bool(entry_meta.get("macro_conflict", False))
    vwap_dist_atr = _safe_float(entry_meta.get("vwap_dist_atr"), 9.99)
    same_div_count = _safe_float(entry_meta.get("same_side_div_count_12"), 0.0)
    adx = _safe_float(row.get("adx"), 0.0)
    liq_ok = bool(entry_meta.get("liquidity_sweep_confirmed", False))
    has_zone = bool(entry_meta.get("has_valid_zone", False))
    body_pct = _safe_float(row.get("body_pct"), 0.0)
    mss_proxy = body_pct >= 0.35 or bool(entry_meta.get("sqzmom_white_confirm", False))
    reset_ok, reset_reason = _choch_reset_event(row, direction)

    # 基础仓位乘数（根据分数）
    # 注意：institutional_alpha_score 的 final_score 实际分布在 6~21 分，
    # 所以门槛必须匹配这个范围，否则所有信号都会被拦截。
    if final_score_100 >= 85:
        base_mult = 1.0  # S级：满仓
    elif final_score_100 >= 70:
        base_mult = 0.6  # A级：60%
    elif final_score_100 >= 55:
        base_mult = 0.3  # B级：30%
    elif final_score_100 >= 40:
        base_mult = 0.1  # C级：10%
    elif final_score_100 >= 20:
        base_mult = 0.05  # D级：5% 极小仓位，让分数说话
    else:
        return False, "REJECT_SCORE_TOO_LOW", 0.0  # <20分：不开

    # 市场状态调整
    if regime == "mud":
        base_mult *= 0.3  # Mud 再砍 70%
    elif regime == "transition":
        base_mult *= 0.7  # Transition 砍 30%

    # 逆势调整
    if trend_dir not in ("None", direction):
        if adx >= 42:
            base_mult *= 0.2  # 强逆势：砍 80%
        elif adx >= 28:
            base_mult *= 0.4  # 中逆势：砍 60%
        else:
            base_mult *= 0.7  # 弱逆势：砍 30%

    # 结构质量调整
    if mitigation_src == "NO_FVG_OB":
        base_mult *= 0.5  # 无SMC结构：砍 50%
    if not sqzmom_trigger_ok and not early_pool:
        base_mult *= 0.3  # 无SQZMOM触发：砍 70%
    if macro_conflict:
        base_mult *= 0.2  # 宏观冲突：砍 80%
    if liq_ok and not (has_zone and mss_proxy):
        base_mult *= 0.3  # Sweep无MSS：砍 70%

    # VWAP 距离调整
    vwap_limit = 3.0 if (regime == "trend" and trend_dir == direction) else 1.75
    if vwap_dist_atr > vwap_limit:
        over_dist = (vwap_dist_atr - vwap_limit) / vwap_limit
        base_mult *= max(0.1, 1.0 - over_dist)  # 越远仓位越小

    # 重复背离惩罚
    if adx >= 48.0 and same_div_count >= 3:
        base_mult *= 0.3

    # 最终仓位 = base_size × base_mult，上限 0.40
    final_mult = min(0.40, base_size * base_mult)
    final_mult = max(0.02, final_mult)  # 最低 2%

    return True, f"ALLOW_MULT_{final_mult:.2f}", final_mult


def _allow_trade_by_regime( row: pd.Series, direction: str, final_score_100: float, exec_ctx: Dict[str, Any], entry_meta: Dict[str, Any], ) -> Tuple[bool, str, float]:
    """兼容旧接口：调用 _regime_position_multiplier 并转换为旧格式。"""
    return _regime_position_multiplier(row, direction, final_score_100, exec_ctx, entry_meta)


def _resolve_entry( df: pd.DataFrame, signal_i: int, direction: str, row: pd.Series, entry_meta: Dict[str, Any], exec_ctx: Dict[str, Any], max_wait_bars: int = 12, max_chase_atr: float = 1.0, ) -> Tuple[bool, int, float, str]:
    direction = str(direction).title()
    if signal_i + 1 >= len(df):
        return False, signal_i, 0.0, "NO_NEXT_BAR"

    signal_close = _safe_float(row.get("close"), 0.0)
    atr = _safe_float(row.get("ATRr_14", exec_ctx.get("atr", 0.0)), signal_close * 0.006)
    next_open = _safe_float(df.iloc[signal_i + 1].get("open", signal_close), signal_close)

    if signal_close <= 0 or atr <= 0:
        return False, signal_i, 0.0, "BAD_ENTRY_CONTEXT"

    if direction == "Long":
        chase_too_far = next_open > signal_close + max_chase_atr * atr
        pullback_price = signal_close + 0.15 * atr
        if not chase_too_far:
            return True, signal_i + 1, next_open, "NEXT_OPEN_MARKET"
        for j in range(signal_i + 1, min(len(df), signal_i + 1 + max_wait_bars)):
            low = _safe_float(df.iloc[j].get("low"), np.nan)
            high = _safe_float(df.iloc[j].get("high"), np.nan)
            if low <= pullback_price <= high:
                return True, j, pullback_price, "VERIFIED_PULLBACK_LONG"
        return False, signal_i, 0.0, "SKIP_LONG_CHASE_TOO_FAR"

    chase_too_far = next_open < signal_close - max_chase_atr * atr
    pullback_price = signal_close - 0.15 * atr
    if not chase_too_far:
        return True, signal_i + 1, next_open, "NEXT_OPEN_MARKET"
    for j in range(signal_i + 1, min(len(df), signal_i + 1 + max_wait_bars)):
        low = _safe_float(df.iloc[j].get("low", np.nan))
        high = _safe_float(df.iloc[j].get("high", np.nan))
        if low <= pullback_price <= high:
            return True, j, pullback_price, "VERIFIED_PULLBACK_SHORT"
    return False, signal_i, 0.0, "SKIP_SHORT_CHASE_TOO_FAR"


def _normalize_rr_plan( direction: str, entry: float, sl: float, tp1: float, tp2: float, tp3: float, row: pd.Series, hist: pd.DataFrame, exec_ctx: Dict[str, Any], min_rr: float, ) -> Tuple[float, float, float, float, float]:
    direction = str(direction).title()
    atr = _safe_float(row.get("ATRr_14", exec_ctx.get("atr", entry * 0.006)), entry * 0.006)
    regime = str(exec_ctx.get("regime", "transition"))

    min_risk = max(0.95 * atr, entry * 0.0025)
    max_risk = max(4.0 * atr, min_risk) 

    raw_risk = (entry - sl) if direction == "Long" else (sl - entry)
    if raw_risk <= min_risk or raw_risk > max_risk:
        risk = min(max(abs(raw_risk), min_risk), max_risk)
        sl = entry - risk if direction == "Long" else entry + risk
    else:
        risk = raw_risk

    atr_pct = _safe_float(exec_ctx.get("atr_pct"), atr / entry if entry > 0 else 0.0)
    if atr_pct >= 0.012:
        lookback_bars = 80
    elif atr_pct <= 0.004:
        lookback_bars = 250
    else:
        lookback_bars = 150
    if direction == "Long":
        target_price = hist['high'].tail(lookback_bars).max() if len(hist) > 0 else entry + 2*risk
        target_r = (target_price - entry) / risk
    else:
        target_price = hist['low'].tail(lookback_bars).min() if len(hist) > 0 else entry - 2*risk
        target_r = (entry - target_price) / risk

    if target_r < 1.2:
        target_r = 1.5 
    
    if regime == "trend":
        r1 = 1.25
        r2 = max(1.90, target_r * 0.95)
        r3 = max(2.80, target_r * 1.45)
    elif regime == "transition":
        r1 = 1.20
        r2 = max(1.80, target_r * 1.00)
        r3 = max(2.50, target_r * 1.20)
    else:
        r1, r2, r3 = 1.15, 1.70, 2.30

    if direction == "Long":
        tp1 = entry + r1 * risk
        tp2 = entry + r2 * risk
        tp3 = entry + r3 * risk
    else:
        tp1 = entry - r1 * risk
        tp2 = entry - r2 * risk
        tp3 = entry - r3 * risk

    rr = abs(tp3 - entry) / max(abs(entry - sl), 1e-12)
    return float(sl), float(tp1), float(tp2), float(tp3), float(rr)


# ==========================================
# Module 4: realistic cost + exit engine
# ==========================================

def _round_trip_cost_r(entry_price: float, sl_price: float, fee_bps: float, slippage_bps: float) -> float:
    """Convert round-trip fee+slippage into R based on the actual entry-stop distance. Old logic subtracted bps directly as R, which almost ignored costs on tight stops. Example: 32 bps round-trip cost with a 0.5% stop is about 0.64R, not 0.0032R. """
    entry = abs(_safe_float(entry_price, 0.0))
    risk_abs = abs(_safe_float(entry_price, 0.0) - _safe_float(sl_price, 0.0))
    if entry <= 0.0 or risk_abs <= 1e-12:
        return 0.0
    round_trip_cost_abs = entry * ((_safe_float(fee_bps, 0.0) + _safe_float(slippage_bps, 0.0)) * 2.0 / 10000.0)
    return round_trip_cost_abs / risk_abs

def _build_trade_exit( df: pd.DataFrame, start_i: int, direction: str, entry_price: float, sl: float, tp1: float, tp2: float, tp3: float, max_hold_bars: int = 96, atr_col: str = "ATRr_14", trail_atr_mult: float = 1.35, time_drawdown_bars: int = 12, min_positive_pct: float = 0.0008, tp1_close_pct: float = 0.30, tp2_close_pct: float = 0.30, regime: str = "trend", ) -> Dict[str, Any]:
    direction = str(direction or "").title()
    entry = float(entry_price)
    stop = float(sl)
    raw_initial_risk = entry - stop if direction == "Long" else stop - entry
    current_atr = _safe_float(df.iloc[start_i].get(atr_col), entry * 0.006)
    initial_risk = max(raw_initial_risk, current_atr * 0.2)

    if initial_risk <= 0:
        return {"exit_i": start_i, "exit_time": df.iloc[start_i].get("datetime", start_i), "exit": entry, "exit_reason": "INVALID_RISK", "partial_pnl": 0.0, "remaining": 1.0, "mfe_r": 0.0}

    max_i = min(len(df) - 1, start_i + int(max_hold_bars))
    remaining = 1.0
    realized_r = 0.0
    tp1_done = False
    tp2_done = False
    tp3_active = False
    best_price = entry
    max_favorable_pct = 0.0
    max_favorable_r = 0.0
    last_action = "OPEN"
    last_action_price = entry
    tp1_stop_atr = 0.08 if str(regime) == "trend" else 0.05

    # V30_REALISTIC_BACKTEST_FIX_20260610:
    # If the entry is NEXT_OPEN_MARKET, the entry bar's high/low can hit SL/TP.
    # Simulate from start_i itself; SL is checked before TP as a conservative same-bar path assumption.
    for j in range(start_i, max_i + 1):
        row = df.iloc[j]
        high = _safe_float(row.get("high"))
        low = _safe_float(row.get("low"))
        close = _safe_float(row.get("close"))
        atr = _safe_float(row.get(atr_col), abs(entry - stop) or entry * 0.006)
        dt = row.get("datetime", j)

        if (j - start_i) >= time_drawdown_bars and not tp1_done:
            if direction == "Long":
                fav_r_now = max(0.0, (max(best_price, high) - entry) / initial_risk)
                if fav_r_now < 0.22 and close <= entry - 0.14 * initial_risk:
                    realized_r += remaining * ((close - entry) / initial_risk)
                    return {"exit_i": j, "exit_time": dt, "exit": close, "exit_reason": "V28_SLOW_INVALIDATION", "partial_pnl": round(realized_r, 4), "remaining": 0.0, "mfe_r": round(max_favorable_r, 4)}
            else:
                fav_r_now = max(0.0, (entry - min(best_price, low)) / initial_risk)
                if fav_r_now < 0.22 and close >= entry + 0.14 * initial_risk:
                    realized_r += remaining * ((entry - close) / initial_risk)
                    return {"exit_i": j, "exit_time": dt, "exit": close, "exit_reason": "V28_SLOW_INVALIDATION", "partial_pnl": round(realized_r, 4), "remaining": 0.0, "mfe_r": round(max_favorable_r, 4)}

        if direction == "Long":
            if low <= stop:
                realized_r += remaining * ((stop - entry) / initial_risk)
                final_reason = "TRAIL_SL" if tp3_active else ("LOCK_PROFIT_TP2" if tp2_done else ("SAFE_SL_TP1" if tp1_done else "SL"))
                return {"exit_i": j, "exit_time": dt, "exit": stop, "exit_reason": final_reason, "partial_pnl": round(realized_r, 4), "remaining": 0.0, "mfe_r": round(max_favorable_r, 4)}

            best_price = max(best_price, high)
            max_favorable_pct = max(max_favorable_pct, (best_price - entry) / entry)
            max_favorable_r = max(max_favorable_r, (best_price - entry) / initial_risk)

            if (not tp1_done) and high >= tp1:
                # V54: TP1 必须真实落袋。V37.6 只把 TP1 当状态机，
                # 大量 +1R 以上机会最后被拖成微亏/微平，压低胜率与 PF。
                close_pct = min(tp1_close_pct, remaining)
                tp1_done = True
                realized_r += close_pct * ((tp1 - entry) / initial_risk)
                remaining = round(remaining - close_pct, 10)
                stop = max(stop, entry + current_atr * 0.05)
                last_action = "TP1_REALIZED_BREAKEVEN"
                last_action_price = tp1
                if remaining <= 0.0:
                    return {"exit_i": j, "exit_time": dt, "exit": tp1, "exit_reason": "TP1_FULL_EXIT", "partial_pnl": round(realized_r, 4), "remaining": 0.0, "mfe_r": round(max_favorable_r, 4)}

            if tp1_done and (not tp2_done) and high >= tp2 and remaining > 0.0:
                close_pct = min(tp2_close_pct, remaining)
                tp2_done = True
                realized_r += close_pct * ((tp2 - entry) / initial_risk)
                remaining = round(remaining - close_pct, 10)
                stop = max(stop, entry + 0.12 * atr)
                last_action = "TP2_LOCK_PROFIT"
                last_action_price = tp2
                continue

            if tp2_done and high >= tp3:
                tp3_active = True
                last_action = "TP3_TRAILING_ACTIVE"
                last_action_price = close

            # 删除时间退出
            if False:
                pass

            if tp3_active:
                stop = max(stop, best_price - trail_atr_mult * atr)

        else:
            if high >= stop:
                realized_r += remaining * ((entry - stop) / initial_risk)
                final_reason = "TRAIL_SL" if tp3_active else ("LOCK_PROFIT_TP2" if tp2_done else ("SAFE_SL_TP1" if tp1_done else "SL"))
                return {"exit_i": j, "exit_time": dt, "exit": stop, "exit_reason": final_reason, "partial_pnl": round(realized_r, 4), "remaining": 0.0, "mfe_r": round(max_favorable_r, 4)}

            best_price = min(best_price, low)
            max_favorable_pct = max(max_favorable_pct, (entry - best_price) / entry)
            max_favorable_r = max(max_favorable_r, (entry - best_price) / initial_risk)

            if (not tp1_done) and low <= tp1:
                # V55: Short TP1 必须由真实 low 触达，不再用 MFE proxy。
                close_pct = min(tp1_close_pct, remaining)
                tp1_done = True
                realized_r += close_pct * ((entry - tp1) / initial_risk)
                remaining = round(remaining - close_pct, 10)
                stop = min(stop, entry - current_atr * 0.05)
                last_action = "TP1_REALIZED_BREAKEVEN"
                last_action_price = tp1
                if remaining <= 0.0:
                    return {"exit_i": j, "exit_time": dt, "exit": tp1, "exit_reason": "TP1_FULL_EXIT", "partial_pnl": round(realized_r, 4), "remaining": 0.0, "mfe_r": round(max_favorable_r, 4)}

            if tp1_done and (not tp2_done) and low <= tp2 and remaining > 0.0:
                close_pct = min(tp2_close_pct, remaining)
                tp2_done = True
                realized_r += close_pct * ((entry - tp2) / initial_risk)
                remaining = round(remaining - close_pct, 10)
                stop = min(stop, entry - 0.12 * atr)
                last_action = "TP2_LOCK_PROFIT"
                last_action_price = tp2
                continue

            if tp2_done and low <= tp3:
                tp3_active = True
                last_action = "TP3_TRAILING_ACTIVE"
                last_action_price = close

            # 删除时间退出
            if False:
                pass

            if tp3_active:
                stop = min(stop, best_price + trail_atr_mult * atr)

    close = _safe_float(df.iloc[max_i].get("close"), entry)
    realized_r += remaining * (((close - entry) / initial_risk) if direction == "Long" else ((entry - close) / initial_risk))
    return {
        "exit_i": max_i,
        "exit_time": df.iloc[max_i].get("datetime", max_i),
        "exit": close,
        "exit_reason": "MAX_HOLD_" + last_action,
        "partial_pnl": round(realized_r, 4),
        "remaining": 0.0,
        "mfe_r": round(max_favorable_r, 4),
        "last_action_price": last_action_price,
    }


# ==========================================
# Module 5: main backtest
# ==========================================
def run_backtest( exec_csv: Any, macro_csv: Optional[Any] = None, symbol: str = "BTC/USDT", warmup: int = 120, max_rows: Optional[int] = None, min_rr: float = 1.35, base_max_hold_bars: int = 96, mitigation_required: bool = True, fee_bps: float = 6.0, slippage_bps: float = 10.0, allow_trend_no_structure: bool = False, save_reject_audit: bool = True, reject_audit_path: str = "reject_audit_v30.csv", allow_b_grade_tiny: bool = True, allow_transition_s: bool = False, **kwargs: Any, ) -> pd.DataFrame:
    print(f"\n🧬 Runner Version: {VERSION} | V31机构评分版：核心质量×环境乘数×执行乘数 / 禁用DIV_BREAKOUT / Transition降权")

    raw_exec = exec_csv.copy() if isinstance(exec_csv, pd.DataFrame) else load_ohlcv_csv(exec_csv)
    if max_rows and int(max_rows) > 0 and int(max_rows) < len(raw_exec):
        raw_exec = raw_exec.tail(int(max_rows) + int(warmup) + 220).reset_index(drop=True)
    df_exec = add_basic_indicators(raw_exec)

    try:
        setup_signals = signals_engine.get_setup_signals(df_exec)
        for col in ["reversal_long", "reversal_short", "breakout_long", "breakout_short", "combo_long", "combo_short"]:
            df_exec[col] = setup_signals.get(col, False).astype(bool)
        df_exec["breakout_vol_z"] = setup_signals.get("breakout_vol_z", 0.0)
        df_exec["breakout_atr_ratio"] = setup_signals.get("breakout_atr_ratio", 0.0)
        print(
            "🧪 Multi-Engine Signals | "
            f"REV_LONG={int(df_exec['reversal_long'].sum())} "
            f"REV_SHORT={int(df_exec['reversal_short'].sum())} "
            f"BRK_LONG={int(df_exec['breakout_long'].sum())} "
            f"BRK_SHORT={int(df_exec['breakout_short'].sum())} "
            f"COMBO={int((df_exec['combo_long'] | df_exec['combo_short']).sum())}"
        )
    except Exception as exc:
        print(f"⚠️ Multi-Engine 信号预计算失败，已自动禁用: {exc}")
        for col in ["reversal_long", "reversal_short", "breakout_long", "breakout_short", "combo_long", "combo_short"]:
            df_exec[col] = False
        df_exec["breakout_vol_z"] = 0.0
        df_exec["breakout_atr_ratio"] = 0.0

    if macro_csv is not None:
        raw_macro = macro_csv.copy() if isinstance(macro_csv, pd.DataFrame) else load_ohlcv_csv(macro_csv)
        if max_rows and int(max_rows) > 0 and int(max_rows) < len(raw_macro):
            raw_macro = raw_macro.tail(max(260, int(max_rows) // 4 + int(warmup))).reset_index(drop=True)
        df_macro = add_basic_indicators(raw_macro)
    else:
        df_macro = pd.DataFrame()

    print("\n=======================================================")
    print(f"⚠️ 实际参与回测的有效 K 线数量：{len(df_exec)} 根")
    print("=======================================================\n")

    # ── 初始化 DiagnosticTracker ──
    dt = DiagnosticTracker(version_name=VERSION)
    dt.total_klines = len(df_exec)

    trades: List[Dict[str, Any]] = []
    reject_rows: List[Dict[str, Any]] = []
    i = max(50, int(warmup)) # V30 Ensure at least 50 bars for eq_price to populate

    def add_reject(reason: str, bucket: str, row: pd.Series, direction: str, exec_ctx: Dict[str, Any], final_score: float, entry_meta: Optional[Dict[str, Any]] = None) -> None:
        meta = entry_meta or {}
        # 记录到 DiagnosticTracker
        details = {
            "idx": int(row.name) if row.name is not None else None,
            "bucket": bucket,
            "direction": direction,
            "regime": exec_ctx.get("regime"),
            "trend_direction": exec_ctx.get("trend_direction"),
            "grade": meta.get("alpha_grade", _grade_from_score(final_score, exec_ctx.get("regime"))),
            "too_extended": bool(meta.get("too_extended", False)),
            "vwap_dist_atr": meta.get("vwap_dist_atr"),
            "mitigation_src": meta.get("mitigation_src"),
            "validated_core": meta.get("validated_core"),
            "has_alpha_trigger": meta.get("has_alpha_trigger"),
            "strong_alpha_trigger": meta.get("strong_alpha_trigger"),
            "alpha_trigger_count": meta.get("alpha_trigger_count"),
            "smc_zone_score": meta.get("smc_zone_score"),
            "liquidity_score": meta.get("liquidity_score"),
            "sqzmom_score": meta.get("sqzmom_score"),
            "sqzmom_trigger_ok": meta.get("sqzmom_trigger_ok"),
            "sqzmom_white_confirm": meta.get("sqzmom_white_confirm"),
            "sqzmom_divergence_age": meta.get("sqzmom_divergence_age"),
            "same_side_div_count_12": meta.get("same_side_div_count_12"),
            "macro_conflict": meta.get("macro_conflict"),
            "macro_reason": meta.get("macro_reason"),
            "location_bonus": meta.get("location_bonus", 0.0),
            "location_reasons": meta.get("location_reasons", "NOT_USED_BY_SMC_IMPULSE"),
            "raw_score_uncapped": meta.get("raw_score_uncapped"),
            "score_cap": meta.get("score_cap"),
            "score_cap_reasons": meta.get("score_cap_reasons"),
            "close": row.get("close"),
            "adx": row.get("adx"),
            "volume_ratio": row.get("volume_ratio"),
            "body_pct": row.get("body_pct"),
            "absorption_risk": meta.get("absorption_risk"),
            "choch_reset": meta.get("choch_reset"),
            "choch_reset_reason": meta.get("choch_reset_reason"),
        }
        dt.log_reject_bulk(
            timestamp=row.get("datetime"),
            reason=reason,
            bucket=bucket,
            row=row,
            direction=direction,
            exec_ctx=exec_ctx,
            score=final_score,
            details=details,
        )
        # 同时保留旧版 reject_rows 用于兼容（如果外部代码依赖它）
        if save_reject_audit:
            reject_rows.append({
                "idx": int(row.name) if row.name is not None else None,
                "datetime": row.get("datetime"),
                "reason": reason,
                "bucket": bucket,
                "direction": direction,
                "regime": exec_ctx.get("regime"),
                "trend_direction": exec_ctx.get("trend_direction"),
                "score": round(float(final_score), 4),
                "grade": meta.get("alpha_grade", _grade_from_score(final_score, exec_ctx.get("regime"))),
                "too_extended": bool(meta.get("too_extended", False)),
                "vwap_dist_atr": meta.get("vwap_dist_atr"),
                "mitigation_src": meta.get("mitigation_src"),
                "validated_core": meta.get("validated_core"),
                "has_alpha_trigger": meta.get("has_alpha_trigger"),
                "strong_alpha_trigger": meta.get("strong_alpha_trigger"),
                "alpha_trigger_count": meta.get("alpha_trigger_count"),
                "smc_zone_score": meta.get("smc_zone_score"),
                "liquidity_score": meta.get("liquidity_score"),
                "sqzmom_score": meta.get("sqzmom_score"),
                "sqzmom_trigger_ok": meta.get("sqzmom_trigger_ok"),
                "sqzmom_white_confirm": meta.get("sqzmom_white_confirm"),
                "sqzmom_divergence_age": meta.get("sqzmom_divergence_age"),
                "same_side_div_count_12": meta.get("same_side_div_count_12"),
                "macro_conflict": meta.get("macro_conflict"),
                "macro_reason": meta.get("macro_reason"),
                "location_bonus": meta.get("location_bonus", 0.0),
                "location_reasons": meta.get("location_reasons", "NOT_USED_BY_SMC_IMPULSE"),
                "raw_score_uncapped": meta.get("raw_score_uncapped"),
                "score_cap": meta.get("score_cap"),
                "score_cap_reasons": meta.get("score_cap_reasons"),
                "close": row.get("close"),
                "adx": row.get("adx"),
                "volume_ratio": row.get("volume_ratio"),
                "body_pct": row.get("body_pct"),
                "absorption_risk": meta.get("absorption_risk"),
                "choch_reset": meta.get("choch_reset"),
                "choch_reset_reason": meta.get("choch_reset_reason"),
            })

    while i < len(df_exec) - 2:
        row = df_exec.iloc[i]
        signal_ts = row.get("datetime", i)
        exec_ctx = build_exec_context(row)
        macro_ctx = build_macro_context(df_macro, signal_ts)
        hist = df_exec.iloc[: i + 1]

        ls_res = fallback_signal_score(row, "Long")
        long_score = ls_res[0] if isinstance(ls_res, tuple) else 0.0
        long_th = ls_res[1] if isinstance(ls_res, tuple) else 4.0

        ss_res = fallback_signal_score(row, "Short")
        short_score = ss_res[0] if isinstance(ss_res, tuple) else 0.0
        short_th = ss_res[1] if isinstance(ss_res, tuple) else 4.0

        div_dir = str(row.get("sqzmom_divergence_dir", "None"))
        if div_dir in ("Long", "Short"):
            direction = div_dir
        else:
            # 综合判断方向：结合 fallback_score + momentum + DMI + 价格位置
            mom = _safe_float(row.get("momentum"), 0.0)
            plus_di = _safe_float(row.get("plus_di"), 0.0)
            minus_di = _safe_float(row.get("minus_di"), 0.0)
            close = _safe_float(row.get("close"), 0.0)
            ema20 = _safe_float(row.get("ema_20"), 0.0)
            ema50 = _safe_float(row.get("ema_50"), 0.0)
            
            # 多头加分项
            bull_extra = 0.0
            if mom > 0: bull_extra += 1.5
            if plus_di > minus_di: bull_extra += 1.0
            if close > ema20: bull_extra += 1.0
            if close > ema50: bull_extra += 0.5
            
            # 空头加分项
            bear_extra = 0.0
            if mom < 0: bear_extra += 1.5
            if minus_di > plus_di: bear_extra += 1.0
            if close < ema20: bear_extra += 1.0
            if close < ema50: bear_extra += 0.5
            
            direction = "Long" if (long_score + bull_extra) >= (short_score + bear_extra) else "Short"

        regime = str(exec_ctx.get("regime", "mud"))
        trend_dir = str(exec_ctx.get("trend_direction", "None"))

        reversal_direction = "Long" if _safe_bool(row.get("reversal_long", False)) else ("Short" if _safe_bool(row.get("reversal_short", False)) else "None")
        div_breakout_direction = "Long" if _safe_bool(row.get("breakout_long", False)) else ("Short" if _safe_bool(row.get("breakout_short", False)) else "None")
        # V31: Breakout/DIV_BREAKOUT path disabled because the module had negative contribution.
        div_breakout_direction = "None"
        breakout_same_as_reversal = bool(
            (div_breakout_direction == "Long" and _safe_bool(row.get("reversal_long", False)))
            or (div_breakout_direction == "Short" and _safe_bool(row.get("reversal_short", False)))
        )

        if div_breakout_direction in ("Long", "Short"):
            br_vol_z = _safe_float(row.get("breakout_vol_z"), 0.0)
            br_atr_ratio = _safe_float(row.get("breakout_atr_ratio"), 0.0)
            br_mom_ok = bool(_sqzmom_trigger_state(row, div_breakout_direction).get("sqzmom_momentum_confirm", False))
            br_dmi_ok = bool(_sqzmom_trigger_state(row, div_breakout_direction).get("sqzmom_dmi_aligned", False))
            br_liq_ok = bool(_liquidity_sweep_context(row, div_breakout_direction).get("liquidity_sweep_confirmed", False))
            if regime != "transition":
                add_reject("REJECT_DIV_BREAKOUT_NOT_TRANSITION_V29_5", "7_Regime权限过滤", row, div_breakout_direction, exec_ctx, 0.0, {"alpha_grade": "DIV_BREAKOUT", "breakout_vol_z": round(br_vol_z, 4), "breakout_atr_ratio": round(br_atr_ratio, 4)})
                i += 1
                continue
            br_body_pct = _safe_float(row.get("body_pct"), 0.0)
            breakout_impulse_ok = bool(
                (br_vol_z >= 1.15 and br_atr_ratio >= 1.00)
                or (br_vol_z >= 0.95 and br_atr_ratio >= 1.08)
            )
            breakout_body_or_liq_ok = bool(br_body_pct >= 0.42 or br_liq_ok)
            if not (br_mom_ok and br_dmi_ok and breakout_impulse_ok and breakout_body_or_liq_ok):
                add_reject("REJECT_DIV_BREAKOUT_NO_STRICT_FUSION_V29_7", "3_结构/VWAP/ADX细节过滤", row, div_breakout_direction, exec_ctx, 0.0, {"alpha_grade": "DIV_BREAKOUT", "breakout_vol_z": round(br_vol_z, 4), "breakout_atr_ratio": round(br_atr_ratio, 4), "body_pct": round(br_body_pct, 4), "liq_ok": br_liq_ok})
                i += 1
                continue
            breakout_meta: Dict[str, Any] = {
                "setup_type": "COMBO_SETUP" if breakout_same_as_reversal else "BREAKOUT_ONLY",
                "alpha_grade": "DIV_BREAKOUT",
                "breakout_vol_z": round(_safe_float(row.get("breakout_vol_z"), 0.0), 4),
                "breakout_atr_ratio": round(_safe_float(row.get("breakout_atr_ratio"), 0.0), 4),
                "size_mult": 0.12,
                "mitigation_src": "DIV_BREAKOUT_FORCED_BAR_EXTREME_SL",
                "vwap_dist_atr": None,
                "too_extended": False,
            }
            entry_ok, entry_i, entry, entry_mode = _resolve_entry(df_exec, i, div_breakout_direction, row, breakout_meta, exec_ctx, max_wait_bars=3, max_chase_atr=0.70)
            if not entry_ok or entry <= 0:
                add_reject(entry_mode, "6_入场追价过远跳过", row, div_breakout_direction, exec_ctx, 0.0, breakout_meta)
                i += 1
                continue

            atr_now = _safe_float(row.get("ATRr_14"), entry * 0.006)
            if div_breakout_direction == "Long":
                sl = min(_safe_float(row.get("low"), entry - atr_now), entry - 0.20 * atr_now)
                risk = max(entry - sl, 1e-12)
                tp1, tp2, tp3 = entry + 0.80 * risk, entry + 1.55 * risk, entry + 2.40 * risk
            else:
                sl = max(_safe_float(row.get("high"), entry + atr_now), entry + 0.20 * atr_now)
                risk = max(sl - entry, 1e-12)
                tp1, tp2, tp3 = entry - 0.80 * risk, entry - 1.55 * risk, entry - 2.40 * risk
            rr = abs(tp3 - entry) / max(abs(entry - sl), 1e-12)

            if not risk_is_acceptable(entry, sl, atr_now, max_risk_atr=3.5):
                add_reject("REJECT_DIV_BREAKOUT_RISK_NOT_ACCEPTABLE", "5_风控过滤", row, div_breakout_direction, exec_ctx, 0.0, breakout_meta)
                i += 1
                continue

            exit_info = _build_trade_exit(
                df_exec, entry_i, div_breakout_direction, entry, sl, tp1, tp2, tp3,
                max_hold_bars=_adaptive_max_hold_bars(exec_ctx, base_max_hold_bars),
                trail_atr_mult=1.10, time_drawdown_bars=5,
                tp1_close_pct=0.30, tp2_close_pct=0.30, regime=regime,
            )
            raw_pnl_r = _safe_float(exit_info.get("partial_pnl"), 0.0)
            cost_r = _round_trip_cost_r(entry, sl, fee_bps, slippage_bps)
            pnl_r = (raw_pnl_r - cost_r) * _safe_float(breakout_meta.get("size_mult", 0.35), 0.35)
            exit_i = int(exit_info.get("exit_i", entry_i))

            trades.append({
                "symbol": symbol,
                "setup_type": "COMBO_SETUP" if breakout_same_as_reversal else "BREAKOUT_ONLY",
                "direction": div_breakout_direction,
                "signal_at": signal_ts,
                "opened_at": df_exec.iloc[entry_i].get("datetime", entry_i),
                "closed_at": exit_info.get("exit_time"),
                "entry_mode": entry_mode,
                "entry": round(entry, 8),
                "sl": round(sl, 8),
                "tp1": round(tp1, 8),
                "tp2": round(tp2, 8),
                "tp3": round(tp3, 8),
                "exit_price": round(_safe_float(exit_info.get("exit")), 8),
                "exit_reason": exit_info.get("exit_reason"),
                "pnl_r": round(pnl_r, 4),
                "raw_pnl_r": round(raw_pnl_r, 4),
                "cost_r": round(cost_r, 4),
                "rr": round(rr, 4),
                "bars_held": max(0, exit_i - entry_i),
                "score": 0.0,
                "grade": "DIV_BREAKOUT",
                "size_mult": _safe_float(breakout_meta.get("size_mult", 0.35), 0.35),
                "regime": regime,
                "volatility": exec_ctx.get("volatility"),
                "squeeze": exec_ctx.get("squeeze"),
                "trend_direction": trend_dir,
                "allow_reason": "ALLOW_COMBO_SETUP_INDEPENDENT_POOL" if breakout_same_as_reversal else "ALLOW_BREAKOUT_ONLY_INDEPENDENT_POOL",
                "mitigation_src": breakout_meta.get("mitigation_src"),
                "breakout_vol_z": breakout_meta.get("breakout_vol_z"),
                "breakout_atr_ratio": breakout_meta.get("breakout_atr_ratio"),
                "mfe_r": exit_info.get("mfe_r"),
                "reject_reason_before_entry": "DIV_BREAKOUT_DIRECT_DISPATCH",
            })
            i = max(i + 1, exit_i + 1)
            continue

        fw_ok, fw_reason, fw_meta = _volume_absorption_firewall(row, direction, exec_ctx)
        if not fw_ok:
            add_reject(fw_reason, "9_吸收防火墙/破冰", row, direction, exec_ctx, 0.0, fw_meta)
            i += 1
            continue

        regime_bonus = 10.0 if regime == "trend" and trend_dir == direction else 5.0 if regime == "transition" and trend_dir == direction else -2.0 if regime == "mud" else 0.0
        raw_pattern_score = long_score if direction == "Long" else short_score
        pattern_score_100 = raw_pattern_score * 18.0
        # 根据方向选择对应的 smc_quality_score
        if direction == "Long":
            quality_score_100 = _safe_float(row.get("smc_quality_score_bull", row.get("smc_quality_score", 40.0)), 40.0)
        else:
            quality_score_100 = _safe_float(row.get("smc_quality_score_bear", row.get("smc_quality_score", 40.0)), 40.0)
        preliminary_score_100 = min(max((pattern_score_100 * 0.55) + (quality_score_100 * 0.45) + regime_bonus, 0.0), 100.0)

        if regime == "trend" and trend_dir != "None" and direction != trend_dir and _safe_float(row.get("adx"), 0.0) >= 58.0 and not bool(fw_meta.get("choch_reset", False)):
            add_reject("REJECT_EXTREME_TREND_AGAINST", "1_逆势锁死", row, direction, exec_ctx, preliminary_score_100, fw_meta)
            i += 1
            continue

        # ===== 初始化 entry_meta / alpha_meta（供 V34 引擎使用） =====
        entry_meta = dict(fw_meta) if isinstance(fw_meta, dict) else {}
        entry_meta["preliminary_score"] = preliminary_score_100
        entry_meta["pattern_score"] = pattern_score_100
        entry_meta["quality_score"] = quality_score_100
        entry_meta["regime_bonus"] = regime_bonus
        
        alpha_meta = {
            "score": preliminary_score_100,
            "grade": _grade_from_score(preliminary_score_100, regime),
        }

        # ===== V34 Regime Switching Engine 核心决策 =====
        v34_result = v34_regime_decision(
            row=row,
            direction=direction,
            exec_ctx=exec_ctx,
            macro_ctx=macro_ctx,
            entry_meta=entry_meta,
            alpha_meta=alpha_meta,
            base_risk=0.02,
        )
        
        regime = v34_result["regime"]
        final_score_100 = v34_result["score"]
        entry_allowed = v34_result["entry_allowed"]
        entry_reason = v34_result["entry_reason"]
        allowed_size = v34_result["position_size"]
        
        logger.debug(f"V34 Regime: {regime} | Score: {final_score_100} | Entry: {entry_allowed} | Reason: {entry_reason}")
        logger.debug(f"AFTER diagnostic: {dt.adjust(final_score_100)}")
        
        if not entry_allowed or allowed_size <= 0.0:
            add_reject(entry_reason, "7_V34_Regime权限过滤", row, direction, exec_ctx, final_score_100, entry_meta)
            i += 1
            continue

        br_res = _squeeze_false_breakout_filter(df_exec, i, direction)
        if isinstance(br_res, tuple) and len(br_res) > 0 and not br_res[0]:
            add_reject("REJECT_4_" + br_res[1], "4_Squeeze假突破过滤", row, direction, exec_ctx, final_score_100, entry_meta)
            i += 1
            continue

        entry_ok, entry_i, entry, entry_mode = _resolve_entry(df_exec, i, direction, row, entry_meta, exec_ctx)
        if not entry_ok or entry <= 0:
            add_reject(entry_mode, "6_入场追价过远跳过", row, direction, exec_ctx, final_score_100, entry_meta)
            i += 1
            continue

        dyn_res = calculate_dynamic_tp_sl(direction, row, hist, exec_ctx, min_rr, {})
        sl = dyn_res[0] if isinstance(dyn_res, tuple) else 0.0
        tp1 = dyn_res[1] if isinstance(dyn_res, tuple) else 0.0
        tp2 = dyn_res[2] if isinstance(dyn_res, tuple) else 0.0
        tp3 = dyn_res[3] if isinstance(dyn_res, tuple) else 0.0

        sl, tp1, tp2, tp3, rr = _normalize_rr_plan(direction, entry, sl, tp1, tp2, tp3, row, hist, exec_ctx, min_rr)

        if not risk_is_acceptable(entry, sl, _safe_float(row.get("ATRr_14"), entry * 0.006), max_risk_atr=3.5):
            add_reject("REJECT_RISK_NOT_ACCEPTABLE", "5_风控过滤", row, direction, exec_ctx, final_score_100, entry_meta)
            i += 1
            continue

        time_decay_bars = 6 if regime == "TREND" else 8 if regime == "TRANSITION" else 5
        trail_mult = 1.35 if regime == "TREND" else 1.15 if regime == "TRANSITION" else 0.95

        exit_info = _build_trade_exit(
            df_exec, entry_i, direction, entry, sl, tp1, tp2, tp3,
            max_hold_bars=_adaptive_max_hold_bars(exec_ctx, base_max_hold_bars),
            trail_atr_mult=trail_mult, time_drawdown_bars=time_decay_bars,
            tp1_close_pct=0.30, tp2_close_pct=0.30, regime=regime,
        )

        raw_pnl_r = _safe_float(exit_info.get("partial_pnl"), 0.0)
        cost_r = _round_trip_cost_r(entry, sl, fee_bps, slippage_bps)
        size_mult = allowed_size
        pnl_r = (raw_pnl_r - cost_r) * size_mult
        exit_i = int(exit_info.get("exit_i", entry_i))

        trades.append({
            "symbol": symbol,
            "setup_type": v34_result["portfolio"].get("setup_type", "V34_PORTFOLIO"),
            "direction": direction,
            "signal_at": signal_ts,
            "opened_at": df_exec.iloc[entry_i].get("datetime", entry_i),
            "closed_at": exit_info.get("exit_time"),
            "entry_mode": entry_mode,
            "entry": round(entry, 8),
            "sl": round(sl, 8),
            "tp1": round(tp1, 8),
            "tp2": round(tp2, 8),
            "tp3": round(tp3, 8),
            "exit_price": round(_safe_float(exit_info.get("exit")), 8),
            "exit_reason": exit_info.get("exit_reason"),
            "pnl_r": round(pnl_r, 4),
            "raw_pnl_r": round(raw_pnl_r, 4),
            "cost_r": round(cost_r, 4),
            "rr": round(rr, 4),
            "bars_held": max(0, exit_i - entry_i),
            "score": round(final_score_100, 2),
            "grade": _grade_from_score(final_score_100, regime),
            "size_mult": round(size_mult, 4),
            "regime": regime,
            "volatility": exec_ctx.get("volatility"),
            "squeeze": exec_ctx.get("squeeze"),
            "trend_direction": trend_dir,
            "allow_reason": entry_reason,
            "mitigation_src": entry_meta.get("mitigation_src"),
            "vwap_dist_atr": entry_meta.get("vwap_dist_atr"),
            "too_extended": bool(entry_meta.get("too_extended", False)),
            "vwap_extension_limit": entry_meta.get("vwap_extension_limit"),
            "absorption_risk": entry_meta.get("absorption_risk"),
            "choch_reset": entry_meta.get("choch_reset"),
            "choch_reset_reason": entry_meta.get("choch_reset_reason"),
            "validated_core": bool(entry_meta.get("validated_core", False)),
            "has_alpha_trigger": bool(entry_meta.get("has_alpha_trigger", False)),
            "strong_alpha_trigger": bool(entry_meta.get("strong_alpha_trigger", False)),
            "alpha_trigger_count": entry_meta.get("alpha_trigger_count"),
            "smc_zone_score": entry_meta.get("smc_zone_score"),
            "liquidity_score": entry_meta.get("liquidity_score"),
            "sqzmom_score": entry_meta.get("sqzmom_score"),
            "sqzmom_trigger_ok": entry_meta.get("sqzmom_trigger_ok"),
            "sqzmom_white_confirm": entry_meta.get("sqzmom_white_confirm"),
            "sqzmom_divergence_age": entry_meta.get("sqzmom_divergence_age"),
            "sqzmom_divergence_strength": entry_meta.get("sqzmom_divergence_strength"),
            "same_side_div_count_12": entry_meta.get("same_side_div_count_12"),
            "macro_conflict": entry_meta.get("macro_conflict"),
            "macro_reason": entry_meta.get("macro_reason"),
            "location_bonus": entry_meta.get("location_bonus"),
            "location_reasons": entry_meta.get("location_reasons"),
            "zone_near_atr": entry_meta.get("zone_near_atr"),
            "liquidity_sweep_confirmed": entry_meta.get("liquidity_sweep_confirmed"),
            "divergence_confirmed": entry_meta.get("divergence_confirmed"),
            "hf_confirmed": entry_meta.get("hf_confirmed"),
            "mfe_r": exit_info.get("mfe_r"),
            "early_reversal_pool": bool(entry_meta.get("early_reversal_pool", False)),
            "early_pool_reason": entry_meta.get("early_pool_reason"),
            "sqzmom_momentum_confirm": entry_meta.get("sqzmom_momentum_confirm"),
            "sqzmom_dmi_aligned": entry_meta.get("sqzmom_dmi_aligned"),
            "momentum_strength": entry_meta.get("momentum_strength"),
            "momentum_strength_slope": entry_meta.get("momentum_strength_slope"),
            "squeeze_level": entry_meta.get("squeeze_level"),
            "reject_reason_before_entry": entry_reason,
        })

        i = max(i + 1, exit_i + 1)

    # ── 使用 DiagnosticTracker 打印最终报告 ──
    dt.print_final_report()

    # ── 导出 Reject Audit ──
    if save_reject_audit:
        dt.export_reject_audit(reject_audit_path)

    df_res = pd.DataFrame(trades)
    if len(df_res) > 0:
        summary_res = summarize_backtest(df_res)
        print("\n📊 V30 SMART MONEY TP 统计概览:")
        print(summary_res["overall"])

        print("\n🔥 表现最强的前 10 笔交易明细:")
        best_trades = df_res.sort_values(by="pnl_r", ascending=False).head(10)
        pd.set_option("display.max_columns", None)
        pd.set_option("display.width", 1000)
        print(best_trades[["opened_at", "setup_type", "direction", "pnl_r", "score", "grade", "regime", "entry_mode", "exit_reason", "allow_reason"]])

        print("\n⚠️ 开始执行压力测试...")
        stress_test(df_res)
    else:
        print("\n⚠️ 警报：交易数量为 0！")
        # 快速诊断：利用 DiagnosticTracker 的 reject_reasons 统计
        print("\n🔍 拦截统计 (Top 5 原因):")
        for reason, count in dt.reject_reasons.most_common(5):
            print(f" {reason}: {count} 次")
        print(f"\n💡 建议：查看 {reject_audit_path} 获取完整拦截明细")

    return df_res


# ==========================================
# Module 6: reports and stress test
# ==========================================
def summarize_backtest(trades: pd.DataFrame) -> Dict[str, Any]:
    if trades is None or trades.empty:
        return {"overall": {"trades": 0, "win_rate": 0.0, "pf": 0.0}, "by_grade": {}, "by_state": {}, "by_setup_type": {}}

    trades = trades.copy()
    if "grade" not in trades.columns:
        trades["grade"] = trades["score"].apply(_grade_from_score)

    def calc_stats(df_sub: pd.DataFrame) -> Dict[str, Any]:
        if len(df_sub) == 0:
            return {"trades": 0, "win_rate": 0.0, "pf": 0.0, "pnl": 0.0, "avg_r": 0.0}
        wins = df_sub[df_sub["pnl_r"] > 0]["pnl_r"].sum()
        losses = abs(df_sub[df_sub["pnl_r"] < 0]["pnl_r"].sum())
        pf = wins / losses if losses > 0 else (999.0 if wins > 0 else 0.0)
        return {
            "trades": int(len(df_sub)),
            "win_rate": round(float((df_sub["pnl_r"] > 0).mean()), 4),
            "pf": round(float(pf), 4),
            "pnl": round(float(df_sub["pnl_r"].sum()), 4),
            "avg_r": round(float(df_sub["pnl_r"].mean()), 4),
        }

    overall = calc_stats(trades)
    grade_stats = {g: calc_stats(trades[trades["grade"] == g]) for g in ["S", "A", "B", "C", "D", "HOT"]}
    state_stats = {s: calc_stats(trades[trades["regime"] == s]) for s in trades["regime"].dropna().unique()}
    if "setup_type" not in trades.columns:
        trades["setup_type"] = "REVERSAL_ONLY"
    setup_stats = {
        s: calc_stats(trades[trades["setup_type"] == s])
        for s in ["REVERSAL_ONLY", "BREAKOUT_ONLY", "COMBO_SETUP", "EARLY_REVERSAL_POOL"]
    }

    cross_stats: Dict[str, Any] = {}
    if "regime" in trades.columns and "grade" in trades.columns:
        for (regime, grade), sub in trades.groupby(["regime", "grade"]):
            cross_stats[f"{regime}_{grade}"] = calc_stats(sub)

    return {"overall": overall, "by_grade": grade_stats, "by_state": state_stats, "by_setup_type": setup_stats, "cross_regime_grade": cross_stats}


def stress_test(trades_df: pd.DataFrame, sim_count: int = 1000) -> Dict[str, Any]:
    if trades_df is None or trades_df.empty:
        return {}

    df_sorted = trades_df.sort_values(by="pnl_r", ascending=False).copy()
    df_no_outliers = df_sorted.iloc[5:].copy() if len(df_sorted) > 5 else df_sorted.copy()
    wins_no_out = df_no_outliers[df_no_outliers["pnl_r"] > 0]["pnl_r"].sum()
    loss_no_out = abs(df_no_outliers[df_no_outliers["pnl_r"] < 0]["pnl_r"].sum()) + 1e-9
    pf_no_outliers = round(float(wins_no_out / loss_no_out), 4)

    print(f"\n👉 剔除 Top 5 运气单后的真实 PF: {pf_no_outliers}")
    print(f"👉 剔除后的 Win Rate: {round(float((df_no_outliers['pnl_r'] > 0).mean()), 4)}")

    if "regime" in trades_df.columns and "grade" in trades_df.columns:
        cross_stats = trades_df.groupby(["regime", "grade"]).apply(
            lambda x: pd.Series({
                "count": len(x),
                "win_rate": round(float((x["pnl_r"] > 0).mean()), 4),
                "pf": round(float(x[x["pnl_r"] > 0]["pnl_r"].sum() / (abs(x[x["pnl_r"] < 0]["pnl_r"].sum()) + 1e-9)), 4),
                "total_r": round(float(x["pnl_r"].sum()), 4),
            })
        )
        print("\n🚨 状态与评级交叉分析报表:\n", cross_stats)

    pnl_array = trades_df["pnl_r"].values
    random_indices = np.random.randint(0, len(pnl_array), size=(sim_count, len(pnl_array)))
    sim_pnls = pnl_array[random_indices]
    sim_cum_pnl = np.cumsum(sim_pnls, axis=1)
    sim_drawdowns = np.maximum.accumulate(sim_cum_pnl, axis=1) - sim_cum_pnl
    worst_drawdown_r = round(float(np.max(np.max(sim_drawdowns, axis=1))), 2)
    avg_drawdown_r = round(float(np.mean(np.max(sim_drawdowns, axis=1))), 2)

    print(f"\n⚠️ 蒙特卡洛 {sim_count} 次模拟结果:")
    print(f"平均最大回撤: -{avg_drawdown_r} R")
    print(f"最极端的深渊回撤: -{worst_drawdown_r} R")

    return {"pf_no_outliers": pf_no_outliers, "worst_drawdown_r": worst_drawdown_r, "avg_drawdown_r": avg_drawdown_r}


def deep_diagnostic_test(trades_df: pd.DataFrame) -> None:
    if trades_df is None or trades_df.empty:
        print("没有交易可诊断。")
        return

    print("\n" + "=" * 55)
    print("🚨 量化系统深度诊断报告 (Deep Diagnostic Report) 🚨")
    print("=" * 55)

    total_trades = len(trades_df)
    wins = trades_df[trades_df["pnl_r"] > 0]
    losses = trades_df[trades_df["pnl_r"] <= 0]
    win_rate = len(wins) / total_trades if total_trades > 0 else 0.0
    gross_profit = wins["pnl_r"].sum() if len(wins) else 0.0
    gross_loss = abs(losses["pnl_r"].sum()) if len(losses) else 0.0
    pf = gross_profit / gross_loss if gross_loss > 0 else 999.0

    print(f"总交易数: {total_trades}")
    print(f"胜率: {win_rate:.2%}")
    print(f"PF: {pf:.4f}")
    print(f"总 R: {trades_df['pnl_r'].sum():.4f}")
    print(f"平均 R: {trades_df['pnl_r'].mean():.4f}")

    for col in ["regime", "grade", "entry_mode", "exit_reason", "mitigation_src", "allow_reason", "too_extended", "score_cap_reasons", "macro_reason", "location_reasons"]:
        if col in trades_df.columns:
            print(f"\n--- {col} 分布 ---")
            print(trades_df.groupby(col)["pnl_r"].agg(["count", "mean", "sum"]).sort_values("sum", ascending=False))

    # ==========================================
    # 🌟 新增：SMC 与 SQZMOM 因子交叉验证报表
    # ==========================================
    print("\n" + "=" * 55)
    print("🔬 核心因子交叉验证 (SMC vs SQZMOM 归因分析)")
    print("=" * 55)

    # 1. 安全提取：防范字段藏在 entry_meta 字典里未被展平的情况
    df_factors = trades_df.copy()
    for col in ['smc_passed', 'sqz_passed']:
        if col not in df_factors.columns:
            if 'entry_meta' in df_factors.columns:
                df_factors[col] = df_factors['entry_meta'].apply(
                    lambda x: x.get(col, False) if isinstance(x, dict) else False
                )
            else:
                df_factors[col] = False

    # 2. 生成直观的组合标签
    def get_factor_label(row):
        smc = "✅" if row.get('smc_passed', False) else "❌ (被打回)"
        sqz = "✅" if row.get('sqz_passed', False) else "❌ (打8折)"
        return f"SMC:{smc} | SQZ:{sqz}"

    df_factors['factor_combo'] = df_factors.apply(get_factor_label, axis=1)

    # 3. 分组计算核心统计量
    if not df_factors.empty:
        factor_stats = df_factors.groupby('factor_combo').apply(
            lambda x: pd.Series({
                '交易次数': len(x),
                '胜率': round((x['pnl_r'] > 0).mean(), 4),
                'PF (盈亏比)': round(x[x['pnl_r'] > 0]['pnl_r'].sum() / (abs(x[x['pnl_r'] < 0]['pnl_r'].sum()) + 1e-9), 4),
                '总净利(R)': round(x['pnl_r'].sum(), 2),
                '单笔期望(R)': round(x['pnl_r'].mean(), 4)
            })
        ).sort_values('交易次数', ascending=False)

        print(factor_stats.to_string())
    else:
        print("没有足够的交易数据进行因子分析。")
