# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Any, Dict, Tuple
import pandas as pd

def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None: return default
        x = float(value)
        if x != x: return default
        return x
    except Exception:
        return default

def _atr_from_curr(curr: Any) -> float:
    for key in ("ATRr_14", "atr", "ATR", "atr_14"):
        try:
            v = curr.get(key) if hasattr(curr, "get") else curr[key]
            x = _num(v, 0.0)
            if x > 0: return x
        except Exception: pass
    return 0.0

def _cfg(cfg: Dict[str, Any] | None) -> Dict[str, Any]:
    cfg = cfg or {}
    sf = cfg.get("strategy_filters", {}) if isinstance(cfg, dict) else {}
    v = sf.get("vwap", {}) if isinstance(sf, dict) else {}
    return {
        "enabled": bool(v.get("enabled", True)),
        "max_chase_atr": float(v.get("max_chase_atr", 1.8)),
        "reclaim_atr": float(v.get("reclaim_atr", 0.25)),
        "min_slope_atr": float(v.get("min_slope_atr", -0.10)),
        "slope_lookback": int(v.get("slope_lookback", 8)),
        "strict_when_edge_lte": float(v.get("strict_when_edge_lte", 2.0)),
    }

def add_session_vwap(df: pd.DataFrame, datetime_col: str = "datetime") -> pd.DataFrame:
    if df is None or len(df) == 0: return df
    out = df.copy()
    required = {"high", "low", "close", "volume"}
    if not required.issubset(set(out.columns)): return out

    high = pd.to_numeric(out["high"], errors="coerce")
    low = pd.to_numeric(out["low"], errors="coerce")
    close = pd.to_numeric(out["close"], errors="coerce")
    vol = pd.to_numeric(out["volume"], errors="coerce").fillna(0.0)
    typical = (high + low + close) / 3.0
    pv = typical * vol

    if datetime_col in out.columns:
        dt = pd.to_datetime(out[datetime_col], errors="coerce")
        session = dt.dt.date
        cum_pv = pv.groupby(session).cumsum()
        cum_vol = vol.groupby(session).cumsum()
    elif "timestamp" in out.columns:
        dt = pd.to_datetime(out["timestamp"], unit="ms", errors="coerce")
        session = dt.dt.date
        cum_pv = pv.groupby(session).cumsum()
        cum_vol = vol.groupby(session).cumsum()
    else:
        cum_pv = pv.cumsum()
        cum_vol = vol.cumsum()

    vwap = cum_pv / cum_vol.replace(0, pd.NA)
    out["session_vwap"] = vwap.ffill()
    out["vwap"] = out["session_vwap"]

    atr_col = "ATRr_14" if "ATRr_14" in out.columns else ("atr" if "atr" in out.columns else None)
    if atr_col:
        atr = pd.to_numeric(out[atr_col], errors="coerce").replace(0, pd.NA)
        out["vwap_dist_atr"] = (close - out["vwap"]) / atr
    else:
        out["vwap_dist_atr"] = 0.0
    return out

def enrich_exec_ctx_with_vwap(df: pd.DataFrame, exec_ctx: Dict[str, Any] | None, slope_lookback: int = 8) -> Dict[str, Any]:
    exec_ctx = exec_ctx if isinstance(exec_ctx, dict) else {}
    if df is None or len(df) == 0: return exec_ctx
    x = add_session_vwap(df)
    if x is None or "vwap" not in x.columns or len(x) == 0: return exec_ctx
    curr = x.iloc[-1]
    atr = _atr_from_curr(curr)
    vwap = _num(curr.get("vwap"), 0.0)
    close = _num(curr.get("close"), 0.0)
    dist = ((close - vwap) / atr) if atr > 0 and vwap > 0 else 0.0
    lb = max(1, int(slope_lookback))
    if len(x) > lb and atr > 0:
        prev = _num(x.iloc[-lb].get("vwap"), vwap)
        slope = (vwap - prev) / atr
    else:
        slope = 0.0
    exec_ctx["vwap"] = float(vwap)
    exec_ctx["session_vwap"] = float(vwap)
    exec_ctx["vwap_dist_atr"] = float(dist)
    exec_ctx["vwap_slope_atr"] = float(slope)
    return exec_ctx

def check_vwap_filter(direction: str, curr: Any, df: pd.DataFrame, exec_ctx: Dict[str, Any] | None = None, cfg: Dict[str, Any] | None = None) -> Tuple[bool, str, Dict[str, Any]]:
    c = _cfg(cfg)
    if not c["enabled"]: return True, "VWAP过滤关闭", {}
    if df is None or len(df) < 20: return True, "VWAP样本不足，跳过", {}

    x = add_session_vwap(df)
    if x is None or "vwap" not in x.columns: return True, "VWAP无法计算，跳过", {}

    row = x.iloc[-1]
    atr = _atr_from_curr(row if curr is None else curr)
    close = _num(row.get("close"), _num(curr.get("close") if hasattr(curr, "get") else None, 0.0))
    vwap = _num(row.get("vwap"), 0.0)
    if atr <= 0 or close <= 0 or vwap <= 0: return True, "VWAP/ATR无效，跳过", {}

    dist = (close - vwap) / atr
    lb = max(1, int(c["slope_lookback"]))
    if len(x) > lb:
        prev_vwap = _num(x.iloc[-lb].get("vwap"), vwap)
        slope = (vwap - prev_vwap) / atr
    else:
        slope = 0.0

    if isinstance(exec_ctx, dict):
        exec_ctx["vwap"] = float(vwap)
        exec_ctx["session_vwap"] = float(vwap)
        exec_ctx["vwap_dist_atr"] = float(dist)
        exec_ctx["vwap_slope_atr"] = float(slope)

    direction = str(direction or "").lower()
    long_score = _num((exec_ctx or {}).get("long_score"), _num((exec_ctx or {}).get("score_long"), 0.0))
    short_score = _num((exec_ctx or {}).get("short_score"), _num((exec_ctx or {}).get("score_short"), 0.0))
    edge = abs(long_score - short_score) if (long_score or short_score) else 999.0

    metrics = {
        "vwap": float(vwap),
        "vwap_dist_atr": float(dist),
        "vwap_slope_atr": float(slope),
        "vwap_edge": float(edge),
    }

    regime = (exec_ctx or {}).get("regime", "unknown")
    dyn_multiplier = 2.5 if regime == "trend" else 1.0
    dyn_max_chase = c["max_chase_atr"] * dyn_multiplier

    if "long" in direction:
        if dist > dyn_max_chase: return False, f"VWAP过滤：多单离VWAP过远，疑似追高 {dist:.2f} ATR > {dyn_max_chase:.2f}", metrics
        if dist < -c["reclaim_atr"]: return False, f"VWAP过滤：多单仍在VWAP下方 {dist:.2f} ATR，未重新站回机构均价", metrics
        if slope < c["min_slope_atr"]: return False, f"VWAP过滤：VWAP斜率反向，多单动能不足 slope={slope:.2f} ATR", metrics
        if edge <= c["strict_when_edge_lte"] and close < vwap: return False, "VWAP过滤：方向分数优势不足，多单必须站上VWAP", metrics
        return True, f"VWAP过滤通过：多单 dist={dist:.2f}ATR slope={slope:.2f}", metrics

    if "short" in direction:
        if dist < -dyn_max_chase: return False, f"VWAP过滤：空单离VWAP过远，疑似追空 {dist:.2f} ATR < -{dyn_max_chase:.2f}", metrics
        if dist > c["reclaim_atr"]: return False, f"VWAP过滤：空单仍在VWAP上方 {dist:.2f} ATR，未跌回机构均价下方", metrics
        if slope > -c["min_slope_atr"]: return False, f"VWAP过滤：VWAP斜率反向，空单动能不足 slope={slope:.2f} ATR", metrics
        if edge <= c["strict_when_edge_lte"] and close > vwap: return False, "VWAP过滤：方向分数优势不足，空单必须跌破VWAP", metrics
        return True, f"VWAP过滤通过：空单 dist={dist:.2f}ATR slope={slope:.2f}", metrics

    return True, "VWAP过滤：方向未知，跳过", metrics
