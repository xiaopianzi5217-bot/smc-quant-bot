# -*- coding: utf-8 -*-
"""Dynamic risk plan: liquidity/structure stop + ATR targets + Trailing Stop + Kelly Size."""
from __future__ import annotations
from typing import Any, Dict, Tuple
import numpy as np
import pandas as pd

def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None: return default
        v = float(value)
        if np.isnan(v) or np.isinf(v): return default
        return v
    except Exception: return default

def _last_atr(curr: Dict[str, Any], hist_exec=None) -> float:
    for key in ["ATRr_14", "atr", "ATR", "atr14", "atr_14"]:
        if key in curr:
            val = _safe_float(curr.get(key), 0.0)
            if val > 0: return val
    if hist_exec is not None and hasattr(hist_exec, "columns"):
        for key in ["ATRr_14", "atr", "ATR", "atr14", "atr_14"]:
            if key in hist_exec.columns:
                s = pd.to_numeric(hist_exec[key], errors="coerce").dropna()
                if not s.empty and float(s.iloc[-1]) > 0: return float(s.iloc[-1])
    close = _safe_float(curr.get("close"), 0.0)
    return close * 0.006 if close > 0 else 0.0

def _swing_low(hist_exec, lookback: int = 20) -> float:
    if hist_exec is None or not hasattr(hist_exec, "columns") or hist_exec.empty: return 0.0
    if "last_swing_low" in hist_exec.columns:
        s = pd.to_numeric(hist_exec["last_swing_low"], errors="coerce").dropna()
        if not s.empty and float(s.iloc[-1]) > 0: return float(s.iloc[-1])
    return float(pd.to_numeric(hist_exec["low"].tail(lookback), errors="coerce").min())

def _swing_high(hist_exec, lookback: int = 20) -> float:
    if hist_exec is None or not hasattr(hist_exec, "columns") or hist_exec.empty: return 0.0
    if "last_swing_high" in hist_exec.columns:
        s = pd.to_numeric(hist_exec["last_swing_high"], errors="coerce").dropna()
        if not s.empty and float(s.iloc[-1]) > 0: return float(s.iloc[-1])
    return float(pd.to_numeric(hist_exec["high"].tail(lookback), errors="coerce").max())

def calculate_dynamic_tp_sl(direction: str, curr: Dict[str, Any], hist_exec=None, exec_ctx: Dict[str, Any] | None = None, min_rr: float = 1.2, sym_strategy: Dict[str, Any] | None = None, **kwargs, ) -> Tuple[float, float, float, float, float]:
    if hist_exec is None and kwargs.get("df") is not None: hist_exec = kwargs.get("df")
    direction_l = str(direction or "").lower()
    row = curr.to_dict() if hasattr(curr, "to_dict") else dict(curr or {})
    exec_ctx = exec_ctx or {}; sym_strategy = sym_strategy or {}

    entry = _safe_float(row.get("close"), 0.0)
    if entry <= 0: return 0.0, 0.0, 0.0, 0.0, 0.0
    atr = _last_atr(row, hist_exec)
    if atr <= 0: atr = entry * 0.006
    vwap = _safe_float(row.get("vwap_48", row.get("VWAP", row.get("vwap", 0.0))), 0.0)
    if vwap <= 0: vwap = _safe_float(row.get("ema_20", entry), entry)

    signal_tier = str(exec_ctx.get("signal_tier", "C"))

    if signal_tier == "A+":
        tp1_mult, tp2_mult, tp3_mult = 1.50, 2.20, 3.00
        sl_loose, sl_tight = 1.40, None
    elif signal_tier == "A":
        tp1_mult, tp2_mult, tp3_mult = 1.30, 1.80, 2.50
        sl_loose, sl_tight = 1.20, None
    elif signal_tier == "B":
        tp1_mult, tp2_mult, tp3_mult = 1.10, 1.60, 2.20
        sl_loose, sl_tight = 1.20, 2.50
    else: 
        tp1_mult, tp2_mult, tp3_mult = 1.00, 1.50, 2.00
        sl_loose, sl_tight = 1.00, 2.00

    _hard_min_stop = 1.20
    min_stop_atr = float(sym_strategy.get("min_stop_atr", max(sl_loose, _hard_min_stop)))
    max_stop_atr = float(sym_strategy.get("max_stop_atr", sl_tight if sl_tight else 2.5))
    buffer_atr = float(sym_strategy.get("liquidity_buffer_atr", 0.35))
    tp1_atr = float(sym_strategy.get("tp1_atr", tp1_mult))
    tp2_atr = float(sym_strategy.get("tp2_atr", max(tp2_mult, min_rr)))
    tp3_atr = float(sym_strategy.get("tp3_atr", max(tp3_mult, min_rr + 1.10)))

    bsl = _safe_float(exec_ctx.get("bsl_level"), 0.0)
    ssl = _safe_float(exec_ctx.get("ssl_level"), 0.0)

    if "long" in direction_l:
        swing = _swing_low(hist_exec, 24)
        stop_candidates = [entry - sl_loose * atr, vwap - 1.75 * atr]
        if swing > 0: stop_candidates.append(swing - buffer_atr * atr)
        bull_ob = exec_ctx.get("bullish_ob")
        if bull_ob and isinstance(bull_ob, (list, tuple)) and len(bull_ob) >= 2:
            ob_low = min(float(bull_ob[0]), float(bull_ob[1]))
            stop_candidates.append(ob_low - 0.5 * atr)
            
        raw_sl = min(stop_candidates)
        risk = entry - raw_sl
        risk = max(min_stop_atr * atr, min(max_stop_atr * atr, risk))
        sl = entry - risk
        
        # 【修复-止盈过近】不再用 BSL/OB 作为 TP1 候选位置
        # BSL/OB 是阻力/支撑区，价格到了容易反转，做止盈目标不合理
        # 只用 ATR 乘数 + 止损距离保底，确保 tp1 真实可触及
        _sl_dist = abs(entry - sl)
        
        # tp1: max(ATR目标, 止损距离×1.5)，确保足够空间
        tp1_min_dist = max(tp1_atr * atr, _sl_dist * 1.5)
        tp1 = entry + tp1_min_dist
        
        # tp2: 基于实际止损距离，保证真实 RR 不虚高
        tp2_min_dist = max(tp2_atr * atr, _sl_dist * 2.0)
        tp2 = entry + tp2_min_dist
        
        # tp3: 更远的目标
        tp3_min_dist = max(tp3_atr * atr, _sl_dist * 3.0)
        tp3 = entry + tp3_min_dist
        
    else:
        swing = _swing_high(hist_exec, 24)
        stop_candidates = [entry + sl_loose * atr, vwap + 1.75 * atr]
        if swing > 0: stop_candidates.append(swing + buffer_atr * atr)
        bear_ob = exec_ctx.get("bearish_ob")
        if bear_ob and isinstance(bear_ob, (list, tuple)) and len(bear_ob) >= 2:
            ob_high = max(float(bear_ob[0]), float(bear_ob[1]))
            stop_candidates.append(ob_high + 0.5 * atr)
            
        raw_sl = max(stop_candidates)
        risk = raw_sl - entry
        risk = max(min_stop_atr * atr, min(max_stop_atr * atr, risk))
        sl = entry + risk
        
        # 【修复-止盈过近】同上，空单不再用 SSL/OB 做 TP1
        _sl_dist = abs(entry - sl)
        
        tp1_min_dist = max(tp1_atr * atr, _sl_dist * 1.5)
        tp1 = entry - tp1_min_dist
        
        tp2_min_dist = max(tp2_atr * atr, _sl_dist * 2.0)
        tp2 = entry - tp2_min_dist
        
        tp3_min_dist = max(tp3_atr * atr, _sl_dist * 3.0)
        tp3 = entry - tp3_min_dist

    # 【修复】RR 基于 tp1（实际第一止盈位），避免虚假高 RR
    # 之前用 tp2 算 RR 导致显示高但 tp1 实际很近，止盈就被扫了
    rr = abs(tp1 - entry) / max(abs(entry - sl), 1e-12)
    # 同时保留 tp2 的 RR 在 exec_ctx 中供参考
    _rr2 = abs(tp2 - entry) / max(abs(entry - sl), 1e-12)
    
    return float(sl), float(tp1), float(tp2), float(tp3), float(rr)

# 恢复遗漏的原版风控检查函数
def risk_is_acceptable(entry: float, sl: float, atr: float, max_risk_atr: float = 2.5) -> bool:
    if entry <= 0 or atr <= 0: return False
    return abs(entry - sl) <= max_risk_atr * atr

def dynamic_position_risk(trade_history: list, exec_ctx: Dict[str, Any] | None = None) -> float:
    """
    打分系统无缝对接：读取评分卡输出的 position_multiplier。
    """
    exec_ctx = exec_ctx or {}
    base_mult = _safe_float(exec_ctx.get("position_multiplier"), 1.0)
    
    if not trade_history or len(trade_history) < 3: 
        return base_mult
        
    recent = trade_history[-3:]
    if all(_safe_float(t.get('pnl', 0)) < 0 for t in recent): 
        return max(0.05, 0.5 * base_mult)
    if any(_safe_float(t.get('pnl', 0)) > 0 for t in recent): 
        return base_mult
        
    return base_mult

def check_partial_close_and_trail(direction: str, current_price: float, entry_price: float, current_sl: float, tp1: float, tp2: float, atr: float = 0.0, stage: int = 0) -> dict:
    """
    动态追踪步长：1.5 倍 ATR，淘汰固定百分比追踪。
    """
    d = str(direction or "").lower()
    res = {"action": "HOLD", "close_pct": 0.0, "new_sl": current_sl, "new_stage": stage}
    if entry_price <= 0 or current_price <= 0: return res

    trail_dist = (atr * 1.5) if atr > 0 else (current_price * 0.01)

    if "long" in d:
        if stage < 2 and current_price >= tp2:
            res["action"] = "PARTIAL_CLOSE"
            res["close_pct"] = 0.30 
            res["new_sl"] = max(current_sl, tp1)
            res["new_stage"] = 2
        elif stage < 1 and current_price >= tp1:
            res["action"] = "PARTIAL_CLOSE"
            res["close_pct"] = 0.30 
            res["new_sl"] = max(current_sl, entry_price * 1.002) 
            res["new_stage"] = 1
        elif stage >= 2 and current_price > entry_price:
            res["action"] = "TRAIL_ONLY"
            res["new_sl"] = max(current_sl, current_price - trail_dist)
    
    if "short" in d:
        if stage < 2 and current_price <= tp2:
            res["action"] = "PARTIAL_CLOSE"
            res["close_pct"] = 0.30
            res["new_sl"] = min(current_sl, tp1) if current_sl > 0 else tp1
            res["new_stage"] = 2
        elif stage < 1 and current_price <= tp1:
            res["action"] = "PARTIAL_CLOSE"
            res["close_pct"] = 0.30
            res["new_sl"] = min(current_sl, entry_price * 0.998) if current_sl > 0 else entry_price * 0.998
            res["new_stage"] = 1
        elif stage >= 2 and current_price < entry_price:
            res["action"] = "TRAIL_ONLY"
            res["new_sl"] = min(current_sl, current_price + trail_dist) if current_sl > 0 else current_price + trail_dist
            
    return res