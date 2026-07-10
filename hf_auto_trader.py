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
from state.trade_journal import journal as trade_journal
from config import STRATEGY_PARAMS, SYMBOL_STRATEGY
from utils.symbols import load_symbol_strategy
from utils.time_utils import series_ms_to_bj

# ---------- V56.5 主引擎（唯一生产决策管线） ----------
from final_forge.v56_5_stable_engine import (
    V565Config,
    generate_v56_candidates,
    enrich_v565_candidates,
    select_v565_portfolio,
    execute_v565,
    add_v56_indicators,
    load_ohlcv,
)
from strategy.v565_quality_gate import v565_quality_gate
from decision.v37_gate import v37_final_gate

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

# 【修复20260704】去重与质量加强参数
SIGNAL_COOLDOWN_SECONDS = 900  # 同品种同方向 15 分钟内不再重复开单
TREND_END_PULLBACK_ATR = 3.0  # 价格离 swing_high（Short）或 swing_low（Long）超过 N 倍 ATR 则不开

def _check_cooldown(symbol):
    last = _last_stop_loss_time.get(symbol, 0)
    if time.time() - last < STOP_LOSS_COOLDOWN:
        print(f"[{symbol}] cooling skip")
        return False
    return True

_OBSERVER_HISTORY: dict = {} # symbol -> {event_key: bool}
_LAST_SAFE_SEND_TIME: float = 0.0
_OBSERVER_COOL_DOWN: dict = {} # symbol -> last_push_time

# ========== 新: Observer 事件状态跟踪（状态变化时才推送） ==========
_OBSERVER_EVENT_ACTIVE: dict = {}   # symbol -> {event_key: bool}  记录事件当前是否激活
_OBSERVER_EVENT_PUSHED: dict = {}   # symbol -> {event_key: bool}  记录事件是否已推送过（避免重复）
_OBSERVER_PERIODIC_LAST: dict = {}  # symbol -> {event_type: last_periodic_send_time}
OBSERVER_PERIODIC_INTERVAL = 1800  # 连续状态事件每 30 分钟汇总推送一次

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
        print(f"[safe_send] 全局限流 {now - _LAST_SAFE_SEND_TIME:.0f}s < 60s")
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
        
    # ===== V56.5 唯一决策管线 =====
    # 使用 V56_5_Engine 的候选-评分-选择-执行链路
    # 注意：V565Config 默认 min_score=65.0，生产环境已足够
    # 但 scan_and_decide 的 DataFreme 只有 320 bars（15m），回测引擎需要更多数据
        # 因此这里用宽松的 V56 Config
    from final_forge.v56_5_stable_engine import V565Config
    _loose_cfg = V565Config(
        min_score=55.0,  # 放低分数门槛
        allowed_hours=tuple(range(24)),  # ✅ 生产环境放开全部时间段，让评分做最终筛选
    )
  
    df_v56 = add_v56_indicators(load_ohlcv(df_exec))
    if df_v56 is None or len(df_v56) < 260:
        print(f"[{symbol}] V56 指标计算后数据不足")
        return None
    
    broad = generate_v56_candidates(df_v56, None)
    if broad is None or broad.empty:
        print(f"[{symbol}] V56 无候选信号")
        return None
    
    # 注入 exec_ctx 的 SMC 结构信息（原 V37 的 build_exec_context）
    df_exec = add_all_indicators(df_exec, STRATEGY_PARAMS["wvf_std_mult"])
    df_macro = add_all_indicators(df_macro, STRATEGY_PARAMS["wvf_std_mult"])
    macro_ctx = build_macro_context(df_macro)
    exec_ctx = build_exec_context(df_exec)
    exec_ctx["data_source"] = "hf_auto"
    
    enriched = enrich_v565_candidates(broad, _loose_cfg)
    
    # 直接交给 select_v565_portfolio（它内部已有 Quality Gate + Top-N 逻辑）
    # 无需在外部重复筛选，避免双重拦截
    selected = select_v565_portfolio(enriched, _loose_cfg)
    
    if selected is None or selected.empty:
        print(f"[{symbol}] select_v565_portfolio 选择后无信号，使用宽松Top-N模式重试")
        # 策略2：绕过 Tier 限制，允许所有通过 min_score 的信号进入
        cand2 = enriched[
            (pd.to_numeric(enriched["score"], errors="coerce") >= float(_loose_cfg.min_score))
        ].copy()
        if not cand2.empty:
            # 直接取前3名
            cand2 = cand2.sort_values("decision_score", ascending=False).head(3)
            selected = cand2
            print(f"[{symbol}] 宽松模式选中 {len(selected)} 条信号")
        else:
            print(f"[{symbol}] 宽松模式也无候选信号")
            return None
    
    trades = execute_v565(df_v56, selected, _loose_cfg)
    
    if trades is None or trades.empty:
        print(f"[{symbol}] 执行后无交易")
        return None
    
    # 取最高 score 的交易作为本次推送
    best = trades.sort_values("score", ascending=False).iloc[0]
    
    direction = best.get("direction", None)
    if not direction:
        print(f"[{symbol}] 无有效方向")
        return None
    
    # 用 exec_ctx 计算 entry quality（SMC 结构验证）
    curr = df_exec.iloc[-1]
    entry_price = float(curr["close"])
    
    # 从 best row 读取 TP/SL
    # V56.5 执行引擎的列名是 "initial_sl" 而不是 "sl"
    sl = float(best.get("initial_sl", best.get("sl", 0)))
    tp1 = float(best.get("tp1", 0))
    tp2 = float(best.get("tp2", 0))
    tp3 = float(best.get("tp3", 0))
    rr = float(best.get("estimated_rr", 0))
    score = float(best.get("score", 0))
    ev = float(best.get("model_ev", 0))
    
    print(f"[{symbol}] V56.5 选定: {direction} score={score:.1f} ev={ev:.4f} "
          f"setup={best.get('setup_type','?')} price={entry_price:.2f}")
    
    # 【修复20260705】多空评分改为使用 exec_ctx 中的独立质量评分
    _exec_lq = float(exec_ctx.get("long_quality", 0))
    _exec_sq = float(exec_ctx.get("short_quality", 0))
    _use_long_score = _exec_lq if _exec_lq > 0 else (float(score) if direction == "Long" else 0.0)
    _use_short_score = _exec_sq if _exec_sq > 0 else (0.0 if direction == "Long" else float(score))

    # 构建兼容返回格式
    return {
        "symbol": symbol,
        "direction": direction,
        "expected_value": round(ev, 4),
        "score": round(score, 2),
        "entry": entry_price,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "rr": round(rr, 2),
        "approved": True,
        "reason": f"V56.5_{best.get('setup_type','?')}_{best.get('gate_reason','PASSED')}",
        "regime": best.get("regime", "unknown"),
        "vol_state": exec_ctx.get("volatility", "unknown"),
        "book": "V56_5",
        "size": 0.05,  # 固定 5%，后续让 position_manager 调整
        "decision": {"signal": best.to_dict()},
        "df_exec": df_exec,
        "exec_ctx": exec_ctx,
        "macro_ctx": macro_ctx,
        "curr": curr,
        "observer_events": [],
        "long_score": _use_long_score,
        "short_score": _use_short_score,
        "long_ev": round(ev, 4) if direction == "Long" else 0.0,
        "short_ev": 0.0 if direction == "Long" else round(ev, 4),
        "long_entry": entry_price,
        "long_sl": sl if direction == "Long" else 0,
        "long_tp1": tp1 if direction == "Long" else 0,
        "long_tp2": tp2 if direction == "Long" else 0,
        "long_tp3": tp3 if direction == "Long" else 0,
        "long_rr": round(rr, 2) if direction == "Long" else 0,
        "short_entry": entry_price,
        "short_sl": sl if direction == "Short" else 0,
        "short_tp1": tp1 if direction == "Short" else 0,
        "short_tp2": tp2 if direction == "Short" else 0,
        "short_tp3": tp3 if direction == "Short" else 0,
        "short_rr": round(rr, 2) if direction == "Short" else 0,
        "price": entry_price,
        "rsi": float(curr.get("rsi", 0)),
        "adx": float(exec_ctx.get("adx", curr.get("adx", 0))),
        "atr": float(curr.get("ATRr_14", exec_ctx.get("atr", 0))),
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
            
    # 【修复20260705】SQZMOM 白线检测优先从 exec_ctx 读取
    sqzmom_white_long = _bool(exec_ctx.get("sqzmom_white_reversal_long", False))
    sqzmom_white_short = _bool(exec_ctx.get("sqzmom_white_reversal_short", False))
    # 兜底：如果 curr 有则覆盖
    if hasattr(curr, 'get'):
        if _bool(curr.get("sqzmom_white_reversal_long")):
            sqzmom_white_long = True
        if _bool(curr.get("sqzmom_white_reversal_short")):
            sqzmom_white_short = True
        
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
    bullish_ob = exec_ctx.get("bullish_ob", None)
    bearish_ob = exec_ctx.get("bearish_ob", None)
    
    if near_bullish_ob and bullish_ob:
        ob_high = _float(bullish_ob[0]) if isinstance(bullish_ob, (list, tuple)) and len(bullish_ob) > 0 else 0
        ob_low = _float(bullish_ob[1]) if isinstance(bullish_ob, (list, tuple)) and len(bullish_ob) > 1 else 0
        events.append({"type": "NEAR_OB", "dir": "Long", "desc": f"接近 Bullish OB ({ob_low:.1f}~{ob_high:.1f})", "key": "ob_bull"})
    elif near_bullish_ob:
        events.append({"type": "NEAR_OB", "dir": "Long", "desc": "接近 Bullish OB", "key": "ob_bull"})
    if near_bearish_ob and bearish_ob:
        ob_high = _float(bearish_ob[0]) if isinstance(bearish_ob, (list, tuple)) and len(bearish_ob) > 0 else 0
        ob_low = _float(bearish_ob[1]) if isinstance(bearish_ob, (list, tuple)) and len(bearish_ob) > 1 else 0
        events.append({"type": "NEAR_OB", "dir": "Short", "desc": f"接近 Bearish OB ({ob_low:.1f}~{ob_high:.1f})", "key": "ob_bear"})
    elif near_bearish_ob:
        events.append({"type": "NEAR_OB", "dir": "Short", "desc": "接近 Bearish OB", "key": "ob_bear"})
        
    is_bsl_swept = _bool(exec_ctx.get("is_bsl_swept", False))
    is_ssl_swept = _bool(exec_ctx.get("is_ssl_swept", False))
    bsl_level = _float(exec_ctx.get("bsl_level", 0))
    ssl_level = _float(exec_ctx.get("ssl_level", 0))
    close_price = _float(curr.get("close", exec_ctx.get("close", 0))) if hasattr(curr, 'get') else _float(exec_ctx.get("close", 0))
    atr_val = max(_float(exec_ctx.get("atr", 1)), 1e-12)
    
    if is_bsl_swept:
        events.append({"type": "LIQUIDITY_SWEEP", "dir": "Short", "desc": f"BSL Sweep@{bsl_level:.1f}", "key": "bsl_sweep"})
    elif bsl_level > 0 and close_price > 0:
        dist_atr = abs(close_price - bsl_level) / atr_val
        if dist_atr <= 0.75:
            events.append({"type": "NEAR_LIQUIDITY", "dir": "Short", "desc": f"接近 BSL@{bsl_level:.1f}，距离{dist_atr:.2f}ATR", "key": "near_bsl"})
    if is_ssl_swept:
        events.append({"type": "LIQUIDITY_SWEEP", "dir": "Long", "desc": f"SSL Sweep@{ssl_level:.1f}", "key": "ssl_sweep"})
    elif ssl_level > 0 and close_price > 0:
        dist_atr = abs(close_price - ssl_level) / atr_val
        if dist_atr <= 0.75:
            events.append({"type": "NEAR_LIQUIDITY", "dir": "Long", "desc": f"接近 SSL@{ssl_level:.1f}，距离{dist_atr:.2f}ATR", "key": "near_ssl"})
        
    swing_high = _float(exec_ctx.get("swing_high", 0))
    swing_low = _float(exec_ctx.get("swing_low", 0))
    # close_price 已在上面 LIQUIDITY 部分定义
    _cp = _float(curr.get("close", exec_ctx.get("close", 0))) if hasattr(curr, 'get') else _float(exec_ctx.get("close", 0))
    
    if swing_high > 0 and _cp > swing_high:
        events.append({"type": "CHOCH", "dir": "Long", "desc": f"MSS 突破前高 {swing_high:.1f}", "key": "choch_long"})
    if swing_low > 0 and _cp < swing_low:
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
    """状态变化去重：事件从无→有才推送（首次触发）；持续存在不再重复推送。
    连续状态事件（背离/接近side/K线变白等）每 30 分钟汇总推送一次状态摘要。
    """
    global _OBSERVER_EVENT_ACTIVE, _OBSERVER_EVENT_PUSHED, _OBSERVER_PERIODIC_LAST
    if symbol not in _OBSERVER_EVENT_ACTIVE:
        _OBSERVER_EVENT_ACTIVE[symbol] = {}
    if symbol not in _OBSERVER_EVENT_PUSHED:
        _OBSERVER_EVENT_PUSHED[symbol] = {}
    if symbol not in _OBSERVER_PERIODIC_LAST:
        _OBSERVER_PERIODIC_LAST[symbol] = {}

    active = _OBSERVER_EVENT_ACTIVE[symbol]
    pushed = _OBSERVER_EVENT_PUSHED[symbol]
    periodic_last = _OBSERVER_PERIODIC_LAST[symbol]
    now = time.time()

    # --- 连续状态类事件类型（状态持续时，只发一次，之后每30分钟汇总）---
    _CONTINUOUS_TYPES = {"DIVERGENCE_R", "NEAR_OB", "NEAR_LIQUIDITY", "CANDLE_COLOR", "SQZMOM_WHITE"}

    # 1) 构建本次扫描到的 events key 集合
    current_keys = set()
    event_map = {}  # key -> ev dict
    for ev in events:
        key = ev.get("key", ev.get("type", ""))
        current_keys.add(key)
        event_map[key] = ev

    # 2) 检测状态变化，返回应推送的新事件
    new_events = []
    for key, ev in event_map.items():
        was_active = active.get(key, False)
        ev_type = ev.get("type", "")

        if not was_active:
            # 事件从无→有：首次触发，必须推送
            new_events.append(ev)
            active[key] = True
            pushed[key] = True
            # 对于连续状态类型，记录本次推送时间为周期性汇总的起点
            if ev_type in _CONTINUOUS_TYPES:
                periodic_last[ev_type] = now
            print(f"[{symbol}] Observer 事件触发: {key} (首次)")
        else:
            # 事件持续存在（连续状态）
            # 如果之前已经推送过，不再重复推送
            if pushed.get(key, False):
                # 但对于连续状态类型，每 OBSERVER_PERIODIC_INTERVAL 秒汇总一次
                if ev_type in _CONTINUOUS_TYPES:
                    last_periodic = periodic_last.get(ev_type, 0)
                    if now - last_periodic >= OBSERVER_PERIODIC_INTERVAL:
                        # 标记为"定期汇总推送"（在调用方会生成摘要消息而非完整推送）
                        ev["is_periodic_summary"] = True
                        new_events.append(ev)
                        periodic_last[ev_type] = now
                        print(f"[{symbol}] Observer 状态持续汇总: {key} ({OBSERVER_PERIODIC_INTERVAL//60}min)")
            else:
                # 理论上不应走到这里，但以防万一
                active[key] = True
                pushed[key] = True
                new_events.append(ev)

    # 3) 检测事件从有→无：清除激活状态
    for key in list(active.keys()):
        if key not in current_keys:
            was_active = active.pop(key, False)
            pushed.pop(key, None)  # 清除推送标记，下次出现时可重新触发
            if was_active:
                print(f"[{symbol}] Observer 事件消失: {key}")

    # 4) 对于未激活的事件（不在 current_keys），确保推送标记也清除
    for key in list(pushed.keys()):
        if key not in current_keys:
            pushed.pop(key, None)

    # 5) 清理过期记录（超过 2 小时未更新的 key 删除）
    stale_active = [k for k in list(active.keys()) if k not in current_keys]
    for k in stale_active:
        active.pop(k, None)
        pushed.pop(k, None)
    stale_periodic = [k for k, v in list(periodic_last.items()) if now - v > 7200]
    for k in stale_periodic:
        periodic_last.pop(k, None)

    return new_events


# ============================================================
# Observer 推送（增强版，含完整技术数据）
# ============================================================

def _push_observer_event(
    symbol: str, ev: dict,
    long_score: float = 0, short_score: float = 0,
    long_ev: float = 0, short_ev: float = 0,
    long_entry: float = 0, long_sl: float = 0, long_tp1: float = 0, long_rr: float = 0,
    short_entry: float = 0, short_sl: float = 0, short_tp1: float = 0, short_rr: float = 0,
    v37_dir: str = "N/A",
    price: float = 0, rsi: float = 0, adx: float = 0, atr: float = 0,
    macd_hist: float = 0, volume_ratio: float = 1.0,
    candle_color: str = "", color_changed: bool = False,
    regime: str = "", vol_state: str = "", squeeze: str = "",
    trend_direction: str = "",
    bsl_level: float = 0, ssl_level: float = 0,
    is_bsl_swept: bool = False, is_ssl_swept: bool = False,
    bullish_ob=None, bearish_ob=None,
    bullish_fvg=None, bearish_fvg=None,
    funding_rate=None,
):
    """推送 Observer 指标事件 + 完整技术数据（从 V37 z_fixer 增强版移植）"""
    icons = {
        "SQZMOM_WHITE": "⚪", "DIVERGENCE_R": "🔮", "SQZMOM_EF": "🌀",
        "NEAR_OB": "🧱", "NEAR_LIQUIDITY": "🎯",
        "LIQUIDITY_SWEEP": "🗑️", "CHOCH": "🔄", "BOS": "💥",
        "FVG": "📐", "CANDLE_COLOR": "🎨", "SQUEEZE_RELEASE": "💨",
    }
    icon = icons.get(ev["type"], "📊")
    type_names = {
        "SQZMOM_WHITE": "SQZMOM K线变白", "DIVERGENCE_R": "背离R",
        "SQZMOM_EF": "SQZMOM 力竭", "NEAR_OB": "接近主力建仓区",
        "NEAR_LIQUIDITY": "接近流动性区",
        "LIQUIDITY_SWEEP": "流动性扫单", "CHOCH": "市场结构转变",
        "BOS": "结构突破", "FVG": "价格失衡区",
        "CANDLE_COLOR": "K线变色", "SQUEEZE_RELEASE": "SQZMOM 挤压释放",
    }
    type_name = type_names.get(ev["type"], ev["type"])
    dir_emoji = {"Long": "📈 多头", "Short": "📉 空头", "N/A": "⚖️ 中性"}
    dir_text = dir_emoji.get(ev.get("dir", ""), ev.get("dir", ""))

    # 操作建议
    if abs(long_score - short_score) < 8:
        suggestion = "方向分歧大（分差<8），等待关键位确认。"
    elif long_score >= short_score:
        suggestion = "偏多占优；等回踩防守区，不追高。"
    else:
        suggestion = "偏空占优；等反弹防守区，不追低。"

    vr = volume_ratio or 1.0
    vol_zone = "极度缩量" if vr < 0.35 else ("缩量" if vr < 0.65 else ("正常" if vr < 1.20 else ("温和放量" if vr < 1.80 else "明显放量")))
    rsi_zone = "超卖" if rsi < 30 else ("偏弱" if rsi < 45 else ("中性" if rsi < 55 else ("偏强" if rsi < 70 else "超买")))
    adx_zone = "弱趋势/震荡" if adx < 20 else ("趋势萌芽" if adx < 25 else ("有效趋势" if adx < 35 else "强趋势"))
    macd_dir = "偏多" if macd_hist > 0 else ("偏空" if macd_hist < 0 else "中性")
    atr_pct = atr / price * 100 if price > 0 and atr > 0 else 0
    atr_zone = "低波动" if atr_pct < 0.25 else ("正常" if atr_pct < 0.70 else ("高波动" if atr_pct < 1.20 else "极高波动"))
    regime_cn = {"TREND": "趋势", "CHOP": "震荡", "TRANSITION": "过渡", "CRISIS_RISK_OFF": "避险"}
    vol_cn = {"HIGH_VOL": "高波动", "MID_VOL": "正常", "LOW_VOL": "低波动"}
    squeeze_cn = {"release": "已释放", "building": "压缩中", "released": "已释放"}

    def _fmt_ob(ob):
        if ob is None: return "暂无"
        if isinstance(ob, (list, tuple)) and len(ob) >= 2:
            try: return f"{float(ob[0]):.2f}~{float(ob[1]):.2f}"
            except: return str(ob)
        return str(ob)
    def _fmt_fvg(fvg):
        if fvg is None: return "暂无"
        try: return f"{float(fvg):.2f}"
        except: return str(fvg)

    # ── 操作建议统一格式 ──────────────────────
    lp = long_score
    sp = short_score
    score_gap = abs(lp - sp)
    # 推断方向中文名与对应 EV
    if v37_dir == "Long":
        dir_cn_local = "多头"
        ev_local = long_ev
    elif v37_dir == "Short":
        dir_cn_local = "空头"
        ev_local = short_ev
    else:
        dir_cn_local = "多军" if lp >= sp else "空军"
        ev_local = max(long_ev, short_ev)
    if score_gap >= 15 and ((v37_dir == "Long" and lp > sp) or (v37_dir == "Short" and sp > lp)):
        suggest_text = (
            f"✅ 【建议开{v37_dir}】\n"
            f"原因：{dir_cn_local}评分 {max(lp,sp):.0f}分，EV {ev_local:+.4f}，"
            f"反向 {min(lp,sp):.0f}分，分差 {score_gap:.0f}分，AI 判断此方向可执行。\n"
            f"操作：按下方风控计划挂单，不建议追高，等价格回到入场参考附近。"
        )
    elif score_gap >= 8:
        suggest_text = (
            f"⚠️ 【偏向{dir_cn_local}，但暂不开单】\n"
            f"多头 {lp:.0f}分 vs 空头 {sp:.0f}分，接近开单门槛。\n"
            f"操作：等回踩下方防守区（SSL/买方OB），或等放量/扫止损确认后再入场，不追高。"
        )
    else:
        suggest_text = (
            f"⏸️ 【优势不明显，不开单】\n"
            f"多头 {lp:.0f}分 / 空头 {sp:.0f}分，分差 {score_gap:.0f}分，两者均不够突出。\n"
            f"操作：以观察为主，等待评分差距扩大或有流动性触发信号。"
        )

    lines = [
        f"{icon} [{type_name}] {symbol}",
        f"方向: {dir_text} | {ev['desc']}",
        "",
        "━━━ 多空博弈 ━━━",
        f"多头: {lp:.1f}分 EV:{long_ev:+.4f}  空头: {sp:.1f}分 EV:{short_ev:+.4f}",
        f"分差: {score_gap:.1f}分 | 建议: {suggestion}",
        "",
        "━━━ 行情环境 ━━━",
        f"趋势: {trend_direction or 'N/A'} | 状态: {regime_cn.get(regime, regime)}",
        f"波动: {vol_cn.get(vol_state, vol_state)} | 压缩: {squeeze_cn.get(squeeze.lower(), squeeze)}",
        f"成交量: {vr:.2f}x ({vol_zone})",
        "",
        "━━━ 指标透视 ━━━",
        f"K线: {candle_color or 'N/A'} | 变色: {'是' if color_changed else '否'}",
        f"RSI: {rsi:.1f}({rsi_zone}) ADX: {adx:.1f}({adx_zone}) MACD: {macd_hist:.4f}({macd_dir})",
        f"ATR: {atr:.2f} | {atr_pct:.2f}% ({atr_zone})",
        "",
        "━━━ 流动性/关键位 ━━━",
    ]
    if bsl_level > 0:
        bsl_dist = abs(price - bsl_level) / price * 100 if price > 0 else 0
        lines.append(f"BSL: {bsl_level:.2f}(距离{bsl_dist:.2f}%) | 已扫: {'是' if is_bsl_swept else '否'}")
    if ssl_level > 0:
        ssl_dist = abs(price - ssl_level) / price * 100 if price > 0 else 0
        lines.append(f"SSL: {ssl_level:.2f}(距离{ssl_dist:.2f}%) | 已扫: {'是' if is_ssl_swept else '否'}")
    lines.append(f"买方OB: {_fmt_ob(bullish_ob)}  卖方OB: {_fmt_ob(bearish_ob)}")
    lines.append(f"多头FVG: {_fmt_fvg(bullish_fvg)}  空头FVG: {_fmt_fvg(bearish_fvg)}")
    if funding_rate is not None:
        try:
            fr = float(funding_rate)
            lines.append(f"资金费率: {fr:.4f}%")
        except: pass

    # ── 操作建议（替代旧的简短建议） ──
    lines.append("")
    lines.append("━━━ 操作建议 ━━━")
    lines.append(suggest_text)

    ref_entry = long_entry if v37_dir == "Long" else (short_entry if v37_dir == "Short" else 0)
    ref_sl = long_sl if v37_dir == "Long" else (short_sl if v37_dir == "Short" else 0)
    ref_tp1 = long_tp1 if v37_dir == "Long" else (short_tp1 if v37_dir == "Short" else 0)
    ref_rr = long_rr if v37_dir == "Long" else (short_rr if v37_dir == "Short" else 0)
    if v37_dir in ("Long", "Short") and ref_sl and ref_sl > 0 and ref_entry > 0:
        lines.append("")
        lines.append("━━━ 开单参数 ━━━")
        lines.append(f"入场: {ref_entry:.2f} SL: {ref_sl:.2f} TP1: {ref_tp1:.2f}")
        lines.append(f"RR: {ref_rr:.2f} EV: {ev_local:+.4f} Score: {max(lp,sp):.1f}")
    elif v37_dir in ("Long", "Short"):
        lines.append("")
        lines.append("━━━ 开单参数 ━━━")
        lines.append("暂无可用入场参数")

    safe_send("\n".join(lines))
    print(f"[{symbol}] Observer 推送: {ev['type']} {ev.get('dir','')}")


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
    regime = str(result.get("regime", "unknown"))
    vol_state = str(result.get("vol_state", "unknown"))
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
    
    # ===== 【修复20260704】趋势位置检查：防止开在趋势末尾 =====
    # Short 开单检查：如果价格已经从 swing_high 下跌超过一定幅度，不开
    exec_ctx = result.get("exec_ctx", {}) or {}
    swing_high = exec_ctx.get("swing_high", 0) or 0
    swing_low = exec_ctx.get("swing_low", 0) or 0
    atr_val = result.get("atr", 0) or 1
    entry_price = entry
    
    if direction == "Short" and swing_high > 0 and swing_high > entry_price:
        drop_from_high = (swing_high - entry_price) / max(atr_val, 1)
        print(f"[{symbol}] Short: swing_high={swing_high:.1f} price={entry_price:.1f} "
              f"drop={drop_from_high:.1f}atr (limit={TREND_END_PULLBACK_ATR}atr)")
        if drop_from_high > TREND_END_PULLBACK_ATR:
            print(f"[{symbol}] 价格已从高位下跌 {drop_from_high:.1f}ATR > {TREND_END_PULLBACK_ATR}ATR，趋势末端不开Short")
            return False
    elif direction == "Long" and swing_low > 0 and swing_low < entry_price:
        rise_from_low = (entry_price - swing_low) / max(atr_val, 1)
        print(f"[{symbol}] Long: swing_low={swing_low:.1f} price={entry_price:.1f} "
              f"rise={rise_from_low:.1f}atr (limit={TREND_END_PULLBACK_ATR}atr)")
        if rise_from_low > TREND_END_PULLBACK_ATR:
            print(f"[{symbol}] 价格已从低点上涨 {rise_from_low:.1f}ATR > {TREND_END_PULLBACK_ATR}ATR，趋势末端不开Long")
            return False

    # ===== 【修复20260704】RR 硬校验：如果最终 RR < 1.0 直接拒绝 =====
    actual_rr = result.get("rr", 0) or 0
    if actual_rr < 1.0:
        print(f"[{symbol}] RR={actual_rr:.2f} < 1.0 skip")
        return False
        
    # ===== V37 Final Gate（V56.5 管线的最终闸门）=====
    _v37_decision = {
        "approved": True,
        "direction": direction,
        "reason": "V56.5_QUALITY_PASSED",
    }
    _v37_ctx = {
        "long_score": result.get("long_score", 0),
        "short_score": result.get("short_score", 0),
        "regime": result.get("regime", "unknown"),
        "vol_state": result.get("vol_state", "unknown"),
        "setup_type": str(result.get("decision", {}).get("signal", {}).get("setup_type", "V56_SIGNAL")),
        "rr": actual_rr,
        "entry": result.get("entry", 0),
        "sl": result.get("sl", 0),
        "tp1": result.get("tp1", 0),
        "tp2": result.get("tp2", 0),
        "tp3": result.get("tp3", 0),
        "score": result.get("score", 0),
        "expected_value": result.get("expected_value", 0.0),
        "atr": result.get("atr", 0),
        "funding_rate": result.get("funding_rate"),
        "symbol": symbol,
        **result.get("exec_ctx", {}),
    }
    _v37_passed, _v37_reason, _v37_size_mult = v37_final_gate(_v37_decision, _v37_ctx)
    if not _v37_passed:
        print(f"[{symbol}] V37 Gate 拦截: {_v37_reason}")
        return False
    else:
        print(f"[{symbol}] V37 Gate 通过 ({_v37_reason}), size_mult={_v37_size_mult}")

    # 获取 signal_tier 用于调试消息和日志
    _tier = None
    _decision = result.get("decision", {})
    _signal = _decision.get("signal", {})
    if _signal:
        _tier = _signal.get("signal_tier")
    
    emoji_dir = "L" if direction == "Long" else "S"
    _debug_long_vs_short = f"Lv{result.get('long_score',0):.1f} Sv{result.get('short_score',0):.1f}"
    _debug_tier = _tier or "?"
    _debug_atr_val = result.get("atr", 0)
    dir_cn = "多头" if direction == "Long" else "空头"
    dir_emoji2 = "📈" if direction == "Long" else "📉"
    regime_cn = {"TREND": "趋势", "CHOP": "震荡", "TRANSITION": "过渡", "CRISIS_RISK_OFF": "避险", "trend": "趋势", "chop": "震荡"}.get(str(regime).upper(), regime)
    vol_cn = {"HIGH_VOL": "高波动", "MID_VOL": "正常", "LOW_VOL": "低波动", "high_vol": "高波动", "mid_vol": "正常", "low_vol": "低波动"}.get(str(vol_state).upper(), vol_state)
    rsi_zone = "超买" if result.get("rsi", 50) > 70 else ("超卖" if result.get("rsi", 50) < 30 else ("偏强" if result.get("rsi", 50) > 55 else ("偏弱" if result.get("rsi", 50) < 45 else "中性")))
    _atr_val = result.get("atr", 0) or 0
    atr_pct = _atr_val / entry * 100 if entry > 0 and _atr_val > 0 else 0
    vol_ratio_str = f"{result.get('volume_ratio', 1.0):.2f}x"

    # ── 操作建议 ──
    lp_s = result.get('long_score', 0)
    sp_s = result.get('short_score', 0)
    ev_dir = result.get('long_ev', 0) if direction == 'Long' else result.get('short_ev', 0)
    sg = abs(lp_s - sp_s)
    suggest_text_strategy = (
        f"✅ 【建议开{direction}】\n"
        f"原因：{dir_cn}评分 {max(lp_s,sp_s):.0f}分，EV {ev_dir:+.4f}，"
        f"反向 {min(lp_s,sp_s):.0f}分，分差 {sg:.0f}分，AI 判断此方向可执行。\n"
        f"操作：按下方风控计划挂单，不建议追高，等价格回到入场参考附近。"
    )
    # ── 流动性/关键位 ──
    _bsl_x = result.get('bsl_level', 0)
    _ssl_x = result.get('ssl_level', 0)
    _price_x = result.get('price', 0)
    _liq_lines = []
    if _bsl_x > 0:
        _bsl_dx = abs(_price_x - _bsl_x) / _price_x * 100 if _price_x > 0 else 0
        _liq_lines.append(f"BSL: {_bsl_x:.2f}(距离{_bsl_dx:.2f}%) | 已扫: {'是' if result.get('is_bsl_swept',False) else '否'}")
    if _ssl_x > 0:
        _ssl_dx = abs(_price_x - _ssl_x) / _price_x * 100 if _price_x > 0 else 0
        _liq_lines.append(f"SSL: {_ssl_x:.2f}(距离{_ssl_dx:.2f}%) | 已扫: {'是' if result.get('is_ssl_swept',False) else '否'}")
    def _fmt_ob_x(ob): return '暂无' if ob is None else (f'{float(ob[0]):.2f}~{float(ob[1]):.2f}' if isinstance(ob, (list, tuple)) and len(ob) >= 2 else str(ob))
    def _fmt_fvg_x(fvg): return '暂无' if fvg is None else (f'{float(fvg):.2f}' if isinstance(fvg, (str, int, float)) else str(fvg))
    _liq_lines.append(f"买方OB: {_fmt_ob_x(result.get('bullish_ob'))}  卖方OB: {_fmt_ob_x(result.get('bearish_ob'))}")
    _liq_lines.append(f"多头FVG: {_fmt_fvg_x(result.get('bullish_fvg'))}  空头FVG: {_fmt_fvg_x(result.get('bearish_fvg'))}")
    _fr_x = result.get('funding_rate')
    if _fr_x is not None:
        try: _liq_lines.append(f'资金费率: {float(_fr_x):.4f}%')
        except: pass
    _liq_text_x = '\n'.join(_liq_lines) if _liq_lines else ''

    msg = (
        f"━━━ [信号单] {dir_emoji2} {dir_cn} {symbol} ━━━\n"
        f"📐 [价格失衡区] {result.get('bsl_level',0):.0f}~{result.get('ssl_level',0):.0f}\n"
        f"方向: {dir_emoji2} {dir_cn}\n"
        f"━━━ 多空博弈 ━━━\n"
        f"多头: {lp_s:.1f}分 EV:{result.get('long_ev',0):+.4f}  空头: {sp_s:.1f}分 EV:{result.get('short_ev',0):+.4f}  分差: {sg:.1f}分\n"
        f"━━━ 行情环境 ━━━\n"
        f"趋势: {regime_cn} | 波动: {vol_cn} | 成交量: {vol_ratio_str}\n"
        f"━━━ 指标透视 ━━━\n"
        f"RSI: {result.get('rsi',0):.1f}({rsi_zone}) ATR: {_atr_val:.2f} | {atr_pct:.2f}%\n"
        f"━━━ 流动性/关键位 ━━━\n"
        f"{_liq_text_x}\n"
        f"━━━ 操作建议 ━━━\n"
        f"{suggest_text_strategy}\n"
        f"━━━ 开单参数 ━━━\n"
        f"入场: {entry:.2f} SL: {sl:.2f} TP1: {tp1:.2f} TP2: {tp2:.2f} TP3: {tp3:.2f}\n"
        f"RR: {rr:.2f} EV: {ev:.4f} Score: {score:.1f}\n"
        f"书签: {book} | 仓位: {size*100:.1f}%"
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
    
    # ===== 【开单日志写入】 =====
    # 1. TradeJournal（日志审计）
    _order_id = None
    try:
        _order_id = trade_journal.open_trade(
            symbol=symbol,
            direction=direction,
            open_price=entry,
            sl=sl,
            tp1=tp1,
            tp2=tp2 if tp2 else 0,
            tp3=tp3 if tp3 else 0,
            rr=rr,
            score=score,
            regime=result.get("regime", ""),
            volume=size,
            note=f"ev={ev:.4f}_adx={result.get('adx',0):.1f}_atr={result.get('atr',0):.1f}_tier={_debug_tier}",
        )
        # 把 order_id 存入 position_manager，供后续平仓追溯
        if _order_id:
            _pos_data = position_manager.get(symbol)
            if _pos_data:
                _pos_data["order_id"] = _order_id
                position_manager.update(symbol, _pos_data)
    except Exception as tj_err:
        print(f"[TradeJournal] 写入失败: {tj_err}")
    
    # 2. FeatureStore（信号特征分析）
    try:
        _adx_val = result.get("adx", 0) if result.get("adx") else (result.get("exec_ctx", {}) or {}).get("adx", 0)
        regime2 = "Trend" if _adx_val > 25 else ("Compression" if "squeeze" in str(result.get("squeeze", "")).lower() else "Range")
        
        # 获取 score_raw 用于记录分析
        _raw = None
        if _signal:
            _raw = _signal.get("score_raw")
        
        trade_features = {
            "symbol": symbol,
            "direction": direction,
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
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
            "signal_tier": _tier,
            "score_raw": _raw,
            "entry_price_level": f"bsl={result.get('bsl_level',0):.1f}_ssl={result.get('ssl_level',0):.1f}",
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
            # 【修复20260705】profit_r2 改为 R 倍数（价格差 / 风险），而非价格百分比
            _risk = abs(pos["entry"] - pos["current_sl"])
            profit_r2 = (new_sl - pos["entry"]) / max(_risk, 1e-12)
            if pos["direction"] == "Short":
                profit_r2 = (pos["entry"] - new_sl) / max(_risk, 1e-12)
            
            mfe_val = pos.get("mfe", 0)
            giveback = 0.0
            if mfe_val > 0:
                giveback = abs((mfe_val - profit_r2) / mfe_val)
            
            # ===== 写入 TradeJournal 平仓记录 =====
            try:
                _oid = pos.get("order_id", "")
                if _oid:
                    trade_journal.close_trade(
                        order_id=_oid,
                        close_price=current_price,
                        pnl_r=profit_r2,
                        exit_reason=stage_label,
                        mfe_r=mfe_val,
                        mae_r=pos.get("mae", 0),
                        max_r_before_stop=pos.get("max_r", 0),
                        note=f"giveback={giveback:.2f}",
                    )
            except Exception as tj_err:
                print(f"[TradeJournal] 平仓记录失败: {tj_err}")
                
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

    # ---- 止损检查 ----
    if direction == "Long" and current_price <= pos["current_sl"]:
        _trigger_stop_loss(symbol, pos, current_price)
    elif direction == "Short" and current_price >= pos["current_sl"]:
        _trigger_stop_loss(symbol, pos, current_price)


def _trigger_stop_loss(symbol: str, pos: dict, current_price: float):
    """止损触发推送 + 特征记录"""
    if pos.get("sl_hit"):
        return
    pos["sl_hit"] = True

    pnl_pct = ((current_price / pos["entry"]) - 1) * 100
    if pos["direction"] == "Short":
        pnl_pct = ((pos["entry"] / current_price) - 1) * 100

        msg = (
        f"⛔ [SL] {symbol} {'多头' if pos['direction'] == 'Long' else '空头'}\n"
        f"入场: {pos['entry']:.2f} 出场: {current_price:.2f}\n"
        f"盈亏: {pnl_pct:+.2f}%"
    )
    print(f"[{symbol}] ❌ 止损触发 ({pnl_pct:+.2f}%)")
    safe_send(msg)

    try:
        profit_r = pnl_pct / 100.0  # 价格变化百分比 → R 倍数
        
        # 【修复20260705】止损时同步写入 TradeJournal
        _oid = pos.get("order_id", "")
        if _oid:
            try:
                trade_journal.close_trade(
                    order_id=_oid,
                    close_price=current_price,
                    pnl_r=profit_r,
                    exit_reason="SL",
                    mfe_r=pos.get("mfe", 0),
                    mae_r=pos.get("mae", 0),
                    max_r_before_stop=pos.get("max_r", 0),
                )
            except Exception as tj_err:
                print(f"[TradeJournal] 止损记录失败: {tj_err}")
        
        feature_store.save_trade({
            "symbol": symbol,
            "direction": pos["direction"],
            "exit_reason": "SL",
            "pnl_r": profit_r,
            "mfe": pos.get("mfe", 0),
            "mae": pos.get("mae", 0),
            "max_r": pos.get("max_r", 0),
            "max_r_before_stop": pos.get("max_r", 0),
        })
    except Exception as feat_e:
        print(f"[Feature] 止损特征更新异常: {feat_e}")

    position_manager.remove(symbol)


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
                print(f"[hf_auto_trader] {symbol} scan_and_decide 返回: {'非空' if result else 'None'}")
                
                if result:
                    print(f"[{symbol}] main_loop: result 非空，curr={type(result.get('curr'))}, exec_ctx={type(result.get('exec_ctx'))}, macro_ctx={type(result.get('macro_ctx'))}")

                    # ---- 【第 1 步】Observer 指标事件推送（含完整技术数据） ----
                    _curr = result.get("curr")
                    _exec_ctx = result.get("exec_ctx", {}) or {}
                    _macro_ctx = result.get("macro_ctx", {}) or {}
                    _events = _detect_observer_events(_curr, _exec_ctx, _macro_ctx,
                                  result.get("long_score", 0), result.get("short_score", 0))
                    print(f"[{symbol}] _detect_observer_events 返回 {len(_events)} 个事件: {[e['type'] for e in _events]}")
                    obs_events = _new_observer_events(symbol, _events)
                    print(f"[{symbol}] 新 Observer 事件: {len(obs_events)} 个 (总 {len(_events)} 个)")
                    for ev in obs_events:
                        # 区分：首次触发事件用完整推送；定期汇总用轻量摘要
                        if ev.get("is_periodic_summary"):
                            # 轻量汇总：仅发一条简短状态持续消息
                            _type_names_short = {
                                "DIVERGENCE_R": "背离R",
                                "NEAR_OB": "接近OB",
                                "NEAR_LIQUIDITY": "接近流动性",
                                "CANDLE_COLOR": "K线变色",
                                "SQZMOM_WHITE": "SQZMOM白线",
                            }
                            _icons_short = {"DIVERGENCE_R": "🔮", "NEAR_OB": "🧱", "NEAR_LIQUIDITY": "🎯", "CANDLE_COLOR": "🎨", "SQZMOM_WHITE": "⚪"}
                            _icon = _icons_short.get(ev["type"], "📊")
                            _type_name = _type_names_short.get(ev["type"], ev["type"])
                            _dir_text = {"Long": "📈多头", "Short": "📉空头", "N/A": "⚖️中性"}.get(ev.get("dir", ""), "")
                            _summary_msg = (
                                f"{_icon} [{symbol}] {_type_name} 持续中\n"
                                f"方向: {_dir_text} | {ev.get('desc', '')}\n"
                                f"⏱️ 状态已持续30分钟，等待变化"
                            )
                            safe_send(_summary_msg)
                            print(f"[{symbol}] Observer 汇总推送(轻量): {ev['type']}")
                        else:
                            _push_observer_event(
                                symbol, ev,
                                long_score=result.get("long_score", 0),
                                short_score=result.get("short_score", 0),
                                long_ev=result.get("long_ev", 0),
                                short_ev=result.get("short_ev", 0),
                                long_entry=result.get("long_entry", 0),
                                long_sl=result.get("long_sl", 0),
                                long_tp1=result.get("long_tp1", 0),
                                long_rr=result.get("long_rr", 0),
                                short_entry=result.get("short_entry", 0),
                                short_sl=result.get("short_sl", 0),
                                short_tp1=result.get("short_tp1", 0),
                                short_rr=result.get("short_rr", 0),
                                v37_dir=result.get("direction", "N/A") or "N/A",
                                price=result.get("price", 0),
                                rsi=result.get("rsi", 0),
                                adx=result.get("adx", 0),
                                atr=result.get("atr", 0),
                                macd_hist=result.get("macd_hist", 0),
                                volume_ratio=result.get("volume_ratio", 1.0),
                                candle_color=result.get("candle_color", ""),
                                color_changed=result.get("color_changed", False),
                                regime=result.get("regime", ""),
                                vol_state=result.get("vol_state", ""),
                                squeeze=result.get("squeeze", ""),
                                trend_direction=result.get("trend_direction", ""),
                                bsl_level=result.get("bsl_level", 0),
                                ssl_level=result.get("ssl_level", 0),
                                is_bsl_swept=result.get("is_bsl_swept", False),
                                is_ssl_swept=result.get("is_ssl_swept", False),
                                bullish_ob=result.get("bullish_ob"),
                                bearish_ob=result.get("bearish_ob"),
                                bullish_fvg=result.get("bullish_fvg"),
                                bearish_fvg=result.get("bearish_fvg"),
                                funding_rate=result.get("funding_rate"),
                            )

                    # ---- 【第 2 步】Strategy 开单推送 ----
                    check_and_open(result)

            # ---- 【第 3 步】持仓追踪 ----
            try:
                all_positions = position_manager.get()
                if all_positions:
                    for sym, pos in list(all_positions.items()):
                        try:
                            curr_price = _fetch_ticker_price(sym)
                            if curr_price is None or curr_price <= 0:
                                print(f"[{sym}] 无法获取实时价格，跳过追踪")
                                continue
                            check_trailing(sym, pos, curr_price)
                        except Exception as e:
                            print(f"[{sym}] 追踪异常: {e}")
                            traceback.print_exc()
                else:
                    print("[持仓] 无活跃持仓")
            except Exception as e:
                print(f"[持仓追踪] 整体异常: {e}")
                traceback.print_exc()
                    
            # 扫描间隔休眠
            await asyncio.sleep(SCAN_INTERVAL)
            
        except Exception as e:
            print(f"[hf_auto_trader] 主循环发生异常: {e}")
            traceback.print_exc()
            await asyncio.sleep(60)  # 发生异常时等待60秒后重试