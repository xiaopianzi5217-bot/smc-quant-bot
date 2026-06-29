# -*- coding: utf-8 -*-
"""FVG / Order Block / Stop Hunt detection helpers. 
This module is deliberately dependency-light and can be used by both live and backtest code. 
It accepts standard OHLCV pandas DataFrames and returns the same frame with additional columns used by the upgraded entry/exit logic. 
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        v = float(value)
        if np.isnan(v) or np.isinf(v):
            return default
        return v
    except Exception:
        return default


@dataclass
class Zone:
    kind: str
    direction: str
    top: float
    bottom: float
    mid: float
    created_i: int
    mitigated: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "direction": self.direction,
            "top": float(self.top),
            "bottom": float(self.bottom),
            "mid": float(self.mid),
            "created_i": int(self.created_i),
            "mitigated": bool(self.mitigated),
        }


def add_true_range_atr(df: pd.DataFrame, period: int = 14, out_col: str = "ATRr_14") -> pd.DataFrame:
    out = df.copy()
    high = out["high"].astype(float)
    low = out["low"].astype(float)
    close = out["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat([(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    out["true_range"] = tr
    out[out_col] = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return out


def add_vwap(df: pd.DataFrame, window: int = 48, out_col: str = "vwap_48") -> pd.DataFrame:
    out = df.copy()
    typical = (out["high"].astype(float) + out["low"].astype(float) + out["close"].astype(float)) / 3.0
    vol = out.get("volume", pd.Series(1.0, index=out.index)).astype(float).replace(0, np.nan)
    pv = typical * vol
    out[out_col] = pv.rolling(window, min_periods=max(5, window // 4)).sum() / vol.rolling(window, min_periods=max(5, window // 4)).sum()
    out[out_col] = out[out_col].ffill().fillna(typical)
    return out


def add_swing_points(df: pd.DataFrame, left: int = 2, right: int = 2) -> pd.DataFrame:
    out = df.copy()
    highs = out["high"].astype(float)
    lows = out["low"].astype(float)
    out["swing_high"] = False
    out["swing_low"] = False
    for i in range(left, len(out) - right):
        h = highs.iloc[i]
        l = lows.iloc[i]
        if h >= highs.iloc[i - left : i + right + 1].max():
            out.iat[i, out.columns.get_loc("swing_high")] = True
        if l <= lows.iloc[i - left : i + right + 1].min():
            out.iat[i, out.columns.get_loc("swing_low")] = True
    out["last_swing_high"] = np.nan
    out["last_swing_low"] = np.nan
    last_h = np.nan
    last_l = np.nan
    for i in range(len(out)):
        if bool(out.iloc[i]["swing_high"]):
            last_h = float(out.iloc[i]["high"])
        if bool(out.iloc[i]["swing_low"]):
            last_l = float(out.iloc[i]["low"])
        out.iat[i, out.columns.get_loc("last_swing_high")] = last_h
        out.iat[i, out.columns.get_loc("last_swing_low")] = last_l
    return out


def add_fvg_zones(df: pd.DataFrame, min_gap_atr: float = 0.20, atr_col: str = "ATRr_14") -> pd.DataFrame:
    """Detect 3-candle fair value gaps."""
    out = df.copy()
    cols = {
        "fvg_direction": None,
        "fvg_top": np.nan,
        "fvg_bottom": np.nan,
        "fvg_mid": np.nan,
        "fvg_age": np.nan,
        "near_fvg_mid": False,
    }
    for k, v in cols.items():
        out[k] = v

    active: List[Zone] = []
    for i in range(len(out)):
        if i >= 2:
            h0 = _safe_float(out.iloc[i - 2]["high"])
            l0 = _safe_float(out.iloc[i - 2]["low"])
            hi = _safe_float(out.iloc[i]["high"])
            li = _safe_float(out.iloc[i]["low"])
            atr = _safe_float(out.iloc[i].get(atr_col), _safe_float(out.iloc[i]["close"]) * 0.006)
            min_gap = max(atr * min_gap_atr, _safe_float(out.iloc[i]["close"]) * 0.0003)
            if li - h0 >= min_gap:
                top = li
                bottom = h0
                active.append(Zone("FVG", "Long", top, bottom, (top + bottom) / 2.0, i))
            if l0 - hi >= min_gap:
                top = l0
                bottom = hi
                active.append(Zone("FVG", "Short", top, bottom, (top + bottom) / 2.0, i))

        close = _safe_float(out.iloc[i]["close"])
        atr = _safe_float(out.iloc[i].get(atr_col), close * 0.006)
        fresh: List[Zone] = []
        for z in active[-30:]:
            if z.direction == "Long" and _safe_float(out.iloc[i]["low"]) <= z.mid:
                z.mitigated = True
            if z.direction == "Short" and _safe_float(out.iloc[i]["high"]) >= z.mid:
                z.mitigated = True
            if not z.mitigated:
                fresh.append(z)
        if fresh:
            nearest = min(fresh, key=lambda z: abs(close - z.mid))
            out.at[out.index[i], "fvg_direction"] = nearest.direction
            out.at[out.index[i], "fvg_top"] = nearest.top
            out.at[out.index[i], "fvg_bottom"] = nearest.bottom
            out.at[out.index[i], "fvg_mid"] = nearest.mid
            out.at[out.index[i], "fvg_age"] = i - nearest.created_i
            out.at[out.index[i], "near_fvg_mid"] = abs(close - nearest.mid) <= 0.50 * atr
    return out


def add_order_block_proxy(df: pd.DataFrame, lookback: int = 12, atr_col: str = "ATRr_14") -> pd.DataFrame:
    """Simple OB proxy based on the last opposite candle before impulse."""
    out = df.copy()
    for col in ["ob_direction", "ob_top", "ob_bottom", "ob_mid", "near_ob"]:
        out[col] = None if col == "ob_direction" else (False if col == "near_ob" else np.nan)

    for i in range(lookback, len(out)):
        close = _safe_float(out.iloc[i]["close"])
        atr = _safe_float(out.iloc[i].get(atr_col), close * 0.006)
        body = abs(_safe_float(out.iloc[i]["close"]) - _safe_float(out.iloc[i]["open"]))
        if body < 0.8 * atr:
            continue
        impulse_long = _safe_float(out.iloc[i]["close"]) > _safe_float(out.iloc[i]["open"])
        impulse_short = _safe_float(out.iloc[i]["close"]) < _safe_float(out.iloc[i]["open"])
        if not (impulse_long or impulse_short):
            continue
        target_dir = "Long" if impulse_long else "Short"
        for k in range(i - 1, max(-1, i - lookback - 1), -1):
            o = _safe_float(out.iloc[k]["open"])
            c = _safe_float(out.iloc[k]["close"])
            opposite = (target_dir == "Long" and c < o) or (target_dir == "Short" and c > o)
            if opposite:
                top = max(_safe_float(out.iloc[k]["open"]), _safe_float(out.iloc[k]["close"]), _safe_float(out.iloc[k]["high"]))
                bottom = min(_safe_float(out.iloc[k]["open"]), _safe_float(out.iloc[k]["close"]), _safe_float(out.iloc[k]["low"]))
                mid = (top + bottom) / 2.0
                out.at[out.index[i], "ob_direction"] = target_dir
                out.at[out.index[i], "ob_top"] = top
                out.at[out.index[i], "ob_bottom"] = bottom
                out.at[out.index[i], "ob_mid"] = mid
                out.at[out.index[i], "near_ob"] = abs(close - mid) <= 0.60 * atr
                break
    for col in ["ob_direction", "ob_top", "ob_bottom", "ob_mid"]:
        out[col] = out[col].ffill()
    close_s = out["close"].astype(float)
    atr_s = out[atr_col].fillna(close_s * 0.006).astype(float) if atr_col in out.columns else close_s * 0.006
    out["near_ob"] = (close_s - out["ob_mid"].astype(float)).abs() <= 0.60 * atr_s
    out["near_ob"] = out["near_ob"].fillna(False)
    return out


def add_stop_hunt_detection(df: pd.DataFrame, atr_col: str = "ATRr_14", volume_window: int = 20) -> pd.DataFrame:
    """Detect liquidity sweep / stop hunt candles."""
    out = df.copy()
    if "last_swing_high" not in out.columns or "last_swing_low" not in out.columns:
        out = add_swing_points(out)
    vol = out.get("volume", pd.Series(1.0, index=out.index)).astype(float)
    out["volume_z"] = (vol - vol.rolling(volume_window, min_periods=5).mean()) / vol.rolling(volume_window, min_periods=5).std().replace(0, np.nan)
    out["volume_z"] = out["volume_z"].fillna(0.0)
    out["bullish_stop_hunt"] = False
    out["bearish_stop_hunt"] = False
    out["stop_hunt_direction"] = None
    out["stop_hunt_score"] = 0.0

    for i in range(3, len(out)):
        close = _safe_float(out.iloc[i]["close"])
        low = _safe_float(out.iloc[i]["low"])
        high = _safe_float(out.iloc[i]["high"])
        atr = _safe_float(out.iloc[i].get(atr_col), close * 0.006)
        prev_swing_low = _safe_float(out.iloc[i - 1].get("last_swing_low"), 0.0)
        prev_swing_high = _safe_float(out.iloc[i - 1].get("last_swing_high"), 0.0)
        vol_z = _safe_float(out.iloc[i].get("volume_z"), 0.0)
        wick_down = min(_safe_float(out.iloc[i]["open"]), close) - low
        wick_up = high - max(_safe_float(out.iloc[i]["open"]), close)
        bull = prev_swing_low > 0 and low < prev_swing_low - 0.05 * atr and close > prev_swing_low and wick_down >= 0.35 * atr
        bear = prev_swing_high > 0 and high > prev_swing_high + 0.05 * atr and close < prev_swing_high and wick_up >= 0.35 * atr
        if bull:
            score = 1.0 + max(0.0, vol_z) * 0.25 + min(2.0, wick_down / max(atr, 1e-12)) * 0.35
            out.at[out.index[i], "bullish_stop_hunt"] = True
            out.at[out.index[i], "stop_hunt_direction"] = "Long"
            out.at[out.index[i], "stop_hunt_score"] = round(score, 4)
        elif bear:
            score = 1.0 + max(0.0, vol_z) * 0.25 + min(2.0, wick_up / max(atr, 1e-12)) * 0.35
            out.at[out.index[i], "bearish_stop_hunt"] = True
            out.at[out.index[i], "stop_hunt_direction"] = "Short"
            out.at[out.index[i], "stop_hunt_score"] = round(score, 4)
    return out


def add_sqzmom_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    【新增】专门用于计算 Squeeze Momentum 以及右侧变白/缩头确认特征。
    将原本写在 runner.py 中的逻辑下沉到特征工程层，确保实盘/回测逻辑严格一致。
    """
    out = df.copy()
    close = out["close"].astype(float)
    
    if "momentum" not in out.columns:
        bb_mid = close.rolling(20, min_periods=10).mean()
        out["momentum"] = close - bb_mid
        
    # 提取当前与前一根 Bar 的绝对动量
    curr_mom = out["momentum"].astype(float)
    prev_mom = curr_mom.shift(1).astype(float)
    
    # 动态注入右侧衰竭特征变白（当前绝对动量值 <= 前一根Bar的92%）
    out["sqzmom_white"] = curr_mom.abs() <= (prev_mom.abs() * 0.92)
    
    # 简单的动能方向判断
    out["mom_bullish"] = curr_mom > 0
    out["mom_bearish"] = curr_mom < 0
    
    return out


def prepare_smc_features(df: pd.DataFrame) -> pd.DataFrame:
    """主特征调度管道，实盘与回测均由此接入"""
    out = df.copy()
    if "ATRr_14" not in out.columns:
        out = add_true_range_atr(out)
    if "vwap_48" not in out.columns:
        out = add_vwap(out)
    out = add_swing_points(out)
    out = add_fvg_zones(out)
    out = add_order_block_proxy(out)
    out = add_stop_hunt_detection(out)
    
    # 【新增】将右侧变白特征检测正式集成到主管道中
    out = add_sqzmom_features(out)
    
    return out


def nearest_mitigation_price(row: pd.Series, direction: str) -> Tuple[Optional[float], str]:
    direction = str(direction or "").title()
    candidates: List[Tuple[float, str]] = []
    fvg_dir = str(row.get("fvg_direction") or "")
    if fvg_dir == direction and pd.notna(row.get("fvg_mid")):
        candidates.append((_safe_float(row.get("fvg_mid")), "FVG_MID"))
    ob_dir = str(row.get("ob_direction") or "")
    if ob_dir == direction and pd.notna(row.get("ob_mid")):
        # Long prefers upper edge/mid; Short prefers lower edge/mid. Mid is stable for backtest.
        candidates.append((_safe_float(row.get("ob_mid")), "OB_MID"))
    if not candidates:
        return None, "NO_FVG_OB"
    close = _safe_float(row.get("close"))
    price, src = min(candidates, key=lambda x: abs(close - x[0]))
    return price, src
