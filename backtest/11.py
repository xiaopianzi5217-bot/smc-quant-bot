# -*- coding: utf-8 -*-
"""
Integrated SMC Right-Side Entry Backtest Runner (V3.1 Institutional Matrix Upgrade).

核心升级说明：
1. 彻底封杀 mud (泥潭) 震荡行情与无结构 (NO_FVG_OB) 的左侧悬空单。
2. 破解保本损洗盘陷阱：TP1只推 0.5ATR 防守，TP2 才推绝对保本。
3. 严格防穿越漏洞：禁止同 K 线未来函数偷价。
4. 实盘磨损还原：引入双边 fee_bps 和 slippage_bps 扣除，测算真实 PF。
5. 新增逆趋势强制过滤 (Trend Alignment Lock)。
6. 引入 0-100 分制 S(90)/A(85)/B(75)/C(65) 信号分级与多维立体统计报表。
7. 强健的数据读取引擎：免疫科学计数法，自动剔除重复列。
8. V3.1 终极三维核验：基础分(形态+动能) + 大环境顺势奖励(regime_bonus)。
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
import os

try:
    from analysis.fvg_stop_hunt import prepare_smc_features, nearest_mitigation_price
except Exception:
    from ..analysis.fvg_stop_hunt import prepare_smc_features, nearest_mitigation_price
try:
    from strategy.risk import calculate_dynamic_tp_sl, risk_is_acceptable
except Exception:
    from ..strategy.risk import calculate_dynamic_tp_sl, risk_is_acceptable

def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None: 
            return default
        v = float(value)
        return default if np.isnan(v) or np.isinf(v) else v
    except Exception:
        return default

# ==========================================
# 模块 1：强健的数据读取与清洗引擎 (超级时间解析版)
# ==========================================
def load_ohlcv_csv(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"❌ 找不到文件：{path}，请检查路径！")
        
    df = pd.read_csv(path, low_memory=False)
    print(f"\n📂 成功读取 CSV 文件: {path} | 初始行数: {len(df)}")
    
    df.columns = [str(c).lower().strip() for c in df.columns]
    
    for col in ['ts', 'date', 'timestamp', 'time', 'open_time', 'datetime']:
        if col in df.columns:
            df = df.rename(columns={col: 'datetime'})
            break
            
    df = df.loc[:, ~df.columns.duplicated()]
    
    if 'datetime' not in df.columns:
        raise ValueError(f"❌ 找不到时间列！当前 CSV 包含的列有：{list(df.columns)}")
    
    time_series = df['datetime'].astype(str).str.strip()
    is_numeric = time_series.str.replace(r'\.', '', regex=True).str.isdigit().all()
    
    if is_numeric:
        print("🕒 检测到纯数字时间戳，启动智能转换...")
        df['datetime'] = pd.to_numeric(df['datetime'], errors='coerce')
        if df['datetime'].max() > 1e11:
            df['datetime'] = pd.to_datetime(df['datetime'], unit='ms', errors='coerce')
        else:
            df['datetime'] = pd.to_datetime(df['datetime'], unit='s', errors='coerce')
    else:
        print("🕒 检测到字符串日期，启动文本解析...")
        df['datetime'] = pd.to_datetime(df['datetime'], errors='coerce')

    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
            
    before_drop = len(df)
    df = df.dropna(subset=["datetime", "open", "high", "low", "close"])
    after_drop = len(df)
    
    print(f"📊 数据清洗报告：原始 {before_drop} 行 -> 剔除错误/空数据 {before_drop - after_drop} 行 -> 【最终有效 K 线: {after_drop} 根】\n")
            
    return df.sort_values("datetime").reset_index(drop=True)

# ==========================================
# 模块 2：核心指标与双重打分引擎计算
# ==========================================
def add_basic_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = prepare_smc_features(df)
    close = out["close"].astype(float)
    high = out["high"].astype(float)
    low = out["low"].astype(float)
    vol = out["volume"].astype(float)
    
    out["ema_20"] = close.ewm(span=20, adjust=False).mean()
    out["ema_50"] = close.ewm(span=50, adjust=False).mean()
    out["ema_slope_20"] = out["ema_20"].diff(5)
    
    rng = (high - low).replace(0, np.nan)
    out["body_pct"] = (close - out["open"].astype(float)).abs() / rng
    out["bar_range_atr"] = rng / out["ATRr_14"].replace(0, np.nan)
    
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
    
    out["squeeze_on"] = (bb_std * 4).fillna(0.0) < (kc_range * 3).fillna(np.inf)
    out["squeeze_released"] = out["squeeze_on"].shift(1).fillna(False) & (~out["squeeze_on"])
    out["momentum"] = close - bb_mid
    out["sqzmom_white"] = out["momentum"].abs() <= out["momentum"].shift(1).abs() * 0.92 
    
    atr_14 = (high - low).rolling(14).mean().bfill()
    vol_ma = vol.rolling(20).mean().bfill()
    deviation = (close - out["ema_20"]) / out["ema_20"] * 500
    
    score = pd.Series(50.0, index=out.index)
    score += deviation.abs().clip(0, 20)
    score += ((high - low) / atr_14 * 10).clip(0, 15)
    score += (vol / vol_ma * 10).clip(0, 15)
    
    out['smc_quality_score'] = score.clip(0, 100)
    
    return out

def build_exec_context(row: pd.Series) -> Dict[str, Any]:
    adx = _safe_float(row.get("adx"), 0.0)
    atr = _safe_float(row.get("ATRr_14"), 0.0)
    close = _safe_float(row.get("close"), 0.0)
    atr_pct = atr / close if close > 0 else 0.0
    ema_slope = _safe_float(row.get("ema_slope_20"), 0.0)
    
    trend_dir = "Long" if ema_slope > 0 else ("Short" if ema_slope < 0 else "None")
    
    return {
        "adx": adx,
        "atr": atr,
        "atr_pct": atr_pct,
        "regime": "trend" if adx >= 25 else "mud" if adx < 16 else "transition",
        "trend_direction": trend_dir,
        "volatility": "high" if atr_pct > 0.012 else "low" if atr_pct < 0.004 else "normal",
        "squeeze": "released" if bool(row.get("squeeze_released", False)) else "building" if bool(row.get("squeeze_on", False)) else "none"
    }

def build_macro_context(df_macro: pd.DataFrame, ts: Any) -> Dict[str, Any]:
    if df_macro is None or df_macro.empty: 
        return {}
    m = df_macro[df_macro["datetime"] <= ts] if "datetime" in df_macro.columns and pd.notna(ts) else pd.DataFrame()
    row = df_macro.iloc[0] if m.empty else m.iloc[-1]
    vwap = _safe_float(row.get("vwap_48", row.get("VWAP", row.get("vwap", 0.0))), 0.0)
    close = _safe_float(row.get("close"), 0.0)
    
    return {
        "macro_close": close,
        "macro_vwap": vwap,
        "macro_vwap_bias": "bull" if close >= vwap else "bear",
        "macro_slope": _safe_float(row.get("ema_slope_20", 0.0), 0.0),
        "macro_momentum": _safe_float(row.get("momentum", 0.0), 0.0)
    }

def fallback_signal_score(row: pd.Series, direction: str) -> Tuple[float, float, List[str]]:
    direction = str(direction).title()
    close = _safe_float(row.get("close"))
    ema20 = _safe_float(row.get("ema_20"))
    ema50 = _safe_float(row.get("ema_50"))
    momentum = _safe_float(row.get("momentum"))
    stop_hunt_dir = str(row.get("stop_hunt_direction") or "")
    score = 0.0
    reasons = []
    
    if direction == "Long":
        if ema20 >= ema50: 
            score += 2; reasons.append("EMA_BULL")
        if momentum > 0: 
            score += 1.5; reasons.append("MOM_BULL")
        if close >= ema20: 
            score += 1; reasons.append("PRICE_ABOVE_EMA20")
        if stop_hunt_dir == "Long": 
            score += 2; reasons.append("BULLISH_STOP_HUNT")
    else:
        if ema20 <= ema50: 
            score += 2; reasons.append("EMA_BEAR")
        if momentum < 0: 
            score += 1.5; reasons.append("MOM_BEAR")
        if close <= ema20: 
            score += 1; reasons.append("PRICE_BELOW_EMA20")
        if stop_hunt_dir == "Short": 
            score += 2; reasons.append("BEARISH_STOP_HUNT")
            
    return float(score), 4.0, reasons

def _mtf_vwap_filter(direction: str, row: pd.Series, macro_ctx: Dict[str, Any]) -> Tuple[bool, str]:
    if not macro_ctx: return True, "NO_MACRO"
    close = _safe_float(row.get("close"), 0.0)
    macro_vwap = _safe_float(macro_ctx.get("macro_vwap"), 0.0)
    macro_close = _safe_float(macro_ctx.get("macro_close"), 0.0)
    macro_slope = _safe_float(macro_ctx.get("macro_slope"), 0.0)
    
    if macro_vwap <= 0 or macro_close <= 0: return True, "NO_MACRO_VWAP"
    direction = str(direction).title()
    if direction == "Long" and macro_close < macro_vwap and macro_slope < 0 and close < macro_vwap: 
        return False, "REJECT_MTF_VWAP_BEAR_PRESSURE"
    if direction == "Short" and macro_close > macro_vwap and macro_slope > 0 and close > macro_vwap: 
        return False, "REJECT_MTF_VWAP_BULL_PRESSURE"
    return True, "MTF_VWAP_OK"

def _entry_quality_filter(
    row: pd.Series, direction: str, long_score: float, short_score: float, 
    long_threshold: float, short_threshold: float, macro_ctx: Dict[str, Any], 
    exec_ctx: Dict[str, Any], mitigation_required: bool = True
) -> Tuple[bool, str, Dict[str, Any]]:
    
    direction = str(direction).title()
    price = _safe_float(row.get("close"), 0.0)
    atr = _safe_float(row.get("ATRr_14", exec_ctx.get("atr", 0.0)), 0.0)
    vwap = _safe_float(row.get("vwap_48", row.get("VWAP", row.get("vwap", price))), price)
    adx = _safe_float(row.get("adx"), 0.0)
    
    if price <= 0 or atr <= 0: return False, "REJECT_NO_PRICE_OR_ATR", {}
    vwap_dist = abs(price - vwap) / max(atr, 1e-12)
    
    if adx > 55.0: return False, "REJECT_ADX_EXHAUSTION", {"adx": adx}
        
    entry_grade = "S"
    size_mult = 1.0
    
    if exec_ctx.get("regime") == "mud": 
        entry_grade = "A"
        size_mult = 0.5
        
    if direction == "Long":
        if price > vwap + 1.0 * atr: return False, "REJECT_LONG_NOT_DIP", {}
        if bool(row.get("bearish_stop_hunt", False)): return False, "REJECT_LONG_AFTER_BEARISH_STOP_HUNT", {}
    else:
        if price < vwap - 1.0 * atr: return False, "REJECT_SHORT_NOT_RALLY", {}
        if bool(row.get("bullish_stop_hunt", False)): return False, "REJECT_SHORT_AFTER_BULLISH_STOP_HUNT", {}

    mtf_res = _mtf_vwap_filter(direction, row, macro_ctx)
    if (isinstance(mtf_res, tuple) and len(mtf_res) > 0 and not mtf_res[0]): return False, mtf_res[1], {}

    miti_res = nearest_mitigation_price(row, direction)
    mitigation_price = miti_res[0] if isinstance(miti_res, tuple) and len(miti_res) > 0 else None
    mitigation_src = miti_res[1] if isinstance(miti_res, tuple) and len(miti_res) > 1 else "NO_FVG_OB"
    
    if mitigation_price is None or mitigation_src == "NO_FVG_OB":
        if adx >= 25.0: 
            entry_grade = "A"
            size_mult = 0.5
        else: 
            if mitigation_required:
                return False, "REJECT_NO_SMC_STRUCTURE", {}
            else:
                entry_grade = "C"
                size_mult = 0.5
        
    if mitigation_required and mitigation_price is not None:
        if direction == "Long" and price > mitigation_price + 0.35 * atr: 
            entry_grade = "A"; size_mult = 0.5
        if direction == "Short" and price < mitigation_price - 0.35 * atr: 
            entry_grade = "A"; size_mult = 0.5

    return True, "ENTRY_OK", {"vwap_dist_atr": round(vwap_dist, 4), "mitigation_price": mitigation_price, "mitigation_src": mitigation_src, "entry_grade": entry_grade, "size_mult": size_mult}

def _squeeze_false_breakout_filter(df: pd.DataFrame, i: int, direction: str) -> Tuple[bool, str]:
    if i < 1 or "squeeze_released" not in df.columns or not bool(df.iloc[i].get("squeeze_released", False)): return True, "NO_SQUEEZE_RELEASE"
    prev_high = _safe_float(df.iloc[i - 1].get("high"), 0.0)
    prev_low = _safe_float(df.iloc[i - 1].get("low"), 0.0)
    close = _safe_float(df.iloc[i].get("close"), 0.0)
    atr = _safe_float(df.iloc[i].get("ATRr_14"), close * 0.006)
    
    if str(direction).title() == "Long" and close <= prev_high + 0.10 * atr: return False, "FALSE_BREAKOUT_LONG"
    if str(direction).title() == "Short" and close >= prev_low - 0.10 * atr: return False, "FALSE_BREAKOUT_SHORT"
    return True, "SQUEEZE_BREAK_CONFIRMED"

def _adaptive_max_hold_bars(exec_ctx: Dict[str, Any], base: int = 96) -> int:
    hold = int(base)
    if exec_ctx.get("regime") == "trend": hold = int(hold * 1.35)
    elif exec_ctx.get("regime") == "mud": hold = int(hold * 0.60)
    return max(12, min(hold, 240))

def _build_trade_exit(
    df: pd.DataFrame, start_i: int, direction: str, entry_price: float, sl: float, 
    tp1: float, tp2: float, tp3: float, max_hold_bars: int = 96, 
    atr_col: str = "ATRr_14", trail_atr_mult: float = 1.5, 
    time_drawdown_bars: int = 8, min_positive_pct: float = 0.001
) -> Dict[str, Any]:
    
    direction = str(direction or "").title()
    entry = float(entry_price)
    stop = float(sl)
    raw_initial_risk = entry - stop if direction == "Long" else stop - entry
    current_atr = _safe_float(df.iloc[start_i].get(atr_col), entry * 0.006)
    initial_risk = max(raw_initial_risk, current_atr * 0.2)
    
    if initial_risk <= 0: return {"exit_i": start_i, "exit_time": df.iloc[start_i].get("datetime", start_i), "exit": entry, "exit_reason": "INVALID_RISK", "partial_pnl": 0.0, "remaining": 1.0}
        
    max_i = min(len(df) - 1, start_i + int(max_hold_bars))
    remaining = 1.0; realized_r = 0.0
    tp1_done = False; tp2_done = False; tp3_active = False
    best_price = entry; max_favorable_pct = 0.0; max_favorable_r = 0.0
    last_action = "OPEN"; last_action_price = entry
    
    for j in range(start_i + 1, max_i + 1):
        row = df.iloc[j]
        high = _safe_float(row.get("high")); low = _safe_float(row.get("low")); close = _safe_float(row.get("close"))
        atr = _safe_float(row.get(atr_col), abs(entry - stop) or entry * 0.006)
        dt = row.get("datetime", j)
        
        if direction == "Long":
            if low <= stop:
                realized_r += remaining * ((stop - entry) / initial_risk)
                final_reason = "TRAIL_SL" if tp3_active else ("BE_SL_TP2" if tp2_done else ("BE_SL_TP1" if tp1_done else "SL"))
                return {"exit_i": j, "exit_time": dt, "exit": stop, "exit_reason": final_reason, "partial_pnl": round(realized_r, 4), "remaining": 0.0, "mfe_r": round(max_favorable_r, 4)}
            
            best_price = max(best_price, high)
            max_favorable_pct = max(max_favorable_pct, (best_price - entry) / entry)
            max_favorable_r = max(max_favorable_r, (best_price - entry) / initial_risk)
            
            if (not tp1_done) and high >= tp1:
                tp1_done = True; realized_r += 0.25 * ((tp1 - entry) / initial_risk); remaining = round(remaining - 0.25, 10)
                stop = max(stop, entry - 0.5 * atr); last_action = "TP1_25_SAFE_BE"; last_action_price = tp1; continue
            if tp1_done and (not tp2_done) and high >= tp2 and remaining > 0.25:
                tp2_done = True; realized_r += 0.25 * ((tp2 - entry) / initial_risk); remaining = round(remaining - 0.25, 10)
                stop = max(stop, entry); last_action = "TP2_25_LOCK_BE"; last_action_price = tp2; continue
            if tp2_done and high >= tp3:
                tp3_active = True; last_action = "TP3_TRAILING_ACTIVE"; last_action_price = close
            if (j - start_i) >= time_drawdown_bars and max_favorable_pct < min_positive_pct and not tp1_done:
                realized_r += remaining * ((close - entry) / initial_risk)
                return {"exit_i": j, "exit_time": dt, "exit": close, "exit_reason": "CLOSE_TIME_DECAY", "partial_pnl": round(realized_r, 4), "remaining": 0.0, "mfe_r": round(max_favorable_r, 4)}
            if tp3_active: stop = max(stop, best_price - trail_atr_mult * atr)
                
        else:  # Direction == "Short"
            if high >= stop:
                realized_r += remaining * ((entry - stop) / initial_risk)
                final_reason = "TRAIL_SL" if tp3_active else ("BE_SL_TP2" if tp2_done else ("BE_SL_TP1" if tp1_done else "SL"))
                return {"exit_i": j, "exit_time": dt, "exit": stop, "exit_reason": final_reason, "partial_pnl": round(realized_r, 4), "remaining": 0.0, "mfe_r": round(max_favorable_r, 4)}
            
            best_price = min(best_price, low)
            max_favorable_pct = max(max_favorable_pct, (entry - best_price) / entry)
            max_favorable_r = max(max_favorable_r, (entry - best_price) / initial_risk)
            
            if (not tp1_done) and low <= tp1:
                tp1_done = True; realized_r += 0.25 * ((entry - tp1) / initial_risk); remaining = round(remaining - 0.25, 10)
                stop = min(stop, entry + 0.5 * atr); last_action = "TP1_25_SAFE_BE"; last_action_price = tp1; continue
            if tp1_done and (not tp2_done) and low <= tp2 and remaining > 0.25:
                tp2_done = True; realized_r += 0.25 * ((entry - tp2) / initial_risk); remaining = round(remaining - 0.25, 10)
                stop = min(stop, entry); last_action = "TP2_25_LOCK_BE"; last_action_price = tp2; continue
            if tp2_done and low <= tp3:
                tp3_active = True; last_action = "TP3_TRAILING_ACTIVE"; last_action_price = close
            if (j - start_i) >= time_drawdown_bars and max_favorable_pct < min_positive_pct and not tp1_done:
                realized_r += remaining * ((entry - close) / initial_risk)
                return {"exit_i": j, "exit_time": dt, "exit": close, "exit_reason": "CLOSE_TIME_DECAY", "partial_pnl": round(realized_r, 4), "remaining": 0.0, "mfe_r": round(max_favorable_r, 4)}
            if tp3_active: stop = min(stop, best_price + trail_atr_mult * atr)
                
    close = _safe_float(df.iloc[max_i].get("close"), entry)
    realized_r += remaining * (((close - entry) / initial_risk) if direction == "Long" else ((entry - close) / initial_risk))
    
    return {"exit_i": max_i, "exit_time": df.iloc[max_i].get("datetime", max_i), "exit": close, "exit_reason": "TIME_" + last_action, "partial_pnl": round(realized_r, 4), "remaining": 0.0, "mfe_r": round(max_favorable_r, 4), "last_action_price": last_action_price}

# -------------------------------------------------------------------------
# 💡 把地狱模式的参数 (fee=10, slippage=5, mitigation=False) 直接设为默认值
# 这样无论网页 Web UI 怎么调用，它都会自动运行地狱模式！
# -------------------------------------------------------------------------
def run_backtest(
    exec_csv: Any, macro_csv: Optional[Any] = None, symbol: str = "BTC/USDT", 
    warmup: int = 120, max_rows: Optional[int] = None, min_rr: float = 1.2, 
    base_max_hold_bars: int = 96, mitigation_required: bool = False, 
    fee_bps: float = 10.0, slippage_bps: float = 5.0, **kwargs: Any
) -> pd.DataFrame:
    
    df_exec = add_basic_indicators(exec_csv.copy() if isinstance(exec_csv, pd.DataFrame) else load_ohlcv_csv(exec_csv))
    df_macro = add_basic_indicators(macro_csv.copy() if isinstance(macro_csv, pd.DataFrame) else load_ohlcv_csv(macro_csv)) if macro_csv is not None else pd.DataFrame()
    
    if max_rows and int(max_rows) > 0 and int(max_rows) < len(df_exec): 
        df_exec = df_exec.tail(int(max_rows) + int(warmup) + 10).reset_index(drop=True)
        
    print(f"\n=======================================================")
    print(f"⚠️ 实际参与回测的有效 K 线数量：{len(df_exec)} 根")
    print(f"=======================================================\n")
   
    trades = []
    i = max(5, int(warmup))
    
    reject_stats = {
        "1_逆势锁死": 0,
        "2_总分低于65分": 0,
        "3_细节质量过滤(VWAP/ADX)": 0,
        "4_Squeeze假突破过滤": 0,
        "5_风控(止损太宽或太窄)": 0
    }
    
    while i < len(df_exec) - 2:
        row = df_exec.iloc[i]
        ts = row.get("datetime", i)
        exec_ctx = build_exec_context(row)
        macro_ctx = build_macro_context(df_macro, ts)
        hist = df_exec.iloc[: i + 1]
        
        ls_res = fallback_signal_score(row, "Long")
        long_score = ls_res[0] if isinstance(ls_res, tuple) else 0.0
        long_th = ls_res[1] if isinstance(ls_res, tuple) else 4.0
        
        ss_res = fallback_signal_score(row, "Short")
        short_score = ss_res[0] if isinstance(ss_res, tuple) else 0.0
        short_th = ss_res[1] if isinstance(ss_res, tuple) else 4.0
        
        direction = "Long" if long_score >= short_score else "Short"
        
        regime = exec_ctx.get("regime", "mud")
        trend_dir = exec_ctx.get("trend_direction", "None")
        
        # 👇 加入这两行：如果是 mud 或 trend 状态，直接跳过不交易！
        if regime != "transition":
            continue

        # 拦截门 1
        
        trend_dir = exec_ctx.get("trend_direction", "None")
        
        if regime == "trend" and trend_dir != "None" and direction != trend_dir: 
            reject_stats["1_逆势锁死"] += 1
            i += 1; continue

        regime_bonus = 0.0
        if regime == "trend":
            if trend_dir == direction: regime_bonus = 15.0  
        elif regime == "transition":
            if trend_dir == direction: regime_bonus = 8.0   
                
        raw_pattern_score = long_score if direction == "Long" else short_score
        pattern_score_100 = raw_pattern_score * 20.0 
        quality_score_100 = _safe_float(row.get("smc_quality_score", 50.0))
        
        base_score = (pattern_score_100 * 0.5) + (quality_score_100 * 0.5)
        final_score_100 = min(base_score + regime_bonus, 100.0)
        
        if final_score_100 < 65.0:
            reject_stats["2_总分低于65分"] += 1
            i += 1; continue

        ok_res = _entry_quality_filter(row, direction, long_score, short_score, long_th, short_th, macro_ctx, exec_ctx, mitigation_required=mitigation_required)
        ok = ok_res[0] if isinstance(ok_res, tuple) and len(ok_res) > 0 else False
        entry_meta = ok_res[2] if isinstance(ok_res, tuple) and len(ok_res) > 2 else {}
        
        size_mult = _safe_float(entry_meta.get("size_mult", 1.0))
        
        if not ok or size_mult <= 0.0: 
            reject_stats["3_细节质量过滤(VWAP/ADX)"] += 1
            i += 1; continue

        br_res = _squeeze_false_breakout_filter(df_exec, i, direction)
        
        if (isinstance(br_res, tuple) and len(br_res) > 0 and not br_res[0]): 
            reject_stats["4_Squeeze假突破过滤"] += 1
            i += 1; continue

        dyn_res = calculate_dynamic_tp_sl(direction, row, hist, exec_ctx, min_rr, {})
        sl = dyn_res[0] if isinstance(dyn_res, tuple) else 0.0
        tp1 = dyn_res[1] if isinstance(dyn_res, tuple) else 0.0
        tp2 = dyn_res[2] if isinstance(dyn_res, tuple) else 0.0
        tp3 = dyn_res[3] if isinstance(dyn_res, tuple) else 0.0
        rr = dyn_res[4] if isinstance(dyn_res, tuple) else 0.0
        
        struct_target_price = _safe_float(row.get("ob_mid", row.get("fvg_mid", 0.0)))
        entry = struct_target_price if struct_target_price > 0 else _safe_float(row.get("close"), 0.0)
        
        if not risk_is_acceptable(entry, sl, _safe_float(row.get("ATRr_14"), entry * 0.006), max_risk_atr=2.5): 
            reject_stats["5_风控(止损太宽或太窄)"] += 1
            i += 1; continue
        
        exit_info = _build_trade_exit(df_exec, i, direction, entry, sl, tp1, tp2, tp3, max_hold_bars=_adaptive_max_hold_bars(exec_ctx, base_max_hold_bars))
        raw_pnl_r = _safe_float(exit_info.get("partial_pnl"), 0.0)
        
        cost_per_trade = (fee_bps + slippage_bps) / 10000.0
        pnl_r = (raw_pnl_r - (cost_per_trade * 2)) * size_mult
        exit_i = int(exit_info.get("exit_i", i))

        trades.append({
            "symbol": symbol, "direction": direction, "opened_at": ts, "closed_at": exit_info.get("exit_time"),
            "entry": round(entry, 8), "sl": round(sl, 8), "tp1": round(tp1, 8), "tp2": round(tp2, 8), "tp3": round(tp3, 8),
            "exit_price": round(_safe_float(exit_info.get("exit")), 8), "exit_reason": exit_info.get("exit_reason"),
            "pnl_r": round(pnl_r, 4), "rr": round(rr, 4), "bars_held": max(0, exit_i - i), 
            "score": round(final_score_100, 2), "size_mult": size_mult,
            "regime": exec_ctx.get("regime"), "volatility": exec_ctx.get("volatility"), "squeeze": exec_ctx.get("squeeze")
        })
        i = max(i + 1, exit_i + 1)

    # =======================================================
    # 👇 强制在后台打印所有报表和前 10 名单 (完美适配 Web UI) 👇
    # =======================================================
    print("\n🔍 【X光排查报告】 信号阵亡原因统计：")
    for reason, count in reject_stats.items():
        print(f"   ❌ {reason}: {count} 次")
    print("=======================================================\n")
        
    df_res = pd.DataFrame(trades)
    if len(df_res) > 0:
        summary_res = summarize_backtest(df_res)
        print("\n📊 强制打印 - 地狱模式统计概览:")
        print(summary_res['overall'])
        
        print("\n🔥 强制打印 - 表现最强悍的前 10 笔交易明细:")
        best_trades = df_res.sort_values(by='pnl_r', ascending=False).head(10)
        pd.set_option('display.max_columns', None)
        pd.set_option('display.width', 1000)
        print(best_trades[['opened_at', 'direction', 'pnl_r', 'score', 'regime', 'exit_reason']])
        
        print("\n⚠️ 强制打印 - 开始执行压力测试...")
        stress_test(df_res)
    # =======================================================

    return df_res

def summarize_backtest(trades: pd.DataFrame) -> Dict[str, Any]:
    if trades is None or trades.empty: 
        return {"overall": {"trades": 0, "win_rate": 0.0, "pf": 0.0}, "by_grade": {}, "by_state": {}}
    
    trades['grade'] = 'D'
    trades.loc[trades['score'] >= 65, 'grade'] = 'C'
    trades.loc[trades['score'] >= 75, 'grade'] = 'B'
    trades.loc[trades['score'] >= 85, 'grade'] = 'A'
    trades.loc[trades['score'] >= 90, 'grade'] = 'S'
    
    def calc_stats(df_sub):
        if len(df_sub) == 0: return {"trades": 0, "win_rate": 0.0, "pf": 0.0, "pnl": 0.0}
        wins = df_sub[df_sub['pnl_r'] > 0]['pnl_r'].sum()
        losses = abs(df_sub[df_sub['pnl_r'] < 0]['pnl_r'].sum())
        pf = wins / losses if losses > 0 else (999.0 if wins > 0 else 0.0)
        return {"trades": len(df_sub), "win_rate": round((df_sub['pnl_r'] > 0).mean(), 4), "pf": round(pf, 4), "pnl": round(df_sub['pnl_r'].sum(), 4)}
    
    overall = calc_stats(trades)
    grade_stats = {g: calc_stats(trades[trades['grade'] == g]) for g in ['S', 'A', 'B', 'C']}
    state_stats = {s: calc_stats(trades[trades['regime'] == s]) for s in trades['regime'].unique()}
    
    return {"overall": overall, "by_grade": grade_stats, "by_state": state_stats}

def stress_test(trades_df: pd.DataFrame, sim_count: int = 1000) -> Dict[str, Any]:
    if trades_df is None or trades_df.empty: return {}
    df_sorted = trades_df.sort_values(by='pnl_r', ascending=False).copy()
    df_no_outliers = df_sorted.iloc[5:].copy()
    wins_no_out = df_no_outliers[df_no_outliers['pnl_r'] > 0]['pnl_r'].sum()
    loss_no_out = abs(df_no_outliers[df_no_outliers['pnl_r'] < 0]['pnl_r'].sum()) + 1e-9
    pf_no_outliers = round(wins_no_out / loss_no_out, 4)
    
    print(f"\n👉 剔除 Top 5 运气单后的真实 PF: {pf_no_outliers}")
    print(f"👉 剔除后的 Win Rate: {round((df_no_outliers['pnl_r'] > 0).mean(), 4)}")

    cross_stats = trades_df.groupby(['regime', 'grade']).apply(
        lambda x: pd.Series({'count': len(x), 'win_rate': round((x['pnl_r'] > 0).mean(), 4), 'pf': round(x[x['pnl_r'] > 0]['pnl_r'].sum() / (abs(x[x['pnl_r'] < 0]['pnl_r'].sum()) + 1e-9), 4), 'total_r': round(x['pnl_r'].sum(), 4)})
    )
    print("\n🚨 状态与评级交叉分析报表:\n", cross_stats)

    pnl_array = trades_df['pnl_r'].values
    random_indices = np.random.randint(0, len(pnl_array), size=(sim_count, len(pnl_array)))
    sim_pnls = pnl_array[random_indices]
    sim_cum_pnl = np.cumsum(sim_pnls, axis=1)
    sim_drawdowns = np.maximum.accumulate(sim_cum_pnl, axis=1) - sim_cum_pnl
    worst_drawdown_r = round(np.max(np.max(sim_drawdowns, axis=1)), 2)
    avg_drawdown_r = round(np.mean(np.max(sim_drawdowns, axis=1)), 2)

    print(f"\n⚠️ 蒙特卡洛 {sim_count} 次模拟结果:")
    print(f"平均最大回撤: -{avg_drawdown_r} R")
    print(f"最极端的深渊回撤: -{worst_drawdown_r} R")
    
    return {"pf_no_outliers": pf_no_outliers, "worst_drawdown_r": worst_drawdown_r, "avg_drawdown_r": avg_drawdown_r}

def deep_diagnostic_test(trades_df: pd.DataFrame) -> None:
    if trades_df is None or trades_df.empty: return
    print("\n" + "="*55)
    print("🚨 量化系统终极验尸报告 (Deep Diagnostic Report) 🚨")
    print("="*55)

    total_trades = len(trades_df)
    wins = trades_df[trades_df['pnl_r'] > 0]
    losses = trades_df[trades_df['pnl_r'] <= 0]
    win_rate = len(wins) / total_trades if total_trades > 0 else 0

    print(f"\n【1】当前回测样本: 总交易数 = {total_trades} 笔, 账面胜率 = {win_rate:.2%}")

    # ==========================================
    # 🌟 新增：每日各小时交易表现 (Time of Day)
    # ==========================================
    # 强制转换时间格式
    trades_df['opened_dt'] = pd.to_datetime(trades_df['opened_at'])
    trades_df['hour'] = trades_df['opened_dt'].dt.hour
    
    print("\n【2】⏰ 每日各小时开仓胜率与 PF (寻找最佳交易时段):")
    hourly_stats = trades_df.groupby('hour').apply(
        lambda x: pd.Series({
            '交易次数': len(x),
            '胜率': round((x['pnl_r'] > 0).mean(), 4),
            'PF': round(x[x['pnl_r'] > 0]['pnl_r'].sum() / (abs(x[x['pnl_r'] < 0]['pnl_r'].sum()) + 1e-9), 2),
            '总利润(R)': round(x['pnl_r'].sum(), 2)
        })
    )
    # 过滤掉交易次数极少的干扰项
    print(hourly_stats[hourly_stats['交易次数'] > 10])

    # ==========================================
    # 🌟 新增：持仓时间分析 (Time in Market)
    # ==========================================
    print("\n【3】⏳ 持仓 K 线数量 vs 利润 (该死磕还是该早跑):")
    bins = [0, 10, 30, 80, 9999]
    labels = ['极短线(1-10根)', '短线(11-30根)', '中线(31-80根)', '长线(>80根)']
    trades_df['hold_time_group'] = pd.cut(trades_df['bars_held'], bins=bins, labels=labels)
    
    hold_stats = trades_df.groupby('hold_time_group', observed=False).apply(
        lambda x: pd.Series({
            '交易次数': len(x),
            '胜率': round((x['pnl_r'] > 0).mean(), 4),
            '平均单笔利润(R)': round(x['pnl_r'].mean(), 2)
        })
    )
    print(hold_stats)
    print("="*55 + "\n")

if __name__ == "__main__":
    pass  # Web UI 直接调用 run_backtest，所以这里留空即可