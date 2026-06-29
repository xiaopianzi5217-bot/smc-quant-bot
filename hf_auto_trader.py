# -*- coding: utf-8 -*-
"""
Hugging Face 自动交易模块
清理了二进制乱码的完整恢复版
"""
from __future__ import annotations
import os
import sys
import json
import time
import threading
import traceback
import asyncio
from pathlib import Path

# 确保根目录在 sys.path 中
_root = Path(__file__).parent.absolute()
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import pandas as pd

# ---------- 基础指标与策略模块 ----------
from indicators.basic import add_all_indicators
from strategy.smc import build_macro_context, build_exec_context
from strategy.risk import calculate_dynamic_tp_sl, check_partial_close_and_trail
from notifier.observer.funding import fetch_funding_rate_safe, normalize_swap_symbol
from notifier.telegram import send_telegram
from config import STRATEGY_PARAMS, SYMBOL_STRATEGY
from utils.symbols import load_symbol_strategy
from utils.time_utils import series_ms_to_bj

# ---------- V37 主引擎 ----------
from core.alpha_master_engine import V37MasterEngine

# ---------- 状态与特征存储 ----------
from state.position_manager import position_manager
from feature_store import feature_store

# ---------- 全局参数 ----------
MAX_DRAWDOWN_PCT = 15.0 
_peak_equity = 0.0 

# ---------- 订单追踪 ----------
from execution.order_tracker import OrderTracker, get_order_tracker

# ============================================================
# 交易配置
# ============================================================
SYMBOLS = ["BTC/USDT", "ETH/USDT"] # , "SOL/USDT"
SCAN_INTERVAL = 300 
MAX_CANDLES = 320 

# Strategy 推送阈值
MIN_EV_FOR_PUSH = 0.15 
MIN_SCORE_FOR_PUSH = 35 
MIN_SCORE_GAP = 6.0 

# ----- 止损冷却 -----
STOP_LOSS_COOLDOWN = 300
_last_stop_loss_time = {}

def _check_cooldown(symbol):
    last = _last_stop_loss_time.get(symbol, 0)
    if time.time() - last < STOP_LOSS_COOLDOWN:
        print(f"[{symbol}] cooling skip")
        return False
    return True

_OBSERVER_HISTORY: dict = {} # symbol -> {event_key: bool}
_LAST_SAFE_SEND_TIME: float = 0.0
_OBSERVER_COOL_DOWN: dict = {} # symbol -> last_push_time

_OBSERVER_ICONS = {
    "SQZMOM_WHITE": "⚪",
    "DIVERGENCE_R": "🔮",
    "SQZMOM_EF": "🌀",
    "NEAR_OB": "🧱",
    "NEAR_LIQUIDITY": "🎯",
    "LIQUIDITY_SWEEP": "🗑️",
    "CHOCH": "🔄",
    "BOS": "💥",
    "FVG": "📐",
    "CANDLE_COLOR": "🎨",
    "SQUEEZE_RELEASE": "💨",
}

_OBSERVER_TYPE_NAMES = {
    "SQZMOM_WHITE": "SQZMOM K线变白",
    "DIVERGENCE_R": "背离R",
    "SQZMOM_EF": "SQZMOM 力竭",
    "NEAR_OB": "接近主力建仓区",
    "NEAR_LIQUIDITY": "接近流动性区",
    "LIQUIDITY_SWEEP": "流动性扫单",
    "CHOCH": "市场结构转变",
    "BOS": "结构突破",
    "FVG": "价格失衡区",
    "CANDLE_COLOR": "K线变色",
    "SQUEEZE_RELEASE": "SQZMOM 挤压释放",
}
_OBSERVER_DIR_EMOJI = {"Long": "📈 多头", "Short": "📉 空头", "N/A": "⚖️ 中性"}

def safe_send(msg: str) -> str:
    global _LAST_SAFE_SEND_TIME
    now = time.time()
    if now - _LAST_SAFE_SEND_TIME < 60:
        print(f"[safe_send] 触发全局限流，跳过本次推送 (距离上次仅 {now - _LAST_SAFE_SEND_TIME:.1f}s)")
        return "RATELIMITED_GLOBAL"
    
    _LAST_SAFE_SEND_TIME = now
    try:
        print(f"[safe_send] 开始推送，消息长度: {len(msg)} 字符")
        result = send_telegram(msg)
        print(f"[safe_send] 推送完成: {result[:100] if result else 'None'}")
        return result
    except Exception as e:
        print(f"[safe_send] 推送异常: {e}")
        traceback.print_exc()
        return traceback.format_exc()

def _fetch_ticker_price(symbol: str) -> float | None:
    import requests
    for attempt in range(3):
        try:
            sym_raw = normalize_swap_symbol(symbol)
            sym = sym_raw.split("/")[0] + sym_raw.split("/")[1].split(":")[0]
            url = "https://api.bitget.com/api/v2/mix/market/candles"
            params = {"symbol": sym, "productType": "umcbl", "granularity": "1m", "limit": 1}
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code != 200:
                continue
            data = resp.json()
            if data.get("code") != "00000":
                continue
            bars = data.get("data")
            if bars and len(bars) > 0:
                return float(bars[0][4])
        except Exception:
            if attempt < 2:
                import time
                time.sleep(1)
                continue
            return None

async def fetch_ohlcv(symbol: str, timeframe: str = "15m", limit: int = 320) -> pd.DataFrame | None:
    import requests
    import urllib3
    urllib3.disable_warnings()
    
    def _do_fetch(verify_ssl=True):
        sym_raw = normalize_swap_symbol(symbol)
        sym = sym_raw.split("/")[0] + sym_raw.split("/")[1].split(":")[0]
        tf_map = {
            "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", 
            "30m": "30m", "1h": "1H", "2h": "2H", "4h": "4H", 
            "6h": "6Hutc", "12h": "12Hutc", "1d": "1Dutc", 
            "3d": "3Dutc", "1w": "1Wutc", "1M": "1Mutc",
        }
        granularity = tf_map.get(timeframe, "15m")
        url = "https://api.bitget.com/api/v2/mix/market/candles"
        params = {"symbol": sym, "productType": "umcbl", "granularity": granularity, "limit": min(limit, 500)}
        resp = requests.get(url, params=params, timeout=15, verify=verify_ssl)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if data.get("code") != "00000":
            return None
        bars = data.get("data", [])
        if not bars:
            return None
        df = pd.DataFrame(bars, columns=["timestamp", "open", "high", "low", "close", "volume", "quoteVol"])
        df = df[["timestamp", "open", "high", "low", "close", "volume"]]
        df["timestamp"] = df["timestamp"].astype("int64")
        df["open"] = df["open"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        df["close"] = df["close"].astype(float)
        df["volume"] = df["volume"].astype(float)
        df["datetime"] = series_ms_to_bj(df["timestamp"])
        return df

    import time as _time
    for attempt in range(3):
        try:
            result = _do_fetch(verify_ssl=True)
            if result is not None:
                return result
        except Exception:
            pass
        try:
            result = _do_fetch(verify_ssl=False)
            if result is not None:
                return result
        except Exception:
            pass
        if attempt < 2:
            await asyncio.sleep(1.5)
            
    print(f"[{symbol}] 3次重试均失败")
    return None

async def scan_and_decide(symbol: str) -> dict | None:
    from runner.v11_institutional_runner import make_sample_ohlcv
    
    exec_task = fetch_ohlcv(symbol, "15m", MAX_CANDLES)
    macro_task = fetch_ohlcv(symbol, "1h", MAX_CANDLES)
    exec_result, macro_result = await asyncio.gather(exec_task, macro_task)
    
    df_exec = exec_result
    if df_exec is None or len(df_exec) < 100:
        print(f"[{symbol}] 数据不足，跳过")
        return None
        
    df_macro = macro_result
    if df_macro is None or len(df_macro) < 50:
        df_macro = make_sample_ohlcv(start=102.0)
        
    df_exec = add_all_indicators(df_exec, STRATEGY_PARAMS["wvf_std_mult"])
    df_macro = add_all_indicators(df_macro, STRATEGY_PARAMS["wvf_std_mult"])
    
    macro_ctx = build_macro_context(df_macro)
    exec_ctx = build_exec_context(df_exec)
    exec_ctx["data_source"] = "hf_auto"
    
    curr = df_exec.iloc[-1]
    engine = V37MasterEngine()
    decision = engine.decide(curr, exec_ctx, macro_ctx)
    
    allow = bool(decision.get("allow", False))
    signal = decision.get("signal", {})
    expected_value = signal.get("expected_value", -1.0)
    score = signal.get("score", 0.0)
    direction = signal.get("direction", None)
    
    long_sig = engine.generate_signal(curr, "Long", exec_ctx, macro_ctx)
    short_sig = engine.generate_signal(curr, "Short", exec_ctx, macro_ctx)
    long_score = long_sig.get("score", 0.0)
    short_score = short_sig.get("score", 0.0)
    long_ev = long_sig.get("expected_value", -1.0)
    short_ev = short_sig.get("expected_value", -1.0)
    
    sym_strategy = load_symbol_strategy(symbol, SYMBOL_STRATEGY)
    min_rr = sym_strategy.get("min_rr", 2.0)
    
    sl, tp1, tp2, tp3, rr = calculate_dynamic_tp_sl(
        direction or "Long", curr, df_exec, exec_ctx, min_rr, sym_strategy
    )
    long_sl, long_tp1, long_tp2, long_tp3, long_rr = calculate_dynamic_tp_sl(
        "Long", curr, df_exec, exec_ctx, min_rr, sym_strategy
    )
    short_sl, short_tp1, short_tp2, short_tp3, short_rr = calculate_dynamic_tp_sl(
        "Short", curr, df_exec, exec_ctx, min_rr, sym_strategy
    )
    
    observer_events = _detect_observer_events(curr, exec_ctx, macro_ctx, long_score, short_score)
    
    return {
        "symbol": symbol,
        "direction": direction,
        "expected_value": round(float(expected_value), 4),
        "score": round(float(score), 2),
        "entry": float(curr["close"]),
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "rr": round(rr, 2),
        "approved": bool(allow),
        "reason": decision.get("reason", "UNKNOWN"),
        "regime": decision.get("regime", "unknown"),
        "vol_state": decision.get("vol_state", "unknown"),
        "book": decision.get("book", "NONE"),
        "size": decision.get("size", 0.0),
        "decision": decision,
        "df_exec": df_exec,
        "exec_ctx": exec_ctx,
        "macro_ctx": macro_ctx,
        "curr": curr,
        "observer_events": observer_events,
        "long_score": round(float(long_score), 2),
        "short_score": round(float(short_score), 2),
        "long_ev": round(float(long_ev), 4),
        "short_ev": round(float(short_ev), 4),
        "long_entry": float(curr["close"]),
        "long_sl": long_sl,
        "long_tp1": long_tp1,
        "long_tp2": long_tp2,
        "long_tp3": long_tp3,
        "long_rr": round(long_rr, 2),
        "short_entry": float(curr["close"]),
        "short_sl": short_sl,
        "short_tp1": short_tp1,
        "short_tp2": short_tp2,
        "short_tp3": short_tp3,
        "short_rr": round(short_rr, 2),
        "price": float(curr["close"]),
        "rsi": float(curr.get("rsi", 0)),
        "adx": float(exec_ctx.get("adx", curr.get("adx", 0))),
        "atr": float(exec_ctx.get("atr", curr.get("ATRr_14", 0))),
        "macd_hist": float(curr.get("MACDh_12_26_9", 0)),
        "volume_ratio": float(curr.get("volume_ratio", 1)),
        "candle_color": str(exec_ctx.get("curr_color", "")),
        "color_changed": bool(exec_ctx.get("color_changed", False)),
        "squeeze": str(exec_ctx.get("squeeze", "")),
        "trend_direction": str(exec_ctx.get("trend_direction", "")),
        "bsl_level": float(exec_ctx.get("bsl_level", 0)),
        "ssl_level": float(exec_ctx.get("ssl_level", 0)),
        "is_bsl_swept": bool(exec_ctx.get("is_bsl_swept", False)),
        "is_ssl_swept": bool(exec_ctx.get("is_ssl_swept", False)),
        "bullish_ob": exec_ctx.get("bullish_ob", None),
        "bearish_ob": exec_ctx.get("bearish_ob", None),
        "bullish_fvg": exec_ctx.get("bullish_fvg", None),
        "bearish_fvg": exec_ctx.get("bearish_fvg", None),
        "funding_rate": None,
    }

# ============================================================
# Observer 事件检测
# ============================================================
def _detect_observer_events(curr, exec_ctx, macro_ctx, long_score: float, short_score: float):
    events = []
    
    def _bool(val):
        if val is None:
            return False
        if isinstance(val, (bool, int, float)):
            return bool(val)
        if isinstance(val, str):
            return val.strip().lower() in ("1", "true", "yes", "y")
        return False
        
    def _float(val, default: float = 0.0) -> float:
        try:
            return float(val) if val is not None else default
        except:
            return default
            
    sqzmom_white_long = _bool(curr.get("sqzmom_white_reversal_long")) if hasattr(curr, 'get') else False
    if not sqzmom_white_long:
        sqzmom_white_long = _bool(exec_ctx.get("sqzmom_white_reversal_long", False))
        
    sqzmom_white_short = _bool(curr.get("sqzmom_white_reversal_short")) if hasattr(curr, 'get') else False
    if not sqzmom_white_short:
        sqzmom_white_short = _bool(exec_ctx.get("sqzmom_white_reversal_short", False))
        
    if sqzmom_white_long:
        events.append({"type": "SQZMOM_WHITE", "dir": "Long", "desc": "SQZMOM 白线（多头动量衰竭）", "key": "sqz_white_long"})
    if sqzmom_white_short:
        events.append({"type": "SQZMOM_WHITE", "dir": "Short", "desc": "SQZMOM 白线（空头动量衰竭）", "key": "sqz_white_short"})
        
    has_bot_div = _bool(exec_ctx.get("has_bot_div", False))
    has_top_div = _bool(exec_ctx.get("has_top_div", False))
    if hasattr(curr, 'get'):
        if not has_bot_div:
            has_bot_div = _bool(curr.get("has_bot_div", False))
        if not has_top_div:
            has_top_div = _bool(curr.get("has_top_div", False))
            
    if has_bot_div:
        events.append({"type": "DIVERGENCE_R", "dir": "Long", "desc": "底背离 R", "key": "div_bot"})
    if has_top_div:
        events.append({"type": "DIVERGENCE_R", "dir": "Short", "desc": "顶背离 R", "key": "div_top"})
        
    curr_color = str(exec_ctx.get("curr_color", ""))
    prev_color = str(exec_ctx.get("prev_color", ""))
    if curr_color and "白色" in curr_color and prev_color and ("红色" in prev_color or "蓝色" in prev_color or "绿色" in prev_color):
        events.append({"type": "SQZMOM_EF", "dir": "N/A", "desc": f"颜色 {prev_color}→{curr_color}，动量耗尽", "key": "sqz_ef"})
        
    near_bullish_ob = _bool(exec_ctx.get("near_bullish_ob", False))
    near_bearish_ob = _bool(exec_ctx.get("near_bearish_ob", False))
    
    if near_bullish_ob:
        events.append({"type": "NEAR_OB", "dir": "Long", "desc": "接近 Bullish OB", "key": "ob_bull"})
    if near_bearish_ob:
        events.append({"type": "NEAR_OB", "dir": "Short", "desc": "接近 Bearish OB", "key": "ob_bear"})
        
    is_bsl_swept = _bool(exec_ctx.get("is_bsl_swept", False))
    is_ssl_swept = _bool(exec_ctx.get("is_ssl_swept", False))
    bsl_level = _float(exec_ctx.get("bsl_level", 0))
    ssl_level = _float(exec_ctx.get("ssl_level", 0))
    
    if is_bsl_swept:
        events.append({"type": "LIQUIDITY_SWEEP", "dir": "Short", "desc": f"BSL Sweep@{bsl_level:.1f}", "key": "bsl_sweep"})
    if is_ssl_swept:
        events.append({"type": "LIQUIDITY_SWEEP", "dir": "Long", "desc": f"SSL Sweep@{ssl_level:.1f}", "key": "ssl_sweep"})
        
    swing_high = _float(exec_ctx.get("swing_high", 0))
    swing_low = _float(exec_ctx.get("swing_low", 0))
    close_price = _float(curr.get("close", exec_ctx.get("close", 0))) if hasattr(curr, 'get') else _float(exec_ctx.get("close", 0))
    
    if swing_high > 0 and close_price > swing_high:
        events.append({"type": "CHOCH", "dir": "Long", "desc": f"MSS 突破前高 {swing_high:.1f}", "key": "choch_long"})
    if swing_low > 0 and close_price < swing_low:
        events.append({"type": "CHOCH", "dir": "Short", "desc": f"MSS 破前低 {swing_low:.1f}", "key": "choch_short"})
        
    bullish_fvg = exec_ctx.get("bullish_fvg", None)
    bearish_fvg = exec_ctx.get("bearish_fvg", None)
    
    if bullish_fvg is not None:
        events.append({"type": "FVG", "dir": "Long", "desc": "多头 FVG", "key": "fvg_long"})
    if bearish_fvg is not None:
        events.append({"type": "FVG", "dir": "Short", "desc": "空头 FVG", "key": "fvg_short"})
        
    color_changed = _bool(exec_ctx.get("color_changed", False))
    if color_changed:
        events.append({"type": "CANDLE_COLOR", "dir": "Long" if ("bull" in str(curr_color).lower() or "蓝" in str(curr_color)) else "Short", "desc": f"K线变色 {curr_color}", "key": f"color_{curr_color}"})
        
    squeeze = str(exec_ctx.get("squeeze", ""))
    if squeeze.lower() in ("release", "squeeze_release", "released"):
        events.append({"type": "SQUEEZE_RELEASE", "dir": "N/A", "desc": "SQZMOM 挤压释放", "key": "sqz_release"})
        
    return events

def _new_observer_events(symbol: str, events: list) -> list:
    global _OBSERVER_HISTORY
    if symbol not in _OBSERVER_HISTORY:
        _OBSERVER_HISTORY[symbol] = {}
    last = _OBSERVER_HISTORY[symbol]
    new_events = []
    for ev in events:
        key = ev.get("key", ev.get("type", ""))
        if not last.get(key, False):
            new_events.append(ev)
        last[key] = True
    return new_events

# ============================================================
# Strategy 信号推送与去重
# ============================================================
_PROCESSED_SIGNALS: dict = {}

def _signal_id(result: dict) -> str:
    symbol = result["symbol"]
    direction = result["direction"] or "NONE"
    period = 900
    now_slot = int(time.time()) // period
    return f"{symbol}_{direction}_{now_slot}"

def _is_signal_processed(signal_id: str) -> bool:
    if signal_id in _PROCESSED_SIGNALS:
        return True
    _PROCESSED_SIGNALS[signal_id] = time.time()
    cutoff = time.time() - 14400
    stale = [k for k, v in _PROCESSED_SIGNALS.items() if v < cutoff]
    for k in stale:
        del _PROCESSED_SIGNALS[k]
    return False

def check_and_open(result: dict) -> bool:
    symbol = result["symbol"]
    direction = result["direction"]
    
    # ---- 止损冷却 ----
    if not _check_cooldown(symbol):
        print(f"[{symbol}] cooling skip")
        return False
        
    approved = result["approved"]
    if not approved or not direction:
        return False
        
    ev = result.get("expected_value", 0.0)
    score = result.get("score", 0.0)
    
    if ev < MIN_EV_FOR_PUSH:
        print(f"[{symbol}] EV={ev:.4f}<{MIN_EV_FOR_PUSH} skip")
        return False
        
    if score < MIN_SCORE_FOR_PUSH:
        print(f"[{symbol}] score={score:.1f}<{MIN_SCORE_FOR_PUSH} skip")
        return False
        
    entry = result["entry"]
    sl = result["sl"]
    tp1 = result["tp1"]
    tp2 = result["tp2"]
    tp3 = result["tp3"]
    rr = result["rr"]
    regime = result["regime"]
    book = result["book"]
    size = result["size"]
    reason = result["reason"]
    
    funding = result.get("funding_rate")
    if funding is not None and abs(funding) > 0.0005:
        if (direction == "Long" and funding > 0.0003) or (direction == "Short" and funding < -0.0003):
            print(f"[{symbol}] funding {funding:.6f} adverse for {direction}, skip")
            return False
            
    long_score = result.get("long_score", 0)
    short_score = result.get("short_score", 0)
    score_gap = abs(long_score - short_score)
    
    if direction == "Long" and long_score - short_score < MIN_SCORE_GAP:
        print(f"[{symbol}] Long({long_score:.1f}) vs Short({short_score:.1f}) gap {score_gap:.1f}<{MIN_SCORE_GAP}, skip")
        return False
    if direction == "Short" and short_score - long_score < MIN_SCORE_GAP:
        print(f"[{symbol}] Short({short_score:.1f}) vs Long({long_score:.1f}) gap {score_gap:.1f}<{MIN_SCORE_GAP}, skip")
        return False
        
    sig_id = _signal_id(result)
    if _is_signal_processed(sig_id):
        print(f"[{symbol}] signal {sig_id} already processed")
        return False
        
    if position_manager.exists(symbol):
        print(f"[{symbol}] already has position")
        return False
        
    emoji_dir = "L" if direction == "Long" else "S"
    msg = (
        f"[Strategy] {emoji_dir} {symbol}\n"
        f"ID: {sig_id}\n"
        f"Entry: {entry:.2f} SL: {sl:.2f}\n"
        f"TP1: {tp1:.2f} TP2: {tp2:.2f} TP3: {tp3:.2f}\n"
        f"RR: {rr:.2f} EV: {ev:.4f} Score: {score:.1f}\n"
        f"Regime: {regime} Book: {book}\n"
        f"Size: {size*100:.1f}%\n"
        f"Reason: {reason}"
    )
    safe_send(msg)
    
    position_manager.update(symbol, {
        "direction": direction,
        "entry": entry,
        "current_sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "stage": 0,
        "sl_hit": False,
        "last_sl_msg": "",
    })
    print(f"[{symbol}] Strategy open pushed (EV={ev:.4f}, score={score:.1f})")
    
    try:
        _adx_val = result.get("adx", 0) if result.get("adx") else (result.get("exec_ctx", {}) or {}).get("adx", 0)
        regime2 = "Trend" if _adx_val > 25 else ("Compression" if "squeeze" in str(result.get("squeeze", "")).lower() else "Range")
        
        trade_features = {
            "symbol": symbol,
            "direction": direction,
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "rr": rr,
            "ev": ev,
            "score": score,
            "regime": result.get("regime", ""),
            "regime2": regime2,
            "book": result.get("book", ""),
            "adx": float(_adx_val),
            "atr": result.get("atr", 0),
            "div_count": 0,
            "signal_age": 0,
            "mfe": 0.0,
            "mae": 0.0,
            "max_r": 0.0,
            "max_r_before_stop": 0.0,
            "exit_reason": "OPEN",
            "pnl_r": None,
            "weekday": __import__("datetime").datetime.now().weekday(),
            "hour": __import__("datetime").datetime.now().hour,
        }
        feature_store.save_trade(trade_features)
    except Exception as feat_e:
        print(f"[Feature] save trade error: {feat_e}")
        
    return True

# ============================================================
# 追踪止损与仓位管理
# ============================================================
def check_trailing(symbol: str, pos: dict, current_price: float):
    direction = pos["direction"]
    entry = pos["entry"]
    sl = pos["current_sl"]
    
    risk = abs(entry - sl)
    profit_r = 0.0
    if risk > 0:
        if direction == "Long":
            profit_r = (current_price - entry) / risk
        else:
            profit_r = (entry - current_price) / risk
            
    pos["mfe"] = max(pos.get("mfe", 0.0), profit_r)
    pos["mae"] = min(pos.get("mae", 0.0), profit_r)
    pos["max_r"] = max(pos.get("max_r", 0.0), profit_r)
    
    action_plan = check_partial_close_and_trail(
        direction=direction,
        current_price=current_price,
        entry_price=entry,
        current_sl=sl,
        tp1=pos["tp1"],
        tp2=pos["tp2"],
        stage=pos.get("stage", 0),
    )
    
    action = action_plan["action"]
    if action == "PARTIAL_CLOSE":
        close_pct = action_plan["close_pct"] * 100
        new_sl = action_plan["new_sl"]
        new_stage = action_plan["new_stage"]
        stage_label = {1: "TP1", 2: "TP2"}.get(new_stage, "TPx")
        
        msg = f"[{stage_label}] {symbol} close {close_pct:.0f}% SL->{new_sl:.2f}"
        msg_key = f"{stage_label}_{new_stage}"
        
        if pos.get("last_sl_msg") != msg_key:
            safe_send(msg)
            pos["last_sl_msg"] = msg_key
            
        try:
            profit_r2 = (new_sl - pos["entry"]) / pos["entry"]
            if pos["direction"] == "Short":
                profit_r2 = (pos["entry"] - new_sl) / pos["entry"]
            
            mfe_val = pos.get("mfe", 0)
            giveback = 0.0
            if mfe_val > 0:
                giveback = abs((mfe_val - profit_r2) / mfe_val)
                
            feature_store.save_trade({
                "symbol": symbol,
                "direction": pos["direction"],
                "exit_reason": stage_label,
                "pnl_r": profit_r2,
                "mfe": mfe_val,
                "mae": pos.get("mae", 0),
                "max_r": pos.get("max_r", 0),
                "giveback_ratio": round(giveback, 4),
            })
        except Exception:
            pass

# ============================================================
# 自动交易主循环
# ============================================================
async def main_loop():
    """
    自动交易主循环：定期扫描信号并执行
    """
    print("[hf_auto_trader] 自动信号扫描主循环已启动...")
    await asyncio.sleep(5)  # 启动缓冲
    
    while True:
        try:
            for symbol in SYMBOLS:
                print(f"[hf_auto_trader] 正在扫描 {symbol}...")
                result = await scan_and_decide(symbol)
                
                if result:
                    # 检查是否满足条件并推送/开仓
                    check_and_open(result)
                    
            # 扫描间隔休眠
            await asyncio.sleep(SCAN_INTERVAL)
            
        except Exception as e:
            print(f"[hf_auto_trader] 主循环发生异常: {e}")
            traceback.print_exc()
            await asyncio.sleep(60)  # 发生异常时等待60秒后重试