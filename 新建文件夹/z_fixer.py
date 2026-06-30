# -*- coding: utf-8 -*-
"""Hugging Face 自动化信号扫描 + 持仓追踪 + 推送
在 Hugging Face Space 持续运行，自动:
  1. 每 60 秒扫描一次信号
  2. 【Observer 层】检测到 K线变白/背离R/EF/近OB/CHOCH/BOS 等指标事件自动推送 + 附多空评分
  3. 【Strategy 层】V37 完整决策批准后推送开单信号（含 EV/评分/RR/仓位）
  4. 注册持仓到后台追踪
  5. 每分钟检查追踪止损/止盈，动态推送状态变化
"""
from __future__ import annotations

import os
import json
import time
import threading
import traceback
import asyncio
from pathlib import Path

# 确保项目根目录在 import 路径中
_root = Path(__file__).parent.absolute()
if str(_root) not in os.sys.path:
    os.sys.path.insert(0, str(_root))

import pandas as pd

# ---------- 导入项目模块 ----------
from indicators.basic import add_all_indicators
from strategy.smc import build_macro_context, build_exec_context
from strategy.risk import calculate_dynamic_tp_sl, check_partial_close_and_trail
from notifier.observer.funding import fetch_funding_rate_safe, normalize_swap_symbol
from notifier.telegram import send_telegram
from config import STRATEGY_PARAMS, SYMBOL_STRATEGY
from utils.symbols import load_symbol_strategy
from utils.time_utils import series_ms_to_bj

# ---------- V37 完整决策链路 ----------
from core.alpha_master_engine import V37MasterEngine

# ---------- 线程安全的全局持仓管理器 ----------
from state.position_manager import position_manager
from feature_store import feature_store

# ---------- 风控参数 ----------
MAX_DRAWDOWN_PCT = 15.0    # 最大回撤百分比，超出后熔断
_peak_equity = 0.0          # 用于计算回撤的峰值权益

# ---------- Order Tracker（为实盘准备） ----------
from execution.order_tracker import OrderTracker, get_order_tracker

# ============================================================
#  配置
# ============================================================
SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
SCAN_INTERVAL = 60          # 扫描间隔（秒）
MAX_CANDLES = 320           # 拉取 K 线数量

# Strategy 开单推送过滤
MIN_EV_FOR_PUSH = 0.05      # 最低期望值
MIN_SCORE_FOR_PUSH = 55     # 最低评分
MIN_SCORE_GAP = 8.0         # 最低分差：选定方向比反方向至少高8分，避免多空分歧过大

# Observer 指标过滤——只有当新事件出现时才推送
# 全局持仓（通过 PositionManager 线程安全访问）
# Observer 事件历史（防止重复推送同一事件）
_OBSERVER_HISTORY: dict = {}  # symbol -> {event_key: bool}


def safe_send(msg: str) -> str:
    """安全发送推送（同时走 Telegram + PushPlus）"""
    try:
        print(f"[safe_send] 开始推送，消息长度: {len(msg)} 字符")
        result = send_telegram(msg)
        print(f"[safe_send] 推送完成: {result[:100] if result else 'None'}")
        return result
    except Exception as e:
        print(f"[safe_send] 推送异常: {e}")
        traceback.print_exc()
        return traceback.format_exc()


async def fetch_ohlcv(symbol: str, timeframe: str = "15m", limit: int = 320) -> pd.DataFrame | None:
    """异步从 Bitget 拉取 OHLCV 数据"""
    import ccxt.async_support as ccxt_async
    import ccxt
    try:
        exchange = ccxt_async.bitget({
            "options": {"defaultType": "swap"},
            "enableRateLimit": True,
        })
        sym = normalize_swap_symbol(symbol)
        bars = await exchange.fetch_ohlcv(sym, timeframe=timeframe, limit=limit)
        await exchange.close()
        df = pd.DataFrame(bars, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["datetime"] = series_ms_to_bj(df["timestamp"])
        return df
    except Exception as e:
        print(f"[{symbol}] 拉取数据失败: {e}")
        return None


async def scan_and_decide(symbol: str) -> dict | None:
    """
    使用 V37MasterEngine 完整决策链路扫描交易对。
    
    流程:
      V37MasterEngine.decide()
        ├─ classify_regime()     → 硬规则判断 regime
        ├─ volatility_state()    → 波动率状态
        ├─ generate_signal(Long) → 含 base_trigger + smc_impulse_score + EV + scorecard
        ├─ generate_signal(Short)
        ├─ choose_signal()       → 选多空 EV 更高的方向
        ├─ tail_filter()         → EV 过软降级
        ├─ risk_budget()         → 风险预算 + 回撤压缩
        └─ allocate()            → 最终仓位分配
    
    返回的 dict 同时包含：
      - 决策结果（供 Strategy 开单用）
            - 多空评分快照（供 Observer 指标推送附评分用）
    """
    from runner.v11_institutional_runner import make_sample_ohlcv

    # 并发拉取双周期数据
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

    # 计算指标
    df_exec = add_all_indicators(df_exec, STRATEGY_PARAMS["wvf_std_mult"])
    df_macro = add_all_indicators(df_macro, STRATEGY_PARAMS["wvf_std_mult"])

    # 构建上下文
    macro_ctx = build_macro_context(df_macro)
    exec_ctx = build_exec_context(df_exec)
    exec_ctx["data_source"] = "hf_auto"

    # 当前 K 线
    curr = df_exec.iloc[-1]

    # ===== 使用 V37MasterEngine 完整决策 =====
    engine = V37MasterEngine()
    decision = engine.decide(curr, exec_ctx, macro_ctx)

    allow = bool(decision.get("allow", False))
    signal = decision.get("signal", {})

    expected_value = signal.get("expected_value", -1.0)
    score = signal.get("score", 0.0)
    direction = signal.get("direction", None)  # V37 选出的胜方方向

    # ---- 提取多空双方的评分快照（供 Observer 推送附评分用） ----
    # 我们可以直接通过 V37 的 generate_signal 获取双方评分
    long_sig = engine.generate_signal(curr, "Long", exec_ctx, macro_ctx)
    short_sig = engine.generate_signal(curr, "Short", exec_ctx, macro_ctx)
    long_score = long_sig.get("score", 0.0)
    short_score = short_sig.get("score", 0.0)
    long_ev = long_sig.get("expected_value", -1.0)
    short_ev = short_sig.get("expected_value", -1.0)

        # 构建开单所需的 SL/TP/RR
    sym_strategy = load_symbol_strategy(symbol, SYMBOL_STRATEGY)
    min_rr = sym_strategy.get("min_rr", 2.0)
    sl, tp1, tp2, tp3, rr = calculate_dynamic_tp_sl(
        direction or "Long", curr, df_exec, exec_ctx, min_rr, sym_strategy
    )

    # ---- 为 Observer 事件准备多方向 SL/TP ----
    #   Long 方向的参考 SL/TP（当 Observer 事件方向=Long 时使用）
    long_sl, long_tp1, long_tp2, long_tp3, long_rr = calculate_dynamic_tp_sl(
        "Long", curr, df_exec, exec_ctx, min_rr, sym_strategy
    )
    # Short 方向的参考 SL/TP（当 Observer 事件方向=Short 时使用）
    short_sl, short_tp1, short_tp2, short_tp3, short_rr = calculate_dynamic_tp_sl(
        "Short", curr, df_exec, exec_ctx, min_rr, sym_strategy
    )

    # ===== 检测 Observer 指标事件 =====
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
        # Observer 层指标事件
        "observer_events": observer_events,
        # 多空评分快照
        "long_score": round(float(long_score), 2),
        "short_score": round(float(short_score), 2),
        "long_ev": round(float(long_ev), 4),
        "short_ev": round(float(short_ev), 4),
                # Observer 配套 SL/TP 参考
        "long_entry": float(curr["close"]),
        "long_sl": long_sl, "long_tp1": long_tp1, "long_tp2": long_tp2, "long_tp3": long_tp3, "long_rr": round(long_rr, 2),
        "short_entry": float(curr["close"]),
        "short_sl": short_sl, "short_tp1": short_tp1, "short_tp2": short_tp2, "short_tp3": short_tp3, "short_rr": round(short_rr, 2),
        # Observer 附带的 K 线和技术数据
        "price": float(curr["close"]),
        "rsi": float(curr.get("rsi", 0)),
        "adx": float(exec_ctx.get("adx", curr.get("adx", 0))),
        "atr": float(exec_ctx.get("atr", curr.get("ATRr_14", 0))),
        "macd_hist": float(curr.get("MACDh_12_26_9", 0)),
        "volume_ratio": float(curr.get("volume_ratio", 1)),
        "candle_color": str(exec_ctx.get("curr_color", "")),
        "color_changed": bool(exec_ctx.get("color_changed", False)),
        "regime": decision.get("regime", "unknown"),
        "vol_state": decision.get("vol_state", "unknown"),
        "squeeze": str(exec_ctx.get("squeeze", "")),
        "trend_direction": str(exec_ctx.get("trend_direction", "")),
        # 流动性区
        "bsl_level": float(exec_ctx.get("bsl_level", 0)),
        "ssl_level": float(exec_ctx.get("ssl_level", 0)),
        "is_bsl_swept": bool(exec_ctx.get("is_bsl_swept", False)),
        "is_ssl_swept": bool(exec_ctx.get("is_ssl_swept", False)),
        # OB 区间
        "bullish_ob": exec_ctx.get("bullish_ob", None),
        "bearish_ob": exec_ctx.get("bearish_ob", None),
        # FVG
        "bullish_fvg": exec_ctx.get("bullish_fvg", None),
        "bearish_fvg": exec_ctx.get("bearish_fvg", None),
        # 资金费率（暂缺，需额外 fetch）
        "funding_rate": None,
    }


# ============================================================
#  【Observer 层】底层指标事件检测（无视打分，只看结构变化）
# ============================================================

def _detect_observer_events(curr, exec_ctx, macro_ctx, long_score: float, short_score: float):
    """
    检测当前 K 线的所有关键指标事件。
    和 app.py 中 "Observer 层瞬发信号" 的 dispatch_observer_snapshot(send_all=True) 效果一致。
    
    检测范围：
      ✅ SQZMOM 变白（衰竭反转）
      ✅ 背离 R（顶背离/底背离）
      ✅ EF（Equilibrium 力竭）
      ✅ 接近 Buyside/Sellside OB（做多/做空反转区）
      ✅ CHOCH（市场结构转变）
      ✅ BOS（结构突破）
      ✅ 流动性扫单（BSL/SSL Sweep）
      ✅ FVG 出现
    
    注意：大部分字段存在 exec_ctx 字典中，curr (pandas row) 中不一定有。
    """
    events = []

    # ---- 辅助判断函数 ----
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
            v = float(val) if val is not None else default
            return v
        except Exception:
            return default

    # ===== 1. SQZMOM 变白（动量衰竭反转信号） =====
    # 优先从 curr 取，再 fallback 到 exec_ctx
    sqzmom_white_long = _bool(curr.get("sqzmom_white_reversal_long")) if hasattr(curr, 'get') else False
    if not sqzmom_white_long:
        sqzmom_white_long = _bool(exec_ctx.get("sqzmom_white_reversal_long", False))
    sqzmom_white_short = _bool(curr.get("sqzmom_white_reversal_short")) if hasattr(curr, 'get') else False
    if not sqzmom_white_short:
        sqzmom_white_short = _bool(exec_ctx.get("sqzmom_white_reversal_short", False))

    if sqzmom_white_long:
        events.append({
            "type": "SQZMOM_WHITE", "dir": "Long",
            "desc": "SQZMOM K线变白（多头动量衰竭，可能反转向下）",
            "key": "sqz_white_long"
        })
    if sqzmom_white_short:
        events.append({
            "type": "SQZMOM_WHITE", "dir": "Short",
            "desc": "SQZMOM K线变白（空头动量衰竭，可能反转向上）",
            "key": "sqz_white_short"
        })

    # ===== 2. 背离 R（顶背离/底背离） =====
    # 这些字段在 exec_ctx 中，不在 curr 中
    has_bot_div = _bool(exec_ctx.get("has_bot_div", False))
    has_top_div = _bool(exec_ctx.get("has_top_div", False))
    # 也检查 curr（部分地方可能设了）
    if hasattr(curr, 'get'):
        if not has_bot_div: has_bot_div = _bool(curr.get("has_bot_div", False))
        if not has_top_div: has_top_div = _bool(curr.get("has_top_div", False))

    if has_bot_div:
        strength = str(exec_ctx.get("bot_div_strength", curr.get("bot_div_strength", "") if hasattr(curr, 'get') else ""))
        events.append({
            "type": "DIVERGENCE_R", "dir": "Long",
            "desc": f"底背离 R 出现 (strength: {strength})",
            "key": "div_bot"
        })
    if has_top_div:
        strength = str(exec_ctx.get("top_div_strength", curr.get("top_div_strength", "") if hasattr(curr, 'get') else ""))
        events.append({
            "type": "DIVERGENCE_R", "dir": "Short",
            "desc": f"顶背离 R 出现 (strength: {strength})",
            "key": "div_top"
        })

    # ===== 3. EF（Equilibrium 力竭）= XTL 颜色从红/绿变白 =====
    curr_color = str(exec_ctx.get("curr_color", ""))
    prev_color = str(exec_ctx.get("prev_color", ""))
    if curr_color and "白色" in curr_color and (prev_color and ("红色" in prev_color or "藍色" in prev_color or "綠色" in prev_color)):
        events.append({
            "type": "SQZMOM_EF", "dir": "N/A",
            "desc": f"SQZMOM 力竭信号：颜色从 {prev_color}→{curr_color}，动量耗尽",
            "key": "sqz_ef"
        })

    # ===== 4. 接近 Buyside/Sellside OB（主力建仓区） =====
    near_bullish_ob = _bool(exec_ctx.get("near_bullish_ob", False))
    near_bearish_ob = _bool(exec_ctx.get("near_bearish_ob", False))
    bullish_ob = exec_ctx.get("bullish_ob", None)
    bearish_ob = exec_ctx.get("bearish_ob", None)

    if near_bullish_ob and bullish_ob:
        ob_high = _float(bullish_ob[0]) if isinstance(bullish_ob, (list, tuple)) and len(bullish_ob) > 0 else 0
        ob_low = _float(bullish_ob[1]) if isinstance(bullish_ob, (list, tuple)) and len(bullish_ob) > 1 else 0
        events.append({
            "type": "NEAR_OB", "dir": "Long",
            "desc": f"价格接近 Bullish OB ({ob_low:.1f}~{ob_high:.1f})，潜在做多反转区",
            "key": "ob_bull"
        })
    elif near_bullish_ob:
        events.append({
            "type": "NEAR_OB", "dir": "Long",
            "desc": "价格接近 Bullish OB，潜在做多反转区",
            "key": "ob_bull"
        })

    if near_bearish_ob and bearish_ob:
        ob_high = _float(bearish_ob[0]) if isinstance(bearish_ob, (list, tuple)) and len(bearish_ob) > 0 else 0
        ob_low = _float(bearish_ob[1]) if isinstance(bearish_ob, (list, tuple)) and len(bearish_ob) > 1 else 0
        events.append({
            "type": "NEAR_OB", "dir": "Short",
            "desc": f"价格接近 Bearish OB ({ob_low:.1f}~{ob_high:.1f})，潜在做空反转区",
            "key": "ob_bear"
        })
    elif near_bearish_ob:
        events.append({
            "type": "NEAR_OB", "dir": "Short",
            "desc": "价格接近 Bearish OB，潜在做空反转区",
            "key": "ob_bear"
        })

    # ===== 5. 流动性扫单（BSL/SSL Sweep）和接近 BSL/SSL =====
    is_bsl_swept = _bool(exec_ctx.get("is_bsl_swept", False))
    is_ssl_swept = _bool(exec_ctx.get("is_ssl_swept", False))
    bsl_level = _float(exec_ctx.get("bsl_level", 0))
    ssl_level = _float(exec_ctx.get("ssl_level", 0))

    if is_bsl_swept:
        events.append({
            "type": "LIQUIDITY_SWEEP", "dir": "Short",
            "desc": f"Buyside 流动性被扫 (BSL Sweep@{bsl_level:.1f})" if bsl_level > 0 else "Buyside 流动性被扫 (BSL Sweep)",
            "key": "bsl_sweep"
        })
    elif bsl_level > 0:
        # 没被扫但接近的——通过收盘价距离判断
        close_price = _float(curr.get("close", exec_ctx.get("close", 0))) if hasattr(curr, 'get') else _float(exec_ctx.get("close", 0))
        atr_val = _float(exec_ctx.get("atr", 1))
        if close_price > 0 and atr_val > 0:
            dist_atr = abs(close_price - bsl_level) / max(atr_val, 1e-12)
            if dist_atr <= 0.75:
                events.append({
                    "type": "NEAR_LIQUIDITY", "dir": "Short",
                    "desc": f"价格接近 Buyside 流动性区 (BSL@{bsl_level:.1f}，距离{dist_atr:.2f}ATR)",
                    "key": "near_bsl"
                })
    if is_ssl_swept:
        events.append({
            "type": "LIQUIDITY_SWEEP", "dir": "Long",
            "desc": f"Sellside 流动性被扫 (SSL Sweep@{ssl_level:.1f})" if ssl_level > 0 else "Sellside 流动性被扫 (SSL Sweep)",
            "key": "ssl_sweep"
        })
    elif ssl_level > 0:
        close_price = _float(curr.get("close", exec_ctx.get("close", 0))) if hasattr(curr, 'get') else _float(exec_ctx.get("close", 0))
        atr_val = _float(exec_ctx.get("atr", 1))
        if close_price > 0 and atr_val > 0:
            dist_atr = abs(close_price - ssl_level) / max(atr_val, 1e-12)
            if dist_atr <= 0.75:
                events.append({
                    "type": "NEAR_LIQUIDITY", "dir": "Long",
                    "desc": f"价格接近 Sellside 流动性区 (SSL@{ssl_level:.1f}，距离{dist_atr:.2f}ATR)",
                    "key": "near_ssl"
                })

    # ===== 6. MSS（市场结构转变）/ CHOCH =====
    # 用 swing_low/swing_high 判断结构变化：连续 HL > LH 为多头结构变化
    swing_high = _float(exec_ctx.get("swing_high", 0))
    swing_low = _float(exec_ctx.get("swing_low", 0))
    close_price = _float(curr.get("close", exec_ctx.get("close", 0))) if hasattr(curr, 'get') else _float(exec_ctx.get("close", 0))

    # 检查是否有结构突破：close > swing_high = 突破上方结构（CHOCH 多头），close < swing_low = 突破下方结构（CHOCH 空头）
    atr_val = _float(exec_ctx.get("atr", _float(exec_ctx.get("atr_pct", 0.01))) * close_price if close_price > 0 and exec_ctx.get("atr_pct") else 200)
    if swing_high > 0 and close_price > swing_high:
        events.append({
            "type": "CHOCH", "dir": "Long",
            "desc": f"市场结构转变 CHOCH：价格突破前高 {swing_high:.1f}（打破下降结构转多）",
            "key": "choch_long"
        })
    if swing_low > 0 and close_price < swing_low:
        events.append({
            "type": "CHOCH", "dir": "Short",
            "desc": f"市场结构转变 CHOCH：价格跌破前低 {swing_low:.1f}（打破上升结构转空）",
            "key": "choch_short"
        })

    # ===== 7. FVG 出现 =====
    bullish_fvg = exec_ctx.get("bullish_fvg", None)
    bearish_fvg = exec_ctx.get("bearish_fvg", None)
    if bullish_fvg is not None:
        events.append({
            "type": "FVG", "dir": "Long",
            "desc": f"多头 FVG 出现（看涨不平衡区 @{_float(bullish_fvg):.1f}）",
            "key": "fvg_long"
        })
    if bearish_fvg is not None:
        events.append({
            "type": "FVG", "dir": "Short",
            "desc": f"空头 FVG 出现（看跌不平衡区 @{_float(bearish_fvg):.1f}）",
            "key": "fvg_short"
        })

    # ===== 8. K线变色 =====
    color_changed = _bool(exec_ctx.get("color_changed", False))
    if color_changed:
        events.append({
            "type": "CANDLE_COLOR", "dir": "Long" if ("bull" in str(curr_color).lower() or "藍" in str(curr_color)) else "Short",
            "desc": f"K线变色为 {curr_color}",
            "key": f"color_{curr_color}"
        })

    # ===== 9. SQZMOM 挤压释放 =====
    squeeze = str(exec_ctx.get("squeeze", ""))
    if squeeze.lower() in ("release", "squeeze_release", "released"):
        events.append({
            "type": "SQUEEZE_RELEASE", "dir": "N/A",
            "desc": "SQZMOM 挤压释放（波动即将展开）",
            "key": "sqz_release"
        })

    return events


def _new_observer_events(symbol: str, events: list) -> list:
    """
    过滤出上次推送后新出现的 Observer 事件（避免重复推送）。
    """
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


def _push_observer_event(
    symbol: str, ev: dict,
    long_score: float = 0, short_score: float = 0,
    long_ev: float = 0, short_ev: float = 0,
    long_entry: float = 0, long_sl: float = 0, long_tp1: float = 0, long_rr: float = 0,
    short_entry: float = 0, short_sl: float = 0, short_tp1: float = 0, short_rr: float = 0,
    v37_dir: str = "N/A",
    # 额外技术数据
    price: float = 0, rsi: float = 0, adx: float = 0, atr: float = 0,
    macd_hist: float = 0, volume_ratio: float = 1.0,
    candle_color: str = "", color_changed: bool = False,
    regime: str = "", vol_state: str = "", squeeze: str = "",
    trend_direction: str = "",
    bsl_level: float = 0, ssl_level: float = 0,
    is_bsl_swept: bool = False, is_ssl_swept: bool = False,
    bullish_ob = None, bearish_ob = None,
    bullish_fvg = None, bearish_fvg = None,
    funding_rate = None,
):
    """推送 Observer 指标事件 + 完整技术数据"""
    icons = {
        "SQZMOM_WHITE": "⚪", "DIVERGENCE_R": "🔮", "SQZMOM_EF": "🌀",
        "NEAR_OB": "🧱", "NEAR_BSL": "🎯", "NEAR_SSL": "🎯",
        "LIQUIDITY_SWEEP": "🗑️", "CHOCH": "🔄", "BOS": "💥",
        "FVG": "📐", "CANDLE_COLOR": "🎨", "SQUEEZE_RELEASE": "💨",
    }
    icon = icons.get(ev["type"], "📊")
    type_names = {
        "SQZMOM_WHITE": "SQZMOM K线变白", "DIVERGENCE_R": "背离R",
        "SQZMOM_EF": "SQZMOM 力竭", "NEAR_OB": "接近主力建仓区",
        "NEAR_BSL": "接近上方流动性区", "NEAR_SSL": "接近下方流动性区",
        "LIQUIDITY_SWEEP": "流动性扫单", "CHOCH": "市场结构转变",
        "BOS": "结构突破", "FVG": "价格失衡区",
        "CANDLE_COLOR": "K线变色", "SQUEEZE_RELEASE": "SQZMOM 挤压释放",
    }
    type_name = type_names.get(ev["type"], ev["type"])
    dir_emoji = {"Long": "📈 多头", "Short": "📉 空头", "N/A": "⚖️ 中性"}
    dir_text = dir_emoji.get(ev.get("dir", ""), ev.get("dir", ""))

    # 操作建议
    if abs(long_score - short_score) < 8:
        suggestion = "方向分歧大（分差<8），等待关键位触发后再做方向判断。"
    elif long_score >= short_score:
        suggestion = "偏多占优；等回踩防守区、扫下方止损或量能确认，不追高。"
    else:
        suggestion = "偏空占优；等反弹防守区、扫上方止损或量能确认，不追低。"

    # 成交量化
    vr = volume_ratio or 1.0
    vol_zone = "极度缩量" if vr < 0.35 else ("缩量" if vr < 0.65 else ("正常" if vr < 1.20 else ("温和放量" if vr < 1.80 else "明显放量")))

    # RSI 区间
    rsi_zone = "超卖" if rsi < 30 else ("偏弱" if rsi < 45 else ("中性" if rsi < 55 else ("偏强" if rsi < 70 else "超买")))
    adx_zone = "弱趋势/震荡" if adx < 20 else ("趋势萌芽" if adx < 25 else ("有效趋势" if adx < 35 else "强趋势"))
    macd_dir = "偏多" if macd_hist > 0 else ("偏空" if macd_hist < 0 else "中性")
    atr_pct = atr / price * 100 if price > 0 and atr > 0 else 0
    atr_zone = "低波动" if atr_pct < 0.25 else ("正常" if atr_pct < 0.70 else ("高波动" if atr_pct < 1.20 else "极高波动"))

    regime_cn = {"TREND": "趋势", "CHOP": "震荡", "TRANSITION": "过渡", "CRISIS_RISK_OFF": "避险"}
    vol_cn = {"HIGH_VOL": "高波动", "MID_VOL": "正常", "LOW_VOL": "低波动"}
    squeeze_cn = {"release": "已经释放", "building": "正在压缩", "released": "已经释放"}

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

    lines = []
    lines.append(f"{icon} <b>【{type_name}】</b>")
    lines.append(f"品种: {symbol}")
    lines.append(f"方向: {dir_text}")
    lines.append(f"描述: {ev['desc']}")
    lines.append("")
    lines.append("━━━ ① AI 多空博弈天平 ━━━")
    lines.append(f"多头: {long_score:.1f}分 | EV: {long_ev:+.4f}")
    lines.append(f"空头: {short_score:.1f}分 | EV: {short_ev:+.4f}")
    lines.append(f"分差: {abs(long_score - short_score):.1f}分")
    lines.append(f"操作建议: {suggestion}")
    lines.append("")
    lines.append("━━━ ② 行情环境 ━━━")
    lines.append(f"趋势方向: {trend_direction or 'N/A'}")
    lines.append(f"行情状态: {regime_cn.get(regime, regime)}")
    lines.append(f"波动状态: {vol_cn.get(vol_state, vol_state)}")
    lines.append(f"压缩状态: {squeeze_cn.get(squeeze.lower(), squeeze)}")
    lines.append(f"成交量: {vr:.2f}x ({vol_zone})")
    lines.append("")
    lines.append("━━━ ③ 指标透视 ━━━")
    lines.append(f"K线颜色: {candle_color or 'N/A'} | 变色: {'是' if color_changed else '否'}")
    lines.append(f"RSI: {rsi:.2f} ({rsi_zone})")
    lines.append(f"ADX: {adx:.2f} ({adx_zone})")
    lines.append(f"MACD: {macd_hist:.4f} ({macd_dir})")
    lines.append(f"ATR: {atr:.2f} | {atr_pct:.2f}% ({atr_zone})")
    lines.append("")
    lines.append("━━━ ④ 流动性/关键位 ━━━")
    if bsl_level > 0:
        bsl_dist = abs(price - bsl_level) / price * 100 if price > 0 else 0
        lines.append(f"上方止损区: {bsl_level:.2f} (距离 {bsl_dist:.2f}%) | 已扫: {'是' if is_bsl_swept else '否'}")
    if ssl_level > 0:
        ssl_dist = abs(price - ssl_level) / price * 100 if price > 0 else 0
        lines.append(f"下方止损区: {ssl_level:.2f} (距离 {ssl_dist:.2f}%) | 已扫: {'是' if is_ssl_swept else '否'}")
    lines.append(f"买方OB: {_fmt_ob(bullish_ob)}")
    lines.append(f"卖方OB: {_fmt_ob(bearish_ob)}")
    lines.append(f"多头FVG: {_fmt_fvg(bullish_fvg)}")
    lines.append(f"空头FVG: {_fmt_fvg(bearish_fvg)}")
    if funding_rate is not None:
        try:
            fr = float(funding_rate)
            lines.append(f"资金费率: {fr:.4f}% ({'多头付' if fr > 0 else '空头付' if fr < 0 else '中性'})")
        except: pass
    lines.append("")

    ref_entry = long_entry if v37_dir == "Long" else (short_entry if v37_dir == "Short" else 0)
    ref_sl = long_sl if v37_dir == "Long" else (short_sl if v37_dir == "Short" else 0)
    ref_tp1 = long_tp1 if v37_dir == "Long" else (short_tp1 if v37_dir == "Short" else 0)
    ref_rr = long_rr if v37_dir == "Long" else (short_rr if v37_dir == "Short" else 0)
    v37_dir_text = dir_emoji.get(v37_dir, v37_dir)

    if v37_dir in ("Long", "Short") and ref_sl and ref_sl > 0 and ref_entry > 0:
        lines.append("━━━ ⑤ 参考开单参数 ━━━")
        lines.append(f"方向: {v37_dir_text}")
        lines.append(f"入场: {ref_entry:.2f}")
        lines.append(f"止损: {ref_sl:.2f}")
        lines.append(f"TP1: {ref_tp1:.2f}")
        lines.append(f"RR: {ref_rr:.2f}")
        lines.append("")
        lines.append(f"💡 回复 CONFIRM {symbol}_{v37_dir} 可启动追踪")

    msg = "\n".join(lines)
    safe_send(msg)
    print(f"[{symbol}] Observer 事件推送: {ev['type']} {ev.get('dir','')}")


# ============================================================
#  【Strategy 层】V37 开单推送 + 信号ID防重复
# ============================================================

# 已处理过的信号ID（防止重复推送同一信号）
_PROCESSED_SIGNALS: dict = {}  # signal_id -> timestamp

def _signal_id(result: dict) -> str:
    """生成唯一信号ID：symbol + direction + 时间戳（按分钟截断）"""
    symbol = result["symbol"]
    direction = result["direction"] or "NONE"
    # 用当前时间戳的整分钟作为分段，同一分钟内的相同信号视为同一个
    now_minute = int(time.time()) // 60
    return f"{symbol}_{direction}_{now_minute}"


def _is_signal_processed(signal_id: str) -> bool:
    """检查是否已经处理过这个信号ID"""
    if signal_id in _PROCESSED_SIGNALS:
        return True
    _PROCESSED_SIGNALS[signal_id] = time.time()
    # 清理超过1小时的旧记录（避免内存无限增长）
    cutoff = time.time() - 3600
    stale = [k for k, v in _PROCESSED_SIGNALS.items() if v < cutoff]
    for k in stale:
        del _PROCESSED_SIGNALS[k]
    return False


def check_and_open(result: dict) -> bool:
    """
    检查 V37 决策结果，满足条件时推送开单信号并注册持仓。

    防重复机制（方案B）：
      每个信号有唯一ID (symbol+direction+分钟级时间戳)
            同一ID只推送一次，不会重复发
    """
    symbol = result["symbol"]
    direction = result["direction"]
    approved = result["approved"]
    ev = result["expected_value"]
    score = result["score"]
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

    # ---- 风控：最大回撤熔断 ----
    global _peak_equity
    _peak_equity = max(_peak_equity, entry)
    if _peak_equity > 0:
        dd_pct = (_peak_equity - entry) / _peak_equity * 100
        if dd_pct > MAX_DRAWDOWN_PCT:
            print(f"[{symbol}] 回撤 {dd_pct:.1f}% > {MAX_DRAWDOWN_PCT}%，熔断触发，跳过开单")
            return False

            
    if not approved:
        return False
    if not direction:
        return False
    if ev < MIN_EV_FOR_PUSH:
        print(f"[{symbol}] EV={ev:.4f}<{MIN_EV_FOR_PUSH}，跳过推送")
        return False
    if score < MIN_SCORE_FOR_PUSH:
        print(f"[{symbol}] score={score:.1f}<{MIN_SCORE_FOR_PUSH}，跳过推送")
        return False

    # ---- 分差检查：选定方向必须明显优于反方向 ----
    long_score = result.get("long_score", 0)
    short_score = result.get("short_score", 0)
    score_gap = abs(long_score - short_score)
    # 选定方向的分必须比反方向高至少 MIN_SCORE_GAP 分
    if direction == "Long" and long_score - short_score < MIN_SCORE_GAP:
        print(f"[{symbol}] 多头({long_score:.1f}) vs 空头({short_score:.1f}) 分差{score_gap:.1f}<{MIN_SCORE_GAP}，分歧过大跳过")
        return False
    if direction == "Short" and short_score - long_score < MIN_SCORE_GAP:
        print(f"[{symbol}] 空头({short_score:.1f}) vs 多头({long_score:.1f}) 分差{score_gap:.1f}<{MIN_SCORE_GAP}，分歧过大跳过")
        return False

        # ---- 信号ID防重复：同一方向+同一分钟不重复推送 ----
    sig_id = _signal_id(result)
    if _is_signal_processed(sig_id):
        print(f"[{symbol}] 信号ID {sig_id} 已处理过，跳过推送")
        return False

    # ---- 已持仓不重复注册（手动开单不阻塞自动信号，但只推送不开追踪） ----
    has_existing_position = position_manager.exists(symbol)
    if has_existing_position:
        print(f"[{symbol}] 已有手动持仓，开单信号仅供查看，不重复注册追踪")

    emoji_dir = "📈 多头" if direction == "Long" else "📉 空头"
    msg = (
        f"🚀 <b>【Strategy 开单信号】</b>\n"
        f"信号ID: {sig_id}\n"
        f"品种: {symbol}\n"
        f"方向: {emoji_dir}\n"
        f"入场: {entry:.2f}\n"
        f"止损: {sl:.2f}\n"
        f"TP1: {tp1:.2f}\n"
        f"TP2: {tp2:.2f}\n"
        f"TP3: {tp3:.2f}\n"
        f"R:R: {rr:.2f}\n"
        f"----------------\n"
        f"EV: {ev:.4f} | 评分: {score:.1f}\n"
        f"Regime: {regime} | Book: {book}\n"
        f"仓位: {size*100:.1f}%\n"
        f"分差: {abs(long_score - short_score):.1f}分 | 优选:{direction}\n"
        f"原因: {reason}"
    )
    if has_existing_position:
        msg += "\n⚠️ 已有手动持仓，仅作参考未注册追踪"
    safe_send(msg)

        # 无持仓时才自动注册追踪
    if not has_existing_position:
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
        print(f"[{symbol}] ✅ Strategy 开单已推送并注册持仓 (EV={ev:.4f}, score={score:.1f})")

        # V38.0: 保存开单特征（仅实际开单时）
        try:
            regime2 = "Trend" if adx > 25 else ("Compression" if "squeeze" in str(result.get("squeeze", "")).lower() else "Range")
            trade_features = {
                "symbol": symbol, "direction": direction,
                "entry": entry, "sl": sl, "tp1": tp1,
                "rr": rr, "ev": ev, "score": score,
                "regime": result.get("regime", ""), "regime2": regime2,
                "book": result.get("book", ""),
                "adx": result.get("adx", 0) if result.get("adx") else (result.get("exec_ctx", {}) or {}).get("adx", 0),
                "atr": result.get("atr", 0), "div_count": 0,
                "signal_age": 0,
                "mfe": 0.0, "mae": 0.0, "max_r": 0.0, "max_r_before_stop": 0.0,
                "exit_reason": "OPEN", "pnl_r": None,
                "weekday": __import__("datetime").datetime.now().weekday(),
                "hour": __import__("datetime").datetime.now().hour,
            }
            feature_store.save_trade(trade_features)
        except Exception as feat_e:
            print(f"[Feature] 保存开单特征异常: {feat_e}")

    else:
        print(f"[{symbol}] 手动持仓存在，仅推送不注册追踪")
    return True


# ============================================================
#  持仓追踪（不变）
# ============================================================

def check_trailing(symbol: str, pos: dict, current_price: float):
    """V38.1 最终版 - 实时更新 MFE/MAE/Max R，并检查追踪止损/止盈"""
    direction = pos["direction"]
    entry = pos["entry"]
    sl = pos["current_sl"]

    # ---- V38.1: 计算当前 R 倍数，更新 MFE/MAE/MaxR ----
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
        msg = (
            f"🏆 <b>【{stage_label}到达】</b>\n"
            f"品种: {symbol}\n"
            f"平仓比例: {close_pct:.0f}%\n"
            f"新止损: {new_sl:.2f}\n"
            f"当前价: {current_price:.2f}\n"
            f"方向: {'多头' if direction == 'Long' else '空头'}"
        )
        msg_key = f"{stage_label}_{new_stage}"
        if pos.get("last_sl_msg") != msg_key:
            safe_send(msg)
            pos["last_sl_msg"] = msg_key
            # V38.1: TP 到达时更新特征
            try:
                profit_r = (new_sl - pos["entry"]) / pos["entry"]
                if pos["direction"] == "Short":
                    profit_r = (pos["entry"] - new_sl) / pos["entry"]
                feature_store.save_trade({
                    "symbol": symbol,
                    "direction": pos["direction"],
                    "exit_reason": stage_label,
                    "pnl_r": profit_r,
                    "mfe": pos.get("mfe", 0),
                    "mae": pos.get("mae", 0),
                    "max_r": pos.get("max_r", 0),
                })
            except Exception as feat_e:
                print(f"[Feature] TP 特征更新异常: {feat_e}")
        pos["current_sl"] = new_sl
        pos["stage"] = new_stage

    elif action == "TRAIL_ONLY":
        new_sl = action_plan["new_sl"]
        if abs(pos["current_sl"] - new_sl) > current_price * 0.001:
            msg = (
                f"🛡️ <b>【追踪止损推移】</b>\n"
                f"品种: {symbol}\n"
                f"止损: {pos['current_sl']:.2f} → {new_sl:.2f}\n"
                f"当前价: {current_price:.2f}\n"
                f"方向: {'多头' if direction == 'Long' else '空头'}"
            )
            safe_send(msg)
            pos["current_sl"] = new_sl
            pos["stage"] = action_plan.get("new_stage", pos["stage"])

    if direction == "Long" and current_price <= pos["current_sl"]:
        _trigger_stop_loss(symbol, pos, current_price)
    elif direction == "Short" and current_price >= pos["current_sl"]:
        _trigger_stop_loss(symbol, pos, current_price)


def _trigger_stop_loss(symbol: str, pos: dict, current_price: float):
    """止损触发推送"""
    if pos.get("sl_hit"):
        return
    pos["sl_hit"] = True

    pnl_pct = ((current_price / pos["entry"]) - 1) * 100
    if pos["direction"] == "Short":
        pnl_pct = ((pos["entry"] / current_price) - 1) * 100

    msg = (
        f"⛔ <b>【止损触发】</b>\n"
        f"品种: {symbol}\n"
        f"方向: {'多头' if pos['direction'] == 'Long' else '空头'}\n"
        f"入场: {pos['entry']:.2f}\n"
        f"出场: {current_price:.2f}\n"
        f"盈亏: {pnl_pct:+.2f}%\n"
        f"-----\n"
        f"持仓已平仓，可等待下次开单信号"
    )
    print(f"[{symbol}] ❌ 止损触发 (盈亏: {pnl_pct:+.2f}%)")

    # V38.1: 平仓时更新特征记录
    try:
        profit_r = pnl_pct / 100.0  # 简单换算 R 倍数
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
#  主循环
# ============================================================

async def main_loop():
    """
    主循环：
      第 1 步 → 扫描 + Observer 指标事件推送（无视打分，附评分供人工判断）
      第 2 步 → Strategy 开单推送（V37 完整决策）
      第 3 步 → 持仓追踪止损
        """
    import ccxt

    try:
        safe_send("🟢 <b>HF 自动化系统已启动</b>\n"
                  "双通道推送:\n"
                  "• Observer: 变白/背离R/OB/CHOCH/BOS + 多空评分\n"
                  "• Strategy: V37 开单信号\n"
                  "监控: " + ", ".join(SYMBOLS))
    except Exception as exc:
        print(f"[启动] safe_send 失败: {exc}")
        traceback.print_exc()

    while True:
        try:
            # 并发扫描所有品种
            tasks = [scan_and_decide(s) for s in SYMBOLS]
            results = await asyncio.gather(*tasks)

            for symbol, result in zip(SYMBOLS, results):
                if result is None:
                    continue

                                # ---- 【第 1 步】Observer 指标事件推送（无视打分） ----
                obs_events = _new_observer_events(symbol, result.get("observer_events", []))
                for ev in obs_events:
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
                        # 技术数据
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
                opened = check_and_open(result)
                if opened:
                    print(f"[{symbol}] Strategy 新开单已推送")

            # ---- 【第 3 步】持仓追踪 ----
            exchange = ccxt.bitget({
                "options": {"defaultType": "swap"},
                "enableRateLimit": True,
            })
            all_positions = position_manager.get()
            if all_positions:
                for sym, pos in list(all_positions.items()):
                    try:
                        ticker = exchange.fetch_ticker(normalize_swap_symbol(sym))
                        curr_price = float(ticker["last"])
                        check_trailing(sym, pos, curr_price)
                    except Exception as e:
                        print(f"[{sym}] 追踪异常: {e}")
                exchange.close()

            # 状态日志
            active = position_manager.get()
            if active:
                status = " | ".join(
                    f"{s}: {p['direction']} @{p['entry']:.0f} SL={p['current_sl']:.0f}"
                    for s, p in active.items()
                )
                print(f"[持仓] {status}")
            else:
                print("[持仓] 无活跃持仓")


        except Exception as e:
            print(f"[主循环] 异常: {e}")
            traceback.print_exc()

        # V38.4: 每小时更新一次 EV 统计
        import time as _time
        if int(_time.time()) % 3600 < 60:
            try:
                feature_store.update_ev_statistics()
            except Exception as feat_e:
                print(f"[Feature] 统计更新异常: {feat_e}")

        await asyncio.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main_loop())
