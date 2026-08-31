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
from utils.structured_logger import slog

# 确保根目录在 sys.path 中
_root = Path(__file__).parent.absolute()
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import pandas as pd
import uuid

# ---------- 基础指标与策略模块 ----------
from indicators.basic import add_all_indicators, calculate_advanced_sqzmom
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
from analytics.trade_funnel import trade_funnel  # V59.7 漏斗统计
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
from state.signal_deduper import signal_deduper
from state.position_reconciler import position_reconciler
from utils.safe_extract import safe_get, safe_get_str, safe_get_float, safe_get_bool

# ---------- 【新增20260723】工具类导入 ----------
from utils.adaptive_features import AdaptiveFeatureWeighter
from utils.probability_calibrator import ProbabilityEngine as ProbabilityCalibrator
from utils.feedback_loop import FeedbackLoop  # 全链路闭环
from utils.signal_tracker import SignalTracker
from utils.daily_risk_guard import DailyRiskGuard
from utils.signal_audit_log import signal_audit_log
from utils.reject_audit import get_reject_audit
from utils.smart_position_sizer import SmartPositionSizer, get_smart_sizer
from utils.smart_position_sizer_v2 import SmartPositionSizerV2, get_smart_sizer_v2
from risk.equity_circuit_breaker import EquityCircuitBreaker
from analytics.feature_learning import FeatureLearningEngine, get_feature_learner
from utils.daily_panel import DailyPanel, get_daily_panel
from utils.event_bus import emit
import utils.v6_event_hooks



# ---------- EVRealityGuard (ML EV Guard) ----------
from utils.ev_reality_guard import EVRealityGuard
# ---------- V60.5 标准融合层 (Decision Fusion Layer) ----------
from ml.decision_fusion import DecisionFusionLayer, FusionInput, get_decision_fusion

# ---------- V58.6 统一事件日志与结果回填 ----------
from analytics.event_schema import event_logger
from analytics.outcome_db import OutcomeDatabase
from analytics.feature_hash import generate_feature_hash
from analytics.exit_event_logger import exit_logger

# ---------- 全局参数 ----------
MAX_DRAWDOWN_PCT = 15.0 
_peak_equity = 0.0 

# ---------- 【新增】K线版本缓存：避免K线未更新时重复全量重算 ----------
_last_bar_dt_by_symbol: dict = {} 

# ---------- 资金曲线熔断器 + V2 仓位管理器 ----------
_breaker = EquityCircuitBreaker(max_daily_loss_r=3.0, max_consec_losses=3)
_sizer_v2 = get_smart_sizer_v2() 

# ---------- 订单追踪 ----------
from execution.order_tracker import OrderTracker, get_order_tracker
from execution.probe_manager import probe_manager

# ============================================================
# 交易配置
# ============================================================
SYMBOLS = ["BTC/USDT", "ETH/USDT"] # , "SOL/USDT"
SCAN_INTERVAL = 300 
MAX_CANDLES = 320 

# Strategy 推送阈值
MIN_EV_FOR_PUSH = 0.02  # 20260905: 实盘 model_ev 通常在 -0.2~0.08，取 0.02 过滤负EV
MIN_SCORE_FOR_PUSH = 45  # 20260905: 实盘 score 量级 30~55，45 属优质区（约前25%）
MIN_SCORE_GAP = 3.5  # 20260905: 加大方向噪音过滤

# ----- 止损冷却 -----
STOP_LOSS_COOLDOWN = 300
_last_stop_loss_time = {}

# ----- 【修复20260824】同类信号冷却（防连续 Sweep）-----
_last_signal_time: dict = {}  # symbol_direction_reason -> timestamp

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
    min_score=45.0,
    allowed_hours=(0, 1, 2, 3, 4, 6, 7, 16, 17, 18, 19, 21, 23),
    # 【20260905】实盘 score 量级为 30~55（trade_journal 92 笔均值 40.8、最高 54.3），
    strong_tier2_score=50.0,
))
# ---------- EVRealityGuard / ML EV Guard ----------
_EV_REALITY_GUARD = EVRealityGuard(model_dir="models")

# ---------- V60.5 Decision Fusion Layer 单例 ----------
_DECISION_FUSION = get_decision_fusion()

# 【20260916】数据收集模式：注释掉回测 bucket_ev 加载
# 原因：回测 HIGH 桶(score>=80) EV(+0.65~0.73) 与实盘 score(30~55) 严重脱节，
# 加载会引入不匹配的加权偏差。数据收集阶段保持纯 model_ev 模式更干净。
# _bucket_ev_path = Path("data/v56_5_bucket_ev.json")
# if _bucket_ev_path.exists():
#     try:
#         _buckets = json.loads(_bucket_ev_path.read_text(encoding="utf-8"))
#         _V56_ENGINE.load_history_buckets(_buckets)
#         slog.info(f"[V56_5_Engine] 加载回测 bucket_ev 成功 ({len(_buckets)} 个分桶), 来自 {_bucket_ev_path}")
#     except Exception as e:
#         slog.error(f"[V56_5_Engine] 加载 bucket_ev 失败: {e}")

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

        pos_tmp = {
            "direction": direction,
            "entry": entry,
            "current_sl": current_sl,
            "tp1": tp1,
            "tp2": tp2,
            "stage": stage,
            "atr": atr,
        }

        action_plan = check_partial_close_and_trail(
            pos_tmp,
            close,
        )
        if action_plan.get("action") == "PARTIAL_CLOSE":
            current_sl = action_plan.get("new_sl", current_sl)
            stage = action_plan.get("stage", stage)
        elif action_plan.get("action") == "MOVE_SL":
            current_sl = action_plan.get("new_sl", current_sl)

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
    if signal_deduper.is_sl_cooled(symbol):
        slog.warning(f"[{symbol}] cooling skip (deduper SL)")
        return False
    last = _last_stop_loss_time.get(symbol, 0)
    if time.time() - last < STOP_LOSS_COOLDOWN:
        slog.warning(f"[{symbol}] cooling skip")
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

OBSERVER_EVENT_COOLDOWN = 180     # 同一类 Observer 事件最短间隔（秒）
OBSERVER_BURST_MAX = 3            # 同一扫描窗口最多放行条数
OBSERVER_BURST_WINDOW = 15        # 爆发窗口（秒）

_LAST_SAFE_SEND_TIME: float = 0.0
_OBSERVER_LAST_BY_KEY: dict = {}  # event_key -> ts
_OBSERVER_BURST_TIMES: list = []  # 最近成功推送时间戳


def _short_signal_id(signal_id: str) -> str:
    """从完整 signal_id 生成可搜索的短号。

    V6_BTCUSDT_1786636761 -> BTC-6761
    RES_BTCUSDT_1786636761 -> BTC-6761
    同一持仓生命周期内开单/加仓/移损/平仓推送复用同一短号。
    """
    if not signal_id:
        return ""
    try:
        _sid = str(signal_id).strip()
        _parts = _sid.split("_")
        if len(_parts) >= 3:
            _sym = _parts[1]
            if str(_sym).endswith("USDT"):
                _sym = str(_sym)[:-4]
            _ts = _parts[2] if len(_parts) > 2 else ""
            if _ts and str(_ts).isdigit():
                _suffix = str(_ts)[-4:] if len(str(_ts)) >= 4 else str(_ts)
                return f"{_sym}-{_suffix}"
        _raw = str(_sid)[-6:]
        return f"#{_raw}" if _raw else ""
    except Exception:
        return ""





_reason_cn_map = {
    "STOP_LOSS": ("🔴", "止损"),
    "TP1_HIT": ("🟡", "止盈TP1"),
    "TP2_HIT": ("✅", "止盈TP2"),
    "BREAKEVEN_PROTECT": ("🛡️", "保本"),
    "TRAILING_STOP": ("🛡️", "追踪"),
    "TRAIL_SL": ("🛡️", "追踪止损"),
    "CLOSE_ALL": ("🔴", "平仓"),
    "SL": ("🔴", "止损"),
    "TP": ("✅", "止盈"),
    "MAX_HOLD_TIMEOUT": ("⏰", "超时平仓"),
    "TIME_OUT": ("⏰", "超时平仓"),
}

def _exit_reason_cn(reason: str) -> str:
    """把英文退出原因映射为中文分类标签。

    输入: STOP_LOSS / TP1_HIT / TP2_HIT / BREAKEVEN_PROTECT / TRAILING_STOP
    输出: 止损 / 止盈TP1 / 止盈TP2 / 保本 / 追踪
    """
    if not reason:
        return "平仓"
    _r = str(reason).upper()
    if _r.startswith("REVERSE_SIGNAL"):
        return "反手平仓"
    if _r in _reason_cn_map:
        return _reason_cn_map[_r][1]
    # fallback: 原样返回
    return reason


_exit_reason_alias = {
    "SL": "STOP_LOSS",
    "TRAIL": "TRAIL_SL",
    "TRAILING_STOP": "TRAIL_SL",
    "TP": "TP2_HIT",
    "TP1": "TP1_HIT",
    "TP2": "TP2_HIT",
    "BREAKEVEN": "BREAKEVEN_PROTECT",
    "BREAKEVEN_PROTECT": "BREAKEVEN_PROTECT",
    "REVERSE_SIGNAL_LONG": "REVERSE_SIGNAL_LONG",
    "REVERSE_SIGNAL_SHORT": "REVERSE_SIGNAL_SHORT",
    "REVERSE_SIGNAL": "REVERSE_SIGNAL",
    "CLOSE_ALL": "CLOSE_ALL",
    "TIME_OUT": "TIME_OUT",
    "NO_RISK": "NO_RISK",
}

def _normalize_exit_reason(reason: str) -> str:
    """把旧枚举归一化到和推送一致的枚举，保证库里/推送/统计对得上。

    SL      -> STOP_LOSS
    TRailing-> TRAIL_SL
    TP1_HIT -> TP1_HIT
    TP2_HIT -> TP2_HIT
    BREAKEVEN_PROTECT -> BREAKEVEN_PROTECT
    REVERSE_SIGNAL_*  -> 原样
    """
    if not reason:
        return "CLOSE_ALL"
    _r = str(reason).upper().strip()
    if _r in _exit_reason_alias:
        return _exit_reason_alias[_r]
    if _r.startswith("REVERSE_SIGNAL"):
        return _r
    return _r



def safe_send(msg: str, priority: str = "AUTO") -> str:
    global _LAST_SAFE_SEND_TIME, _OBSERVER_BURST_TIMES
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

    # ===== 【修复推送重复】给 TRADE/SYSTEM 消息追加时间戳防止微信判重 =====
    _now_dt = __import__("datetime").datetime.now()
    _ts_suffix = f" [{_now_dt.strftime('%H:%M:%S.%f')[:-3]}]"
    if priority in ("TRADE", "SYSTEM"):
        msg = msg + _ts_suffix

    if priority in ("TRADE", "SYSTEM"):
        slog.info(f"[safe_send] {priority} 消息直发，无限流: {msg[:80]}")
    else:
        # 1) 从消息里抽一个粗事件 key，避免同类刷屏
        _key = "OBSERVER"
        _mu = msg.upper()
        for k in ("FVG LONG", "FVG SHORT", "SQUEEZE", "NEAR_LIQUIDITY", "NEAR_BSL", "NEAR_SSL",
                  "CHOCH", "BOS", "ORDER BLOCK", "LIQUIDITY"):
            if k in _mu:
                _key = k
                break

        _last_same = _OBSERVER_LAST_BY_KEY.get(_key, 0.0)
        if now - _last_same < OBSERVER_EVENT_COOLDOWN:
            slog.warning(f"[safe_send] OBSERVER 同类限流 {_key} {now - _last_same:.0f}s < {OBSERVER_EVENT_COOLDOWN}s")
            return "RATELIMITED_EVENT"

        # 2) 短窗口爆发上限（同轮多事件可发，但不超过 BURST_MAX）
        _OBSERVER_BURST_TIMES = [t for t in _OBSERVER_BURST_TIMES if now - t < OBSERVER_BURST_WINDOW]
        if len(_OBSERVER_BURST_TIMES) >= OBSERVER_BURST_MAX:
            slog.info(f"[safe_send] OBSERVER 爆发限流 {len(_OBSERVER_BURST_TIMES)}/{OBSERVER_BURST_MAX} in {OBSERVER_BURST_WINDOW}s")
            return "RATELIMITED_BURST"

        _OBSERVER_LAST_BY_KEY[_key] = now
        _OBSERVER_BURST_TIMES.append(now)
        _LAST_SAFE_SEND_TIME = now

    try:
        slog.info(f"[safe_send] 开始推送，消息长度: {len(msg)} 字符 priority={priority}")
        result = send_telegram(msg)
        slog.info(f"[safe_send] 推送完成: {result[:100] if result else 'None'}")
        return result
    except Exception as e:
        slog.error(f"[safe_send] 推送异常: {e}")
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
        # ===== 【优化1 - 动态 TimeOut】行情感知自动分发 =====
        # 趋势环境给更长 timeout（数据量更大需要更久），震荡/未知给较短 timeout
        _current_regime_for_timeout = "TREND"
        try:
            from strategy.htf_regime_filter import get_htf_regime_filter
            if hasattr(get_htf_regime_filter(), 'get_cached_regime'):
                _cached = get_htf_regime_filter().get_cached_regime()
                if _cached:
                    _current_regime_for_timeout = _cached
        except Exception:
            pass
        _regime_key = str(_current_regime_for_timeout).upper()
        _timeout_map = {'TREND': 20, 'RANGE': 8, 'CHOP': 8, 'TRANSITION': 10}
        _timeout = _timeout_map.get(_regime_key, 10)
        resp = requests.get(url, params=params, timeout=_timeout, verify=verify_ssl)
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
            
    slog.error(f"[{symbol}] 3次重试均失败")
    return None


def _build_ev_guard_ctx(best, exec_ctx, curr, df_macro=None) -> dict:
    """构建 EVRealityGuard 上下文特征（统一与 ev_model_metadata.json feature_cols 对齐）"""
    _st = str(best.get("setup_type", ""))
    _dir = str(best.get("direction", ""))
    _reg = str(best.get("regime", "range")).strip().lower()
    _tier = int(best.get("tier", 2))
    _hour = int(best.get("hour", 0))
    _dow = int(best.get("dow", 0))

    _score = float(best.get("score", 0.0))
    _model_ev = float(best.get("model_ev", 0.0))
    _win_prob = float(best.get("win_prob", best.get("win_prob_model", 0.5)))
    _rr = float(best.get("expected_rr_model", best.get("estimated_rr", 1.82)))
    _decision_score = float(best.get("decision_score", _score))
    _bucket_ev = float(best.get("bucket_ev", _model_ev))
    _cluster_score = float(best.get("cluster_score", 0.0))
    _size_scale = float(best.get("size_scale", 1.0))
    _rank_score = float(best.get("rank_score", _score))
    _expected_value = float(best.get("expected_value", _model_ev))

    _adx = float(exec_ctx.get("adx", curr.get("ADX_14", 0)))
    _lq = float(exec_ctx.get("long_quality", 0))
    _sq = float(exec_ctx.get("short_quality", 0))
    _dir_score = _lq if _dir == "Long" else _sq
    _opp_score = _sq if _dir == "Long" else _lq

    _regime_factor = float(best.get("regime_factor", 1.0))
    _session_factor = float(best.get("session_factor", 1.0))
    _gate_passed = float(best.get("gate_passed", 1.0))

    # 方向 => 1=Long, 0=Short
    _dir_num = 1.0 if _dir == "Long" else 0.0

    _regime_trend = 1.0 if ("trend" in _reg or _reg in ("bull", "bear", "strong_uptrend", "strong_downtrend")) else 0.0
    _regime_range = 1.0 if "range" in _reg else 0.0
    _regime_mixed = 1.0 if ("mixed" in _reg or _reg == "unknown") else 0.0

    _rsi = float(curr.get("rsi", 50.0))
    _vol_z = float(exec_ctx.get("vol_z", 0.0))
    _body_pct = float(exec_ctx.get("body_pct", 0.0))

    ctx = {
        "rsi": _rsi,
        "trend_strength": _regime_trend * float(exec_ctx.get("adx", 0)) / 50.0,
        "vol_z": _vol_z,
        "body_pct": _body_pct,
        "hour": float(_hour),
        "dow": float(_dow),
        "tier": float(_tier),
        "regime_factor": _regime_factor,
        "session_factor": _session_factor,
        "win_prob_model": _win_prob,
        "expected_rr_model": _rr,
        "model_ev": _model_ev,
        "decision_score": _decision_score,
        "bucket_ev": _bucket_ev,
        "cluster_score": _cluster_score,
        "size_scale": _size_scale,
        "score": _score,
        "rank_score": _rank_score,
        "direction": _dir_num,
        "regime_trend": _regime_trend,
        "regime_range": _regime_range,
        "regime_mixed": _regime_mixed,
        "gate_passed": _gate_passed,
        "win_prob": _win_prob,
        "estimated_rr": _rr,
        "expected_value": _expected_value,
    }
    return ctx


async def scan_and_decide(symbol: str) -> dict | None:
    from runner.v11_institutional_runner import make_sample_ohlcv

    # 【新增】前置K线版本预检：先轻量拉2根K线，未变化则跳过全量拉取与重算
    try:
        _probe = await fetch_ohlcv(symbol, "15m", 2)
        if _probe is not None and not _probe.empty:
            _probe_key = str(_probe["datetime"].iloc[-1])
            if _last_bar_dt_by_symbol.get(symbol) == _probe_key:
                # 仅做快速跳过，不写缓存——缓存统一由下方全量层 _last_bar_key 写入，
                # 否则新K线首次检出时缓存已被探针提前改成新时间，全量层会误判跳过本轮重算。
                slog.debug(f"[{symbol}] K线未更新（{_probe_key}），跳过本轮全量拉取与重算")
                return None
    except Exception as _probe_e:
        slog.warning(f"[{symbol}] K线预检失败，继续全量拉取: {_probe_e}")

    exec_task = fetch_ohlcv(symbol, "15m", MAX_CANDLES)
    macro_task = fetch_ohlcv(symbol, "1h", MAX_CANDLES)
    exec_result, macro_result = await asyncio.gather(exec_task, macro_task)
    
    df_exec = exec_result
    if df_exec is None or len(df_exec) < 100:
        slog.warning(f"[{symbol}] 数据不足，跳过")
        get_reject_audit().log(
            symbol, "DATA_INSUFFICIENT_EXEC",
            score=0.0, ev=0.0, regime="unknown",
            extra={"len_exec": len(df_exec) if df_exec is not None else 0},
        )
        return None

    # 诊断：确认真实行情是否在更新（避免静默卡在固定历史数据）
    try:
        _last_ts = df_exec["datetime"].iloc[-1] if "datetime" in df_exec.columns else df_exec.index[-1]
        slog.info(f"[{symbol}] 最新K线时间: {_last_ts} | bars={len(df_exec)}")
    except Exception:
        slog.info(f"[{symbol}] 最新K线时间: (无法解析) | bars={len(df_exec)}")

    # 双保险：全量拉取后再次比对最新K线，防止预检与全量之间出现时间窗口空洞
    _last_bar_key = str(df_exec["datetime"].iloc[-1]) if "datetime" in df_exec.columns else str(len(df_exec))
    if _last_bar_dt_by_symbol.get(symbol) == _last_bar_key:
        slog.debug(f"[{symbol}] K线未更新（{_last_bar_key}），跳过本轮全量重算")
        return None
    _last_bar_dt_by_symbol[symbol] = _last_bar_key

    df_macro = macro_result
    if df_macro is None or len(df_macro) < 50:
        slog.info(f"[{symbol}] ⚠️ macro 数据不足，临时使用 sample OHLCV（仅兜底，不应用于正式评估）")
        df_macro = make_sample_ohlcv(start=102.0)

    # ===== V56.5 唯一决策管线（使用预加载回测 bucket_ev 的 Engine）=====
    # 全局 _V56_ENGINE 已预加载回测 bucket_ev（365天BTC 15m数据训练）
    # 每次扫描生成候选 → enrich（含 bucket_ev 匹配）→ 选择 → 执行

    df_v56 = add_v56_indicators(load_ohlcv(df_exec))
    if df_v56 is None or len(df_v56) < 260:
        slog.info(f"[{symbol}] V56 指标计算后数据不足")
        get_reject_audit().log(
            symbol, "DATA_INSUFFICIENT_V56",
            score=0.0, ev=0.0, regime="unknown",
            extra={"len_v56": len(df_v56) if df_v56 is not None else 0},
        )
        return None

    # 【新增】传递 symbol 到候选 DataFrame（修复 UNKNOWN_ 前缀问题）
    df_v56.attrs["symbol"] = symbol

    # Step 1: generate_candidates
    trade_funnel.add("scan")  # V59.7 漏斗统计：扫描开始
    candidates = _V56_ENGINE.generate_candidates(df_v56)
    from analytics.daily_report import daily_report
    daily_report.record_candidate()
    trade_funnel.add("candidate")  # V59.7 漏斗统计
    if candidates is None or candidates.empty:
        slog.info(f"[{symbol}] V56.5 引擎无候选信号")
        get_reject_audit().log(
            symbol, "NO_CANDIDATES",
            score=0.0, ev=0.0, regime="unknown",
        )
        return None

    if "idx" in candidates.columns:
        last_idx = int(df_v56.index.max())
        # 原 LOOKBACK=5（仅75分钟）过紧，SMC 结构常需多根确认。
        # 改为 16 根（15m × 16 ≈ 4 小时）；排重逻辑仍会防止重复开仓。
        LOOKBACK_CANDLES = 16
        recent_threshold = max(0, last_idx - LOOKBACK_CANDLES + 1)  # 修复: 实际保留16根(304~319)
        _full_count = len(candidates)
        _idx_min = int(candidates["idx"].min()) if _full_count else -1
        _idx_max = int(candidates["idx"].max()) if _full_count else -1
        print(
            f"[{symbol}] 全量候选: {_full_count} | idx范围: {_idx_min}~{_idx_max} | "
            f"last_idx={last_idx} | 窗口阈值: idx>={recent_threshold} (lookback={LOOKBACK_CANDLES})"
        )
        candidates = candidates[candidates["idx"] >= recent_threshold].copy()
        slog.info(f"[{symbol}] 🔍 正在扫描候选信号... 限制窗口至最新 {LOOKBACK_CANDLES} 根K线: idx>={recent_threshold}")
        if candidates.empty:
            print(
                f"[{symbol}] 仅历史信号存在，跳过本轮扫描 "
                f"(全量={_full_count}, 最近窗口=0, lookback={LOOKBACK_CANDLES})"
            )
            get_reject_audit().log(
                symbol, "HISTORY_ONLY",
                score=0.0, ev=0.0, regime="unknown",
                extra={
                    "lookback": LOOKBACK_CANDLES,
                    "full_count": _full_count,
                    "idx_min": _idx_min,
                    "idx_max": _idx_max,
                    "last_idx": last_idx,
                },
            )
            return None
        # 🔒 信号排重（只读查询，不在这里标记）
        seen_signal_ids = set()
        deduped_rows = []
        for _, signal in candidates.iterrows():
            sig_id = _candidate_signal_id(signal)
            if sig_id in seen_signal_ids or _is_signal_already_processed(sig_id):
                slog.warning(f"[{symbol}] 信号 {sig_id} 之前已执行过，跳过排重。")
                continue
            seen_signal_ids.add(sig_id)
            deduped_rows.append(signal)

        candidates = pd.DataFrame(deduped_rows, columns=candidates.columns) if deduped_rows else pd.DataFrame(columns=candidates.columns)
        if candidates.empty:
            slog.warning(f"[{symbol}] 排重后无有效候选信号，跳过本轮扫描")
            get_reject_audit().log(
                symbol, "DEDUPED_EMPTY",
                score=0.0, ev=0.0, regime="unknown",
            )
            return None

    slog.info(f"[{symbol}] V56.5 候选信号数: {len(candidates)}, score范围: {candidates['score'].min():.1f}~{candidates['score'].max():.1f}")

    # 检查是否有 bucket_ev 列
    if "bucket_ev" in candidates.columns:
        _has_bucket = (candidates["bucket_ev"] != candidates["model_ev"]).sum()
        if _has_bucket > 0:
            slog.info(f"[{symbol}] bucket_ev 生效: {_has_bucket}/{len(candidates)} 个信号使用历史分桶 EV")
    
    # 注入 exec_ctx 的 SMC 结构信息（原 V37 的 build_exec_context）
    df_exec = add_all_indicators(df_exec, STRATEGY_PARAMS["wvf_std_mult"])
    df_macro = add_all_indicators(df_macro, STRATEGY_PARAMS["wvf_std_mult"])
    macro_ctx = build_macro_context(df_macro)
    exec_ctx = build_exec_context(df_exec, symbol=symbol, timeframe="15m")
    exec_ctx["data_source"] = "hf_auto"
    
    # Step 2: select_trades — 包含 Quality Gate + Top-N + 集群风险缩放 + 执行
    trades = _V56_ENGINE.select_trades(candidates)
    
    if trades is None or trades.empty:
        slog.info(f"[{symbol}] V56.5 选择后无交易")
        # ── Observer-only 返回：即使无交易，也推送结构事件 ──
        curr = df_exec.iloc[-1]
        # 【修复】_atr_val 需在无交易路径也预先赋值，避免 UnboundLocalError
        _atr_val = max(float(curr.get("ATRr_14", exec_ctx.get("atr", 0))), float(curr["close"]) * 0.0025)
        regime_name = str(get_htf_regime_filter().analyze(df_macro).get("regime", "UNKNOWN")).upper().strip()
        long_score = float(exec_ctx.get("long_quality", 0))
        short_score = float(exec_ctx.get("short_quality", 0))
        return {
            "symbol": symbol,
            "direction": None, "expected_value": 0.0, "score": 0.0,
            "approved": False, "reason": "OBSERVER_ONLY",
            "regime": regime_name.lower(), "vol_state": exec_ctx.get("volatility", "unknown"),
            "book": "OBSERVER", "size": 0.0,
            "df_exec": df_exec, "exec_ctx": exec_ctx, "macro_ctx": macro_ctx, "curr": curr,
            "observer_events": [],
            "long_score": long_score, "short_score": short_score,
            "long_ev": 0.0, "short_ev": 0.0,
            "long_entry": 0.0, "long_sl": 0.0, "long_tp1": 0.0, "long_tp2": 0.0, "long_tp3": 0.0, "long_rr": 0.0,
            "short_entry": 0.0, "short_sl": 0.0, "short_tp1": 0.0, "short_tp2": 0.0, "short_tp3": 0.0, "short_rr": 0.0,
            "rr": 0.0, "price": float(curr["close"]),
            "rsi": float(curr.get("rsi", 0)),
            "adx": float(exec_ctx.get("adx", curr.get("adx", 0))),
            "atr": _atr_val,  # FIX-20260913 use protected ATR consistent with SL/TP
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
            "_is_observer_only": True,
        }
    else:
        slog.info(f"[{symbol}] V56.5 执行后产生 {len(trades)} 笔交易")
    
    # 取最高 score 的交易作为本次推送
    best = trades.sort_values("score", ascending=False).iloc[0]
    
    direction = best.get("direction", None)
    if not direction:
        slog.info(f"[{symbol}] 无有效方向")
        get_reject_audit().log(
            symbol, "NO_DIRECTION",
            score=float(best.get("score", 0)), ev=float(best.get("model_ev", 0)),
            regime=str(best.get("regime", "unknown")),
            setup_type=str(best.get("setup_type", "")),
        )
        return None
    
                # 用 exec_ctx 计算 entry quality（SMC 结构验证）
    curr = df_exec.iloc[-1]
    entry_price = float(curr["close"])
    
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
    # 【修复20260826】真实预期 RR：由实际 TP1/SL/Entry 计算，不再读取回测引擎的 realized_rr 伪 RR
    # (v56_5_stable_engine._execute_one_v565 把已实现RR写入了 estimated_rr 字段，导致 RR 显示为 0.07 等瞬时值)
    _stop_for_rr = abs(entry_price - sl)
    if _stop_for_rr > 1e-12:
        rr = round(abs(tp1 - entry_price) / _stop_for_rr, 2)
    else:
        rr = round(float(best.get("estimated_rr", 1.0)), 2)
    rr = max(0.1, min(rr, 5.0))
    score = float(best.get("score", 0))
    ev = float(best.get("model_ev", 0))
    slog.info(f"[{symbol}] V56.5 SL/TP 重算: direction={direction} entry={entry_price:.2f} sl={sl:.2f} tp1={tp1:.2f} tp2={tp2:.2f} tp3={tp3:.2f} stop_dist={_stop_dist:.2f} atr={_atr_val:.2f}")

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
                slog.warning(f"[{symbol}] Mud regime + ADX={_adx_check:.1f} < 18, 无强共振例外, 跳过")
                get_reject_audit().log(
                    symbol, "MUD_HARD_BLOCK",
                    score=score, ev=ev, regime=_regime_raw,
                    vol_state=str(exec_ctx.get("volatility", "unknown")),
                    direction=direction or "",
                    setup_type=str(best.get("setup_type", "")),
                    extra={"adx": _adx_check, "strong_exception": _strong_exception},
                )
                return None
            else:
                # 有强共振例外 → 大幅降仓标记
                slog.info(f"[{symbol}] Mud regime + ADX={_adx_check:.1f} < 18, 有强共振例外, 标记降仓")
                _mud_cut_override = 0.3  # 仓位降至 30%
        elif _adx_check < 25:
            slog.info(f"[{symbol}] Mud regime + ADX={_adx_check:.1f} < 25, 中等风险, 标记降仓")
            _mud_cut_override = 0.5
        else:
            slog.info(f"[{symbol}] Mud regime 但 ADX={_adx_check:.1f} >= 25, 允许交易")

    # ===== 【安全校验】重算后的 SL 方向合理性 =====
    if direction == "Long" and sl > entry_price:
        slog.error(f"[{symbol}] SL方向异常(重算后): Long SL({sl:.2f}) > 入场({entry_price:.2f}), atr={_atr_val:.2f} 异常小, 跳过")
        get_reject_audit().log(
            symbol, "SL_DIRECTION_INVALID",
            score=score, ev=ev, regime=_regime_raw,
            direction=direction or "",
            extra={"entry": entry_price, "sl": sl, "atr": _atr_val, "sl_side": "LONG"},
        )
        return None
    if direction == "Short" and sl < entry_price:
        slog.error(f"[{symbol}] SL方向异常(重算后): Short SL({sl:.2f}) < 入场({entry_price:.2f}), atr={_atr_val:.2f} 异常小, 跳过")
        get_reject_audit().log(
            symbol, "SL_DIRECTION_INVALID",
            score=score, ev=ev, regime=_regime_raw,
            direction=direction or "",
            extra={"entry": entry_price, "sl": sl, "atr": _atr_val, "sl_side": "SHORT"},
        )
        return None
    

    # ===== 【新增 Mud Regime 软惩罚（不硬拦截，只削弱评分）】=====
    if "mud" in _regime_raw or "chaos" in _regime_raw:
        _orig_score = score
        _orig_ev = ev
        score = max(0.0, score - 10.0)           # 评分 -10 分
        ev = ev * 0.8                             # EV ×0.8
        slog.info(f"[{symbol}] Mud Regime 软惩罚: score={_orig_score:.1f}->{score:.1f} (-10), ev={_orig_ev:.4f}->{ev:.4f} (×0.8)")
        get_reject_audit().log(
            symbol, "MUD_SOFT_PENALTY",
            score=score, ev=ev, regime=_regime_raw,
            vol_state=str(exec_ctx.get("volatility", "unknown")),
            direction=direction or "",
            setup_type=str(best.get("setup_type", "")),
            extra={"orig_score": _orig_score, "score_delta": -10.0, "ev_mult": 0.8},
        )

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
            get_reject_audit().log(
                symbol, "COLOR_DIRECTION_LONG",
                score=score, ev=ev, regime=_regime_raw, vol_state=str(exec_ctx.get("volatility", "unknown")),
                direction=direction or "", setup_type=str(best.get("setup_type","")),
                extra={"candle_color": _candle_color, "adx": _candle_adx, "has_bot_div": _has_bot_div, "sqz_white_long": _sqz_white_long, "has_fe_bottom": _has_fe_bottom},
            )
            slog.warning(f"[{symbol}] 方向不一致: Long 但 K线红色(看跌) ADX={_candle_adx:.1f}(强趋势), 无底部反转信号, 跳过")
            return None
        elif _candle_adx >= 30:
            slog.info(f"[{symbol}] 方向风险: Long 但 K线红色 ADX={_candle_adx:.1f}(强趋势), 继续但降低评分")
            score *= 0.7  # 红K+强趋势下做多评分打7折
        # 蓝色K线(看涨) + ADX>=25 = 强上涨趋势，此时做空需要顶背离/白线反转/FE摸顶之一
    if direction == "Short" and ("蓝" in _candle_color or "blue" in _candle_color.lower() or "bull" in _candle_color.lower()):
        if _candle_adx >= 25 and not _has_top_div and not _sqz_white_short and not _has_fe_top:
            get_reject_audit().log(
                symbol, "COLOR_DIRECTION_SHORT",
                score=score, ev=ev, regime=_regime_raw, vol_state=str(exec_ctx.get("volatility", "unknown")),
                direction=direction or "", setup_type=str(best.get("setup_type","")),
                extra={"candle_color": _candle_color, "adx": _candle_adx, "has_top_div": _has_top_div, "sqz_white_short": _sqz_white_short, "has_fe_top": _has_fe_top},
            )
            slog.warning(f"[{symbol}] 方向不一致: Short 但 K线蓝色(看涨) ADX={_candle_adx:.1f}(强趋势), 无顶部反转信号, 跳过")
            return None
        elif _candle_adx >= 30:
            slog.info(f"[{symbol}] 方向风险: Short 但 K线蓝色 ADX={_candle_adx:.1f}(强趋势), 继续但降低评分")
            score *= 0.7
    
    slog.info(f"[{symbol}] V56.5 选定: {direction} score={score:.1f} ev={ev:.4f} setup={best.get('setup_type','?')} price={entry_price:.2f}")
    
    # 【修复20260705】多空评分改为使用 exec_ctx 中的独立质量评分
    _exec_lq = float(exec_ctx.get("long_quality", 0))
    _exec_sq = float(exec_ctx.get("short_quality", 0))
    _use_long_score = _exec_lq if _exec_lq > 0 else (float(score) if direction == "Long" else 0.0)
    _use_short_score = _exec_sq if _exec_sq > 0 else (0.0 if direction == "Long" else float(score))

    # ===== 【优化2 - HTF Regime Filter】用 1H 数据校验大方向 =====
    _htf_state = get_htf_regime_filter().analyze(df_macro)
    result_htf_blocked = False
    if direction == "Long" and not _htf_state["allow_long"]:
        slog.info(f"[{symbol}] HTF Regime 拦截 Long: 1H 方向={_htf_state['regime']} (EMA50={_htf_state.get('ema_fast')}, EMA200={_htf_state.get('ema_slow')})")
        result_htf_blocked = True
    elif direction == "Short" and not _htf_state["allow_short"]:
        slog.info(f"[{symbol}] HTF Regime 拦截 Short: 1H 方向={_htf_state['regime']} (EMA50={_htf_state.get('ema_fast')}, EMA200={_htf_state.get('ema_slow')})")
        result_htf_blocked = True
    else:
        slog.info(f"[{symbol}] HTF Regime 通过: 1H={_htf_state['regime']}, allow_long={_htf_state['allow_long']}, allow_short={_htf_state['allow_short']}")

        # ===== 【特征收集 - 用于优化4/5】构建特征字典 =====
    # 【修复20260726】注入 regime 信息，让 feature_penalty 能做 regime-aware 惩罚
    _regime_name = str(_htf_state.get("regime", "UNKNOWN")).upper().strip()
    sqz_data = calculate_advanced_sqzmom(df_exec)
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
        # ===== SQZMOM 高维特征 =====
        "sqz_released": sqz_data["released"],
        "sqz_duration": sqz_data["duration"],
        "sqz_strength": sqz_data["strength"],
        "sqz_vol_ratio": sqz_data["vol_ratio"],
        "sqz_volume_confirmed": sqz_data["volume_confirmed"],
        # 【新增20260726】注入 regime 字段，供 feature_penalty 动态调整惩罚系数
        "regime": _regime_name,
    }

        # ===== 【优化5 - Statistical EV】混合历史EV =====
    _blended_ev = get_statistical_ev().blend(model_ev=ev, features=_features)
    if _blended_ev != ev:
        slog.info(f"[{symbol}] Statistical EV: model={ev:.4f} -> blended={_blended_ev:.4f}")

    # ===== {V4.5} EVRealityGuard: ML EV Reality Check =====
    _ev_guard_ctx = _build_ev_guard_ctx(best, exec_ctx, curr)
    _guard_blocked = False
    _guard_penalty = 0.0
    _guard_win_prob = None
    _guard_ml_ev = None
    _guard_quality = "unknown"
    _guard_reason = ""
    try:
        _ev_guard_result = _EV_REALITY_GUARD.evaluate(
            signal={
                "expected_value": float(best.get("model_ev", ev)),
                "probability": float(best.get("win_prob", best.get("win_prob_model", 0.5))),
                "score": score,
                "direction": direction,
            },
            ctx=_ev_guard_ctx,
        )
        _guard_win_prob = _ev_guard_result.get("ml_win_prob", None)
        _guard_ml_ev = _ev_guard_result.get("ml_predicted_ev", None)
        _guard_quality = _ev_guard_result.get("signal_quality", "unknown")
        _guard_should_enter = _ev_guard_result.get("should_enter", True)
        _guard_reason = _ev_guard_result.get("reason", "")

        if not _guard_should_enter:
            _guard_blocked = True
            slog.warning(
                f"[{symbol}] EVRealityGuard BLOCK: score={score:.1f} ev={ev:.4f} "
                f"ml_win_prob={_guard_win_prob:.4f} ml_ev={_guard_ml_ev:.4f} "
                f"quality={_guard_quality} reason={_guard_reason}"
            )
            get_reject_audit().log(
                symbol, "EV_REALITY_GUARD_BLOCK",
                score=score, ev=ev, regime=_regime_name,
                direction=direction or "",
                setup_type=str(best.get("setup_type", "")),
                extra={
                    "ml_win_prob": round(_guard_win_prob, 4) if _guard_win_prob else None,
                    "ml_ev": round(_guard_ml_ev, 4) if _guard_ml_ev else None,
                    "quality": _guard_quality,
                },
            )
        else:
            # 非阻止……但保留 EV 调整（要求 ML EV >= 0）
            if _guard_ml_ev is not None and _guard_ml_ev < 0:
                _guard_penalty = min(0.5, abs(_guard_ml_ev) * 0.5)
                slog.info(f"[{symbol}] EVRealityGuard negative EV: ml_ev={_guard_ml_ev:.4f}, penalty={_guard_penalty:.3f}")
    except Exception as _ev_guard_err:
        slog.warning(f"[{symbol}] EVRealityGuard error: {_ev_guard_err}")

    if _guard_blocked:
        return None

    # ===== {V4.6} ProbabilityCalibrator Transform -> EV + Confidence =====

    # ===== 【闭环】ProbabilityCalibrator 校准评分 -> EV + Confidence (含regime校准+样本置信) =====
    _calibrated_prob = _calibrator.get_prob(score)
    _calibrated_prob = round(_calibrated_prob, 4)
    _calib_ev_result = _calibrator.calculate_ev(
        score=score,
        reward=float(best.get('estimated_rr', 1.82)),
        risk=1.0,
        regime=_regime_name.lower(),
    )
    _calibrated_ev = _calib_ev_result['ev']
    _calibrated_conf = _calib_ev_result['confidence']
    slog.info(f"[{symbol}] ProbabilityCalibrator: score={score:.1f} -> confidence={_calibrated_prob:.3f} | calib_ev={_calibrated_ev:.4f} sample_conf={_calibrated_conf:.3f}")
        # ===== 【V60 ML Decision Engine】LightGBM 概率评估（并行） =====
    _ml_score, _ml_ev, _ml_conf, _ml_active = _ML_DECISION.evaluate(
        exec_ctx=exec_ctx,
        curr_row=curr,
        regime=_regime_name,
        features_dict=_features,
        direction=direction,
    )
    # 统一成 0~1 概率，供快照 / 后续 Fusion 使用
    _ml_prob = float(_ml_score) / 100.0 if _ml_score is not None else 0.5
    if _ml_active:
        slog.info(
            f"[{symbol}] ML引擎: P(win)={_ml_prob:.3f} EV={_ml_ev:.4f} "
            f"conf={_ml_conf:.3f} score={_ml_score:.1f}"
        )
        if _ml_prob < 0.45:
            slog.info(f"[{symbol}] ML引擎 低概率: {_ml_prob:.3f} < 0.45, 标记降仓")
            _mud_cut_override = min(_mud_cut_override, 0.5)
    else:
        slog.info(f"[{symbol}] ML引擎: 降级模式(人工权重) score={_ml_score:.1f}")

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
    slog.info(f"[{symbol}] FeedbackLoop: score={score:.1f} -> weighted={_fb_result['weighted_score']:.1f}, confidence={_fb_result['confidence']:.3f}, ev={_fb_result['ev']:.4f}, reject={_fb_result['should_reject']} (threshold={_fb_result['reject_threshold']})")

    # ===== 【V60.5 标准融合层】Decision Fusion Layer =====
    # 将 EVRealityGuard + ProbabilityCalibrator + ML Decision Engine + FeedbackLoop
    # 统一融合为最终置信度，替代旧有的单一 EVRealityGuard soft penalty
    _fusion_input = FusionInput(
        calib_prob=_calibrated_prob,
        calib_conf=_calibrated_conf,
        ml_prob=_ml_prob,
        ml_conf=_ml_conf if _ml_conf is not None else 0.0,
        ml_active=bool(_ml_active),
        guard_prob=_guard_win_prob,
        guard_ml_ev=_guard_ml_ev,
        guard_quality=_guard_quality,
        guard_blocked=_guard_blocked,
        guard_penalty=_guard_penalty,
        feedback_score=float(_fb_result.get("weighted_score", score)),
        feedback_ev=float(_fb_result["ev"]),
        feedback_reject=bool(_fb_result.get("should_reject", False)),
        v56_score=score,
        blended_ev=_blended_ev,
        direction=direction,
    )
    try:
        _fusion_result = _DECISION_FUSION.fuse(_fusion_input)
        # 硬拦截：融合层判定 BLOCK
        if _fusion_result.hard_blocked:
            slog.warning(
                f"[{symbol}] DecisionFusion BLOCK: reason={_fusion_result.block_reason} "
                f"prob={_fusion_result.fused_prob:.3f} ev={_fusion_result.fused_ev:.4f}"
            )
            get_reject_audit().log(
                symbol, _fusion_result.block_reason,
                score=score, ev=ev, regime=_regime_name,
                direction=direction or "",
                setup_type=str(best.get("setup_type", "")),
                extra={"fusion": _fusion_result.details},
            )
            return None
        # 记录融合结果（软调整）
        _fused_prob = _fusion_result.fused_prob
        _fused_ev = _fusion_result.fused_ev
        _fused_conf = _fusion_result.fused_conf
        _use_fused = _fusion_result.use_fused_prob
        if _use_fused and _fused_prob > 0:
            # 用融合概率替换校准概率（下游 EV / 快照使用）
            _calibrated_prob = round(float(_fused_prob), 4)
            slog.info(
                f"[{symbol}] DecisionFusion: fused_prob={_fused_prob:.3f} conf={_fused_conf:.3f} "
                f"ev={_fused_ev:.4f} 源权重={_fusion_result.source_weights} "
                f"贡献={_fusion_result.source_contributions}"
            )
        else:
            slog.info(
                f"[{symbol}] DecisionFusion: 保持原分量 (max_diff={_fusion_result.details.get('max_diff', 0):.3f}, "
                f"fused_conf={_fused_conf:.3f}) p={_calibrated_prob:.3f}"
            )
        # 记录融合详情供返回结构使用
        _fusion_details = _fusion_result.details
        _fusion_weights = _fusion_result.source_weights
        _fusion_contrib = _fusion_result.source_contributions
    except Exception as _fusion_err:
        slog.warning(f"[{symbol}] DecisionFusion error: {_fusion_err}")
        _fused_prob = _calibrated_prob
        _fused_ev = _blended_ev
        _fused_conf = 0.0
        _use_fused = False
        _fusion_details = {}
        _fusion_weights = {}
        _fusion_contrib = {}

        # ===== 【V21 FeatureLearningEngine】特征权重调整 =====
    _fl_weighted_score = get_feature_learner().get_weighted_score(_fb_raw_scores)
    _fl_final_score = score * 0.7 + _fl_weighted_score * 0.3 if _fl_weighted_score > 0 else score
    _fl_final_score = min(100.0, _fl_final_score)

    # Apply EVRealityGuard penalty (soft downgrade when ML predicts negative EV)
    if _guard_penalty > 0:
        _fl_final_score_delta = _fl_final_score * 0.15  # 15% cap
        _fl_final_score = max(0.0, _fl_final_score - _guard_penalty * _fl_final_score_delta)
        slog.info(f"[{symbol}] EVRealityGuard penalty applied: ev={ev:.4f} penalty={_guard_penalty:.3f} score={_fl_final_score:.1f}")
    if _fl_final_score != score:
        slog.info(f"[{symbol}] FeatureLearning: score={score:.1f} -> adjusted={_fl_final_score:.1f} (weights={get_feature_learner().get_all_weights()})")

    # ===== 【优化3 - 入场信号强制确认】LIQUIDITY_SWEEP 一票否决 =====
    # 针对假清扫，利用布尔乘法做一票否决，不满足条件分数直接归零
    _setup_name = str(best.get("setup_type", "")).upper()
    _observer_events_list = _detect_observer_events(curr, exec_ctx, macro_ctx, _exec_lq, _exec_sq)
    _observer_event_types = [e["type"] for e in _observer_events_list]
    is_sweep = (_setup_name == 'LIQUIDITY_SWEEP')
    has_choch = ('CHOCH' in _observer_event_types)
    # 动能确认：使用 sqzmom vol_ratio > 1.0 作为动量指标
    has_momentum = (sqz_data.get("vol_ratio", 0.0) > 1.0)
    # 核心判定：要么不是 Sweep 信号直接放行；若是，必须满足全要素
    sweep_approved = (not is_sweep) or (has_choch and has_momentum)
    # 布尔乘法干预：False 转为 0，分数归零触发 ScoreGate 拦截
    if is_sweep and not sweep_approved:
        slog.info(f"[{symbol}] 🚫 LIQUIDITY_SWEEP 一票否决: setup={_setup_name} has_choch={has_choch} has_momentum={has_momentum}(vol_ratio={sqz_data.get('vol_ratio',0):.2f}) score={_fl_final_score:.1f} -> 0")
        _fl_final_score = 0.0  # 分数归零，后续 ScoreGate 直接拦截

    # ===== 【GATE-4 修复】HTF Regime 拦截同样需要分数归零 =====
    if result_htf_blocked:
        slog.info(f"[{symbol}] 🚫 HTF Regime 一票否决: 1H 方向不允许，score={_fl_final_score:.1f} -> 0")
        _fl_final_score = 0.0  # 分数归零，让 approved=False, rejected=True

        # 构建兼容返回格式
    return {
        "_mud_cut": _mud_cut_override,  # mud regime 降仓系数
        "symbol": symbol,
        "direction": direction,
        # 【EVRealityGuard】归零逻辑：一票否决/风控归零后，EV 同步归零，防止下游误用
        "expected_value": round(float(_fb_result["ev"]), 4) if _fl_final_score > 0 else 0.0,
        "score": round(float(_fb_result["weighted_score"]), 2),  # 风控否决/调整后的最终分
        "orig_score": round(score, 2),  # 原始进入 V56.5 / Calibration 评分，用于 V6 路由判断
        "final_score": round(float(_fb_result["weighted_score"]), 2),
        "v6_weighted_score": round(float(_fb_result["weighted_score"]), 2),  # 对外暴露的最终分，供 V6 路由判断
        "rejected": _fl_final_score <= 0.0,  # 被一票否决归零后，路由层必须据此拦截
        "entry": entry_price,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "rr": round(rr, 2),
        "approved": _fl_final_score > 0.0,  # 风控否决后为 False，任何后续 GATE 都不能放行
        "reason": f"V56.5_{best.get('setup_type','?')}_{best.get('gate_reason','PASSED')}",
        "regime": _regime_name,  # FIX-20260913 use HTF value not backtest engine mixed
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
        "sqz_data": sqz_data,
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
        "_ev_guard": {
            "ml_win_prob": _guard_win_prob,
            "ml_ev": _guard_ml_ev,
            "quality": _guard_quality,
            "blocked": _guard_blocked,
            "penalty": _guard_penalty,
        },
        # ===== V60.5 标准融合层 =====
        "_fusion": {
            "fused_prob": _fused_prob,
            "fused_ev": _fused_ev,
            "fused_conf": _fused_conf,
            "use_fused_prob": _use_fused,
            "source_weights": _fusion_weights,
            "source_contributions": _fusion_contrib,
            "detail": _fusion_details,
        },
        #"confidence": _fb_result["confidence"],  # 【闭环】置信度
        "confidence": _calibrated_prob,  # 【闭环】校准后的信心分数
        "grade_result": None,  # check_and_open 中填充
        # ===== 数据闭环：ML / 规则分写入快照 =====
        "ml_active": bool(_ml_active),
        "ml_score": float(_ml_score) if _ml_score is not None else 0.0,
        "ml_prob": float(_ml_prob),
        "ml_ev": float(_ml_ev) if _ml_ev is not None else 0.0,
        "ml_conf": float(_ml_conf) if _ml_conf is not None else 0.0,
        # v6_data_engine.record_open_snapshot 读取这些键
        "p_win_raw": float(_ml_prob),
        "p_win_calibrated": float(_ml_prob) if _ml_active else float(_calibrated_prob),
        "model_ev": float(_ml_ev) if (_ml_active and _ml_ev is not None) else float(_blended_ev),
        "rule_score": float(score),
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

    【20260719 优化】FVG 和 SQUEEZE_RELEASE 是非连续类型，首次触发推送后
    不再重复推送（直到事件消失后重新出现）。连续状态事件每 30 分钟汇总一次。
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
    # --- 一次性事件类型（触发推一次后，直到消失才可再次触发）---
    _ONE_SHOT_TYPES = {"FVG", "SQUEEZE_RELEASE", "LIQUIDITY_SWEEP", "CHOCH", "BOS", "SQZMOM_EF"}

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
            slog.info(f"[{symbol}] Observer 事件触发: {key} (首次)")
        else:
            # 事件持续存在
            # 如果是一类事件：仅首次推送过就不再重复（除非消失后重新出现）
            if ev_type in _ONE_SHOT_TYPES:
                # 一次性事件，已推送过，不再重复
                if pushed.get(key, False):
                    pass  # 完全静默
                else:
                    # 推送标记丢失但 active 还在，补推一次
                    pushed[key] = True
                    new_events.append(ev)
                    slog.info(f"[{symbol}] Observer 事件补推: {key}")
            elif ev_type in _CONTINUOUS_TYPES:
                # 连续状态类型：每 OBSERVER_PERIODIC_INTERVAL 秒汇总一次
                if pushed.get(key, False):
                    last_periodic = periodic_last.get(ev_type, 0)
                    if now - last_periodic >= OBSERVER_PERIODIC_INTERVAL:
                        ev["is_periodic_summary"] = True
                        new_events.append(ev)
                        periodic_last[ev_type] = now
                        slog.info(f"[{symbol}] Observer 状态持续汇总: {key} ({OBSERVER_PERIODIC_INTERVAL//60}min)")
                else:
                    # 推送标记丢失但 active 还在
                    pushed[key] = True
                    new_events.append(ev)
            else:
                # 其他类型：默认走首次推送后不重复
                if not pushed.get(key, False):
                    pushed[key] = True
                    new_events.append(ev)

    # 3) 检测事件从有→无：清除激活状态
    for key in list(active.keys()):
        if key not in current_keys:
            was_active = active.pop(key, False)
            pushed.pop(key, None)
            if was_active:
                slog.info(f"[{symbol}] Observer 事件消失: {key}")

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
    slog.info(f"[{symbol}] Observer 推送: {ev['type']} {ev.get('dir','')}")
# Strategy 信号推送与去重
# ============================================================

def _signal_id(result: dict) -> str:
    """构建高精度信号指纹，确保每次扫描的每个信号都是唯一可追踪的。

    【修复20260823】用 datetime（K线时间戳）替代 idx。
    因为每次 fetch_ohlcv 返回新的 index（0~319），同一物理 K 线
    在不同扫描批次中的 idx 不同，导致指纹变化 → 去重失效。
    K 线的 datetime 是绝对时间戳，恒定不变。

    指纹包含：
    - symbol / direction（基础标识）
    - setup_type（模式类型：LIQUIDITY_SWEEP / WEAK_BOS / FVG_TOUCH 等）
    - datetime（K线绝对时间，唯一标识触发行情位置）
    - score + ev（量化特征，取整到 bucket）
    - regime（市场状态，区分同 setup 在不同环境下的信号）

    这样同一个15-min K线内，同一 setup_type 的不同扫描结果
    会被正确识别为重复信号。
    """
    symbol = result["symbol"]
    direction = result["direction"] or "NONE"
    setup_type = result.get("decision", {}).get("signal", {}).get("setup_type", result.get("reason", "UNKNOWN"))

    # 🟢 用 datetime 替代不稳定的 idx
    sig_dt = result.get("decision", {}).get("signal", {}).get("datetime", None)
    if sig_dt is None:
        # 兜底：用 result 中的 curr 时间戳
        sig_dt = str(result.get("curr", {}).get("datetime", "")) if isinstance(result.get("curr"), dict) else ""
    if not sig_dt:
        sig_dt = "no_dt"

    score_bucket = int(result.get("score", 0) / 10) if result.get("score") else -1
    _ev = result.get("expected_value", 0.0)
    ev_bucket = f"{_ev:+.3f}"[:6]
    regime = str(result.get("regime", "UNKNOWN"))[:4]
    return f"{symbol}_{direction}_{setup_type}_dt{sig_dt}_s{score_bucket}_ev{ev_bucket}_{regime}"


def _candidate_signal_id(signal_row) -> str:
    """Build a signal fingerprint from a candidate row without persisting it.

    【修复20260823】用 datetime（K线时间戳）替代 idx。
    因为每次 fetch_ohlcv 返回新的 index（0~319），同一物理 K 线
    在不同扫描批次中的 idx 不同，导致指纹变化 → 候选去重失效。
    K 线的 datetime 是绝对时间戳，恒定不变。
    """
    if hasattr(signal_row, "to_dict"):
        signal_row = signal_row.to_dict()

    symbol = str(signal_row.get("symbol", "UNKNOWN"))
    direction = str(signal_row.get("direction", "NONE") or "NONE")
    setup_type = str(signal_row.get("setup_type", signal_row.get("reason", "UNKNOWN")))

    # 🟢 用 datetime 替代不稳定的 idx
    sig_dt = signal_row.get("datetime", None)
    if sig_dt is not None:
        sig_dt = str(sig_dt)
    else:
        sig_dt = "no_dt"

    score_bucket = int(float(signal_row.get("score", 0.0)) / 10) if signal_row.get("score", None) is not None else -1
    _ev = float(signal_row.get("model_ev", signal_row.get("expected_value", 0.0)) or 0.0)
    ev_bucket = f"{_ev:+.3f}"[:6]
    regime = str(signal_row.get("regime", "UNKNOWN"))[:4]
    return f"{symbol}_{direction}_{setup_type}_dt{sig_dt}_s{score_bucket}_ev{ev_bucket}_{regime}"


def _is_signal_already_processed(signal_id: str) -> bool:
    """只查询是否已处理，不标记。优先 signal_deduper，兼容 position_manager。"""
    if not signal_id:
        return False
    if signal_deduper.is_processed(signal_id):
        return True
    return position_manager.is_signal_already_processed(signal_id)


def _is_signal_processed(signal_id: str) -> bool:
    """
    信号去重：True = 已处理过应跳过；False = 首次见到（已标记）可继续。
    优先 signal_deduper.should_process，并双写 position_manager 保持兼容。
    """
    if not signal_id:
        return False
    # should_process: True=首次可处理并已标记；False=已见过
    if not signal_deduper.should_process(signal_id):
        return True
    try:
        position_manager.mark_signal_processed(signal_id)
    except Exception:
        pass
    return False
def async_background_task(coro_or_func, *args, **kwargs):
    """Unified background task dispatcher. Compatible with coroutines & sync functions."""
    # ── case 1: coroutine object ──
    if asyncio.iscoroutine(coro_or_func):
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro_or_func)
        except RuntimeError:
            threading.Thread(target=lambda: asyncio.run(coro_or_func), daemon=True).start()
        return

    # ── case 2: coroutine function ──
    if asyncio.iscoroutinefunction(coro_or_func):
        coro = coro_or_func(*args, **kwargs)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro)
        except RuntimeError:
            threading.Thread(target=lambda: asyncio.run(coro), daemon=True).start()
        return

    # ── case 3: normal sync function ──
    try:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, lambda: coro_or_func(*args, **kwargs))
    except RuntimeError:
        threading.Thread(target=lambda: coro_or_func(*args, **kwargs), daemon=True).start()


async def async_record_snapshot_and_push(result: dict, kelly_size: float = 0.0):
    """Background record snapshot and optional HF push."""
    try:
        await asyncio.to_thread(emit, "record_open_snapshot", result, kelly_size=kelly_size)
        # TODO: add live HF hub push here if needed.
    except Exception as exc:
        slog.error(f"[async_record_snapshot_and_push] 异步记录异常: {exc}")


def evaluate_signal_v6_routing(result: dict) -> dict:
    """
    【V6 质量门升级】取消硬拦截，实施 A/B/观察级 四层分级路由
    """
    # 优先使用原始 V56.5 score（若存在），避免风控 / 特征惩罚后把好信号压进 RESEARCH_SILENT/ABSOLUTE_DROP
    score = float(result.get("orig_score", result.get("v6_final_score", result.get("score", 0.0))) or 0.0)
    result["v6_final_score"] = score
    if score <= 0.0:
        result["v6_level"] = "REJECT_GRADE"
        result["action_route"] = "ABSOLUTE_DROP"
        return result

    if score >= 70.0:
        result["v6_level"] = "A_GRADE"
        result["action_route"] = "LIVE_FULL_TRADE"
    elif 55.0 <= score < 70.0:
        result["v6_level"] = "B_GRADE"
        result["action_route"] = "LIVE_HALF_TRADE"
        # ===== 趋势方向硬门槛：逆HTF趋势的B级信号降级为RESEARCH_SILENT（不开仓）=====
        # 现象：55-70分B级信号中部分与1H趋势相反，开仓后逆势被反复扫止损
        # 方案：取V56.5引擎输出的trend_direction（features.trend_direction），
        #       支持 bool（True=顺势/False=逆势）与字符串（"Long"/"Short"/""）两种格式
        try:
            _tdir = result.get("trend_direction")
            if _tdir is None:
                _feats = result.get("features", {}) or {}
                _tdir = _feats.get("trend_direction")
            _sig_dir = str(result.get("direction", "")).lower()
            _allow_route = True
            # 情况1：bool 类型 — False 表示逆势
            if _tdir is False:
                _allow_route = False
            # 情况2：str 类型 — 空值/未知保守降级，方向与信号相反时降级
            elif isinstance(_tdir, str):
                _tdir_l = _tdir.strip().lower()
                if _tdir_l in ("", "false", "0", "no", "unknown"):
                    _allow_route = False
                elif _tdir_l in ("long", "bull", "up", "uptrend"):
                    if _sig_dir == "short":
                        _allow_route = False
                elif _tdir_l in ("short", "bear", "down", "downtrend"):
                    if _sig_dir == "long":
                        _allow_route = False
            if not _allow_route:
                slog.warning(
                    f"[V6 分级路由 - 趋势方向硬门槛] {result.get('symbol', '?')} "
                    f"B_GRADE({score:.1f}分) trend_direction={_tdir}（逆HTF趋势），降级为 RESEARCH_SILENT"
                )
                result["v6_level"] = "OBSERVE_GRADE"
                result["action_route"] = "RESEARCH_SILENT"
                result["_trend_filter_downgrade"] = True
        except Exception as _td_e:
            slog.error(f"[V6 分级路由 - 趋势方向硬门槛异常]: {_td_e}")
    elif 45.0 <= score < 55.0:
        result["v6_level"] = "OBSERVE_GRADE"
        result["action_route"] = "RESEARCH_SILENT"
    else:
        result["v6_level"] = "REJECT_GRADE"
        result["action_route"] = "ABSOLUTE_DROP"

    return result


def check_and_open_v6_with_routing(result: dict) -> bool:
    """完全体分级路由执行中心：让 45-55 分的信号乖乖变成进化燃料"""
    symbol = result.get("symbol", "?")
    result["base_size"] = float(result.get("base_size", result.get("size", 0.05)) or 0.05)
    result["v6_final_score"] = float(result.get("v6_final_score", result.get("score", 0.0)) or 0.0)

    # 【关键修复】风控否决（LIQUIDITY_SWEEP 等一票否决）后，立即拦截，禁止进入路由与实盘推送
    if bool(result.get("rejected", False)) or float(result.get("score", 0.0) or 0.0) <= 0.0:
        _r_reason = "ONE_VETO_REJECT" if bool(result.get("rejected", False)) else "SCORE_ZERO"
        slog.warning(f"[V6 分级路由 - 否决拦截] {symbol} {_r_reason} score={result.get('score', 0.0)}，跳过路由与推送")
        return False

    # ===== 【GATE-4 修复】HTF Regime 宏观方向拦截（与旧 check_and_open 对齐）=====
    if bool(result.get("htf_blocked", False)):
        slog.warning(f"[V6 分级路由 - HTF拦截] {symbol} 1H 趋势方向不允许，跳过路由与推送")
        return False

    result = evaluate_signal_v6_routing(result)
    route = result["action_route"]
    level = result["v6_level"]
    score = result["v6_final_score"]

    if route == "ABSOLUTE_DROP":
        slog.info(f"[V6 分级路由 - DROP] {symbol} {level} 信号 ({score}分) 直接丢弃")
        try:
            event_logger.log_event("REJECT", {
                "symbol": symbol,
                "direction": result.get("direction", ""),
                "score": result.get("v6_final_score", score),
                "ev": result.get("expected_value", 0.0),
                "confidence": result.get("confidence", 0.0),
                "regime": str(result.get("regime", "unknown")),
                "feature_hash": generate_feature_hash(str(result.get("features", {}))),
                "features": result.get("features", {}),
                "entry_price": result.get("entry", 0.0),
                "stop_price": result.get("sl", 0.0),
                "reason": "ABSOLUTE_DROP",
                "v6_level": level,
                "v6_route": route,
            })
        except Exception:
            pass
        return False

    if route == "RESEARCH_SILENT":
        # ===== 【修复】RESEARCH_SILENT 也必须经过冷却检查 =====
        # 根因：此前 RESEARCH_SILENT 分支不查询 is_symbol_cooled，
        #       导致每次扫描时同币种即使无持仓也会反复拍快照入库（RES_ 开头的堆积单）
        _rs_direction = str(result.get("direction", ""))
        if signal_deduper.is_symbol_cooled(symbol, _rs_direction, "RESEARCH_SILENT"):
            slog.warning(f"[V6 分级路由 - 科研观察冷却] {symbol} {_rs_direction} 仍在冷却中，跳过快照记录")
            return False
        research_id = f"RES_{symbol.replace('/', '')}_{int(time.time())}"
        result["signal_id"] = research_id
        result["exit_reason"] = "RESEARCH_OBSERVE"
        slog.info(f"[V6 分级路由 - 科研观察] {symbol} {level} 信号 ({score}分) | 实盘静默, 拍摄特征快照入云端铁盒")
        try:
            event_logger.log_event("REJECT", {
                "symbol": symbol,
                "direction": result.get("direction", ""),
                "score": result.get("v6_final_score", score),
                "ev": result.get("expected_value", 0.0),
                "confidence": result.get("confidence", 0.0),
                "regime": str(result.get("regime", "unknown")),
                "feature_hash": generate_feature_hash(str(result.get("features", {}))),
                "features": result.get("features", {}),
                "entry_price": result.get("entry", 0.0),
                "stop_price": result.get("sl", 0.0),
                "reason": "RESEARCH_SILENT",
                "v6_level": level,
                "v6_route": route,
            })
        except Exception:
            pass
        async_background_task(async_record_snapshot_and_push(result, kelly_size=0.0))
        # ===== [V6-增强] 注册虚拟持仓追踪 =====
        # 让 RESEARCH_SILENT 信号也能获得平仓结果（pnl_r / exit_reason）
        # 成为 DynamicFeatureOptimizer 可学习的带标签数据
        try:
            from utils.research_tracker import get_research_tracker
            _rt = get_research_tracker()
            _rt.register(
                signal_id=research_id,
                symbol=symbol,
                direction=_rs_direction,
                entry_price=float(result.get("entry", 0.0)),
                sl_price=float(result.get("sl", 0.0)),
                tp1_price=float(result.get("tp1", 0.0)),
            )
        except Exception as _rt_e:
            slog.error(f"[V6 RESEARCH_SILENT] 虚拟持仓注册失败: {_rt_e}")
        # ===== [V6-增强] 注册虚拟持仓追踪 - 结束 =====
        # 【修复】记录 RESEARCH_SILENT 快照后也标记冷却，防止连续堆积
        try:
            signal_deduper.mark_symbol_fired(symbol, _rs_direction, "RESEARCH_SILENT")
        except Exception:
            pass
        return False
    trade_size = result["base_size"]
    if route == "LIVE_HALF_TRADE":
        trade_size *= 0.5
        result["size"] = trade_size
    sig_id = f"V6_{symbol.replace('/', '')}_{int(time.time())}"
    result["signal_id"] = sig_id
    # 【修复20260810】V6 路由实盘激活推送前必须经过统一冷却拦截。
    # 根因：此前该函数推送开单通知时未调用 is_symbol_cooled，
    #      且 sig_id 使用秒级时间戳导致 should_process 永远通过，
    #      造成每根新 15min K 线都推送一次相同开单信号。
    _route_direction = str(result.get("direction", ""))
    _route_reason = str(route or "LIVE_ROUTE")
    if signal_deduper.is_symbol_cooled(symbol, _route_direction, _route_reason):
        slog.warning(f"[V6 分级路由 - 冷却拦截] {symbol} {level} {route} 方向={_route_direction} 仍在冷却中，跳过推送")
        return False
    # 记录一次信号指纹去重（兼容旧链路）
    try:
        if _is_signal_already_processed(sig_id):
            slog.warning(f"[V6 分级路由 - 信号去重] {symbol} {sig_id} 已处理过，跳过推送")
            return False
        signal_deduper.should_process(sig_id)
    except Exception:
        pass
    # ===== 【修复20260826】持仓感知：同向加仓 / 反向平仓 =====
    _existing_pos = position_manager.get(symbol)
    if _existing_pos is not None:
        _existing_direction = str(_existing_pos.get("direction", ""))
        _new_direction = _route_direction
        _existing_entry0 = float(_existing_pos.get("entry") or 0.0)
        _existing_sl0 = float(_existing_pos.get("current_sl") or _existing_pos.get("sl") or 0.0)

        # 同方向信号 → 加仓（合并均价、累加仓位）
        if _existing_direction and _new_direction and _existing_direction == _new_direction:
            _new_size = float(trade_size)
            # 【修复20260904】P1：验证期限制加仓，防止仓位叠加过大
            # 最多加仓 1 次（add_count=1），总 size 上限 0.08
            _MAX_ADD_COUNT = 1
            _MAX_TOTAL_SIZE = 0.08
            _cur_add_count = int(_existing_pos.get("add_count") or 0)
            _cur_size = float(_existing_pos.get("size") or 0.0)
            if _cur_add_count >= _MAX_ADD_COUNT or (_cur_size + _new_size) > _MAX_TOTAL_SIZE:
                slog.warning(
                    f"[V6 分级路由 - 加仓拦截] {symbol} add_count={_cur_add_count} "
                    f"size={_cur_size:.4f}+{_new_size:.4f} 超限（max_add={_MAX_ADD_COUNT}, max_size={_MAX_TOTAL_SIZE}），跳过加仓"
                )
                return False
            _old_size = float(_existing_pos.get("size") or 0.0)
            if _old_size <= 0:
                _old_size = _new_size
            _combined_size = _old_size + _new_size
            _new_entry_px = float(result.get("entry") or 0.0)
            if _existing_entry0 > 0 and _new_entry_px > 0:
                _avg_entry_px = (_existing_entry0 * _old_size + _new_entry_px * _new_size) / _combined_size
            else:
                _avg_entry_px = _new_entry_px if _new_entry_px > 0 else _existing_entry0
            _new_sl0 = float(result.get("sl") or 0.0)
            _final_sl = _existing_sl0
            if _existing_direction in ("Long", "long") and _new_sl0 > _existing_sl0:
                _final_sl = _new_sl0
            elif _existing_direction in ("Short", "short") and 0 < _new_sl0 < _existing_sl0:
                _final_sl = _new_sl0

            _existing_pos["entry"] = _avg_entry_px
            _existing_pos["current_sl"] = _final_sl
            _existing_pos["size"] = _combined_size
            _existing_pos["add_count"] = int(_existing_pos.get("add_count") or 0) + 1
            _existing_pos["last_add_time"] = time.time()
            _existing_pos["last_add_price"] = _new_entry_px
            # 【修复20260904】加仓必须复用持仓原 signal_id，禁止换新 id
            # 否则平仓时 signal_id 在 trade_snapshots 无对应开仓行 → UPDATE 0 行假回写
            if not _existing_pos.get("signal_id"):
                _existing_pos["signal_id"] = sig_id
            position_manager.update(symbol, _existing_pos)
            try:
                from analytics.state_recovery import save_positions
                save_positions(position_manager.get())
            except Exception:
                pass
            slog.info(
                f"[V6 分级路由 - 加仓] {symbol} {_new_direction} "
                f"size={_old_size:.4f}+{_new_size:.4f}={_combined_size:.4f} "
                f"avg_entry={_avg_entry_px:.2f} new_SL={_final_sl:.2f} "
                f"add_count={_existing_pos['add_count']}"
            )
            _short_id = _existing_pos.get("short_id") or _short_signal_id(_existing_pos.get("signal_id") or sig_id)
            safe_send(
                f"🟢 加仓 #{_short_id} {symbol} ({_new_direction})\n"
                f"加仓: {_new_size:.4f} (原 {_old_size:.4f} → 总 {_combined_size:.4f})\n"
                f"新均价: {_avg_entry_px:.2f}  SL: {_final_sl:.2f}",
                priority="TRADE",
            )
            try:
                signal_deduper.mark_symbol_fired(symbol, _new_direction, _route_reason)
            except Exception as _mkr_e2:
                slog.error(f"[V6 分级路由] mark_symbol_fired 失败: {_mkr_e2}")
            return True

        # 反方向信号 → 全平旧仓（不立即开反向新仓）
        elif _existing_direction and _new_direction and _existing_direction != _new_direction:
            _close_reason = f"REVERSE_SIGNAL_{_new_direction}"
            _exit_price = float(result.get("entry") or result.get("price") or 0.0)
            slog.warning(
                f"[V6 分级路由 - 反向平仓] {symbol} 旧方向={_existing_direction} "
                f"新信号方向={_new_direction}，全平旧仓并冷却"
            )
            try:
                _close_px = _exit_price if _exit_price > 0 else _existing_sl0
                _trigger_stop_loss(symbol, _existing_pos, _close_px, reason=_close_reason)
            except Exception as _rev_e:
                slog.error(f"[V6 分级路由 - 反向平仓失败] {symbol}: {_rev_e}")
                return False
            try:
                signal_deduper.mark_symbol_fired(symbol, _new_direction, _route_reason)
            except Exception as _mkr_e3:
                slog.error(f"[V6 分级路由] mark_symbol_fired 失败: {_mkr_e3}")
            return False

        else:
            slog.warning(f"[V6 分级路由] {symbol} 无法确定持仓方向，跳过")
            return False

    slog.info(f"[V6 分级路由 - 实盘激活] {symbol} {level} 信号 ({score}分) | 分配仓位: {trade_size}")
    try:
        event_logger.log_event("LIVE_TRADE", {
            "symbol": symbol,
            "direction": result.get("direction", ""),
            "score": result.get("v6_final_score", score),
            "ev": result.get("expected_value", 0.0),
            "confidence": result.get("confidence", 0.0),
            "regime": str(result.get("regime", "unknown")),
            "feature_hash": generate_feature_hash(str(result.get("features", {}))),
            "features": result.get("features", {}),
            "entry_price": result.get("entry", 0.0),
            "stop_price": result.get("sl", 0.0),
            "reason": route,
            "v6_level": level,
            "v6_route": route,
        })
    except Exception:
        pass
    entry = result.get("entry", 0.0)
    sl = result.get("sl", 0.0)
    tp1 = result.get("tp1", 0.0)
    tp2 = result.get("tp2", 0.0)
    tp3 = result.get("tp3", 0.0)
    ev = result.get("expected_value", 0.0)
    rr = result.get("rr", 0.0)
    reason = result.get("reason", "?")

    def _translate_reason(reason_text: str) -> str:
        if not reason_text:
            return ''
        text = str(reason_text).lower()
        parts = []
        if 'v56.5' in text or 'v56_5' in text:
            parts.append('来自 V56.5 信号引擎')
        if 'structure_override' in text or 'override' in text:
            parts.append('结构覆盖通道：该信号结构强度高，满足 V56.5 的 Structure Override 特权通道，优先通过后续质量过滤。')
        if 'liquidity_sweep' in text:
            parts.append('流动性扫单：关键流动性区域被扫，属于高结构信号。')
        if 'choch' in text:
            parts.append('结构转变：市场当前出现结构性转折，方向有变动。')
        if 'fvg' in text:
            parts.append('失衡区：市场存在价格回补机会。')
        if 'bos' in text:
            parts.append('结构突破：价格已突破关键结构位，动量方向明确。')
        if not parts:
            return reason_text
        return '；'.join(parts)

    reason_cn = _translate_reason(reason)
    _short_id = _short_signal_id(sig_id)
    safe_send(
        f"🟢 开单 #{_short_id} {symbol} ({result.get('direction','?')})\n"
        f"级别: {level} ({score:.1f}分) | 仓位: {trade_size:.4f}\n"
        f"入场: {entry:.2f}  SL: {sl:.2f}  TP1: {tp1:.2f}  TP2: {tp2:.2f}  TP3: {tp3:.2f}\n"
        f"评分: {score:.1f} | EV: {ev:.4f} | RR: {rr:.2f}\n"
        f"原因: {reason}\n"
        f"说明: {reason_cn}",
        priority="TRADE",
    )
    # 【修复20260810】推送成功后必须立即标记冷却，否则下一次扫描冷却检查永远通过
    try:
        signal_deduper.mark_symbol_fired(symbol, _route_direction, _route_reason)
    except Exception as _mkr_e:
        slog.error(f"[V6 分级路由] mark_symbol_fired 失败: {_mkr_e}")

    # ===== [修复20260825] V6 路由确认开仓后，必须写入持仓管理器 =====
    # 之前只发通知、返回 True，从不调用 position_manager.update()
    # 导致 main_loop / background_monitor 永远看不到持仓 -> 永远不触发追踪止损/平仓
    try:
        # 生成 trade_id 并保存到 position，供 ExitEventLogger 与 Outcome 去重使用
        _trade_id = str(uuid.uuid4())
        _open_short_id = _short_signal_id(sig_id)
        position_manager.update(symbol, {
            "direction": result.get("direction", "Long"),
            "short_id": _open_short_id,
            "signal_id": sig_id,
            "entry": entry,
            "current_sl": sl,
            "initial_risk": abs(entry - sl) if sl and entry else 0.0,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "stage": 0,
            "sl_hit": False,
            "last_sl_msg": "",
            "score": float(result.get("v6_weighted_score", result.get("v6_final_score", score))),  # FIX-20260913 prefer FeedbackLoop weighted score
            "confidence": result.get("confidence", 0.5),
            "regime": str(result.get("regime", "UNKNOWN")),
            "features": result.get("_feedback_features", []),
            "ev": float(result.get("_feedback_ev", ev)),
            "signal_id": sig_id,
            "atr": float(result.get("atr") or 0.0),
            "ml_prob": float(result.get("ml_prob") or result.get("p_win_raw") or 0.0),
                        "ml_active": bool(result.get("ml_active", False)),
            "trade_id": _trade_id,
            "open_time": time.time(),     # 新增：持仓超时保护需要
            "max_hold_seconds": 14400.0,  # 新增：4小时未到TP1强制平仓
        })
        slog.info(f"[V6路由] {symbol} 持仓已写入 position_manager: {position_manager.get(symbol)}")
        try:
            # 记录 OPEN 事件，便于后续与 EXIT 关联
            try:
                exit_logger.log_open(symbol, position_manager.get(symbol))
            except Exception:
                slog.error(f"[V6路由] exit_logger.log_open 失败: {symbol}")

            try:
                from analytics.state_recovery import save_positions

                try:
                    save_positions(position_manager.get())
                except Exception as _sp_e:
                    slog.error(f"[StateRecovery] save_positions failed: {_sp_e}")
            except Exception:
                pass

            # ===== [修复20260826] V6 路由实盘激活也需记录 v6_research.db =====
            # 之前只有 RESEARCH_SILENT 分支会记录，LIVE_FULL_TRADE / LIVE_HALF_TRADE
            # 缺失 record_open_snapshot 调用，导致实盘开单不进入研究数据库
            try:
                async_background_task(async_record_snapshot_and_push(result, kelly_size=trade_size))
                slog.info(f"[V6路由] {symbol} 实盘开单快照已记录入 v6_research.db")
            except Exception as _snap_e:
                slog.error(f"[V6路由] 实盘开单快照记录失败: {_snap_e}")
        except Exception:
            pass
    except Exception as _pm_e:
        slog.error(f"[V6路由] 持仓写入失败: {_pm_e}")
        traceback.print_exc()

    return True


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
    # ===== 资金曲线熔断器检查 =====
    if not _breaker.can_open():
        _snap = _breaker.snapshot()
        slog.info(f"[check_and_open] 熔断器禁止开单: {_snap}")
        return False

    if not result:
        slog.warning("[check_and_open] result 为空，拒绝开单")
        return False

    # ===== 【RiskGuard增强】总风险 R 硬上限 =====
    try:
        _total_risk_r = 0.0
        for _sym, _pos in position_manager.get().items():
            if _pos.get("direction") and _pos.get("entry") and _pos.get("current_sl"):
                _e = float(_pos["entry"])
                _s = float(_pos["current_sl"])
                if _e > 0 and abs(_e - _s) / _e > 0.001:
                    _total_risk_r += abs(_e - _s) / _e
        if _total_risk_r > 0.03:  # 总风险 > 3%（≈ 3R）
            slog.warning(f"[RiskGuard] 总风险 {_total_risk_r:.4f} > 0.03, 拒绝开单")
            return False
    except Exception:
        pass

    symbol = result.get("symbol", "?")
    direction = result.get("direction", None)

    # 【关键修复】被否决信号（LIQUIDITY_SWEEP 等一票否决归零）在此直接拦截，防止兜底链路误开单
    if bool(result.get("rejected", False)) or float(result.get("score", 0.0) or 0.0) <= 0.0:
        slog.warning(f"[check_and_open] {symbol} 信号已被否决（score={result.get('score', 0.0)}，rejected={result.get('rejected', False)}），拒绝开单")
        return False

    # ---- 止损冷却 ----
    if not _check_cooldown(symbol):
        slog.warning(f"[{symbol}] GATE-2 cooling skip")
        return False

    approved = result.get("approved", False)
    if not approved or not direction:
        slog.warning(f"[{symbol}] GATE-3 approved={approved} direction={direction} 拒绝")
        return False
    
    # ===== 提取优化模块字段 =====
    htf_blocked = result.get("htf_blocked", False)
    features = result.get("features", {})
    blended_ev = result.get("blended_ev", result.get("expected_value", 0.0))
    
        # ===== 【优化2 - HTF Regime Filter】大方向拦截 =====
    if htf_blocked:
        slog.warning(f"[{symbol}] GATE-4 HTF Regime 拦截 {direction}: 1H 趋势方向不允许，直接拒绝")
        return False
    
    # ===== 【优化5 - Statistical EV】使用 blended_ev 替代原始 ev =====
    # blended_ev = 历史实际 EV * 0.6 + model_ev * 0.4
    # 当历史样本不足时 = model_ev
    ev = float(result.get("_feedback_ev", 0.0)) or blended_ev
    score = result.get("score", 0.0)
    
    # ===== 【V56.5 SQZMOM 精细化加分/扣分】=====
    sqz_data = result.get("sqz_data", {}) or {}
    if sqz_data.get("released", False):
        _sqz_adj = 12.0
        if int(sqz_data.get("duration", 0)) >= 15:
            _sqz_adj += 8.0
            slog.info(f"⚡ [{symbol}] [SQZMOM蓄能爆发] 连续蓄势 {sqz_data['duration']} 根 K 线，追加爆发分！")
        if not bool(sqz_data.get("volume_confirmed", False)):
            _sqz_adj -= 10.0
            slog.info(f"⚠️ [{symbol}] [SQZMOM量能背离] 缩量释放 (放量比:{sqz_data.get('vol_ratio', 0):.2f})，触发软扣分。")
        score += _sqz_adj
        result["_sqz_score_adj"] = round(_sqz_adj, 2)
        result["_sqz_data"] = sqz_data
        result["score"] = score
        slog.info(f"[{symbol}] SQZMOM release adjust: duration={sqz_data.get('duration')} strength={sqz_data.get('strength')} vol_ratio={sqz_data.get('vol_ratio')} confirmed={sqz_data.get('volume_confirmed')} adj={_sqz_adj:+.1f}")

    # ===== 【V21 FeatureLearningEngine】权重调整分数 =====
    _fl_score = result.get("_feature_learning_score", 0.0)
    if _fl_score > 0 and _fl_score != score:
        slog.info(f"[{symbol}] FeatureLearning: score={score:.1f} -> {_fl_score:.1f}")
        score = _fl_score

        # ===== 【V6 四级分级路由】替代老式 ScoreGrade =====
    # 将 score 灌入 v6_final_score 后由 check_and_open_v6_with_routing 接管
    result["v6_final_score"] = score
    # 先跑 FeaturePenalty 让分数更精确再路由
    _overlap_penalty = calculate_feature_overlap(features)
    result["feature_penalty"] = _overlap_penalty
    _adjusted_score = apply_feature_penalty(score, features)
    slog.info(f"[{symbol}] FeaturePenalty: 原始score={score:.1f} -> 调整后={_adjusted_score:.1f} (penalty={_overlap_penalty})")
    score = _adjusted_score
    result["score"] = score
    result["v6_final_score"] = score

    # ===== FIX-20260913-FB-BREAKER-BEFORE-ROUTE =====
    _fb_res = result.get("_feedback_result", {})
    if _fb_res.get("should_reject", False):
        slog.warning(f"[{symbol}] FeedbackLoop BREAKER: ev={_fb_res.get('ev', 0):.4f}, confidence={_fb_res.get('confidence', 0):.3f} < threshold={_fb_res.get('reject_threshold', 0.30)}")
        return False

    if not check_and_open_v6_with_routing(result):
        return False

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
        slog.warning(f"[{symbol}] StatisticalEVGate 拒绝: ev={ev:.4f} < threshold={_ev_threshold} (regime={_regime}, vol={_vol_state})")
        return False
    else:
        slog.info(f"[{symbol}] StatisticalEVGate 通过: ev={ev:.4f} >= threshold={_ev_threshold}")
    
    # ---- 原有低阈值检查（已由 StatisticalEVGate 覆盖，保留为安全兜底）----
    if ev < MIN_EV_FOR_PUSH:
        slog.warning(f"[{symbol}] EV={ev:.4f}<{MIN_EV_FOR_PUSH} skip")
        return False

    if score < MIN_SCORE_FOR_PUSH:
        slog.warning(f"[{symbol}] score={score:.1f}<{MIN_SCORE_FOR_PUSH} skip")
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
            slog.warning(f"[{symbol}] funding {funding:.6f} adverse for {direction}, skip")
            return False

    long_score = result.get("long_score", 0)
    short_score = result.get("short_score", 0)
    score_gap = abs(long_score - short_score)

    # ===== 【修复20260726】动态 Gap 阈值 =====
    # 在震荡市（ADX<25或CHOP/RANGE）降低 gap 要求
    _regime_for_gap = str(result.get("regime", "UNKNOWN")).upper().strip()
    _adx_for_gap = safe_get_float(result, "adx", default=0.0) or safe_get_float(result, "exec_ctx", "adx", default=0.0)
    _is_chop = _regime_for_gap in ("CHOP", "RANGE") or _adx_for_gap < 25
    _dynamic_gap = MIN_SCORE_GAP * (0.66 if _is_chop else 0.80)  # ⚠️ 降级: V56.5信号自证实方向，gap仅做噪音过滤
    slog.info(f"[{symbol}] GapCheck: regime={_regime_for_gap} adx={_adx_for_gap:.1f} is_chop={_is_chop} dynamic_gap={_dynamic_gap} long={long_score:.1f} short={short_score:.1f} gap={score_gap:.1f}")

        # ⚠️ 修复: V56.5 引擎决定方向，GapCheck 仅要求总分差 >= threshold
    # 不要求特定方向的分数必须更高 (V56.5 的 direction 和 exec_ctx score 可能来自不同指标源)
    gap_passed = abs(long_score - short_score) >= _dynamic_gap

    if not gap_passed:
        slog.warning(f"[{symbol}] Gap 不满足, skip. ")
        return False

    _reason = safe_get_str(result, "reason", default="?")
    # 统一冷却：开仓前先查 deduper
    if signal_deduper.is_symbol_cooled(symbol, direction, _reason):
        slog.warning(f"[RiskGuard] 同类信号冷却 {symbol}_{direction}_{_reason} (deduper)")
        return False
    # 兼容旧内存冷却字典
    _sig_cool_key = f"{symbol}_{direction}_{_reason}"
    _SIGNAL_COOLDOWN_SECS = 5 * 75  # 5根15m K线
    if _sig_cool_key in _last_signal_time:
        _elapsed = time.time() - _last_signal_time[_sig_cool_key]
        if _elapsed < _SIGNAL_COOLDOWN_SECS:
            slog.warning(f"[RiskGuard] 同类信号冷却 {_sig_cool_key} ({_elapsed:.0f}s < {_SIGNAL_COOLDOWN_SECS}s)")
            return False

    sig_id = _signal_id(result)
    if _is_signal_already_processed(sig_id):
        slog.info(f"[{symbol}] signal {sig_id} already processed")
        return False

    # 通过去重后立刻记冷却，防止并发双开
    signal_deduper.mark_symbol_fired(symbol, direction, _reason)
    _last_signal_time[_sig_cool_key] = time.time()
        
    if position_manager.exists(symbol):
        slog.info(f"[{symbol}] already has position")
        return False
    
    # ===== 【修复20260704】趋势位置检查：防止开在趋势末尾 =====
    # Short 开单检查：如果价格已经从 swing_high 下跌超过一定幅度，不开
    exec_ctx = safe_get(result, "exec_ctx", default={}) or {}
    swing_high = safe_get_float(exec_ctx, "swing_high", default=0.0)
    swing_low = safe_get_float(exec_ctx, "swing_low", default=0.0)
    atr_val = safe_get_float(result, "atr", default=1.0) or 1.0
    entry_price = entry
    
    if direction == "Short" and swing_high > 0 and swing_high > entry_price:
        drop_from_high = (swing_high - entry_price) / max(atr_val, 1)
        slog.info(f"[{symbol}] Short: swing_high={swing_high:.1f} price={entry_price:.1f} drop={drop_from_high:.1f}atr (limit={TREND_END_PULLBACK_ATR}atr)")
        if drop_from_high > TREND_END_PULLBACK_ATR:
            slog.info(f"[{symbol}] 价格已从高位下跌 {drop_from_high:.1f}ATR > {TREND_END_PULLBACK_ATR}ATR，趋势末端不开Short")
            return False
    elif direction == "Long" and swing_low > 0 and swing_low < entry_price:
        rise_from_low = (entry_price - swing_low) / max(atr_val, 1)
        slog.info(f"[{symbol}] Long: swing_low={swing_low:.1f} price={entry_price:.1f} rise={rise_from_low:.1f}atr (limit={TREND_END_PULLBACK_ATR}atr)")
        if rise_from_low > TREND_END_PULLBACK_ATR:
            slog.info(f"[{symbol}] 价格已从低点上涨 {rise_from_low:.1f}ATR > {TREND_END_PULLBACK_ATR}ATR，趋势末端不开Long")
            return False

        # ===== 【修复20260715】RR 软校验：RR < 1.0 仅降仓，不拒单 =====
    # V56.5 estimated_rr 来自信号bar的原始估算，SL纠正后实际RR可能不同
    actual_rr = result.get("rr", 0) or 0
    if actual_rr < 1.0:
        slog.info(f"[{symbol}] RR={actual_rr:.2f} < 1.0, 降仓处理 (size*=0.5)")
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
        slog.info(f"[{symbol}] V37 Gate 拦截: {_v37_reason}")
        return False
    else:
                slog.info(f"[{symbol}] V37 Gate 通过 ({_v37_reason}), size_mult={_v37_size_mult}")

        # ===== 【SmartPositionSizer】智能仓位计算 =====
    _calib = _fb_res.get("calibration", {})
    _sizer = get_smart_sizer()
    # 【新增20260729】ATR 百分比 + 风险金额限制
    _atr_pct = float(result.get("atr", 0)) / max(float(entry if entry > 0 else result.get("entry", 1)), 1e-8)
    _entry_p = float(result.get("entry", 0))
    _sl_p = float(result.get("sl", 0))
        # 从 V6 路由获取仓位倍率（替代已移除的 ScoreGrader）
    _v6_route = result.get("action_route", "")
    _v6_grade_size_mult = 0.5 if _v6_route == "LIVE_HALF_TRADE" else 1.0
    _size_result = _sizer.calculate(
        score=score,
        confidence=result.get("confidence", 0.5),
        avg_win_r=_calib.get("avg_win_r", 0.50),
        avg_loss_r=_calib.get("avg_loss_r", 0.50),
        base_leverage=0.05,
        grade_size_mult=_v6_grade_size_mult,
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
        slog.info(f"[{symbol}] Mud regime: 仓位额外缩减至 {_mud_cut*100:.0f}% -> final_size={_size_result['final_size']:.4f}")

    slog.info(f"[{symbol}] SmartSizer: final_size={_size_result['final_size']:.4f} (Kelly={_size_result['kelly_pct']:.3f} grade={_size_result['grade_mult']:.2f} env={_size_result['env_mult']:.2f} regime={_size_result['regime_mult']:.2f} vol={_size_result['vol_mult']:.2f} cons_loss={_size_result['cons_loss_mult']:.2f} score_mult={_size_result['score_mult']:.2f})")

    # ===== 【新增20260723】DailyRiskGuard 日风险检查 =====
    if not _risk_guard.can_trade():
        slog.info(f"[{symbol}] DailyRiskGuard 拦截: 日内风控限制")
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
        slog.info(f"[{symbol}] AdaptiveWeighter: 统计跟踪 (不影响评分) raw={_raw_feature_scores} weighted={_weighted_score:.2f}")
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

    _REASON_TRANSLATIONS = {
        'liquidity_sweep': '流动性扫单：关键流动性区域被扫，属于高结构信号',
        'choch': '结构转变：市场当前出现结构性转折，方向有变动',
        'fvg': '失衡区：市场存在价格回补机会',
        'bos': '结构突破：价格已突破关键结构位，动量方向明确',
        'structure_override': '结构覆盖通道：该信号结构强度高，满足特权通道',
        'breakout': '突破：价格突破关键位',
        'reversal': '反转：趋势反转信号',
        'sweep': '扫单：流动性被扫',
        'premium': '溢价区做空',
        'discount': '折价区做多',
        'ote': '最优交易区间',
        'wvf': '威廉姆斯鳄鱼线',
        'squeeze': '挤仓释放',
        'macd_bull': 'MACD金叉',
        'macd_bear': 'MACD死叉',
        'rsi_ob': 'RSI超买',
        'rsi_os': 'RSI超卖',
        'trend_break': '趋势破位',
        'supply_zone': '供应区',
        'demand_zone': '需求区',
    }

    def _translate_reason(reason_text: str) -> str:
        if not reason_text:
            return ''
        text = str(reason_text).lower()
        parts = []
        if 'v56.5' in text or 'v56_5' in text:
            parts.append('来自 V56.5 信号引擎')
        for key, desc in _REASON_TRANSLATIONS.items():
            if key in text and desc not in parts:
                parts.append(desc)
        if not parts:
            return reason_text
        return '；'.join(parts)

    reason_cn = _translate_reason(reason)
    msg = "\n".join([
        f"{dir_emoji2} [{symbol}] {dir_cn} 信号通过",
        f"入场: {entry:.2f}  SL: {sl:.2f}  TP1: {tp1:.2f}  TP2: {tp2:.2f}  TP3: {tp3:.2f}",
        f"评分: {score:.1f} | EV: {ev:.4f} | RR: {rr:.2f}",
        f"原因: {reason}",
        f"说明: {reason_cn}",
        f"多头: {lp_s:.1f}分  空头: {sp_s:.1f}分  分差: {sg:.1f}分",
    ])
    safe_send(msg, priority="TRADE")
    slog.info(f"[{symbol}] Strategy open before update: exists={position_manager.exists(symbol)} current={position_manager.get(symbol)}")
    # 【修复 signal_id 一致性】使用 check_and_open_v6_with_routing 生成的 V6_XXX_TIMESTAMP 格式
    _db_signal_id = result.get("signal_id", "")
    if not _db_signal_id:
        _db_signal_id = f"V6_{symbol.replace('/', '')}_{int(time.time())}"
        result["signal_id"] = _db_signal_id
    position_manager.update(symbol, {
        "direction": direction,
        "entry": entry,
        "current_sl": sl,
        "initial_risk": abs(entry - sl) if sl and entry else 0.0,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "stage": 0,
        "sl_hit": False,
        "last_sl_msg": "",
        "score": score,  # 【闭环】用于平仓时回传 Calibrator
        "confidence": result.get("confidence", 0.5),  # 【闭环】
        "regime": str(result.get("regime", "UNKNOWN")),  # 【闭环】用于平仓时更新 RegimeFeatureStats
        "features": result.get("_feedback_features", []),  # 【闭环】用于平仓时更新
        "ev": ev,
        "trade_id": (position_manager.get(symbol) or {}).get("trade_id") or str(uuid.uuid4()),
        "signal_id": _db_signal_id,
        "atr": float(result.get("atr") or 0.0),
        "ml_prob": float(result.get("ml_prob") or result.get("p_win_raw") or 0.0),
        "ml_active": bool(result.get("ml_active", False)),
    })

    try:
        from analytics.state_recovery import save_positions
        try:
            save_positions(position_manager.get())
        except Exception:
            pass
    except Exception:
        pass

    # 🔒 真正开单成功后才标记已处理（避免被 Quality Gate 拒绝后仍占用排重）
    try:
        signal_deduper.mark_processed(sig_id)
        try:
            position_manager.mark_signal_processed(sig_id)
        except Exception:
            pass
    except Exception as _m_e:
        slog.warning(f"[{symbol}] mark_processed failed: {_m_e}")

    # 🟢 钩子 2：瞬间拍摄并锁定高维开单特征快照

    # ===== V2 智能仓位计算器 + 熔断器乘数 =====
    try:
        account_balance = 1000.0
        try:
            from risk.portfolio_state import PortfolioStateManager
            _ps = PortfolioStateManager().load()
            account_balance = float(getattr(_ps, "equity", 1000.0))
        except Exception:
            pass

        _calc = _sizer_v2.calculate(
            score=score,
            confidence=result.get("confidence", 0.5),
            avg_win_r=0.50,
            avg_loss_r=0.50,
            base_leverage=result.get("base_size", 0.05),
            grade_size_mult=result.get("size_multiplier", 1.0),
            env_size_mult=1.0,
            regime=str(result.get("regime", "UNKNOWN")),
            volatility=str(result.get("vol_state", "NORMAL")),
            atr_pct=float(result.get("atr", 0.0)) / max(float(result.get("entry", 1)), 0.001),
            account_balance=account_balance,
            entry_price=entry,
            sl_price=sl,
        )
        _v2_size = _calc["final_size"]
        _breaker_mult = _breaker.size_multiplier()
        _final_size = _v2_size * _breaker_mult
        _final_size = max(0.003, min(0.12, _final_size))
        result["size"] = _final_size
        result["_v2_calc"] = _calc
    except Exception as exc:
        slog.error('[_sizer_v2] calculate failed: {exc}, using original size={result.get("size", 0.05)}')

    emit("record_open_snapshot", result, kelly_size=result.get("size", 0.05))

    slog.info(f"[{symbol}] Strategy open after update: exists={position_manager.exists(symbol)} current={position_manager.get(symbol)}")
    slog.info(f"[{symbol}] Strategy open pushed (EV={ev:.4f}, score={score:.1f})")

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
            try:
                from analytics.state_recovery import save_positions
                save_positions(position_manager.get())
            except Exception:
                pass
            _fb_raw_scores = result.get("_feedback_raw_scores", {})
            if _tracker_signal_id and _fb_raw_scores:
                _feature_learner.record_features(signal_id=_tracker_signal_id, features=_fb_raw_scores)
    except Exception as _tracker_e:
        slog.error(f"[SignalTracker] 记录开单失败: {_tracker_e}")
    
    # ===== 【开单日志写入】 =====
    # 1. TradeJournal（日志审计）
    _order_id = None
    try:
        # V59.5: 构造质量门快照（供 TradeJournal 复盘: 什么条件导致亏损）
        # 优先使用 runner 注入的 gate_snapshot，缺失时用 result 字段构造
        _gate_snapshot = str(result.get("gate_snapshot", "{}"))
        if not _gate_snapshot or _gate_snapshot == "{}":
            try:
                _gate_snapshot = str(result.get("decision", {}).get("gate_snapshot", "{}"))
            except Exception:
                _gate_snapshot = "{}"
        if not _gate_snapshot or _gate_snapshot == "{}":
            _gate_override = False
            try:
                _gate_override = bool(result.get("decision", {}).get("gate_snapshot_override", False)) or bool(result.get("gate_overridden", False))
            except Exception:
                pass
            _gate_snapshot = (
                '{"score":%.1f,"min_score_required":%.1f,"override":%s,"adx":%.1f,"regime":"%s","ev":%.4f}'
                % (
                    float(score),
                    float(result.get("min_score_required", 72.0)),
                    "true" if _gate_override else "false",
                    float(result.get("adx", 0)),
                    str(result.get("regime", "mixed")),
                    float(ev),
                )
            )
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
            gate_snapshot=_gate_snapshot,  # V59.5 质量门快照
        )
        # V59.7 漏斗统计：实际开仓成功
        if _order_id:
            trade_funnel.add("opened")
        # 把 order_id 存入 position_manager，供后续平仓追溯
        if _order_id:
            _pos_data = position_manager.get(symbol)
            if _pos_data:
                _pos_data["order_id"] = _order_id
                position_manager.update(symbol, _pos_data)
                try:
                    from analytics.state_recovery import save_positions
                    save_positions(position_manager.get())
                except Exception:
                    pass
    except Exception as tj_err:
        slog.error(f"[TradeJournal] 写入失败: {tj_err}")
    
    # 2. FeatureStore（信号特征分析）
    try:
        _adx_val = safe_get_float(result, "adx", default=0.0) or safe_get_float(result, "exec_ctx", "adx", default=0.0)
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
        slog.error(f"[Feature] save trade error: {feat_e}")

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
            slog.info(f"[信号后验] {sig_id} max_forward_r={_mf:.2f} max_adverse_r={_ma:.2f} final_r={_fr:.2f} exit={_er}")
                        # 存入 position_manager，供 check_trailing 平仓时更新
            # 【修复 signal_id 一致性】使用 V6 格式的 signal_id 而非指纹格式
            _pos_data2 = position_manager.get(symbol)
            if _pos_data2:
                _pos_data2["signal_id"] = _db_signal_id  # 使用 V6_XXX_TIMESTAMP 格式
                _pos_data2["audit_forward"] = _mf
                _pos_data2["audit_adverse"] = _ma
                _pos_data2["audit_final_r"] = _fr
                _pos_data2["audit_exit"] = _er
                position_manager.update(symbol, _pos_data2)
                try:
                    from analytics.state_recovery import save_positions
                    save_positions(position_manager.get())
                except Exception:
                    pass
    except Exception as _audit_e:
                slog.error(f"[SignalAuditLog] 后验记录异常: {_audit_e}")
        
    # ⚡ DailyReport：记录交易
    from analytics.daily_report import daily_report
    _mode = "PROBE" if result.get("probe_mode") else "NORMAL"
    daily_report.record_trade(mode=_mode)
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



    # 调用风险模块的追踪止损逻辑
    try:
        atr_val = pos.get("atr", 0) or (entry * 0.01)
        stage = pos.get("stage", 0)
        tp1 = pos.get("tp1", 0)
        tp2 = pos.get("tp2", 0)
        # 更新最大有利/不利波动（MFE / MAE）并回写持仓
        try:
            if str(pos.get("direction", "")).lower().startswith("long"):
                pos["mfe"] = max(pos.get("mfe", 0), current_price - entry)
                pos["mae"] = min(pos.get("mae", 0), current_price - entry)
            else:
                pos["mfe"] = max(pos.get("mfe", 0), entry - current_price)
                pos["mae"] = min(pos.get("mae", 0), entry - current_price)
            try:
                position_manager.update_fields(symbol, **pos)
                try:
                    from analytics.state_recovery import save_positions
                    save_positions(position_manager.get())
                except Exception:
                    pass
            except Exception:
                pass
        except Exception:
            pass
        
                # ===== 浮盈回撤保护（ratchet 棘轮逻辑）=====
        # 现象：单子浮盈已超 3R 但 TP1 未触达（TP1距离太远），随后深回调直接把 SL 扫掉，亏损 -1R
        # 方案：MFE 每提升一个利润档位，SL 至少前移到锁盈利点位
        try:
            _stage_now = stage
            _mfe_val = float(pos.get("mfe") or 0.0)
            _init_risk = float(pos.get("initial_risk") or risk or 0.0)
            if _init_risk > 0 and _stage_now < 2:
                _lock_sl = None
                if _mfe_val >= 3.5 * _init_risk:
                    _lock_sl = entry + 2.0 * _init_risk if direction == "Long" else entry - 2.0 * _init_risk
                elif _mfe_val >= 2.5 * _init_risk:
                    _lock_sl = entry + 1.5 * _init_risk if direction == "Long" else entry - 1.5 * _init_risk
                elif _mfe_val >= 1.8 * _init_risk:
                    _lock_sl = entry + 1.0 * _init_risk if direction == "Long" else entry - 1.0 * _init_risk

                if _lock_sl is not None:
                    _sl_locked = float(pos.get("sl_protected") or 0.0)
                    _new_lock = False
                    if direction == "Long":
                        _cap = current_price - 0.5 * _init_risk
                        if _lock_sl > _cap:
                            _lock_sl = _cap
                        if _lock_sl > sl and _lock_sl > _sl_locked and _lock_sl < current_price:
                            _new_lock = True
                    else:
                        _cap = current_price + 0.5 * _init_risk
                        if _lock_sl < _cap:
                            _lock_sl = _cap
                        if _lock_sl < sl and (_lock_sl < _sl_locked or _sl_locked == 0) and _lock_sl > current_price:
                            _new_lock = True

                    if _new_lock:
                        pos["current_sl"] = _lock_sl
                        pos["sl_protected"] = _lock_sl
                        sl = _lock_sl
                        position_manager.update_fields(symbol, **pos)
                        try:
                            from analytics.state_recovery import save_positions
                            save_positions(position_manager.get())
                        except Exception:
                            pass
                        _short_id = pos.get("short_id") or _short_signal_id(pos.get("signal_id") or "")
                        safe_send(
                            f"🔒 浮盈锁定 #{_short_id} {symbol} {direction} | "
                            f"MFE {_mfe_val/_init_risk:.1f}R → SL 移至 {_lock_sl:.2f}（锁定 {abs(_lock_sl - entry)/_init_risk:.1f}R）",
                            priority="TRADE",
                        )
        except Exception as _rat_e:
            slog.error(f"[check_trailing] 浮盈棘轮保护异常 {symbol}: {_rat_e}")

                # ===== 持仓超时保护：开仓超过 max_hold_seconds 仍未到TP1，强制平仓 =====
        try:
            _open_ts = float(pos.get("open_time") or pos.get("opened_at") or pos.get("created_at") or 0.0)
            _max_hold = float(pos.get("max_hold_seconds") or 14400.0)  # 默认4小时
            if _open_ts > 0 and stage < 2 and time.time() > _open_ts + _max_hold:
                _hold_mins = (time.time() - _open_ts) / 60.0
                slog.warning(
                    f"[EXIT_MANAGER] {symbol} {direction} 已持仓 {_hold_mins:.1f}min"
                    f" > 上限 {_max_hold/60.0:.1f}min，且未到TP1（stage={stage}），强制平仓"
                )
                _trigger_stop_loss(symbol, pos, current_price, reason="MAX_HOLD_TIMEOUT")
                return
        except Exception as _timeout_e:
            slog.error(f"[check_trailing] 持仓超时保护异常 {symbol}: {_timeout_e}")

        pos_dict = {
            "direction": direction,
            "entry": entry,
            "current_sl": sl,
            "initial_risk": float(pos.get("initial_risk") or 0.0),
            "tp1": tp1,
            "tp2": tp2,
            "stage": stage,
            "atr": atr_val,
        }

        action_plan = check_partial_close_and_trail(
            pos_dict,
            current_price,
        )

        slog.info(
            f"""
[EXIT_MANAGER]
symbol={symbol}
action={action_plan.get('action')}
reason={action_plan.get('reason')}
R={action_plan.get('profit_r')}
stage={action_plan.get('stage')}
"""
        )

        if action_plan.get("action") == "PARTIAL_CLOSE":
            if action_plan.get('new_sl') is not None:
                pos["current_sl"] = action_plan.get("new_sl")
            pos["stage"] = action_plan.get("stage", pos.get('stage', 0))
            position_manager.update_fields(symbol, **pos)
            try:
                from analytics.state_recovery import save_positions
                save_positions(position_manager.get())
            except Exception:
                pass
            _short_id = pos.get("short_id") or _short_signal_id(pos.get("signal_id") or "")
            _partial_reason_cn = _exit_reason_cn(action_plan.get("reason") or "TP1_HIT")
            _partial_r = action_plan.get("profit_r", 0.0) or 0.0
            _partial_sl_txt = f" | SL推至 {action_plan.get('new_sl')}" if action_plan.get('new_sl') else ""
            safe_send(f"🟡 部分{_partial_reason_cn} #{_short_id} {symbol} {direction} | 入场 {entry:.2f} → 现价 {current_price:.2f} | {_partial_r:+.2f}R{_partial_sl_txt}", priority="TRADE")
        elif action_plan.get("action") == "MOVE_SL" and action_plan.get('new_sl') is not None:
            pos["current_sl"] = action_plan.get('new_sl')
            pos["stage"] = action_plan.get("stage", pos.get('stage', 0))
            position_manager.update_fields(symbol, **pos)
            try:
                from analytics.state_recovery import save_positions
                save_positions(position_manager.get())
            except Exception:
                pass
            _short_id = pos.get("short_id") or _short_signal_id(pos.get("signal_id"))
            _trail_suffix = f" | 浮盈 {action_plan.get('profit_r', 0.0):+.2f}R" if action_plan.get('profit_r') is not None else ""
            _move_reason_cn = _exit_reason_cn(action_plan.get("reason") or "MOVE_SL")
            safe_send(f"🛡️ {_move_reason_cn} #{_short_id} {symbol} {direction} | 新SL {action_plan.get('new_sl')}{_trail_suffix}", priority="TRADE")

        elif action_plan["action"] == "CLOSE_ALL":
            _trigger_stop_loss(symbol, pos, current_price, reason=action_plan.get('reason') or 'CLOSE_ALL')
        elif action_plan["action"] == "HOLD":
            pass
    except Exception as e:
        slog.error(f"[check_trailing] {symbol} 异常: {e}")


def _trigger_stop_loss(symbol: str, pos: dict, current_price: float, reason: str = "SL"):
    """触发止损/平仓：日志、推送、V6 outcome 回写、清持仓。

    [修复20260910] 增加防重入保护：
      app.py Monitor 线程与 hf_auto_trader 主循环可能同时触发同一持仓的 CLOSE_ALL，
      通过持仓存在性检查避免重复推送 / 重复 V6 回写 / 重复 DailyPanel / 重复 Feedback。
    """

    # [修复20260910] 原子抢占持仓：仅抢到持仓的线程被授权执行平仓
    # Monitor 线程与主策略线程可能同时触发同一持仓，原子 pop 确保只有一方成功
    try:
        _popped = position_manager.pop(symbol)
        if _popped is None:
            slog.warning(
                f"[{symbol}] _trigger_stop_loss 原子抢占失败：持仓已被其他线程平仓"
                f" reason={reason} price={current_price}"
            )
            # 已平仓则直接返回，不重复推送/回写/统计
            return False
        # 用最新数据覆盖传入的 stale pos（MFE/MAE/signal_id 等字段更完整）
        pos = _popped
    except Exception as _re_e:
        slog.error(f"[{symbol}] _trigger_stop_loss 原子抢占异常，回退 get 检查: {_re_e}")
        # 回退：position_manager 无 pop 方法时，用 get + 存在性检查兜底
        try:
            _current_store = position_manager.get(symbol)
            if _current_store is None:
                return False
            pos = _current_store
        except Exception:
            pass

    direction = pos.get("direction", "Long")
    entry = float(pos.get("entry") or 0.0)
    sl = float(pos.get("current_sl") or pos.get("sl") or 0.0)
    signal_id = pos.get("signal_id") or ""
    try:
        # 平仓后允许同一 K 线的相同形态信号重新触发（不再永久拦截）
        if signal_id:
            signal_deduper.unmark_processed(signal_id)
            slog.info(f"[{symbol}] 已释放去重标记: {signal_id}")
    except Exception as _um_e:
        slog.error(f"[{symbol}] 释放信号去重标记失败: {_um_e}")

    pnl_r = 0.0
    risk = abs(entry - sl) if sl and entry else 0.0
    if risk <= 0:
        risk = float(pos.get("initial_risk") or 0.0)
    if risk > 0:
        if direction == "Long":
            pnl_r = (current_price - entry) / risk
        else:
            pnl_r = (entry - current_price) / risk

    max_fwd = float(pos.get("audit_forward") or 0.0)
    max_adv = float(pos.get("audit_adverse") or 0.0)

    slog.info(
        f"[{symbol}] 平仓触发: {reason} direction={direction} "
        f"entry={entry:.2f} price={current_price:.2f} pnl_r={pnl_r:.2f} signal_id={signal_id}"
    )
    _short_id = pos.get("short_id") or _short_signal_id(signal_id)
    _close_reason_cn = _exit_reason_cn(reason)
    _close_emoji = "🔴" if pnl_r < 0 else ("✅" if pnl_r >= 1.0 else "🟡")
    safe_send(
        f"{_close_emoji} {_close_reason_cn} #{_short_id} {symbol} {direction} | {reason} 入场 {entry:.2f} → 出场 {current_price:.2f} | {pnl_r:+.2f}R",
        priority="TRADE",
    )

    # V59.7 漏斗统计：平仓完成
    trade_funnel.add("closed")
    # ===== 数据闭环：回写 trade_snapshots.pnl_r / exit_reason =====
    if signal_id:
        try:
            emit(
                "record_close_outcome",
                signal_id=signal_id,
                pnl_r=float(pnl_r),
                exit_reason=_normalize_exit_reason(reason or "CLOSE_ALL"),
                max_fwd=max_fwd,
                max_adv=max_adv,
                exit_timestamp=time.time(),
                exit_price=float(current_price),
            )
            slog.info(f"[{symbol}] V6 outcome 已回写: {signal_id} {pnl_r:+.2f}R reason={reason}")
        except Exception as _oc_e:
            slog.error(f"[{symbol}] V6 outcome 回写失败: {_oc_e}")
    else:
        slog.warning(f"[{symbol}] 平仓无 signal_id，跳过 trade_snapshots 回写")

    # 本地持仓清理：pop 已移除，仅确保快照/持久化 + trade_journal CLOSE 完成
    try:
        if position_manager.get(symbol) is not None:
            # 若仍存在（例如 pop 时为 None 走了回退路径），则完整 close（内部会写 trade_journal）
            position_manager.close(
                symbol, pnl_r=pnl_r, exit_reason=_normalize_exit_reason(reason or "CLOSE_ALL"), exit_price=current_price
            )
        else:
            # 已 pop 移除：手动写 trade_journal CLOSE（原子抢占后需要）
            _order_id = pos.get("order_id") or ""
            if _order_id:
                try:
                    from state.trade_journal import journal as _tj
                    _tj.close_trade(
                        order_id=_order_id,
                        close_price=float(current_price),
                        pnl_r=float(pnl_r or 0.0),
                        exit_reason=str(reason or "CLOSE_ALL"),
                    )
                    slog.info(f"[{symbol}] trade_journal CLOSE 已写入（原子抢占后）: {_order_id}")
                except Exception as _tj_e:
                    slog.error(f"[{symbol}] trade_journal 写入失败: {_tj_e}")
            else:
                slog.warning(f"[{symbol}] 无 order_id，跳过 trade_journal CLOSE")
            # 手动做每日快照 + 持久化（close 内部逻辑的补偿）
            try:
                position_manager._daily_snapshot()
                position_manager._save()
            except Exception as _snap_e:
                slog.error(f"[{symbol}] 快照/持久化补偿失败: {_snap_e}")
    except Exception as _pm_e:
        slog.error(f"[{symbol}] 清除持仓失败: {_pm_e}")
        try:
            position_manager.remove(symbol)
        except Exception:
            pass

    # 持久化当前持仓状态到磁盘，便于进程重启恢复
    try:
        from analytics.state_recovery import save_positions
        try:
            save_positions(position_manager.get())
        except Exception as _sp_e:
            slog.error(f"[StateRecovery] save_positions failed: {_sp_e}")
    except Exception:
        pass

    try:
        _breaker.record_trade(pnl_r)
        _sizer_v2.record_outcome(pnl_r)
    except Exception:
        pass

    # ===== V59.7: EXIT事件统一写入（从 check_trailing 移至此处，覆盖所有平仓路径） =====
    try:
        exit_logger.log_exit(
            symbol=symbol,
            position=pos,
            exit_price=current_price,
            reason=_normalize_exit_reason(reason or "CLOSE_ALL"),
            action='CLOSE_ALL',
            mfe=pos.get('mfe'),
            mae=pos.get('mae')
        )
    except Exception as _el_err:
        slog.error(f"[{symbol}] exit_logger.log_exit 失败: {_el_err}")

    # ===== V59.5: 平仓后激活 DailyPanel + FeedbackLoop（历史从未调用，日报数据一直为空） =====
    # pos 中已保存 score/confidence/regime/features/ev（开仓时注入），此处直接读取
    _close_score = float(pos.get("score", 0) or 0)
    _close_conf = float(pos.get("confidence", 0.5) or 0.5)
    _close_regime = str(pos.get("regime", "UNKNOWN"))
    _close_features = pos.get("features", []) or []
    _close_ev = float(pos.get("ev", 0) or 0)
    try:
        _panel.on_trade_closed(
            regime=_close_regime,
            features=_close_features,
            score=_close_score,
            confidence=_close_conf,
            pnl_r=float(pnl_r),
            direction=dir(pos).get("direction", "") if direction is None else str(direction),
        )
    except Exception as _panel_err:
        slog.error(f"[{symbol}] DailyPanel 平仓记录失败: {_panel_err}")

    try:
        _feedback.on_trade_closed(
            regime=_close_regime,
            features=_close_features,
            score=_close_score,
            confidence=_close_conf,
            pnl_r=float(pnl_r),
            direction=str(direction),
        )
    except Exception as _fb_err:
        slog.error(f"[{symbol}] FeedbackLoop 平仓记录失败: {_fb_err}")

    # 每日凌晨跨日时自动推送日报（仅一次）
    try:
        _panel.try_send_report(safe_send, _panel_today_sent)
    except Exception as _report_err:
        slog.error(f"[{symbol}] DailyPanel 日报推送失败: {_report_err}")

    global _last_stop_loss_time
    _last_stop_loss_time[symbol] = time.time()
    try:
        signal_deduper.mark_sl_hit(symbol)
    except Exception:
        pass
# ============================================================
# 【修复】主循环 — app.py 684 行等待的入口函数
# ============================================================
async def main_loop():
    """自动交易主循环：扫描多品种信号并管理持仓"""
    slog.info(f"[main_loop] 主循环启动，监控品种: {SYMBOLS}")

    # ===== 启动持仓恢复 + 对账（只做一次）=====
    global _RECOVERED_POSITIONS
    try:
        if not _RECOVERED_POSITIONS and ENABLE_RUNTIME_RECOVERY:
            try:
                _report = position_reconciler.startup_recover(do_exchange=True, sync_local_from_exchange=True)
                _n = len(getattr(_report, "recovered_symbols", None) or [])
                _diffs = getattr(_report, "diffs", None) or []
                _issues = [d for d in _diffs if getattr(d, "kind", "ok") != "ok"]
                slog.info(f"[main_loop] reconciler 启动恢复完成: recovered={_n} mode={getattr(_report, 'mode', '?')} issues={len(_issues)}")
                for _d in _issues:
                    slog.info(f"[main_loop] reconciler diff: {getattr(_d, 'kind', '?')} {getattr(_d, 'symbol', '?')} {getattr(_d, 'detail', '')}")
            except Exception as _rec_e:
                slog.error(f"[main_loop] reconciler 失败，回退磁盘恢复: {_rec_e}")
                try:
                    _syms = position_manager.recover_from_disk()
                    slog.info(f"[main_loop] 磁盘恢复: {_syms}")
                except Exception as _disk_e:
                    slog.error(f"[main_loop] 磁盘恢复也失败: {_disk_e}")
            _RECOVERED_POSITIONS = True
    except Exception as _e:
        slog.error(f"[main_loop] 启动恢复异常: {_e}")

    loop_interval = 10
    while True:
        try:
            # 【修复20260816】约每 2 分钟对账一次；reconciler 内部还有 min_interval
            if int(time.time()) % 120 < loop_interval:
                try:
                    position_reconciler.periodic_check(min_interval_sec=120.0)
                except Exception as _pe:
                    slog.error(f"[main_loop] periodic_check: {_pe}")
            for symbol in SYMBOLS:
                try:
                    # ===== [V6-增强] 虚拟持仓价格检查 =====
                    # 检查 RESEARCH_SILENT 虚拟持仓是否触发 SL/TP1/超时
                    try:
                        _vt_price = await _fetch_ticker_price(symbol)
                        if _vt_price is not None and _vt_price > 0:
                            from utils.research_tracker import get_research_tracker
                            _rt = get_research_tracker()
                            _triggers = _rt.update_price(symbol, _vt_price)
                            for _trg in _triggers:
                                try:
                                    emit("record_close_outcome",
                                         signal_id=_trg["signal_id"],
                                         pnl_r=_trg["pnl_r"],
                                         exit_reason=_trg["exit_reason"],
                                         max_fwd=_trg["max_fwd"],
                                         max_adv=_trg["max_adv"],
                                         exit_timestamp=int(time.time()),
                                         exit_price=_trg["exit_price"])
                                    slog.info(f"[main_loop] 虚拟持仓回写: {_trg['signal_id']} "
                                              f"pnl_r={_trg['pnl_r']:+.2f}R reason={_trg['exit_reason']}")
                                except Exception as _trg_e:
                                    slog.error(f"[main_loop] 虚拟持仓回写失败: {_trg_e}")
                    except Exception as _vt_e:
                        slog.error(f"[main_loop] 虚拟持仓检查异常: {_vt_e}")
                    # ===== [V6-增强] 虚拟持仓价格检查 - 结束 =====

                    positions = position_manager.get()
                    pos = positions.get(symbol)
                    if pos is not None:
                        current_price = await _fetch_ticker_price(symbol)
                        if current_price is not None:
                            check_trailing(symbol, pos, current_price)
                        # ===== 【关键修复】持仓互斥：已有未平仓持仓时直接跳过开仓扫描 =====
                        # 根因：原逻辑在 pos 非空时只检查了 check_trailing，但没有阻止后续的
                        #       _breaker.can_open() → scan_and_decide() → check_and_open_v6_with_routing()
                        #       导致已有持仓时仍持续扫描信号、重复开新单/叠加RESEARCH快照
                        slog.info(f"[main_loop] {symbol} 已有未平仓持仓({pos.get('direction','?')})，跳过开仓扫描")
                        continue
                    if _breaker.can_open():
                        result = await scan_and_decide(symbol)
                        if result is not None:
                            # 【关键修复】被风控否决的信号（LIQUIDITY_SWEEP 一票否决等）禁止进入任何路由/推送链路
                            if bool(result.get("rejected", False)) or float(result.get("score", 0.0) or 0.0) <= 0.0:
                                slog.warning(f"[main_loop] {symbol} 信号已被风控否决（score={result.get('score', 0.0)}），跳过开单链路")
                            else:
                                # 【修复20260913】禁止回退到旧 check_and_open
                                # 旧函数没有 HTF/FeedbackLoop 熔断检查，导致拦截后仍强行开单
                                opened = check_and_open_v6_with_routing(result)
                                if not opened:
                                    slog.warning(f"[main_loop] {symbol} V6 路由拒绝开单（HTF/FeedbackLoop熔断），不调用旧链路")
                except Exception as sym_e:
                    slog.error(f"[main_loop] {symbol} 处理异常: {sym_e}")
                    continue
        except asyncio.CancelledError:
            slog.info("[main_loop] 主循环被取消，正常退出")
            break
        except Exception as loop_e:
            slog.error(f"[main_loop] 循环异常: {loop_e}")
        await asyncio.sleep(loop_interval)

