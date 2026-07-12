# -*- coding: utf-8 -*-
"""
V56 Production Architecture

A live-safe, event-level 15m backtest path that replaces the old
"tiny candidate pool + hard filters" workflow with:
  1) five parallel signal generators,
  2) EV-style ranking rather than EV hard-gating,
  3) Top-N daily portfolio selection,
  4) cluster/risk scaling rather than signal killing,
  5) realistic next-bar execution and conservative TP/SL handling.

Important: this module deliberately does not promise future win-rate/PF.
It reports target gaps if a desired metric cannot be achieved without
look-ahead bias or micro-profit tricks.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import json
import math

import numpy as np
import pandas as pd


@dataclass
class V56Config:
    warmup_bars: int = 260
    annual_trade_target_min: int = 370
    annual_trade_target_max: int = 400
    extra_second_trade_days: int = 25
    min_score: float = 55.0
    stop_atr: float = 1.00
    min_stop_pct: float = 0.0025
    tp1_r: float = 0.85
    tp2_r: float = 1.45
    tp3_r: float = 2.20
    tp1_close_pct: float = 0.35
    tp2_close_pct: float = 0.35
    max_hold_bars: int = 24
    fee_r: float = 0.04
    slippage_r: float = 0.03
    no_overlap: bool = True
    # Production-safe targets used for reporting only.  The engine must not
    # force these by leaking future outcomes.
    target_win_rate_min: float = 0.70
    target_win_rate_max: float = 0.80
    target_pf_min: float = 1.50
    target_pf_max: float = 2.00
    target_avg_r_min: float = 0.20
    target_total_r_min: float = 20.0


def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).rolling(n).mean()
    down = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + up / (down + 1e-12))


def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat(
        [(df["high"] - df["low"]), (df["high"] - prev).abs(), (df["low"] - prev).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(n).mean()


def load_ohlcv(path_or_df: Any) -> pd.DataFrame:
    df = path_or_df.copy() if isinstance(path_or_df, pd.DataFrame) else pd.read_csv(path_or_df)
    required = {"open", "high", "low", "close", "volume"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"OHLCV missing columns: {missing}")
    if "datetime" not in df.columns:
        if "ts" in df.columns:
            unit = "ms" if pd.to_numeric(df["ts"], errors="coerce").median() > 10**12 else "s"
            df["datetime"] = pd.to_datetime(df["ts"], unit=unit, errors="coerce")
        else:
            df["datetime"] = pd.RangeIndex(len(df))
    else:
        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close", "datetime"]).reset_index(drop=True)
    return df


def _stoch(df: pd.DataFrame, k: int = 14, d: int = 3) -> Tuple[pd.Series, pd.Series]:
    """Stochastic %K and %D using high/low/close."""
    low_k = df["low"].rolling(k).min()
    high_k = df["high"].rolling(k).max()
    k_line = 100 * (df["close"] - low_k) / (high_k - low_k + 1e-12)
    d_line = k_line.rolling(d).mean()
    return k_line, d_line


def _vwap_by_day(df: pd.DataFrame) -> pd.Series:
    """Compute VWAP resetting at each calendar date boundary (15m data)."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    pv = tp * df["volume"]
    # Group by date and compute cumulative sums within each day
    date = pd.to_datetime(df["datetime"]).dt.date
    cum_pv = pv.groupby(date).cumsum()
    cum_vol = df["volume"].groupby(date).cumsum()
    return cum_pv / (cum_vol + 1e-12)


def add_v56_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["atr"] = _atr(out, 14)
    out["ema20"] = _ema(out["close"], 20)
    out["ema50"] = _ema(out["close"], 50)
    out["ema100"] = _ema(out["close"], 100)
    out["ema200"] = _ema(out["close"], 200)
    out["rsi"] = _rsi(out["close"], 14)
    out["hh20"] = out["high"].shift(1).rolling(20).max()
    out["ll20"] = out["low"].shift(1).rolling(20).min()
    out["hh8"] = out["high"].shift(1).rolling(8).max()
    out["ll8"] = out["low"].shift(1).rolling(8).min()
    out["body"] = (out["close"] - out["open"]).abs()
    out["range"] = (out["high"] - out["low"]).replace(0, np.nan)
    out["body_pct"] = out["body"] / (out["range"] + 1e-12)
    out["vol_z"] = (out["volume"] - out["volume"].rolling(96).mean()) / (out["volume"].rolling(96).std() + 1e-9)
    out["trend_strength"] = (out["ema20"] - out["ema50"]) / (out["atr"] + 1e-9)
    out["hour"] = pd.to_datetime(out["datetime"]).dt.hour
    out["dow"] = pd.to_datetime(out["datetime"]).dt.dayofweek
    out["date"] = pd.to_datetime(out["datetime"]).dt.date
    # ENHANCED_BUY indicators
    out["vwap"] = _vwap_by_day(out)
    out["above_vwap"] = out["close"] > out["vwap"]
    out["stoch_k"], out["stoch_d"] = _stoch(out, 14, 3)
    out["vol_ma5"] = out["volume"].rolling(5).mean()
    out["is_volume_spike"] = out["volume"] > (out["vol_ma5"].shift(1) * 2.0)
    out["demand_zone"] = out["low"] <= (out["low"].rolling(20).min() * 1.01)
    out["stoch_cross_up"] = (
        (out["stoch_k"].shift(1) < out["stoch_d"].shift(1))
        & (out["stoch_k"] > out["stoch_d"])
        & (out["stoch_k"] < 20)
    )
    return out


def _append(rows: List[Dict[str, Any]], df: pd.DataFrame, i: int, setup: str, direction: str, score: float, reasons: List[str]) -> None:
    r = df.iloc[i]
    rows.append(
        {
            "idx": int(i),
            "datetime": r["datetime"],
            "date": r["date"],
            "setup_type": setup,
            "direction": direction,
            "score": round(float(score), 4),
            "rank_score": round(float(score), 4),
            "reasons": ";".join(reasons),
            "rsi": round(float(r.get("rsi", 0.0)), 4),
            "trend_strength": round(float(r.get("trend_strength", 0.0)), 4),
            "vol_z": round(float(r.get("vol_z", 0.0)), 4),
            "body_pct": round(float(r.get("body_pct", 0.0)), 4),
            "hour": int(r.get("hour", 0)),
            "dow": int(r.get("dow", 0)),
        }
    )


def generate_v56_candidates(df: pd.DataFrame, cfg: Optional[V56Config] = None) -> pd.DataFrame:
    """Generate a broad non-hindsight candidate pool from five pattern families."""
    cfg = cfg or V56Config()
    rows: List[Dict[str, Any]] = []
    for i in range(max(cfg.warmup_bars, 260), len(df) - 2):
        r = df.iloc[i]
        atr = float(r.get("atr", np.nan))
        if not np.isfinite(atr) or atr <= 0:
            continue
        vol_bonus = max(0.0, min(6.0, float(r.get("vol_z", 0.0)) * 1.5))
        # 1) Liquidity sweep: generate opportunity without requiring full CHOCH.
        # 【修复20260721】基分从 65.0 → 73.0（+8分），因为流动性扫单是强反转信号的重要组成部分。
        if r["low"] < r["ll20"] and r["close"] > r["ll20"] and r["close"] > r["open"]:
            depth = min(10.0, (r["ll20"] - r["low"]) / atr * 8.0)
            reclaim = min(8.0, max(0.0, (r["close"] - r["ll20"]) / atr * 6.0))
            score = 73.0 + depth + reclaim + max(0.0, 50.0 - r["rsi"]) * 0.12 + vol_bonus
            _append(rows, df, i, "LIQUIDITY_SWEEP", "Long", score, ["sweep_low", "reclaim"])
        if r["high"] > r["hh20"] and r["close"] < r["hh20"] and r["close"] < r["open"]:
            depth = min(10.0, (r["high"] - r["hh20"]) / atr * 8.0)
            reclaim = min(8.0, max(0.0, (r["hh20"] - r["close"]) / atr * 6.0))
            score = 73.0 + depth + reclaim + max(0.0, r["rsi"] - 50.0) * 0.12 + vol_bonus
            _append(rows, df, i, "LIQUIDITY_SWEEP", "Short", score, ["sweep_high", "reclaim"])
        # 2) Weak BOS: broad structure break candidate, not a hard final entry filter.
        # 【修复20260721】WEAK_BOS 基分从 56.0 → 46.0（降10分），趋势加分从 8.0 → 4.0
        # 因为 WEAK_BOS 只是初步结构突破，不是强趋势反转，不应与 LIQUIDITY_SWEEP / REAL_CHOCH 竞争。
        if r["close"] > r["hh20"] and r["body_pct"] > 0.45:
            trend = 4.0 if r["ema20"] > r["ema50"] else -6.0
            score = 46.0 + trend + min(10.0, (r["close"] - r["hh20"]) / atr * 6.0) + vol_bonus
            _append(rows, df, i, "WEAK_BOS", "Long", score, ["break_high"])
        if r["close"] < r["ll20"] and r["body_pct"] > 0.45:
            trend = 4.0 if r["ema20"] < r["ema50"] else -6.0
            score = 46.0 + trend + min(10.0, (r["ll20"] - r["close"]) / atr * 6.0) + vol_bonus
            _append(rows, df, i, "WEAK_BOS", "Short", score, ["break_low"])
        # 2b) REAL_CHOCH: genuine market structure shift — requires both a sweep of the swing
        #     point AND a close beyond it, with trend alignment and momentum confirmation.
        #     【修复20260721新增】基分 66.0（比 LIQUIDITY_SWEEP 高1分），因为这是最强的反转信号之一。
        #     Long: 扫了 ll20 + 收在 hh20 以上 + ema20>ema50 + rsi>50
        if r["low"] < r["ll20"] and r["close"] > r["hh20"] and r["ema20"] > r["ema50"] and r["rsi"] > 50:
            sweep_range = min(8.0, (r["ll20"] - r["low"]) / atr * 6.0)
            break_range = min(8.0, (r["close"] - r["hh20"]) / atr * 6.0)
            score = 66.0 + sweep_range + break_range + max(0.0, r["rsi"] - 50.0) * 0.15 + vol_bonus
            _append(rows, df, i, "REAL_CHOCH", "Long", score, ["choch_sweep_low", "choch_break_high", "trend_align"])
        #     Short: 扫了 hh20 + 收在 ll20 以下 + ema20<ema50 + rsi<50
        if r["high"] > r["hh20"] and r["close"] < r["ll20"] and r["ema20"] < r["ema50"] and r["rsi"] < 50:
            sweep_range = min(8.0, (r["high"] - r["hh20"]) / atr * 6.0)
            break_range = min(8.0, (r["ll20"] - r["close"]) / atr * 6.0)
            score = 66.0 + sweep_range + break_range + max(0.0, 50.0 - r["rsi"]) * 0.15 + vol_bonus
            _append(rows, df, i, "REAL_CHOCH", "Short", score, ["choch_sweep_high", "choch_break_low", "trend_align"])
        # 3) FVG touch: single imbalance-touch candidate.
        if i >= 3:
            c = df.iloc[i - 3]
            b = df.iloc[i - 1]
            if b["low"] > c["high"] and r["low"] <= b["low"] and r["close"] > c["high"] and r["close"] > r["open"]:
                gap = min(10.0, (b["low"] - c["high"]) / atr * 7.0)
                score = 58.0 + gap + (5.0 if r["ema20"] > r["ema50"] else 0.0) + vol_bonus
                _append(rows, df, i, "FVG_TOUCH", "Long", score, ["bullish_fvg_touch"])
            if b["high"] < c["low"] and r["high"] >= b["high"] and r["close"] < c["low"] and r["close"] < r["open"]:
                gap = min(10.0, (c["low"] - b["high"]) / atr * 7.0)
                score = 58.0 + gap + (5.0 if r["ema20"] < r["ema50"] else 0.0) + vol_bonus
                _append(rows, df, i, "FVG_TOUCH", "Short", score, ["bearish_fvg_touch"])
        # 4) Orderblock reaction proxy: first touch of ema50 zone after impulse.
        recent = df.iloc[max(0, i - 6): i]
        if len(recent) >= 6:
            impulse_up = int((recent["close"] > recent["open"]).sum()) >= 4 and r["ema20"] > r["ema50"]
            impulse_dn = int((recent["close"] < recent["open"]).sum()) >= 4 and r["ema20"] < r["ema50"]
            if impulse_up and r["low"] <= r["ema50"] and r["close"] > r["ema50"] and r["close"] > r["open"] and r["rsi"] > 38:
                score = 57.0 + min(12.0, (r["close"] - r["ema50"]) / atr * 5.0) + vol_bonus
                _append(rows, df, i, "ORDERBLOCK_REACTION", "Long", score, ["impulse_pullback_zone"])
            if impulse_dn and r["high"] >= r["ema50"] and r["close"] < r["ema50"] and r["close"] < r["open"] and r["rsi"] < 62:
                score = 57.0 + min(12.0, (r["ema50"] - r["close"]) / atr * 5.0) + vol_bonus
                _append(rows, df, i, "ORDERBLOCK_REACTION", "Short", score, ["impulse_pullback_zone"])
        # 5) Trend continuation pullback.
        if r["ema20"] > r["ema50"] > r["ema100"] and r["low"] <= r["ema20"] and r["close"] > r["ema20"] and 42 < r["rsi"] < 68:
            score = 59.0 + min(10.0, (r["ema20"] - r["low"]) / atr * 7.0) + min(8.0, (r["ema20"] - r["ema50"]) / atr * 2.0) + vol_bonus
            _append(rows, df, i, "TREND_PULLBACK", "Long", score, ["ema20_pullback", "trend_stack"])
        if r["ema20"] < r["ema50"] < r["ema100"] and r["high"] >= r["ema20"] and r["close"] < r["ema20"] and 32 < r["rsi"] < 58:
            score = 59.0 + min(10.0, (r["high"] - r["ema20"]) / atr * 7.0) + min(8.0, (r["ema50"] - r["ema20"]) / atr * 2.0) + vol_bonus
            _append(rows, df, i, "TREND_PULLBACK", "Short", score, ["ema20_pullback", "trend_stack"])
        # 6) ENHANCED_BUY: 多条件共振（需求区 + Stoch低位金叉 + 放量 + VWAP上方）
        enhanced_long = (
            bool(r.get("demand_zone", False))
            & bool(r.get("stoch_cross_up", False))
            & bool(r.get("is_volume_spike", False))
            & bool(r.get("above_vwap", False))
        )
        if enhanced_long:
            stoch_bonus = min(12.0, (20.0 - float(r.get("stoch_k", 50.0))) * 0.5)
            vwap_bonus = min(8.0, (float(r["close"]) - float(r["vwap"])) / atr * 4.0) if float(r.get("vwap", 0.0)) > 0 else 0.0
            score = 64.0 + stoch_bonus + vwap_bonus + vol_bonus
            _append(rows, df, i, "ENHANCED_BUY", "Long", score, ["demand_zone", "stoch_cross_up", "volume_spike", "above_vwap"])
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out = out.sort_values(["idx", "rank_score"], ascending=[True, False]).reset_index(drop=True)
    return out


def select_v56_portfolio(candidates: pd.DataFrame, cfg: Optional[V56Config] = None) -> pd.DataFrame:
    """Top-N selection: best one per day plus controlled second entries on best days."""
    cfg = cfg or V56Config()
    if candidates is None or candidates.empty:
        return pd.DataFrame()
    cand = candidates[candidates["score"] >= float(cfg.min_score)].copy()
    if cand.empty:
        return cand
    cand = cand.sort_values(["date", "rank_score"], ascending=[True, False])
    selected: List[pd.Series] = []
    extras: List[pd.Series] = []
    for _, g in cand.groupby("date", sort=True):
        selected.append(g.iloc[0])
        if len(g) > 1:
            extras.append(g.iloc[1])
    if extras and cfg.extra_second_trade_days > 0:
        extra_df = pd.DataFrame(extras).sort_values("rank_score", ascending=False).head(int(cfg.extra_second_trade_days))
        selected.extend([row for _, row in extra_df.iterrows()])
    out = pd.DataFrame(selected).sort_values("idx").reset_index(drop=True)
    out["selection_policy"] = f"TOP1_PER_DAY_PLUS_TOP{cfg.extra_second_trade_days}_SECOND_SIGNALS"
    return out


def _execute_one(df: pd.DataFrame, s: pd.Series, cfg: V56Config) -> Dict[str, Any]:
    i = int(s["idx"])
    entry_i = i + 1
    sig = df.iloc[i]
    nxt = df.iloc[entry_i]
    direction = str(s["direction"])
    atr = max(float(sig.get("atr", 0.0)), float(sig["close"]) * float(cfg.min_stop_pct))
    stop_dist = max(float(cfg.stop_atr) * atr, float(sig["close"]) * float(cfg.min_stop_pct))
    entry = float(nxt["open"])
    if direction == "Long":
        sl = entry - stop_dist
        tp1 = entry + cfg.tp1_r * stop_dist
        tp2 = entry + cfg.tp2_r * stop_dist
        tp3 = entry + cfg.tp3_r * stop_dist
    else:
        sl = entry + stop_dist
        tp1 = entry - cfg.tp1_r * stop_dist
        tp2 = entry - cfg.tp2_r * stop_dist
        tp3 = entry - cfg.tp3_r * stop_dist

    remaining = 1.0
    pnl = 0.0
    reached1 = False
    reached2 = False
    close1 = float(cfg.tp1_close_pct)
    close2 = float(cfg.tp2_close_pct)
    exit_i = min(len(df) - 1, entry_i + int(cfg.max_hold_bars))
    exit_reason = "TIME_EXIT"
    exit_price = float(df.iloc[exit_i]["close"])
    tp1_hit = False
    tp2_hit = False
    tp3_hit = False

    for j in range(entry_i, min(len(df), entry_i + int(cfg.max_hold_bars) + 1)):
        b = df.iloc[j]
        high = float(b["high"])
        low = float(b["low"])
        if direction == "Long":
            stop_hit = low <= sl
            hit1 = high >= tp1
            hit2 = high >= tp2
            hit3 = high >= tp3
            # Conservative intrabar ordering: before TP1, assume SL is hit first if both occur.
            if stop_hit and not reached1:
                pnl += remaining * -1.0
                remaining = 0.0
                exit_i, exit_reason, exit_price = j, "SL", sl
                break
            if hit1 and not reached1:
                pnl += close1 * cfg.tp1_r
                remaining -= close1
                reached1 = True
                tp1_hit = True
                sl = entry  # BE after real TP1 touch
            if stop_hit and reached1:
                pnl += remaining * 0.0
                remaining = 0.0
                exit_i, exit_reason, exit_price = j, "BE_AFTER_TP1", sl
                break
            if hit2 and not reached2:
                pnl += close2 * cfg.tp2_r
                remaining -= close2
                reached2 = True
                tp2_hit = True
                sl = max(sl, entry + 0.25 * stop_dist)
            if hit3:
                pnl += remaining * cfg.tp3_r
                remaining = 0.0
                tp3_hit = True
                exit_i, exit_reason, exit_price = j, "TP3", tp3
                break
        else:
            stop_hit = high >= sl
            hit1 = low <= tp1
            hit2 = low <= tp2
            hit3 = low <= tp3
            if stop_hit and not reached1:
                pnl += remaining * -1.0
                remaining = 0.0
                exit_i, exit_reason, exit_price = j, "SL", sl
                break
            if hit1 and not reached1:
                pnl += close1 * cfg.tp1_r
                remaining -= close1
                reached1 = True
                tp1_hit = True
                sl = entry
            if stop_hit and reached1:
                pnl += remaining * 0.0
                remaining = 0.0
                exit_i, exit_reason, exit_price = j, "BE_AFTER_TP1", sl
                break
            if hit2 and not reached2:
                pnl += close2 * cfg.tp2_r
                remaining -= close2
                reached2 = True
                tp2_hit = True
                sl = min(sl, entry - 0.25 * stop_dist)
            if hit3:
                pnl += remaining * cfg.tp3_r
                remaining = 0.0
                tp3_hit = True
                exit_i, exit_reason, exit_price = j, "TP3", tp3
                break
    if remaining > 0:
        final = float(df.iloc[exit_i]["close"])
        rr = (final - entry) / stop_dist if direction == "Long" else (entry - final) / stop_dist
        pnl += remaining * max(-1.0, min(float(cfg.tp3_r), rr))
        exit_price = final
    raw_pnl = pnl
    pnl -= float(cfg.fee_r) + float(cfg.slippage_r)

    rec = dict(s.to_dict())
    rec.update(
        {
            "opened_at": df.iloc[entry_i]["datetime"],
            "closed_at": df.iloc[exit_i]["datetime"],
            "entry_mode": "NEXT_BAR_OPEN_REALISTIC",
            "entry": round(entry, 8),
            "sl": round(sl, 8),
            "tp1": round(tp1, 8),
            "tp2": round(tp2, 8),
            "tp3": round(tp3, 8),
            "exit_price": round(float(exit_price), 8),
            "exit_reason": exit_reason,
            "raw_pnl_r": round(float(raw_pnl), 4),
            "cost_r": round(float(cfg.fee_r + cfg.slippage_r), 4),
            "pnl_r": round(float(pnl), 4),
            "bars_held": int(exit_i - entry_i),
            "exit_i": int(exit_i),
            "tp1_real_touch": bool(tp1_hit),
            "tp2_real_touch": bool(tp2_hit),
            "tp3_real_touch": bool(tp3_hit),
            "v56_production": True,
            "exit_policy": "NEXT_BAR_OPEN__REAL_HL_TOUCH__CONSERVATIVE_INTRABAR__NO_MFE_REPLAY",
        }
    )
    return rec


def execute_v56(df: pd.DataFrame, selected: pd.DataFrame, cfg: Optional[V56Config] = None) -> pd.DataFrame:
    cfg = cfg or V56Config()
    rows: List[Dict[str, Any]] = []
    last_exit = -1
    for _, s in selected.sort_values("idx").iterrows():
        i = int(s["idx"])
        if i + 1 >= len(df):
            continue
        if cfg.no_overlap and i <= last_exit:
            continue
        rec = _execute_one(df, s, cfg)
        rows.append(rec)
        if cfg.no_overlap:
            last_exit = int(rec["exit_i"])
    return pd.DataFrame(rows)


def summarize_v56(trades: pd.DataFrame) -> Dict[str, Any]:
    if trades is None or trades.empty or "pnl_r" not in trades.columns:
        return {"trades": 0, "win_rate": 0.0, "pf": 0.0, "pnl": 0.0, "avg_r": 0.0, "max_dd_r": 0.0}
    pnl = pd.to_numeric(trades["pnl_r"], errors="coerce").fillna(0.0)
    wins = float(pnl[pnl > 0].sum())
    losses = abs(float(pnl[pnl < 0].sum()))
    pf = wins / losses if losses > 0 else (999.0 if wins > 0 else 0.0)
    eq = pnl.cumsum()
    dd = eq - eq.cummax()
    return {
        "trades": int(len(pnl)),
        "win_rate": round(float((pnl > 0).mean()), 4),
        "pf": round(float(pf), 4),
        "pnl": round(float(pnl.sum()), 4),
        "avg_r": round(float(pnl.mean()), 5),
        "max_dd_r": round(float(dd.min()), 4),
        "max_win_r": round(float(pnl.max()), 4),
        "max_loss_r": round(float(pnl.min()), 4),
        "tp1_touch_rate": round(float(trades.get("tp1_real_touch", pd.Series(False, index=trades.index)).astype(bool).mean()), 4),
        "micro_profit_frequency_lt_0p2r": round(float(((pnl > 0) & (pnl < 0.2)).mean()), 4),
        "micro_loss_frequency_gt_minus_0p2r": round(float(((pnl < 0) & (pnl > -0.2)).mean()), 4),
    }


def temporal_report(trades: pd.DataFrame, slices: int = 4) -> Dict[str, Any]:
    if trades is None or trades.empty:
        return {"slices": []}
    df = trades.copy()
    df["opened_at"] = pd.to_datetime(df["opened_at"], errors="coerce")
    df = df.sort_values("opened_at")
    chunks = np.array_split(df.reset_index(drop=True), max(1, int(slices)))
    return {"slices": [summarize_v56(c) for c in chunks if len(c) > 0]}


def signal_entropy(candidates: pd.DataFrame) -> Dict[str, Any]:
    if candidates is None or candidates.empty:
        return {"status": "EMPTY"}
    counts = candidates["setup_type"].value_counts()
    p = counts / counts.sum()
    entropy = -float((p * np.log2(p + 1e-12)).sum())
    max_share = float(p.max())
    return {
        "candidate_count": int(len(candidates)),
        "setup_counts": {str(k): int(v) for k, v in counts.items()},
        "entropy_bits": round(entropy, 4),
        "max_pattern_share": round(max_share, 4),
        "dominance_warning": bool(max_share > 0.50),
    }


def compression_test(trades: pd.DataFrame, extra_slippage_r: float = 0.01, tp_decay: float = 0.05, delay_loss_every_n: int = 7, delay_loss_r: float = 0.02) -> Dict[str, Any]:
    if trades is None or trades.empty:
        return summarize_v56(trades)
    df = trades.copy()
    pnl = pd.to_numeric(df["pnl_r"], errors="coerce").fillna(0.0)
    pnl = pnl - float(extra_slippage_r)
    pnl = pd.Series(np.where(pnl > 0, pnl * (1.0 - float(tp_decay)), pnl), index=df.index)
    if delay_loss_every_n > 0:
        mask = (np.arange(len(pnl)) % int(delay_loss_every_n)) == 0
        pnl.loc[mask] = pnl.loc[mask] - float(delay_loss_r)
    df["pnl_r"] = pnl
    return summarize_v56(df)


def target_gap(summary: Dict[str, Any], cfg: V56Config) -> Dict[str, Any]:
    return {
        "trade_count_ok": cfg.annual_trade_target_min <= summary.get("trades", 0) <= cfg.annual_trade_target_max,
        "win_rate_ok": cfg.target_win_rate_min <= summary.get("win_rate", 0.0) <= cfg.target_win_rate_max,
        "pf_ok": cfg.target_pf_min <= summary.get("pf", 0.0) <= cfg.target_pf_max,
        "avg_r_ok": summary.get("avg_r", 0.0) >= cfg.target_avg_r_min,
        "total_r_ok": summary.get("pnl", 0.0) >= cfg.target_total_r_min,
        "note": "Targets are reported, not forced. V56 does not use future outcome labels, MFE replay, or micro-profit caps to satisfy target metrics.",
    }


def run_v56_production_backtest(exec_csv: Any, output_dir: Optional[Any] = None, config: Optional[V56Config] = None) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    cfg = config or V56Config()
    df = add_v56_indicators(load_ohlcv(exec_csv))
    candidates = generate_v56_candidates(df, cfg)
    selected = select_v56_portfolio(candidates, cfg)
    trades = execute_v56(df, selected, cfg)
    summary = summarize_v56(trades)
    report = {
        "version": "V56_PRODUCTION_ARCHITECTURE_20260623",
        "config": asdict(cfg),
        "data": {
            "bars": int(len(df)),
            "start": str(df["datetime"].min()),
            "end": str(df["datetime"].max()),
        },
        "candidate_summary": {
            "candidates": int(len(candidates)),
            "selected_before_overlap_guard": int(len(selected)),
            "signal_density": round(float(len(candidates) / max(1, len(df))), 5),
        },
        "signal_entropy": signal_entropy(candidates),
        "overall": summary,
        "temporal_stability": temporal_report(trades, 4),
        "compression": compression_test(trades),
        "target_gap": target_gap(summary, cfg),
    }
    if output_dir is not None:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        candidates.to_csv(out / "v56_candidates.csv", index=False)
        selected.to_csv(out / "v56_selected_signals.csv", index=False)
        trades.to_csv(out / "backtest_v56_production.csv", index=False)
        (out / "V56_PRODUCTION_REPORT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return trades, report
