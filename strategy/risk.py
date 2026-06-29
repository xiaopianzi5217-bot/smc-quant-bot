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

    # ==================== 信号强度自适应 TP/SL ====================
    # 从 exec_ctx 读取信号分层信息（由上游评分引擎传入）
    
    signal_tier = str(exec_ctx.get("signal_tier", "C")) if exec_ctx else "C"
    final_score = _safe_float(exec_ctx.get("score_raw", 0)) if exec_ctx else 0

    # 信号越强，止盈越远，止损适当放宽让趋势有更多空间
    # 【修复20260625】将止损乘数整体放宽 30-40%，止盈乘数提升 20-30%
    # 解决 15M 级别止损过紧被随机噪声扫掉、止盈不足赔率低的问题
    if signal_tier == "A+":
        tp1_mult = 2.30
        tp2_mult = 3.80
        tp3_mult = 6.00
        sl_loose = 2.20
        sl_tight = None
    elif signal_tier == "A":
        tp1_mult = 1.90
        tp2_mult = 3.20
        tp3_mult = 5.00
        sl_loose = 1.80
        sl_tight = None
    elif signal_tier == "B":
        tp1_mult = 1.55
        tp2_mult = 2.60
        tp3_mult = 3.80
        sl_loose = 1.50
        sl_tight = 4.00
    else:  # C 信号保守参数
        tp1_mult = 1.60
        tp2_mult = 2.80
        tp3_mult = 3.80
        sl_loose = 1.00
        sl_tight = 3.00

    # 允许 sym_strategy 覆盖默认
    min_stop_atr = float(sym_strategy.get("min_stop_atr", sl_loose))
    max_stop_atr = float(sym_strategy.get("max_stop_atr", sl_tight if sl_tight else 4.0))
    buffer_atr = float(sym_strategy.get("liquidity_buffer_atr", 0.35))
    tp1_atr = float(sym_strategy.get("tp1_atr", tp1_mult))
    tp2_atr = float(sym_strategy.get("tp2_atr", max(tp2_mult, min_rr)))
    tp3_atr = float(sym_strategy.get("tp3_atr", max(tp3_mult, min_rr + 1.10)))

    bsl = _safe_float(exec_ctx.get("bsl_level"), 0.0)
    ssl = _safe_float(exec_ctx.get("ssl_level"), 0.0)

    if "long" in direction_l:
        swing = _swing_low(hist_exec, 24)
        # 【修复20260701】止损候选增加 OB/FVG 保护
        stop_candidates = [entry - sl_loose * atr, vwap - 1.75 * atr]
        if swing > 0: stop_candidates.append(swing - buffer_atr * atr)
        # 多单：止损放在 bullish_ob 下方（如果存在）
        bull_ob = exec_ctx.get("bullish_ob")
        if bull_ob and isinstance(bull_ob, (list, tuple)) and len(bull_ob) >= 2:
            ob_low = min(float(bull_ob[0]), float(bull_ob[1]))
            stop_candidates.append(ob_low - 0.5 * atr)
        raw_sl = min(stop_candidates)
        risk = entry - raw_sl
        risk = max(min_stop_atr * atr, risk)
        risk = min(max_stop_atr * atr, risk)
        sl = entry - risk
        # 【修复20260701-重大】多单：止盈在上方，用 BSL (上方) + 牛OB (上方) 结构
        tp1_targets = []
        if bsl > 0: tp1_targets.append(bsl)
        bull_ob = exec_ctx.get("bullish_ob")
        if bull_ob and isinstance(bull_ob, (list, tuple)) and len(bull_ob) >= 2:
            mid = (float(bull_ob[0]) + float(bull_ob[1])) / 2.0
            if mid > entry: tp1_targets.append(mid)  # 牛OB在上方才是多单目标
        best_tp1 = max(tp1_targets) if tp1_targets else (entry + tp1_atr * atr)
        tp1 = best_tp1 if best_tp1 > entry else (entry + tp1_atr * atr)
        # 【修复20260701】确保 RR>=1.2
        _sl_dist = abs(entry - sl)
        _tp1_dist = abs(tp1 - entry)
        if _tp1_dist < _sl_dist * 1.2:
            tp1 = entry + 1.2 * _sl_dist  # RR=1.2 保底
        tp2 = entry + tp2_atr * atr
        tp3 = bsl if (bsl > 0 and bsl > tp2) else (entry + tp3_atr * atr)
    else:
        swing = _swing_high(hist_exec, 24)
        # 【修复20260701】止损候选增加 OB/FVG 保护
        stop_candidates = [entry + sl_loose * atr, vwap + 1.75 * atr]
        if swing > 0: stop_candidates.append(swing + buffer_atr * atr)
        # 空单：止损放在 bearish_ob 上方（如果存在）
        bear_ob = exec_ctx.get("bearish_ob")
        if bear_ob and isinstance(bear_ob, (list, tuple)) and len(bear_ob) >= 2:
            ob_high = max(float(bear_ob[0]), float(bear_ob[1]))
            stop_candidates.append(ob_high + 0.5 * atr)
        raw_sl = max(stop_candidates)
        risk = raw_sl - entry
        risk = max(min_stop_atr * atr, risk)
        risk = min(max_stop_atr * atr, risk)
        sl = entry + risk
        # 【修复20260701-重大】空单：止盈在下方，用 SSL (下方) + 熊OB (下方) 结构
        tp1_targets = []
        if ssl > 0: tp1_targets.append(ssl)
        bear_ob = exec_ctx.get("bearish_ob")
        if bear_ob and isinstance(bear_ob, (list, tuple)) and len(bear_ob) >= 2:
            mid = (float(bear_ob[0]) + float(bear_ob[1])) / 2.0
            if mid < entry: tp1_targets.append(mid)  # 熊OB在下方才是空单目标
        best_tp1 = max(tp1_targets) if tp1_targets else (entry - tp1_atr * atr)
        tp1 = best_tp1 if best_tp1 < entry else (entry - tp1_atr * atr)
        # 【修复20260701】确保 RR>=1.2
        _sl_dist = abs(entry - sl)
        _tp1_dist = abs(tp1 - entry)
        if _tp1_dist < _sl_dist * 1.2:
            tp1 = entry - 1.2 * _sl_dist  # RR=1.2 保底
        tp2 = entry - tp2_atr * atr
        tp3 = ssl if (ssl > 0 and ssl < tp2) else (entry - tp3_atr * atr)

    rr = abs(tp2 - entry) / max(abs(entry - sl), 1e-12)
    return float(sl), float(tp1), float(tp2), float(tp3), float(rr)
def risk_is_acceptable(entry: float, sl: float, atr: float, max_risk_atr: float = 2.5) -> bool:
    if entry <= 0 or atr <= 0: return False
    return abs(entry - sl) <= max_risk_atr * atr

def dynamic_position_risk(trade_history: list, score: float = 5.0, threshold: float = 5.0) -> float:
    base_mult = 1.0
    if score >= threshold + 3: base_mult = 1.25
    elif score <= threshold + 1: base_mult = 0.75
    if not trade_history or len(trade_history) < 3: return base_mult
    recent = trade_history[-3:]
    if all(_safe_float(t.get('pnl', 0)) < 0 for t in recent): return 0.5 * base_mult
    if any(_safe_float(t.get('pnl', 0)) > 0 for t in recent): return 1.0 * base_mult
    return base_mult

# 【进攻型提升】：分批止盈与追踪止损组合逻辑
def check_partial_close_and_trail(direction: str, current_price: float, entry_price: float, current_sl: float, tp1: float, tp2: float, stage: int = 0) -> dict:
    """
    检查是否需要触发部分平仓及移动止损。
    stage: 当前持仓状态 (0: 初始, 1: 已过TP1, 2: 已过TP2)
    返回: {"action": "HOLD"|"PARTIAL_CLOSE"|"TRAIL_ONLY", "close_pct": 0.0, "new_sl": float, "new_stage": int}
    """
    d = str(direction or "").lower()
    res = {"action": "HOLD", "close_pct": 0.0, "new_sl": current_sl, "new_stage": stage}
    if entry_price <= 0 or current_price <= 0: return res

    if "long" in d:
        if stage < 2 and current_price >= tp2:
            res["action"] = "PARTIAL_CLOSE"
            res["close_pct"] = 0.30 # TP2 平 30%
            res["new_sl"] = max(current_sl, tp1)
            res["new_stage"] = 2
        elif stage < 1 and current_price >= tp1:
            res["action"] = "PARTIAL_CLOSE"
            res["close_pct"] = 0.30 # TP1 平 30%
            res["new_sl"] = max(current_sl, entry_price * 1.002) # 推保本+手续费
            res["new_stage"] = 1
        elif stage >= 2 and current_price > entry_price:
            # TP2 之后，单纯跟随价格推止损 (可用更紧的跟踪参数)
            res["action"] = "TRAIL_ONLY"
            res["new_sl"] = max(current_sl, current_price * 0.985)
    
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
            res["new_sl"] = min(current_sl, current_price * 1.015) if current_sl > 0 else current_price * 1.015
            
    return res
