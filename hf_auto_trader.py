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
from config import (
    STRATEGY_PARAMS,
    SYMBOL_STRATEGY,
    SIGNAL_COOLDOWN_SECONDS,
    MAX_DAILY_LOSS_R,
    MAX_TRADES_DAY,
    MAX_CONSECUTIVE_LOSS,
    ENABLE_RUNTIME_RECOVERY,
)
from utils.symbols import load_symbol_strategy
from utils.time_utils import series_ms_to_bj

# ---------- V56.5 主引擎（唯一生产决策管线） ----------
from final_forge.v56_5_stable_engine import (
    V565Config,
    V56_5_Engine,
    generate_v56_candidates,
    enrich_v565_candidates,
    select_v565_portfolio,
    execute_v565,
    add_v56_indicators,
    load_ohlcv,
)
from strategy.v565_quality_gate import v565_quality_gate
from decision.v37_gate import v37_final_gate

# ---------- 优化模块：增强决策管线 ----------
from strategy.statistical_ev_gate import StatisticalEVGate, get_statistical_ev_gate
from strategy.htf_regime_filter import HTFRegimeFilter, get_htf_regime_filter
from strategy.score_grade import ScoreGrader, get_score_grader
from strategy.feature_penalty import calculate_feature_overlap, apply_feature_penalty
from strategy.statistical_ev import StatisticalEV, get_statistical_ev

# ---------- 状态与特征存储 ----------
from state.position_manager import position_manager
from feature_store import feature_store

# ---------- 【新增20260723】工具类导入 ----------
from utils.adaptive_features import AdaptiveFeatureWeighter
from utils.probability_calibrator import ProbabilityEngine as ProbabilityCalibrator
from utils.feedback_loop import FeedbackLoop  # 全链路闭环
from utils.signal_tracker import SignalTracker
from utils.daily_risk_guard import DailyRiskGuard
from utils.signal_audit_log import signal_audit_log
from utils.smart_position_sizer import SmartPositionSizer, get_smart_sizer
from analytics.feature_learning import FeatureLearningEngine, get_feature_learner
from utils.daily_panel import DailyPanel, get_daily_panel

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
MIN_EV_FOR_PUSH = 0.01  # ⚠️ 20260706修复: 从0.15降到0.01，匹配实时model_ev实际分布（通常在-0.2~0.08）
MIN_SCORE_FOR_PUSH = 35 
MIN_SCORE_GAP = 3.0  # 20260715修复: 从4.0降到3.0，V56.5 Short信号在BULL趋势下long_score较高但仍可开单

# ----- 止损冷却 -----
STOP_LOSS_COOLDOWN = 300
_last_stop_loss_time = {}

# 【修复20260704】去重与质量加强参数
TREND_END_PULLBACK_ATR = 2.0  # 价格离 swing_high（Short）或 swing_low（Long）超过 N 倍 ATR 则不开

# ----- 信号后验验证参数 -----
POSTHOC_FUTURE_BARS = 15  # 开单后追踪 15 根 K线（15m = 3.75 小时）
_POSTHOC_CLOSE_BUFFER: dict = {}  # signal_id -> {future_prices, entry, sl, direction}

# ---------- 工具实例（全局单例） ----------
_weighter = AdaptiveFeatureWeighter()
_calibrator = ProbabilityCalibrator()
_tracker = SignalTracker("logs/signal_outcomes.jsonl")
_risk_guard = DailyRiskGuard()
_feedback = FeedbackLoop()  # 全链路反馈闭环引擎
_feature_learner = get_feature_learner()  # V21: Feature Learning Engine
_panel = get_daily_panel()  # 每日监控面板
_panel_today_sent = [False]  # mutable flag

# ===== 【新增20260729】ML Decision Engine (LightGBM 概率引擎) =====
# 现有人工权重继续运行，ML 并行收集数据
# 当训练数据足够后自动切入
from ml.decision_engine import get_ml_decision, MLDecisionEngine
_ML_DECISION = get_ml_decision()
_ML_FIRST_EVAL = True  # 标记是否首次评估（用于日志）
_ML_RETRAIN_COUNTER = 0  # 重训计数器

# ===== 【新增20260729】持仓恢复 + 强制日志标记 =====
_RECOVERED_POSITIONS: bool = False  # 是否已执行重启恢复
_FORCE_CLOSE_LOG_PATH = Path("logs/force_close_log.txt")  # 未追踪到的Open平仓日志

# ---------- V56.5 Engine（预加载回测 bucket_ev） ----------
_V56_ENGINE = V56_5_Engine(V565Config(
    min_score=55.0,
    allowed_hours=tuple(range(24)),
))
_bucket_ev_path = Path("data/v56_5_bucket_ev.json")
if _bucket_ev_path.exists():
    try:
        _buckets = json.loads(_bucket_ev_path.read_text(encoding="utf-8"))
        _V56_ENGINE.load_history_buckets(_buckets)
        print(f"[V56_5_Engine] 加载回测 bucket_ev 成功 ({len(_buckets)} 个分桶), 来自 {_bucket_ev_path}")
    except Exception as e:
        print(f"[V56_5_Engine] 加载 bucket_ev 失败: {e}")

def _compute_future_r(entry: float, sl: float, direction: str, tp1: float, tp2: float, future_df: 'pd.DataFrame | None',
                    max_bars: int = POSTHOC_FUTURE_BARS) -> tuple:
    """用持仓期间的 K线数据计算最大顺向R / 最大逆向R / 最终R。

    假设开单位于 future_df.iloc[0] 的开盘价，SL/TP 用 intrabar high/low 判断。

    Args:
        entry: 入场价
        sl: 止损价
        direction: "Long" / "Short"
        future_df: 包含高开低收的 DataFrame（至少 max_bars 行）
        max_bars: 最多追踪的 K线数量

    Returns:
        (max_forward_r, max_adverse_r, final_r, exit_reason)
        如果数据不足，返回 (None, None, None, "NO_DATA")
    """
    if future_df is None or len(future_df) < 2:
        return None, None, None, "NO_DATA"

    risk = abs(entry - sl)
    if risk <= 0:
        return None, None, None, "NO_RISK"

    max_forward = 0.0
    max_adverse = 0.0
    final_r = 0.0
    exit_reason = "TIME_OUT"

    limit = min(max_bars + 1, len(future_df))
    current_sl = sl
    stage = 0
    atr = 0.0
    if "ATRr_14" in future_df.columns:
        atr = float(future_df["ATRr_14"].iloc[0] or 0)
    elif "atr" in future_df.columns:
        atr = float(future_df["atr"].iloc[0] or 0)
    elif "ATR" in future_df.columns:
        atr = float(future_df["ATR"].iloc[0] or 0)

    for j in range(1, limit):  # j=0 是信号K线，从 j=1 开始是未来K线
        b = future_df.iloc[j]
        high = float(b["high"])
        low = float(b["low"])
        close = float(b["close"])

        if atr == 0.0:
            if "ATRr_14" in b.index:
                atr = float(b["ATRr_14"] or 0)
            elif "atr" in b.index:
                atr = float(b["atr"] or 0)
            elif "ATR" in b.index:
                atr = float(b["ATR"] or 0)

        if direction == "Long":
            this_forward = (high - entry) / risk
            this_adverse = (entry - low) / risk
        else:  # Short
            this_forward = (entry - low) / risk
            this_adverse = (high - entry) / risk

        max_forward = max(max_forward, this_forward)
        max_adverse = max(max_adverse, this_adverse)

        action_plan = check_partial_close_and_trail(
            direction=direction,
            current_price=close,
            entry_price=entry,
            current_sl=current_sl,
            tp1=tp1,
            tp2=tp2,
            atr=atr,
            stage=stage,
        )
        if action_plan["action"] == "PARTIAL_CLOSE":
            current_sl = action_plan["new_sl"]
            stage = action_plan["new_stage"]
        elif action_plan["action"] == "TRAIL_ONLY":
            current_sl = action_plan["new_sl"]

        if direction == "Long":
            if low <= current_sl:
                if stage > 0 and current_sl > entry:
                    exit_reason = "TRAIL_SL"
                    final_r = (current_sl - entry) / risk
                else:
                    exit_reason = "SL"
                    final_r = -1.0
                break
        else:
            if high >= current_sl:
                if stage > 0 and current_sl < entry:
                    exit_reason = "TRAIL_SL"
                    final_r = (entry - current_sl) / risk
                else:
                    exit_reason = "SL"
                    final_r = -1.0
                break

        final_r = (close - entry) / risk if direction == "Long" else (entry - close) / risk

    if exit_reason == "TIME_OUT":
        final_r = (float(future_df.iloc[limit - 1]["close"]) - entry) / risk if direction == "Long" else (entry - float(future_df.iloc[limit - 1]["close"])) / risk

    return round(max_forward, 4), round(max_adverse, 4), round(final_r, 4), exit_reason

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
SAFE_SEND_COOLDOWN = 600


def safe_send(msg: str, priority: str = "AUTO") -> str:
    global _LAST_SAFE_SEND_TIME
    now = time.time()

    def _auto_priority(message: str) -> str:
        if not message:
            return "OBSERVER"
        msg_upper = message.upper()
        high_priority_markers = [
            "强制平仓",
            "恢复",
            "启动恢复",
            "开单",
            "平仓",
            "SL",
            "TP",
            "止损",
            "追踪止损",
        ]
        if any(marker.upper() in msg_upper for marker in high_priority_markers):
            return "TRADE"
        return "OBSERVER"

    priority = str(priority or "AUTO").upper()
    if priority == "AUTO":
        priority = _auto_priority(msg)

    if priority == "TRADE" or priority == "SYSTEM":
        print(f"[safe_send] {priority} 消息直发，无限流: {msg[:80]}")
    else:
        if now - _LAST_SAFE_SEND_TIME < SAFE_SEND_COOLDOWN:
            print(f"[safe_send] 全局限流 {now - _LAST_SAFE_SEND_TIME:.0f}s < {SAFE_SEND_COOLDOWN}s")
            return "RATELIMITED_GLOBAL"
        _LAST_SAFE_SEND_TIME = now

    try:
        print(f"[safe_send] 开始推送，消息长度: {len(msg)} 字符 priority={priority}")
        result = send_telegram(msg)
        print(f"[safe_send] 推送完成: {result[:100] if result else 'None'}")
        return result
    except Exception as e:
        print(f"[safe_send] 推送异常: {e}")
        traceback.print_exc()
        return traceback.format_exc()

async def _fetch_ticker_price(symbol: str) -> float | None:
    import httpx
    for attempt in range(3):
        try:
            sym_raw = normalize_swap_symbol(symbol)
            sym = sym_raw.split("/")[0] + sym_raw.split("/")[1].split(":")[0]
            url = "https://api.bitget.com/api/v2/mix/market/candles"
            params = {"symbol": sym, "productType": "umcbl", "granularity": "1m", "limit": 1}
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, params=params)
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
                await asyncio.sleep(1)
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
            # 🟢 修复：利用 to_thread 彻底释放异步主线程
            result = await asyncio.to_thread(_do_fetch, True)
            if result is not None:
                return result
        except Exception:
            pass
        try:
            # 🟢 修复：同样使用异步线程化处理 fallback
            result = await asyncio.to_thread(_do_fetch, False)
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
        
                # ===== V56.5 唯一决策管线（使用预加载回测 bucket_ev 的 Engine）=====
    # 全局 _V56_ENGINE 已预加载回测 bucket_ev（365天BTC 15m数据训练）
    # 每次扫描生成候选 → enrich（含 bucket_ev 匹配）→ 选择 → 执行

    df_v56 = add_v56_indicators(load_ohlcv(df_exec))
    if df_v56 is None or len(df_v56) < 260:
        print(f"[{symbol}] V56 指标计算后数据不足")
        return None
    
    # Step 1: generate_candidates — 调用 load_ohlcv + add_v56_indicators + generate_v56_candidates + enrich（含bucket_ev）
    candidates = _V56_ENGINE.generate_candidates(df_v56)
    if candidates is None or candidates.empty:
        print(f"[{symbol}] V56.5 引擎无候选信号")
        return None

    if "idx" in candidates.columns:
        last_idx = int(df_v56.index.max())
        recent_threshold = max(0, last_idx - 1)
        candidates = candidates[candidates["idx"] >= recent_threshold].copy()
        print(f"[{symbol}] 限制候选信号到最新2根K线: idx>={recent_threshold}, 剩余{len(candidates)}条")
        if candidates.empty:
            print(f"[{symbol}] 仅历史信号存在，跳过本轮扫描")
            return None

    print(f"[{symbol}] V56.5 候选信号数: {len(candidates)}, score范围: {candidates['score'].min():.1f}~{candidates['score'].max():.1f}")
    # 检查是否有 bucket_ev 列
    if "bucket_ev" in candidates.columns:
        _has_bucket = (candidates["bucket_ev"] != candidates["model_ev"]).sum()
        if _has_bucket > 0:
            print(f"[{symbol}] bucket_ev 生效: {_has_bucket}/{len(candidates)} 个信号使用历史分桶 EV")
    
    # 注入 exec_ctx 的 SMC 结构信息（原 V37 的 build_exec_context）
    df_exec = add_all_indicators(df_exec, STRATEGY_PARAMS["wvf_std_mult"])
    df_macro = add_all_indicators(df_macro, STRATEGY_PARAMS["wvf_std_mult"])
    macro_ctx = build_macro_context(df_macro)
    exec_ctx = build_exec_context(df_exec)
    exec_ctx["data_source"] = "hf_auto"
    
    # Step 2: select_trades — 包含 Quality Gate + Top-N + 集群风险缩放 + 执行
    trades = _V56_ENGINE.select_trades(candidates)
    
    if trades is None or trades.empty:
        print(f"[{symbol}] V56.5 选择后无交易")
        return None
    else:
        print(f"[{symbol}] V56.5 执行后产生 {len(trades)} 笔交易")
    
    # 取最高 score 的交易作为本次推送
    best = trades.sort_values("score", ascending=False).iloc[0]
    
    direction = best.get("direction", None)
    if not direction:
        print(f"[{symbol}] 无有效方向")
        return None
    
                # 用 exec_ctx 计算 entry quality（SMC 结构验证）
    curr = df_exec.iloc[-1]
    entry_price = float(curr["close"])
    
    # ===== V56.5 SL/TP 统一重算（不再依赖 enrich 阶段的 initial_sl 列） =====
    # V56.5《_execute_one_v565》使用 stop_dist 计算 SL/TP，逻辑正确。
    # 但 trades DataFrame 的 initial_sl 列可能来自 enrich 阶段的旧数据，
    # 导致 Long SL > entry 或 Short SL < entry 的错误。
    # 修复：直接重复 V56.5 公式算出 SL/TP，与交易引擎一致。
    _atr_val = max(float(curr.get("ATRr_14", exec_ctx.get("atr", 0))), entry_price * 0.0025)
    _stop_dist = max(0.80 * _atr_val, entry_price * 0.0025)
    if direction == "Long":
        sl = entry_price - _stop_dist
        tp1 = entry_price + 1.00 * _stop_dist
        tp2 = entry_price + 1.80 * _stop_dist
        tp3 = entry_price + 2.80 * _stop_dist
    else:  # Short
        sl = entry_price + _stop_dist
        tp1 = entry_price - 1.00 * _stop_dist
        tp2 = entry_price - 1.80 * _stop_dist
        tp3 = entry_price - 2.80 * _stop_dist
    rr = round(float(best.get("estimated_rr", 1.82)), 2)
    score = float(best.get("score", 0))
    ev = float(best.get("model_ev", 0))
    print(f"[{symbol}] V56.5 SL/TP 重算: direction={direction} entry={entry_price:.2f} "
          f"sl={sl:.2f} tp1={tp1:.2f} tp2={tp2:.2f} tp3={tp3:.2f} "
          f"stop_dist={_stop_dist:.2f} atr={_atr_val:.2f}")

        # ===== 【新增20260729】Mud/低ADX Regime 强硬拦截 =====
    _regime_raw = str(best.get("regime", "unknown")).strip().lower()
    _adx_check = float(curr.get("ADX_14", exec_ctx.get("adx", 0)))
    _strong_exception = bool(exec_ctx.get("strong_smc_exception", False))
    _mud_cut_override = 1.0
    # V9 classifier 对 mud 已标记 tradable=False（通过 squeeze / adx_weak），
    # 但 select_v565_portfolio 可能绕过。这里自保：
    if "mud" in _regime_raw or "chaos" in _regime_raw:
        if _adx_check < 18:
            # mud + 极低ADX：原则上不交易
            if not _strong_exception:
                print(f"[{symbol}] Mud regime + ADX={_adx_check:.1f} < 18, 无强共振例外, 跳过")
                return None
            else:
                # 有强共振例外 → 大幅降仓标记
                print(f"[{symbol}] Mud regime + ADX={_adx_check:.1f} < 18, 有强共振例外, 标记降仓")
                _mud_cut_override = 0.3  # 仓位降至 30%
        elif _adx_check < 25:
            print(f"[{symbol}] Mud regime + ADX={_adx_check:.1f} < 25, 中等风险, 标记降仓")
            _mud_cut_override = 0.5
        else:
            print(f"[{symbol}] Mud regime 但 ADX={_adx_check:.1f} >= 25, 允许交易")

    # ===== 【安全校验】重算后的 SL 方向合理性 =====
    if direction == "Long" and sl > entry_price:
        print(f"[{symbol}] SL方向异常(重算后): Long SL({sl:.2f}) > 入场({entry_price:.2f}), atr={_atr_val:.2f} 异常小, 跳过")
        return None
    if direction == "Short" and sl < entry_price:
        print(f"[{symbol}] SL方向异常(重算后): Short SL({sl:.2f}) < 入场({entry_price:.2f}), atr={_atr_val:.2f} 异常小, 跳过")
        return None
    
        # ===== 【修复20260715】K线颜色 + ADX方向一致性检查 =====
    _candle_color = str(exec_ctx.get("curr_color", ""))
    _candle_adx = float(exec_ctx.get("adx", 0))
    _has_bot_div = bool(exec_ctx.get("has_bot_div", False))
    _has_top_div = bool(exec_ctx.get("has_top_div", False))
    _sqz_white_long = bool(exec_ctx.get("sqzmom_white_reversal_long", False))
    _sqz_white_short = bool(exec_ctx.get("sqzmom_white_reversal_short", False))
    _has_fe_bottom = bool(exec_ctx.get("fe_bottom", False))  # CM Williams Vix Fix 摸底信号
    _has_fe_top = bool(exec_ctx.get("fe_top", False))        # CM Williams Vix Fix 摸顶信号
    # 红色K线(看跌) + ADX>=25 = 强下跌趋势，此时做多需要底背离/白线反转/FE摸底之一
    if direction == "Long" and ("红" in _candle_color or "red" in _candle_color.lower()):
        if _candle_adx >= 25 and not _has_bot_div and not _sqz_white_long and not _has_fe_bottom:
            print(f"[{symbol}] 方向不一致: Long 但 K线红色(看跌) ADX={_candle_adx:.1f}(强趋势), 无底部反转信号, 跳过")
            return None
        elif _candle_adx >= 30:
            print(f"[{symbol}] 方向风险: Long 但 K线红色 ADX={_candle_adx:.1f}(强趋势), 继续但降低评分")
            score *= 0.7  # 红K+强趋势下做多评分打7折
        # 蓝色K线(看涨) + ADX>=25 = 强上涨趋势，此时做空需要顶背离/白线反转/FE摸顶之一
    if direction == "Short" and ("蓝" in _candle_color or "blue" in _candle_color.lower() or "bull" in _candle_color.lower()):
        if _candle_adx >= 25 and not _has_top_div and not _sqz_white_short and not _has_fe_top:
            print(f"[{symbol}] 方向不一致: Short 但 K线蓝色(看涨) ADX={_candle_adx:.1f}(强趋势), 无顶部反转信号, 跳过")
            return None
        elif _candle_adx >= 30:
            print(f"[{symbol}] 方向风险: Short 但 K线蓝色 ADX={_candle_adx:.1f}(强趋势), 继续但降低评分")
            score *= 0.7
    
    print(f"[{symbol}] V56.5 选定: {direction} score={score:.1f} ev={ev:.4f} "
        f"setup={best.get('setup_type','?')} price={entry_price:.2f}")
    
    # 【修复20260705】多空评分改为使用 exec_ctx 中的独立质量评分
    _exec_lq = float(exec_ctx.get("long_quality", 0))
    _exec_sq = float(exec_ctx.get("short_quality", 0))
    _use_long_score = _exec_lq if _exec_lq > 0 else (float(score) if direction == "Long" else 0.0)
    _use_short_score = _exec_sq if _exec_sq > 0 else (0.0 if direction == "Long" else float(score))

    # ===== 【优化2 - HTF Regime Filter】用 1H 数据校验大方向 =====
    _htf_state = get_htf_regime_filter().analyze(df_macro)
    result_htf_blocked = False
    if direction == "Long" and not _htf_state["allow_long"]:
        print(f"[{symbol}] HTF Regime 拦截 Long: 1H 方向={_htf_state['regime']} (EMA50={_htf_state.get('ema_fast')}, EMA200={_htf_state.get('ema_slow')})")
        result_htf_blocked = True
    elif direction == "Short" and not _htf_state["allow_short"]:
        print(f"[{symbol}] HTF Regime 拦截 Short: 1H 方向={_htf_state['regime']} (EMA50={_htf_state.get('ema_fast')}, EMA200={_htf_state.get('ema_slow')})")
        result_htf_blocked = True
    else:
        print(f"[{symbol}] HTF Regime 通过: 1H={_htf_state['regime']}, allow_long={_htf_state['allow_long']}, allow_short={_htf_state['allow_short']}")

        # ===== 【特征收集 - 用于优化4/5】构建特征字典 =====
    # 【修复20260726】注入 regime 信息，让 feature_penalty 能做 regime-aware 惩罚
    _regime_name = str(_htf_state.get("regime", "UNKNOWN")).upper().strip()
    _features = {
        "ema_trend": _htf_state.get("trend_strength", 0) > 0.4,
        "adx": float(exec_ctx.get("adx", 0)) > 25,
        "structure_break": bool(exec_ctx.get("liquidity_sweep_confirmed", False)),
        "momentum": abs(_exec_lq - _exec_sq) > 15 if (_exec_lq > 0 or _exec_sq > 0) else False,
        "trend_direction": direction == str(_htf_state.get("regime", "")).upper().replace("BULL", "Long").replace("BEAR", "Short") or False,
        "atr_expand": float(curr.get("ATRr_14", exec_ctx.get("atr", 0))) > float(curr.get("ATRr_14", 0)) * 1.2 if hasattr(curr, 'get') else False,
        "squeeze_release": str(exec_ctx.get("squeeze", "")).lower() in ("release", "squeeze_release"),
        "volume_break": float(curr.get("volume_ratio", 1)) > 1.5 if hasattr(curr, 'get') else False,
        "bb_width_expand": False,
        "rsi_momentum": abs(float(curr.get("rsi", 50)) - 50) > 20 if hasattr(curr, 'get') else False,
        "macd_cross": abs(float(curr.get("MACDh_12_26_9", 0))) > 0.0001 if hasattr(curr, 'get') else False,
        "price_acceleration": False,
        "volume_surge": float(curr.get("volume_ratio", 1)) > 2.0 if hasattr(curr, 'get') else False,
        "ema_alignment": _htf_state.get("regime") in ("BULL", "BEAR"),
        # 【新增20260726】注入 regime 字段，供 feature_penalty 动态调整惩罚系数
        "regime": _regime_name,
    }

        # ===== 【优化5 - Statistical EV】混合历史EV =====
    _blended_ev = get_statistical_ev().blend(model_ev=ev, features=_features)
    if _blended_ev != ev:
        print(f"[{symbol}] Statistical EV: model={ev:.4f} -> blended={_blended_ev:.4f}")

        # ===== 【V60 ML Decision Engine】LightGBM 概率评估（并行） =====
    _ml_score, _ml_ev, _ml_conf, _ml_active = _ML_DECISION.evaluate(
        exec_ctx=exec_ctx,
        curr_row=curr,
        regime=_regime_name,
        features_dict=_features,
        direction=direction,
    )
    if _ml_active:
        # ML 主管线评估
        _ml_prob = _ml_score / 100.0
        print(f"[{symbol}] ML引擎: P(win)={_ml_prob:.3f} EV={_ml_ev:.4f} "
              f"conf={_ml_conf:.3f} score={_ml_score:.1f}")
        # 如果 ML 概率 < 0.45，标记低置信度
        if _ml_prob < 0.45:
            print(f"[{symbol}] ML引擎 低概率: {_ml_prob:.3f} < 0.45, 标记降仓")
            _mud_cut_override = min(_mud_cut_override, 0.5)
    else:
        print(f"[{symbol}] ML引擎: 降级模式(人工权重) score={_ml_score:.1f}")

    # ===== 【闭环】FeedbackLoop 信号评估 =====
    _fb_features, _fb_raw_scores = _feedback.get_signal_features(
        reason=f"{best.get('setup_type','?')}_{best.get('gate_reason','PASSED')}",
        result={"score": score, "bullish_ob": exec_ctx.get("bullish_ob"), "bearish_ob": exec_ctx.get("bearish_ob"),
                "bullish_fvg": exec_ctx.get("bullish_fvg"), "bearish_fvg": exec_ctx.get("bearish_fvg")},
        exec_ctx=exec_ctx,
    )
    _fb_result = _feedback.evaluate_signal(
        regime=_regime_name,
        features=_fb_features,
        score=score,
        raw_feature_scores=_fb_raw_scores,
        base_ev=_blended_ev,
    )
    print(f"[{symbol}] FeedbackLoop: score={score:.1f} -> weighted={_fb_result['weighted_score']:.1f}, "
          f"confidence={_fb_result['confidence']:.3f}, ev={_fb_result['ev']:.4f}, "
          f"reject={_fb_result['should_reject']} (threshold={_fb_result['reject_threshold']})")

    # ===== 【V21 FeatureLearningEngine】特征权重调整 =====
    _fl_weighted_score = get_feature_learner().get_weighted_score(_fb_raw_scores)
    _fl_final_score = score * 0.7 + _fl_weighted_score * 0.3 if _fl_weighted_score > 0 else score
    _fl_final_score = min(100.0, _fl_final_score)
    if _fl_final_score != score:
        print(f"[{symbol}] FeatureLearning: score={score:.1f} -> adjusted={_fl_final_score:.1f} "
              f"(weights={get_feature_learner().get_all_weights()})")

        # 构建兼容返回格式
    return {
        "_mud_cut": _mud_cut_override,  # mud regime 降仓系数
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
        "bsl_level": float(exec_ctx.get("bsl_level") or 0.0),
        "ssl_level": float(exec_ctx.get("ssl_level") or 0.0),
        "is_bsl_swept": bool(exec_ctx.get("is_bsl_swept", False)),
        "is_ssl_swept": bool(exec_ctx.get("is_ssl_swept", False)),
        "bullish_ob": exec_ctx.get("bullish_ob", None),
        "bearish_ob": exec_ctx.get("bearish_ob", None),
        "bullish_fvg": exec_ctx.get("bullish_fvg", None),
        "bearish_fvg": exec_ctx.get("bearish_fvg", None),
        "funding_rate": None,
        # ===== 优化模块字段 =====
        "htf_state": _htf_state,
        "htf_blocked": result_htf_blocked,
        "features": _features,
        "blended_ev": _blended_ev,
        "_feedback_features": _fb_features,  # 【闭环】特征列表
        "_feedback_raw_scores": _fb_raw_scores,  # 【闭环】原始特征分数
        "_feedback_result": _fb_result,  # 【闭环】全文决策统计
        "_feedback_ev": _fb_result["ev"],  # 【闭环】FeedbackLoop EV
        "_feature_learning_score": _fl_final_score,  # 【V21】FeatureLearning 调整后分数
        "confidence": _fb_result["confidence"],  # 【闭环】置信度
        "grade_result": None,  # check_and_open 中填充
        "feature_penalty": 0.0,  # check_and_open 中填充
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
    """精简版 Observer 推送：只推结构级别事件，去掉所有冗余指标"""
    icons = {
        "CHOCH": "🔄", "LIQUIDITY_SWEEP": "🗑️",
        "DIVERGENCE_R": "🔮", "SQZMOM_WHITE": "⚪",
        "SQUEEZE_RELEASE": "💨",
    }
    icon = icons.get(ev["type"], "📊")
    type_names = {
        "CHOCH": "结构转变", "LIQUIDITY_SWEEP": "流动性扫单",
        "DIVERGENCE_R": "背离", "SQZMOM_WHITE": "动量衰竭",
        "SQUEEZE_RELEASE": "挤压释放",
    }
    type_name = type_names.get(ev["type"], ev["type"])
    dir_emoji = {"Long": "📈 多头", "Short": "📉 空头", "N/A": "⚖️ 中性"}

    # 方向由评分决定
    lp, sp = long_score, short_score
    score_dir = "Long" if lp >= sp else "Short"
    msg = (
        f"{icon} [{type_name}] {symbol}\n"
        f"方向: {dir_emoji.get(score_dir, dir_emoji['N/A'])} | {ev['desc']}\n"
        f"多头: {lp:.1f}分  空头: {sp:.1f}分 | 分差: {abs(lp-sp):.1f}分"
    )

    # CHOCH / 流动性事件附关键价位
    if ev["type"] in ("CHOCH", "LIQUIDITY_SWEEP"):
        if bsl_level > 0:
            msg += f"\nBSL: {bsl_level:.1f}"
        if ssl_level > 0:
            msg += f"  SSL: {ssl_level:.1f}"

    safe_send(msg, priority="OBSERVER")
    print(f"[{symbol}] Observer 推送: {ev['type']} {ev.get('dir','')}")
# Strategy 信号推送与去重
# ============================================================
_PROCESSED_SIGNALS: dict = {}
_SIGNAL_DIARY_PATH = Path("logs/signal_fingerprint_diary.jsonl")

def _load_processed_signals() -> None:
    """从磁盘加载已处理信号，确保进程重启后去重仍然生效。"""
    global _PROCESSED_SIGNALS
    try:
        p = _SIGNAL_DIARY_PATH
        if p.exists() and p.stat().st_size > 0:
            now = time.time()
            loaded = 0
            for line in p.read_text(encoding="utf-8").strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    sig_id = entry.get("signal_id", "")
                    ts = entry.get("ts", 0)
                    if sig_id and now - ts < 86400:
                        _PROCESSED_SIGNALS[sig_id] = ts
                        loaded += 1
                except Exception:
                    continue
            if loaded > 0:
                print(f"[_load_processed_signals] 从磁盘加载了 {loaded} 个已处理信号指纹")
    except Exception as e:
        print(f"[_load_processed_signals] 加载失败: {e}")

def _persist_signal_fingerprint(signal_id: str) -> None:
    """将信号指纹持久化到磁盘JSONL文件。"""
    try:
        _SIGNAL_DIARY_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {"signal_id": signal_id, "ts": time.time()}
        with open(_SIGNAL_DIARY_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[_persist_signal_fingerprint] 持久化失败: {e}")

# 模块加载时自动恢复已处理信号
_load_processed_signals()

def _signal_id(result: dict) -> str:
    """构建高精度信号指纹，确保每次扫描的每个信号都是唯一可追踪的。

    【修复20260730】移除了 entry_price 和 scan_slot，避免同一 K线信号
    因价格微小变化/扫描槽变化被当成不同信号。

    指纹包含：
    - symbol / direction（基础标识）
    - setup_type（模式类型：LIQUIDITY_SWEEP / WEAK_BOS / FVG_TOUCH 等）
    - idx（K线bar索引，唯一标识触发行情位置）
    - score + ev（量化特征，取整到 bucket）
    - regime（市场状态，区分同 setup 在不同环境下的信号）

    这样同一个15-min K线内，同一 setup_type 的不同扫描结果
    会被正确识别为重复信号。
    """
    symbol = result["symbol"]
    direction = result["direction"] or "NONE"
    setup_type = result.get("decision", {}).get("signal", {}).get("setup_type", result.get("reason", "UNKNOWN"))
    idx = result.get("decision", {}).get("signal", {}).get("idx", -1)
    score_bucket = int(result.get("score", 0) / 10) if result.get("score") else -1
    _ev = result.get("expected_value", 0.0)
    ev_bucket = f"{_ev:+.3f}"[:6]
    regime = str(result.get("regime", "UNKNOWN"))[:4]
    return f"{symbol}_{direction}_{setup_type}_idx{idx}_s{score_bucket}_ev{ev_bucket}_{regime}"

def _is_signal_processed(signal_id: str) -> bool:
    """信号去重：检查是否已处理过。

    修复时机：同一 signal_id 只允许触发一次，避免历史 K 线“诈尸”复活。
    通过 signal_id 永久阻止同一根 K 线上的同一个 Setup 再次开单。
    """
    if signal_id in _PROCESSED_SIGNALS:
        return True

    _PROCESSED_SIGNALS[signal_id] = time.time()
    # 持久化到磁盘，防止进程重启后丢失
    _persist_signal_fingerprint(signal_id)
    # 清理旧记录，保留最近 7 天信号指纹，避免无限增长
    stale_cutoff = time.time() - 86400 * 7
    stale = [k for k, v in _PROCESSED_SIGNALS.items() if v < stale_cutoff]
    for k in stale:
        del _PROCESSED_SIGNALS[k]
    return False

def check_and_open(result: dict | None) -> bool:
    """开单检查与推送

    保护链（按执行顺序）：
    1. result 非空检查
    2. 止损冷却 _check_cooldown()
    3. approved + direction 有效性
    4. HTF Regime 宏观方向拦截
    5. Score Grade 分级过滤
    6. Feature Penalty 特征惩罚
    7. Statistical EV Gate 动态阈值
    8. EV/Score 硬阈值兜底
    9. 多空评分差距 MIN_SCORE_GAP
    10. 信号去重 _is_signal_processed()
    11. 已有持仓检查 position_manager.exists()
    12. 趋势末端位置检查
    13. RR 硬校验 >= 1.0
    14. V37 Final Gate 最终闸门
    """
    if not result:
        print("[check_and_open] result 为空，拒绝开单")
        return False

    symbol = result.get("symbol", "?")
    direction = result.get("direction", None)

    # ---- 止损冷却 ----
    if not _check_cooldown(symbol):
        print(f"[{symbol}] GATE-2 cooling skip")
        return False

    approved = result.get("approved", False)
    if not approved or not direction:
        print(f"[{symbol}] GATE-3 approved={approved} direction={direction} 拒绝")
        return False
    
    # ===== 提取优化模块字段 =====
    htf_blocked = result.get("htf_blocked", False)
    features = result.get("features", {})
    blended_ev = result.get("blended_ev", result.get("expected_value", 0.0))
    
        # ===== 【优化2 - HTF Regime Filter】大方向拦截 =====
    if htf_blocked:
        print(f"[{symbol}] GATE-4 HTF Regime 拦截 {direction}, 但强信号(score={result.get('score',0):.1f} ev={blended_ev:.4f}) 允许继续")
        # ⚠️ TEMP: 绕过 HTF 拦截以验证开单流程
    
    # ===== 【优化5 - Statistical EV】使用 blended_ev 替代原始 ev =====
    # blended_ev = 历史实际 EV * 0.6 + model_ev * 0.4
    # 当历史样本不足时 = model_ev
    ev = blended_ev
    score = result.get("score", 0.0)
    
    # ===== 【V21 FeatureLearningEngine】权重调整分数 =====
    _fl_score = result.get("_feature_learning_score", 0.0)
    if _fl_score > 0 and _fl_score != score:
        print(f"[{symbol}] FeatureLearning: score={score:.1f} -> {_fl_score:.1f}")
        score = _fl_score

    # ===== 【优化3 - Score Grade】分级过滤 =====
    _grade_result = get_score_grader().grade(score=score, ev=ev, regime=result.get("regime", "UNKNOWN"))
    result["grade_result"] = _grade_result
    if not _grade_result["allow"]:
        print(f"[{symbol}] GATE-5 ScoreGrade 拒绝: score={score:.1f} ev={ev:.4f} grade={_grade_result['grade']} (min_score={_grade_result['min_score_for_grade']}, min_ev={_grade_result['min_ev_for_grade']})")
        return False
    else:
        print(f"[{symbol}] ScoreGrade 通过: score={score:.1f} ev={ev:.4f} grade={_grade_result['grade']}")
    
    # ===== 【闭环】FeedbackLoop EV 决策替代固定阈值 =====
    _fb_res = result.get("_feedback_result", {})
    if _fb_res.get("should_reject", False):
        print(f"[{symbol}] FeedbackLoop 拒绝: ev={_fb_res.get('ev', 0):.4f}, "
              f"confidence={_fb_res.get('confidence', 0):.3f} < "
              f"threshold={_fb_res.get('reject_threshold', 0.30)}")
        return False
    
    # ===== 【优化4 - Feature Penalty】特征重叠惩罚 =====
    _overlap_penalty = calculate_feature_overlap(features)
    result["feature_penalty"] = _overlap_penalty
    _adjusted_score = apply_feature_penalty(score, features)
    print(f"[{symbol}] FeaturePenalty: 原始score={score:.1f} -> 调整后={_adjusted_score:.1f} (penalty={_overlap_penalty})")
    if _adjusted_score < MIN_SCORE_FOR_PUSH:
        print(f"[{symbol}] FeaturePenalty 后 score={_adjusted_score:.1f}<{MIN_SCORE_FOR_PUSH} skip")
        return False
    # 用调整后的 score 替代原始 score 用于后续检查
    score = _adjusted_score
    
    # ===== 【优化1 - Statistical EV Gate】动态EV阈值 =====
    _regime = str(result.get("regime", "unknown"))
    _vol_state = str(result.get("vol_state", "unknown"))
    _volatility = 0.02
    if "high" in _vol_state.lower():
        _volatility = 0.04
    elif "low" in _vol_state.lower():
        _volatility = 0.01
    _ev_gate_passed = get_statistical_ev_gate().allow(
        model_ev=ev,
        regime=_regime,
        confidence=0.5,
        volatility=_volatility,
    )
    _ev_threshold = get_statistical_ev_gate().dynamic_ev_threshold(_regime, 0.5, _volatility)
    if not _ev_gate_passed:
        print(f"[{symbol}] StatisticalEVGate 拒绝: ev={ev:.4f} < threshold={_ev_threshold} (regime={_regime}, vol={_vol_state})")
        return False
    else:
        print(f"[{symbol}] StatisticalEVGate 通过: ev={ev:.4f} >= threshold={_ev_threshold}")
    
    # ---- 原有低阈值检查（已由 StatisticalEVGate 覆盖，保留为安全兜底）----
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

    # ===== 【修复20260726】动态 Gap 阈值 =====
    # 在震荡市（ADX<25或CHOP/RANGE）降低 gap 要求
    _regime_for_gap = str(result.get("regime", "UNKNOWN")).upper().strip()
    _adx_for_gap = float(result.get("adx", 0) or result.get("exec_ctx", {}).get("adx", 0))
    _is_chop = _regime_for_gap in ("CHOP", "RANGE") or _adx_for_gap < 25
    _dynamic_gap = MIN_SCORE_GAP * (0.66 if _is_chop else 0.80)  # ⚠️ 降级: V56.5信号自证实方向，gap仅做噪音过滤
    print(f"[{symbol}] GapCheck: regime={_regime_for_gap} adx={_adx_for_gap:.1f} "
        f"is_chop={_is_chop} dynamic_gap={_dynamic_gap} "
        f"long={long_score:.1f} short={short_score:.1f} gap={score_gap:.1f}")

        # ⚠️ 修复: V56.5 引擎决定方向，GapCheck 仅要求总分差 >= threshold
    # 不要求特定方向的分数必须更高 (V56.5 的 direction 和 exec_ctx score 可能来自不同指标源)
    gap_passed = abs(long_score - short_score) >= _dynamic_gap

    if not gap_passed:
                # 即使 gap 不满足，检查是否进入 probe 模式
        # probe 模式：EV>0.04 且有 FVG 时允许小仓位试单
        _can_probe = False
        _has_fvg = bool(result.get("bullish_fvg") or result.get("bearish_fvg"))
        if ev > 0.04 and _has_fvg:
            _can_probe = True
            print(f"[{symbol}] PROBE 模式触发: ev={ev:.4f}>0.04, fvg={_has_fvg}")

        if not _can_probe:
            print(f"[{symbol}] Gap 不满足且非 probe 模式, skip. "
                f"({long_score:.1f} vs {short_score:.1f} gap={score_gap:.1f}<{_dynamic_gap})")
            return False
        else:
            # probe 模式：绕过 gap 检查，但标记为小仓位
            print(f"[{symbol}] PROBE 模式: gap={score_gap:.1f}<{_dynamic_gap} 但 EV+FVG 通过, 允许试单")
            result["is_probe"] = True
        
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

        # ===== 【修复20260715】RR 软校验：RR < 1.0 仅降仓，不拒单 =====
    # V56.5 estimated_rr 来自信号bar的原始估算，SL纠正后实际RR可能不同
    actual_rr = result.get("rr", 0) or 0
    if actual_rr < 1.0:
        print(f"[{symbol}] RR={actual_rr:.2f} < 1.0, 降仓处理 (size*=0.5)")
        result["size"] = result.get("size", 0.05) * 0.5
        
        # ===== V37 Final Gate（V56.5 管线的最终闸门）=====
    _v37_decision = {
        "approved": True,
        "direction": direction,
        "reason": "V56.5_QUALITY_PASSED",
        "score": score,
        "expected_value": ev,
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

        # ===== 【SmartPositionSizer】智能仓位计算 =====
    _calib = _fb_res.get("calibration", {})
    _sizer = get_smart_sizer()
    # 【新增20260729】ATR 百分比 + 风险金额限制
    _atr_pct = float(result.get("atr", 0)) / max(float(entry if entry > 0 else result.get("entry", 1)), 1e-8)
    _entry_p = float(result.get("entry", 0))
    _sl_p = float(result.get("sl", 0))
    _size_result = _sizer.calculate(
        score=score,
        confidence=result.get("confidence", 0.5),
        avg_win_r=_calib.get("avg_win_r", 0.50),
        avg_loss_r=_calib.get("avg_loss_r", 0.50),
        base_leverage=0.05,
        grade_size_mult=_grade_result.get("size_mult", 1.0),
        env_size_mult=float(_v37_size_mult),
        regime=str(result.get("regime", "UNKNOWN")),
        volatility=str(result.get("vol_state", "normal")),
        atr_pct=_atr_pct,           # 【新增】ATR 自适应
        account_balance=1000.0,     # 【新增】账户余额（默认1000 USDT）
        entry_price=_entry_p,       # 【新增】入场价
        sl_price=_sl_p,             # 【新增】止损价
    )
    result["size"] = _size_result["final_size"]
    result["_sizer"] = _size_result  # 调试用
        # ===== 【新增20260729】Mud regime 额外降仓 =====
    _mud_cut = result.get("_mud_cut", 1.0)
    if _mud_cut < 1.0:
        _size_result["final_size"] *= _mud_cut
        _size_result["final_size"] = max(0.005, _size_result["final_size"])
        print(f"[{symbol}] Mud regime: 仓位额外缩减至 {_mud_cut*100:.0f}% -> final_size={_size_result['final_size']:.4f}")

    print(f"[{symbol}] SmartSizer: final_size={_size_result['final_size']:.4f} "
          f"(Kelly={_size_result['kelly_pct']:.3f} grade={_size_result['grade_mult']:.2f} "
          f"env={_size_result['env_mult']:.2f} regime={_size_result['regime_mult']:.2f} "
          f"vol={_size_result['vol_mult']:.2f} cons_loss={_size_result['cons_loss_mult']:.2f} "
          f"score_mult={_size_result['score_mult']:.2f})")

    # ===== 【新增20260723】DailyRiskGuard 日风险检查 =====
    if not _risk_guard.can_trade():
        print(f"[{symbol}] DailyRiskGuard 拦截: 日内风控限制")
        return False

    # ===== 【闭环】旧 Weighter 仅用于统计学习跟踪（不再影响评分决策） =====
    # 评分加权已由 FeedbackLoop.evaluate_signal 在 scan_and_decide 中完成
    # 此处只更新 Weighter 统计，不重复加权 score
    _raw_feature_scores = {}
    if "OB" in str(reason) or result.get("bullish_ob") or result.get("bearish_ob"):
        _raw_feature_scores["OB"] = score * 0.15
    if "FVG" in str(reason) or result.get("bullish_fvg") or result.get("bearish_fvg"):
        _raw_feature_scores["FVG"] = score * 0.10
    if "CHOCH" in str(reason) or "MSS" in str(reason):
        _raw_feature_scores["CHOCH"] = score * 0.20
    if "SQZMOM" in str(reason):
        _raw_feature_scores["SQZMOM"] = score * 0.15
    if features.get("squeeze_release") or "DIVERGENCE" in str(reason):
        _raw_feature_scores["DIVERGENCE"] = score * 0.12
    if _raw_feature_scores:
        _weighted_score = _weighter.get_weighted_score(_raw_feature_scores)
        print(f"[{symbol}] AdaptiveWeighter: 统计跟踪 (不影响评分) raw={_raw_feature_scores} weighted={_weighted_score:.2f}")
        result["weighted_score"] = _weighted_score

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

    msg = "\n".join([
        f"{dir_emoji2} [{symbol}] {dir_cn} 信号通过",
        f"入场: {entry:.2f}  SL: {sl:.2f}  TP1: {tp1:.2f}  TP2: {tp2:.2f}  TP3: {tp3:.2f}",
        f"评分: {score:.1f} | EV: {ev:.4f} | RR: {rr:.2f}",
        f"原因: {reason}",
        f"多头: {lp_s:.1f}分  空头: {sp_s:.1f}分  分差: {sg:.1f}分",
    ])
    safe_send(msg, priority="TRADE")
    print(f"[{symbol}] Strategy open before update: exists={position_manager.exists(symbol)} current={position_manager.get(symbol)}")
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
        "open_score": score,  # 【闭环】用于平仓时回传 Calibrator
        "open_confidence": result.get("confidence", 0.5),  # 【闭环】
        "open_regime": str(result.get("regime", "UNKNOWN")),  # 【闭环】用于平仓时更新 RegimeFeatureStats
        "open_features": result.get("_feedback_features", []),  # 【闭环】用于平仓时更新
    })
    print(f"[{symbol}] Strategy open after update: exists={position_manager.exists(symbol)} current={position_manager.get(symbol)}")
    print(f"[{symbol}] Strategy open pushed (EV={ev:.4f}, score={score:.1f})")

    # ===== 【新增20260723】SignalTracker 记录开单 =====
    try:
        _tracker_signal_id = _tracker.record_signal({
            "symbol": symbol,
            "direction": direction,
            "score": score,
            "ev": ev,
            "features": features,
            "entry_price": entry,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "rr": rr,
            "regime": regime,
            "setup_type": reason,
            "book": book,
        })
        # 存入 position_manager 供平仓时更新 outcome
        _pos_data = position_manager.get(symbol)
        if _pos_data:
            _pos_data["tracker_signal_id"] = _tracker_signal_id
            position_manager.update(symbol, _pos_data)
            _fb_raw_scores = result.get("_feedback_raw_scores", {})
            if _tracker_signal_id and _fb_raw_scores:
                _feature_learner.record_features(signal_id=_tracker_signal_id, features=_fb_raw_scores)
    except Exception as _tracker_e:
        print(f"[SignalTracker] 记录开单失败: {_tracker_e}")
    
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

    # ===== 【修复20260721】信号后验验证日志 =====
    try:
        # 从 result 中提取未来 K线（用于计算 max_forward/adverse R）
        _audit_df_exec = result.get("df_exec")
        _audit_future_prices = []
        if _audit_df_exec is not None and hasattr(_audit_df_exec, "iloc"):
            # 找到当前信号 idx 在 df_exec 中的位置
            _audit_signal_idx = result.get("decision", {}).get("signal", {}).get("idx", None)
            if _audit_signal_idx is not None and isinstance(_audit_signal_idx, (int, float)):
                _audit_start = int(_audit_signal_idx) + 1  # 开单后的下一根K线
                _audit_end = min(_audit_start + POSTHOC_FUTURE_BARS, len(_audit_df_exec))
                if _audit_start < len(_audit_df_exec):
                    _audit_future_df = _audit_df_exec.iloc[_audit_start:_audit_end]
                    _audit_future_prices = [float(x) for x in _audit_future_df["close"].tolist()]
                    _audit_snapshot = {
                        "symbol": symbol,
                        "direction": direction,
                        "entry": entry,
                        "sl": sl,
                        "tp1": tp1,
                        "tp2": tp2,
                        "tp3": tp3,
                        "rr": rr,
                        "score": score,
                        "ev": ev,
                        "regime": result.get("regime", "unknown"),
                        "vol_state": result.get("vol_state", "unknown"),
                        "setup_type": str(reason),
                        "book": book,
                        "adx": result.get("adx", 0),
                        "atr": result.get("atr", 0),
                        "rsi": result.get("rsi", 0),
                        "volume_ratio": result.get("volume_ratio", 1.0),
                    }
                    # 计算后验 R
                    _mf, _ma, _fr, _er = _compute_future_r(
                        entry=entry, sl=sl, direction=direction,
                        tp1=tp1, tp2=tp2,
                        future_df=_audit_future_df, max_bars=POSTHOC_FUTURE_BARS,
                    )
                else:
                    _mf, _ma, _fr, _er = None, None, None, "NO_FUTURE_DATA"
        signal_audit_log.record_open(sig_id, _audit_snapshot, _audit_future_prices)

        # 实时日志：打印后验预测
        if _mf is not None:
            print(f"[信号后验] {sig_id} "
                f"max_forward_r={_mf:.2f} max_adverse_r={_ma:.2f} "
                f"final_r={_fr:.2f} exit={_er}")
            # 存入 position_manager，供 check_trailing 平仓时更新
            _pos_data2 = position_manager.get(symbol)
            if _pos_data2:
                _pos_data2["signal_id"] = sig_id
                _pos_data2["audit_forward"] = _mf
                _pos_data2["audit_adverse"] = _ma
                _pos_data2["audit_final_r"] = _fr
                _pos_data2["audit_exit"] = _er
                position_manager.update(symbol, _pos_data2)
    except Exception as _audit_e:
        print(f"[SignalAuditLog] 后验记录异常: {_audit_e}")
        
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
            safe_send(msg, priority="TRADE")
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

                # ===== 【修复20260721】后验验证：更新平仓结果 =====
                try:
                    _audit_sig_id = pos.get("signal_id", "")
                    if _audit_sig_id:
                        _audit_fwd = pos.get("audit_forward") or mfe_val
                        _audit_adv = pos.get("audit_adverse") or pos.get("mae", 0.0)
                        signal_audit_log.record_close(
                            signal_id=_audit_sig_id,
                            final_pnl_r=profit_r2,
                            max_forward_r=_audit_fwd,
                            max_adverse_r=_audit_adv,
                            exit_reason=stage_label,
                        )
                        print(f"[信号后验][{stage_label}] {_audit_sig_id} pnl_r={profit_r2:.2f}")
                except Exception as _tp_audit_e:
                    print(f"[SignalAuditLog] TP后验更新失败: {_tp_audit_e}")

                                # ===== 【闭环】FeedbackLoop 全链路更新 =====
                try:
                    _ts_id = pos.get("tracker_signal_id", "")
                    if _ts_id:
                        _tracker.update_outcome(signal_id=_ts_id, final_r=profit_r2)
                    _risk_guard.on_trade_closed(r=profit_r2)
                    # 【SmartPositionSizer】记录结果用于连续亏损统计
                    get_smart_sizer().record_outcome(pnl_r=profit_r2)
                    # FeedbackLoop 闭环更新
                    _open_score = pos.get("open_score", 0)
                    _open_conf = pos.get("open_confidence", 0.5)
                    _open_regime = pos.get("open_regime", "UNKNOWN")
                    _open_features = pos.get("open_features", [])
                    if "OB" in str(pos.get("last_sl_msg", "")):
                        _open_features = list(set(_open_features + ["OB"]))
                    if profit_r2 > 0 and "CHOCH" not in _open_features:
                        _open_features = list(set(_open_features + ["CHOCH"]))
                    if _open_score > 0:
                        _feedback.on_trade_closed(
                            regime=_open_regime,
                            features=_open_features,
                            score=_open_score,
                            confidence=_open_conf,
                            pnl_r=profit_r2,
                            direction=pos.get("direction", ""),
                        )
                        # ===== 【V21 FeatureLearningEngine】Outcome 闭环更新 =====
                        if _ts_id:
                            _feature_learner.update(
                                signal_id=_ts_id,
                                pnl_r=profit_r2,
                            )
                        # 每日监控面板更新
                        try:
                            get_daily_panel().on_trade_closed(
                                regime=_open_regime,
                                features=_open_features,
                                score=_open_score,
                                confidence=_open_conf,
                                pnl_r=profit_r2,
                                direction=pos.get("direction", ""),
                            )
                        except Exception as _panel_e:
                            print(f"[DailyPanel] TP更新异常: {_panel_e}")
                        # 同时更新旧的 Weighter（兼容旧代码）
                        _weighter.update(features=_open_features, outcome_r=profit_r2)
                except Exception as _new_tools_e:
                    print(f"[NewTools] TP平仓更新异常: {_new_tools_e}")
            except Exception:
                pass

    # ---- 止损检查 ----
    if direction == "Long" and current_price <= pos["current_sl"]:
        _trigger_stop_loss(symbol, pos, current_price)
    elif direction == "Short" and current_price >= pos["current_sl"]:
        _trigger_stop_loss(symbol, pos, current_price)
    
    # ===== 【修复20260817】追踪止损/分批止盈后回写 position_manager =====
    if action != "HOLD":
        try:
            from state.position_manager import position_manager
            _pm_pos = position_manager.get(symbol)
            if _pm_pos and (
                _pm_pos.get("current_sl") != pos.get("current_sl") or
                _pm_pos.get("stage") != pos.get("stage")
            ):
                _pm_pos["current_sl"] = pos.get("current_sl")
                _pm_pos["stage"] = pos.get("stage")
                _pm_pos["last_sl_msg"] = pos.get("last_sl_msg", "")
                position_manager.update(symbol, _pm_pos)
        except Exception:
            pass


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
    safe_send(msg, priority="TRADE")

    try:
        # 修复：用 R 倍数替代百分比（SL 距离 = 1R）
        _risk_dist = abs(pos["entry"] - pos["current_sl"])
        if _risk_dist > 1e-12:
            if pos["direction"] == "Long":
                profit_r = (current_price - pos["entry"]) / _risk_dist
            else:
                profit_r = (pos["entry"] - current_price) / _risk_dist
        else:
            profit_r = pnl_pct / 100.0  # 兜底
        
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

    # profit_r 已在上方 try 块内用 R 倍数公式重新计算
    # ===== 【修复20260721】后验验证：更新平仓结果 =====
    try:
        _audit_sig_id = pos.get("signal_id", "")
        if _audit_sig_id:
            _audit_fwd = pos.get("audit_forward") or (profit_r if profit_r > 0 else 0.0)
            _audit_adv = pos.get("audit_adverse") or (abs(profit_r) if profit_r < 0 else 0.0)
            signal_audit_log.record_close(
                signal_id=_audit_sig_id,
                final_pnl_r=profit_r,
                max_forward_r=_audit_fwd,
                max_adverse_r=_audit_adv,
                exit_reason="SL",
            )
            print(f"[信号后验][SL] {_audit_sig_id} pnl_r={profit_r:.2f}")
    except Exception as _sl_audit_e:
        print(f"[SignalAuditLog] 止损后验更新失败: {_sl_audit_e}")

        # ===== 【闭环】止损时更新 FeedbackLoop =====
    try:
        _ts_id = pos.get("tracker_signal_id", "")
        if _ts_id:
            _tracker.update_outcome(signal_id=_ts_id, final_r=profit_r)
        _risk_guard.on_trade_closed(r=profit_r)
        # 【SmartPositionSizer】记录止损结果用于连续亏损统计
        get_smart_sizer().record_outcome(pnl_r=profit_r)
        # FeedbackLoop 闭环更新
        _open_score = pos.get("open_score", 0)
        _open_conf = pos.get("open_confidence", 0.5)
        _open_regime = pos.get("open_regime", "UNKNOWN")
        _open_features = pos.get("open_features", [])
        if _open_score > 0:
            _feedback.on_trade_closed(
                regime=_open_regime,
                features=_open_features or ["CHOCH"],
                score=_open_score,
                confidence=_open_conf,
                pnl_r=profit_r,
                direction=pos.get("direction", ""),
            )
            # ===== 【V21 FeatureLearningEngine】Outcome 闭环更新（止损） =====
            if _ts_id:
                _feature_learner.update(
                    signal_id=_ts_id,
                    pnl_r=profit_r,
                )
            # 每日监控面板更新
            try:
                get_daily_panel().on_trade_closed(
                    regime=_open_regime,
                    features=_open_features or ["CHOCH"],
                    score=_open_score,
                    confidence=_open_conf,
                    pnl_r=profit_r,
                    direction=pos.get("direction", ""),
                )
            except Exception as _panel_e:
                print(f"[DailyPanel] SL更新异常: {_panel_e}")
        _weighter.update(features=["CHOCH"], outcome_r=profit_r)
    except Exception as _sl_new_e:
        print(f"[NewTools] 止损更新异常: {_sl_new_e}")

    position_manager.remove(symbol)


# ============================================================
# 自动交易主循环
# ============================================================
async def _recover_positions():
    """【新增20260729】启动时从 TradeJournal 恢复持仓追踪。
    
    当系统重启后，position_manager 丢失持仓数据，
    但 TradeJournal 仍有 OPEN 记录。需恢复追踪。
    """
    global _RECOVERED_POSITIONS
    if _RECOVERED_POSITIONS:
        return
    _RECOVERED_POSITIONS = True

    try:
        open_positions = trade_journal.get_open_positions()
        if not open_positions:
            print("[恢复持仓] TradeJournal 无 OPEN 记录")
            return
        print(f"[恢复持仓] TradeJournal 发现 {len(open_positions)} 笔 OPEN 记录")
        
        # 【修复】position_manager 每 symbol 只支持一笔持仓
        # 同一 symbol 多笔 OPEN 只取最新（open_time 最大）的那笔
        _latest_per_symbol: dict[str, dict] = {}
        for op in open_positions:
            sym = op.get("symbol", "")
            if not sym:
                continue
            t = op.get("open_time", "")
            if sym not in _latest_per_symbol or t > _latest_per_symbol[sym].get("open_time", ""):
                _latest_per_symbol[sym] = op
        
        restored_count = 0
        for symbol, op in sorted(_latest_per_symbol.items()):
            oid = op.get("order_id", "")
            direction = op.get("direction", "")
            if not symbol or not oid:
                continue
            # 如果 position_manager 已存在（正常重启后写了持久化），跳过
            if position_manager.exists(symbol):
                print(f"[恢复持仓] {symbol} 已在 position_manager 中，跳过")
                continue
            # 从 CSV 重建持仓信息
            try:
                entry = float(op.get("open_price", 0))
                sl = float(op.get("sl", 0))
                tp1 = float(op.get("tp1", 0))
                tp2 = float(op.get("tp2", 0))
                tp3 = float(op.get("tp3", 0))
                score = float(op.get("score", 0))
                regime = op.get("regime", "UNKNOWN")
            except (ValueError, TypeError):
                continue
            if entry <= 0 or sl <= 0:
                print(f"[恢复持仓] {symbol} 数据无效 entry={entry} sl={sl} 跳过")
                continue
            # 立即检查价格：如果价格已远超 SL，直接平仓
            _live_price = await _fetch_ticker_price(symbol)
            _price_ok = True
            if _live_price and _live_price > 0:
                if direction == "Long" and _live_price <= sl:
                    _price_ok = False
                    _sl_hit = True
                    _reason = "SL"
                elif direction == "Short" and _live_price >= sl:
                    _price_ok = False
                    _sl_hit = True
                    _reason = "SL"
                else:
                    _sl_hit = False
                    _reason = ""
                if _sl_hit:
                    _risk_dist = abs(entry - sl) if sl != entry else 1
                    _pnl_r = (_live_price - entry) / _risk_dist if direction == "Long" else (entry - _live_price) / _risk_dist
                    trade_journal.close_trade(
                        order_id=oid,
                        close_price=_live_price,
                        pnl_r=_pnl_r,
                        exit_reason=_reason,
                        note="恢复持仓-重启时已超SL",
                    )
                    print(f"[恢复持仓] {symbol} {direction} @ {entry} 重启时已超SL, 直接平仓 R={_pnl_r:.2f}")
                    restored_count += 1
                    continue
            # 重建 position
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
                "order_id": oid,
                "open_score": score,
                "open_confidence": 0.5,
                "open_regime": regime,
                "open_features": ["RECOVERED"],
            })
            print(f"[恢复持仓] ✅ {symbol} {direction} @ {entry} order_id={oid}")
            restored_count += 1
        
        if restored_count > 0:
            print(f"[恢复持仓] 总计恢复/处理 {restored_count} 笔")
            # 推送启动通知到 Telegram
            safe_send(f"🔄 【启动恢复】已处理 {restored_count}/{len(_latest_per_symbol)} 笔持仓", priority="SYSTEM")
    except Exception as e:
        print(f"[恢复持仓] 异常: {e}")
        traceback.print_exc()


async def main_loop():
    """
    自动交易主循环：定期扫描信号并执行
    """
    global _ML_RETRAIN_COUNTER  # 【修复】声明全局变量，避免 += 1 时 UnboundLocalError
    print("[hf_auto_trader] 自动信号扫描主循环已启动...")
    await asyncio.sleep(5)  # 启动缓冲
    
    # ===== 【新增20260729】启动时恢复持仓 =====
    await _recover_positions()
    
    # ===== 【新增20260729】强制输出历史性能报告 =====
    if not _RECOVERED_POSITIONS:
        pass  # 已在 _recover_positions 中执行
    try:
        _report_text = trade_journal.generate_report()
        print(f"[性能报告]\n{_report_text}")
        # 强制推送一次报告到 Telegram
        safe_send(f"📊 【启动时性能报告】\n{_report_text}", priority="SYSTEM")
    except Exception as _rep_e:
        print(f"[性能报告] 生成失败: {_rep_e}")

    while True:
        try:
            for symbol in SYMBOLS:
                try:
                    print(f"[hf_auto_trader] 正在扫描 {symbol}...")
                    result = await scan_and_decide(symbol)
                    print(f"[hf_auto_trader] {symbol} scan_and_decide 返回: {'非空' if result else 'None'}")
                except Exception as scan_e:
                    print(f"[{symbol}] scan_and_decide 异常: {scan_e}")
                    traceback.print_exc()
                    result = None
                
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
                            safe_send(_summary_msg, priority="OBSERVER")
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

                                        # ---- 【持仓强制平仓检查】TradeJournal OPEN 但 position_manager 无记录 ----
            all_positions = position_manager.get()  # 【修复】提前获取持仓，避免后面引用时未定义
            try:
                _tj_opens = trade_journal.get_open_positions()
                if _tj_opens:
                    tracked_symbols = set(all_positions.keys()) if all_positions else set()
                    for _tj_pos in _tj_opens:
                        _sym = _tj_pos.get("symbol", "")
                        _oid = _tj_pos.get("order_id", "")
                        if _sym and _sym not in tracked_symbols:
                            # position_manager 已丢失记录，强制查价格
                            _forced_price = await _fetch_ticker_price(_sym)
                            if _forced_price and _forced_price > 0:
                                # 用 open_price 和 sl 判断盈亏
                                try:
                                    _e = float(_tj_pos.get("open_price", 0))
                                    _s = float(_tj_pos.get("sl", 0))
                                    _d = _tj_pos.get("direction", "")
                                    _risk = abs(_e - _s) if _s != _e else 1
                                    if _d == "Long":
                                        _pnl = (_forced_price - _e) / _risk
                                        _hit_sl = _forced_price <= _s
                                    else:
                                        _pnl = (_e - _forced_price) / _risk
                                        _hit_sl = _forced_price >= _s
                                    _reason = "SL" if _hit_sl else "FORCE_CLOSE"
                                    trade_journal.close_trade(
                                        order_id=_oid,
                                        close_price=_forced_price,
                                        pnl_r=_pnl,
                                        exit_reason=_reason,
                                        note="position_manager恢复丢失-强制平仓",
                                    )
                                    print(f"[强制平仓] {_sym} {_oid} {_reason} @ {_forced_price} R={_pnl:.2f}")
                                except Exception as _fe:
                                    print(f"[强制平仓] 异常: {_fe}")
            except Exception as _force_e:
                print(f"[强制平仓检查] 异常: {_force_e}")

            # ---- 【每日监控面板】跨日数据报告 ----
            try:
                _panel.try_send_report(safe_send, _panel_today_sent)
            except Exception as _panel_report_e:
                print(f"[DailyPanel] 报告推送异常: {_panel_report_e}")

            # ---- 【ML 后台重训】每 10 轮执行一次 ----
            _ML_RETRAIN_COUNTER += 1
            if _ML_RETRAIN_COUNTER >= 10:
                _ML_RETRAIN_COUNTER = 0
                try:
                    _retrained = await asyncio.to_thread(_ML_DECISION.retrain_if_needed)
                    if _retrained:
                        print("[ML引擎] ✅ 后台重训完成，已切换到 ML 主管线")
                except Exception as _ml_retrain_e:
                    print(f"[ML引擎] 重训异常: {_ml_retrain_e}")

            # ---- 【第 3 步】持仓追踪 ----
            try:
                all_positions = position_manager.get()
                if all_positions:
                    for sym, pos in list(all_positions.items()):
                        try:
                            curr_price = await _fetch_ticker_price(sym)
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
# ============================================================
# 优化后的 SL/TP 计算模块（20260714）
# ============================================================
def calculate_risk_levels(entry_price, atr_value, side, sl_multiplier=1.5, tp_multiplier=2.0):
    """统一方向 SL/TP 计算

    使用方向乘数：做多为 1，做空为 -1
    统一计算公式，利用乘数自动处理方向

    Args:
        entry_price: 入场价
        atr_value: ATR 值
        side: "LONG" 或 "SHORT"
        sl_multiplier: SL 距离的 ATR 倍数，默认 1.5
        tp_multiplier: TP 距离的 ATR 倍数，默认 2.0

    Returns:
        (sl_price, tp_price)
        Long:  sl = entry - atr * mult, tp = entry + atr * mult
        Short: sl = entry + atr * mult, tp = entry - atr * mult
    """
    dir_mult = 1 if side.upper() == "LONG" else -1
    sl_distance = atr_value * sl_multiplier * dir_mult
    tp_distance = atr_value * tp_multiplier * dir_mult
    sl_price = entry_price - sl_distance  # Short: -(-distance) = +distance, SL 在上方
    tp_price = entry_price + tp_distance  # Short: +(-distance) = -distance, TP 在下方
    return sl_price, tp_price

