# -*- coding: utf-8 -*-
"""Dynamic risk plan: liquidity/structure stop + ATR targets + Trailing Stop + Kelly Size."""
from __future__ import annotations
from typing import Any, Dict, Tuple
import numpy as np
import pandas as pd

from utils.safe import safe_float, safe_bool, safe_str


def _last_atr(curr: Dict[str, Any], hist_exec=None) -> float:
    for key in ["ATRr_14", "atr", "ATR", "atr14", "atr_14"]:
        if key in curr:
            val = safe_float(curr.get(key), 0.0)
            if val > 0: return val
    if hist_exec is not None and hasattr(hist_exec, "columns"):
        for key in ["ATRr_14", "atr", "ATR", "atr14", "atr_14"]:
            if key in hist_exec.columns:
                s = pd.to_numeric(hist_exec[key], errors="coerce").dropna()
                if not s.empty and float(s.iloc[-1]) > 0: return float(s.iloc[-1])
    close = safe_float(curr.get("close"), 0.0)
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

    entry = safe_float(row.get("close"), 0.0)
    if entry <= 0: return 0.0, 0.0, 0.0, 0.0, 0.0
    atr = _last_atr(row, hist_exec)
    if atr <= 0: atr = entry * 0.006
    vwap = safe_float(row.get("vwap_48", row.get("VWAP", row.get("vwap", 0.0))), 0.0)
    if vwap <= 0: vwap = safe_float(row.get("ema_20", entry), entry)

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
        tp1_mult, tp2_mult, tp3_mult = 1.50, 2.20, 3.00
        sl_loose, sl_tight = 1.00, 2.00

    _hard_min_stop = 1.20
    min_stop_atr = float(sym_strategy.get("min_stop_atr", max(sl_loose, _hard_min_stop)))
    max_stop_atr = float(sym_strategy.get("max_stop_atr", sl_tight if sl_tight else 2.5))
    buffer_atr = float(sym_strategy.get("liquidity_buffer_atr", 0.35))
    tp1_atr = float(sym_strategy.get("tp1_atr", tp1_mult))
    tp2_atr = float(sym_strategy.get("tp2_atr", max(tp2_mult, min_rr)))
    tp3_atr = float(sym_strategy.get("tp3_atr", max(tp3_mult, min_rr + 1.10)))

    bsl = safe_float(exec_ctx.get("bsl_level"), 0.0)
    ssl = safe_float(exec_ctx.get("ssl_level"), 0.0)

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
        
        # 【修澶?止盈过近】不再用 BSL/OB 作为 TP1 鍊欓€変綅缃?
        # BSL/OB 是阻加支撑区，价格到了容易反转，做止盈目标不合鐞?
        # 只用 ATR 乘数 + 止损距离保底，确淇?tp1 真实可触以
        _sl_dist = abs(entry - sl)
        
        # tp1: max(ATR目标, 止损距离×1.5)，确保足够空闂?
        tp1_min_dist = max(tp1_atr * atr, _sl_dist * 1.5)
        tp1 = entry + tp1_min_dist
        
        # tp2: 基于实际止损距离，保证真瀹?RR 不虚楂?
        tp2_min_dist = max(tp2_atr * atr, _sl_dist * 2.0)
        tp2 = entry + tp2_min_dist
        
        # tp3: 更远的目鏍?
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
        
        # 【修澶?止盈过近】同上，空单不再用SSL/OB 鍋?TP1
        _sl_dist = abs(entry - sl)
        
        tp1_min_dist = max(tp1_atr * atr, _sl_dist * 1.5)
        tp1 = entry - tp1_min_dist
        
        tp2_min_dist = max(tp2_atr * atr, _sl_dist * 2.0)
        tp2 = entry - tp2_min_dist
        
        tp3_min_dist = max(tp3_atr * atr, _sl_dist * 3.0)
        tp3 = entry - tp3_min_dist

    # 【修澶嶃€慠R 基于 tp1（实际第涓€止盈位），避免虚假高 RR
    # 之前用tp2 绠?RR 导致显示高但 tp1 实际很近，止盈就被扫浜?
    rr = abs(tp1 - entry) / max(abs(entry - sl), 1e-12)
    # 同时保留 tp2 的RR 在exec_ctx 中供参考
    _rr2 = abs(tp2 - entry) / max(abs(entry - sl), 1e-12)
    
    return float(sl), float(tp1), float(tp2), float(tp3), float(rr)

# 恢复遗漏的原版风控检查函鏁?
def risk_is_acceptable(entry: float, sl: float, atr: float, max_risk_atr: float = 2.5) -> bool:
    if entry <= 0 or atr <= 0: return False
    return abs(entry - sl) <= max_risk_atr * atr

def dynamic_position_risk(trade_history: list, exec_ctx: Dict[str, Any] | None = None) -> float:
    """
    打分系统无缝对接：读取评分卡输出的position_multiplier。
    """
    exec_ctx = exec_ctx or {}
    base_mult = safe_float(exec_ctx.get("position_multiplier"), 1.0)
    
    if not trade_history or len(trade_history) < 3: 
        return base_mult
        
    recent = trade_history[-3:]
    if all(safe_float(t.get('pnl', 0)) < 0 for t in recent): 
        return max(0.05, 0.5 * base_mult)
    if any(safe_float(t.get('pnl', 0)) > 0 for t in recent): 
        return base_mult
        
    return base_mult

def check_partial_close_and_trail(
    position,
    current_price
) -> dict:
    """
    动ֹ̬ӯֹ损管理 V58.7 + V59.7 修复

    【V59.7 修复两个致命bug】
    1. TP1 部分ֹӯ被保本逻辑抢先：到达 TP1 ʱ返回 MOVE_SL 而非 PARTIAL_CLOSE
    2. 保本后 risk=0 导致所有ƽ仓信号ʧЧ（92条开仓、0条ƽ仓）

    返回:
    {
        action:
        reason:
        new_sl:
        stage:
    }

    action:
        HOLD
        MOVE_SL
        PARTIAL_CLOSE
        CLOSE_ALL
    """

    if not position:
        return {
            "action": "HOLD",
            "reason": "NO_POSITION"
        }

    # 兼容旧 key: ֧持 `side` 或 `direction`，entry/entry_price，stop_loss/current_sl
    side = position.get("side") or position.get("direction")

    entry = float(position.get("entry") or position.get("entry_price") or 0)
    sl = float(position.get("stop_loss") or position.get("current_sl") or position.get("sl") or 0)

    tp1 = float(position.get("tp1") or 0)
    tp2 = float(position.get("tp2") or 0)

    stage = int(position.get("stage", 0) or 0)

    if entry <= 0:
        return {
            "action": "HOLD",
            "reason": "INVALID_ENTRY"
        }

    # ============================
    # 计算 risk（兼容保本后 sl == entry 的情况）
    # ============================
    # 【V59.7 修复】保本后 current_sl == entry ʱ risk=0，导致所有ƽ仓信号ʧЧ。
    # 方案：优先ʹ用 initial_risk（开仓ʱ保存），其次用 ATR fallback，
    # 保֤保本后 TP1/TP2/׷踪ֹ损仍能正ȷ触发。
    atr = float(position.get("atr") or position.get("ATRr_14") or 0)
    if atr <= 0:
        atr = entry * 0.01 if entry > 0 else 1.0  # fallback: 1% entry

    initial_risk = float(position.get("initial_risk") or position.get("risk") or 0)
    risk_abs = abs(entry - sl)

    if risk_abs > 0:
        risk = risk_abs
        # 首次计算ʱ保存 initial_risk（便于保本后׷溯）
        if initial_risk <= 0:
            initial_risk = risk_abs
    elif initial_risk > 0:
        risk = initial_risk
    else:
        # 保本后无初ʼ风险记¼，用 ATR 作Ϊ合理 fallback
        risk = atr

    if risk <= 0:
        risk = atr

    # ============================
    # 计算 R（基于ʵ际 risk，保本后仍可正ȷ计算）
    # ============================
    if str(side or "").lower().startswith("long"):
        profit_r = (current_price - entry) / risk
    else:
        profit_r = (entry - current_price) / risk

    # ============================
    # 1. 首先检查Ӳֹ损（无条件，即ʹ保本后Ҳ能触发）
    # ============================
    if str(side or "").lower().startswith("long"):
        if current_price <= sl:
            _sl_reason = "TRAIL_SL" if stage >= 2 else ("BREAKEVEN_PROTECT" if stage == 1 else "STOP_LOSS")
            return {
                "action": "CLOSE_ALL",
                "reason": _sl_reason,
                "profit_r": round(profit_r, 3),
                "stage": stage
            }
    else:
        if current_price >= sl:
            _sl_reason = "TRAIL_SL" if stage >= 2 else ("BREAKEVEN_PROTECT" if stage == 1 else "STOP_LOSS")
            return {
                "action": "CLOSE_ALL",
                "reason": _sl_reason,
                "profit_r": round(profit_r, 3),
                "stage": stage
            }

    # ============================
    # 2. TP2 ȫ部退出（无条件，优先于保本/׷踪）
    # 【V59.7 修复】修复 TP2 被׷踪ֹ损抢先、或保本后 risk=0 导致 TP2 永不触发
    # ============================
    if str(side or "").lower().startswith("long"):
        hit_tp2 = (current_price >= tp2)
    else:
        hit_tp2 = (current_price <= tp2)

    if hit_tp2:
        return {
            "action": "CLOSE_ALL",
            "reason": "TP2_HIT",
            "profit_r": round(profit_r, 3),
            "stage": 4
        }

    # ============================
    # 3. TP1 部分ֹӯ（无条件，优先于保本）
    # 【V59.7 修复】修复 TP1 到达ʱ被 BREAKEVEN_PROTECT 抢先导致 PARTIAL_CLOSE 永不触发
    # ============================
    if stage < 2:
        if str(side or "").lower().startswith("long"):
            hit_tp1 = (current_price >= tp1)
        else:
            hit_tp1 = (current_price <= tp1)

        if hit_tp1:
            return {
                "action": "PARTIAL_CLOSE",
                "close_percent": 50,
                "reason": "TP1_HIT",
                "stage": 2,
                "profit_r": round(profit_r, 3)
            }

        # ============================
    # 4. ӯ利 1.2R 保本（仅 stage < 1 ʱ）
    # 原 0.8R 过早，15m 上容易在到 TP2 前被洗到保本出局
    # ============================
    if profit_r >= 1.2 and stage < 1:
        return {
            "action": "MOVE_SL",
            "new_sl": entry,
            "reason": "BREAKEVEN_PROTECT",
            "stage": 1,
            "profit_r": round(profit_r, 3)
        }

    # ============================
    # 5. TP1 后启动׷踪ֹ损（stage >= 2）
    # ============================
    if stage >= 2:
        # V59.4: 分阶段׷踪ֹ损
        # - TP1 后 (stage=2): trail_distance = 1.0R（给趋势足够空间，避免С回调被ɨ）
        # - TP2 后 (stage=3): trail_distance = 0.7R（已有两个Ŀ标利润，开ʼ收紧）
        if stage == 2:
            trail_distance = risk * 1.0
        else:
            trail_distance = risk * 0.7

        if str(side or "").lower().startswith("long"):
            new_sl = current_price - trail_distance
            if new_sl > sl:
                return {
                    "action": "MOVE_SL",
                    "new_sl": new_sl,
                    "reason": "TRAILING_STOP",
                    "stage": 3,
                    "profit_r": round(profit_r, 3)
                }
        else:
            new_sl = current_price + trail_distance
            if new_sl < sl:
                return {
                    "action": "MOVE_SL",
                    "new_sl": new_sl,
                    "reason": "TRAILING_STOP",
                    "stage": 3,
                    "profit_r": round(profit_r, 3)
                }

    return {
        "action": "HOLD",
        "reason": "WAIT",
        "profit_r": round(profit_r, 3),
        "stage": stage
    }

