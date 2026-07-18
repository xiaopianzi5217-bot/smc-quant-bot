# -*- coding: utf-8 -*-
""" 把这一段复制到 app.py 最顶部，用来压制 Gradio/SSR/后台线程环境下的 asyncio fd -1 析构噪音。 这不是策略致命错误，只是启动/退出事件循环清理时的警告。 """

import asyncio
import os
import warnings

# 强制 Python 时区为 Asia/Shanghai，解决日志时间与北京时间不同步的问题
os.environ["TZ"] = "Asia/Shanghai"
try:
    import time as _time_mod
    _time_mod.tzset()
except Exception:
    pass

warnings.filterwarnings("ignore", category=ResourceWarning)

try:
    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
except Exception:
    pass

def ensure_thread_event_loop():
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop

""" SMC V11+V9 Quant System - Integrated Control Console 包含：系统监控、工程体检、策略扫描、快速回测、Telegram 信号工具、后台自愈监控。 """
import os
import json
import traceback
import time
import threading
from pathlib import Path

import pandas as pd
import gradio as gr

from backtest.runner import run_backtest, summarize_backtest
from monitoring.runtime_report import write_json_report
from monitoring.health_check import HealthCheck
from ops.env_config import load_runtime_config, redact_config
from ops.runtime_paths import ensure_runtime_dirs, REPORTS_DIR
from runner.v11_institutional_runner import run_once, make_sample_ohlcv
from validation.system_audit import audit_project
from indicators.basic import add_all_indicators
from strategy.smc import build_macro_context, build_exec_context
from strategy.scoring import adaptive_signal_score
from strategy.risk import calculate_dynamic_tp_sl, check_partial_close_and_trail
from strategy.observer_events import detect_observer_events
from notifier.observer.risk_plan import build_rr_plan
from notifier.observer.signal_collector import build_signal_snapshot
from notifier.observer.funding import fetch_funding_rate_safe, normalize_swap_symbol
from notifier.manager import dispatch_observer_snapshot, dispatch_strategy_decision, dispatch_execution_event
from decision.v9_decision_kernel import V9DecisionKernel
from decision.v37_gate import v37_final_gate
from state.position_manager import position_manager
from config import STRATEGY_PARAMS, SYMBOL_STRATEGY
from utils.symbols import load_symbol_strategy
from utils.time_utils import series_ms_to_bj

try:
    from notifier.telegram import send_telegram, test_telegram
    print("DEBUG: Telegram 模块加载成功")
except Exception as exc:
    _telegram_import_error = traceback.format_exc()
    print("!!! DEBUG: Telegram 模块加载失败 !!!")
    print(_telegram_import_error)
    send_telegram = None

    def test_telegram(_err=_telegram_import_error):
        return "Telegram 模块导入失败:\n" + _err

ensure_runtime_dirs()

# 全局探针与持仓字典 (供后台监控线程使用)
health_monitor = HealthCheck(max_data_age_sec=300)
MANAGED_POSITIONS = {} 
# 结构示例: {'BTC/USDT': {'direction':'Long', 'entry': 60000, 'current_sl': 59000, 'tp1': 61000, 'tp2': 62500, 'stage': 0}}

def is_real_trading_ready():
    return bool(os.getenv('BITGET_API_KEY') and os.getenv('BITGET_SECRET'))

def _json(x): return json.dumps(x, ensure_ascii=False, indent=2, default=str)

def safe_send_telegram(msg):
    try:
        if send_telegram is None: return False, "Telegram 模块未加载"
        return True, send_telegram(msg)
    except Exception: return False, traceback.format_exc()

def system_status():
    try:
        cfg = load_runtime_config()
        safe = redact_config(cfg)
        return "OK", _json(safe)
    except Exception: return "ERROR", traceback.format_exc()

def deep_audit():
    try:
        result = audit_project(Path(__file__).resolve().parent)
        write_json_report("deep_audit.json", result)
        return result.get("status", "UNKNOWN"), _json(result)
    except Exception: return "ERROR", traceback.format_exc()

def dry_run(symbols_text):
    try:
        overrides = {}
        symbols = [x.strip() for x in symbols_text.split(",") if x.strip()]
        if symbols: overrides["symbols"] = symbols
        # 强制使用真实数据模式
        overrides["data_mode"] = "live"
        cfg = load_runtime_config(overrides=overrides)
        # 确保 telegram 配置启用
        if "telegram" not in cfg:
            cfg["telegram"] = {}
        cfg["telegram"]["send_observer"] = True
        cfg["telegram"]["send_approved"] = True
        results = run_once(cfg)
        
        # 记录健康探针打点
        for s in symbols: health_monitor.mark_tick(s)
            
        path = write_json_report("latest_signals.json", results)
        rows = []
        for r in results:
            rows.append({"symbol": r.get("symbol"), "approved": r.get("approved"), "state": r.get("state"), "reason": r.get("reason")})
        return pd.DataFrame(rows), _json(results), f"saved: {path}"
    except Exception: return pd.DataFrame(), traceback.format_exc(), "failed"

def quick_backtest(symbol, exec_csv, macro_csv, max_rows, warmup):
    try:
        # 兜底：如果没有传入文件，自动使用默认路径
        exec_path = exec_csv or "data/BTCUSDTUSDT_15m.csv"
        macro_path = macro_csv or "data/BTCUSDTUSDT_1h.csv"
        
        # 【核心修改：智能解除限制】
        # 如果滑块数值大于 2900，说明用户想跑全量，直接设为 99万行
        rows_to_use = int(max_rows) if int(max_rows) < 2900 else 999999
        
        # 加载数据
        df_exec = pd.read_csv(exec_path)
        if rows_to_use and rows_to_use < len(df_exec):
            df_exec = df_exec.tail(rows_to_use + int(warmup)).reset_index(drop=True)
        
        # 先计算所有技术指标（ATRr_14, ema_50, ema_200, adx 等）
        from config import STRATEGY_PARAMS
        df_exec = add_all_indicators(df_exec, STRATEGY_PARAMS["wvf_std_mult"])
        
        # 断言式强保障：列出策略运行必须死守的底层指标
        absolute_required = ["ema_50", "ema_200", "adx", "ATRr_14"]
        missing = [col for col in absolute_required if col not in df_exec.columns]
        if missing:
            err_msg = f"🛑 拦截：指标计算未成功注入！缺失核心列: {missing}，请检查 add_all_indicators"
            return err_msg, pd.DataFrame(), "failed"
        
        # 强保障通过，100% 安全地切掉冷启动 NaN 行
        df_exec = df_exec.dropna(subset=absolute_required).copy()
        if len(df_exec) < 100:
            return "清洗后可用样本过少（<100 行），请提供更多数据", pd.DataFrame(), "failed"
        
        # ===== SMC 结构点位向量化计算 =====
        # 用 rolling 窗口计算 eq_high / eq_low（结构高低点）
        # 回溯窗口 20 根 K 线：平衡捕捉短期结构变动与避免假突破骗炮
        df_exec["eq_high"] = df_exec["high"].rolling(20, min_periods=5).max()
        df_exec["eq_low"] = df_exec["low"].rolling(20, min_periods=5).min()
        
        # last_lower_high：最近一个未被突破的 Lower High（次高点）
        # 用 diff 找出 eq_high 下降的位置 → 只保留这些下降高点 → ffill 向后顺延
        is_lower_high = df_exec["eq_high"] < df_exec["eq_high"].shift(1)
        lower_high_values = df_exec["eq_high"].where(is_lower_high)
        df_exec["last_lower_high"] = lower_high_values.ffill()
        
        # last_higher_low：最近一个未被突破的 Higher Low（次低点）
        # 用 diff 找出 eq_low 上升的位置 → 只保留这些上升低点 → ffill 向后顺延
        is_higher_low = df_exec["eq_low"] > df_exec["eq_low"].shift(1)
        higher_low_values = df_exec["eq_low"].where(is_higher_low)
        df_exec["last_higher_low"] = higher_low_values.ffill()
        
        # last_swing_low / last_swing_high：用 rolling 最低/最高价作为结构参考
        df_exec["last_swing_low"] = df_exec["low"].rolling(10, min_periods=3).min()
        df_exec["last_swing_high"] = df_exec["high"].rolling(10, min_periods=3).max()
        # ob_low / ob_high：用 rolling 5 根作为 OB 区域参考
        df_exec["ob_low"] = df_exec["low"].rolling(5, min_periods=2).min()
        df_exec["ob_high"] = df_exec["high"].rolling(5, min_periods=2).max()
        # high_5 / low_5：最近 5 根 K 线的最高/最低（用于突破探针）
        df_exec["high_5"] = df_exec["high"].rolling(5, min_periods=2).max()
        df_exec["low_5"] = df_exec["low"].rolling(5, min_periods=2).min()
        
        # bsl / ssl：Buyside / Sellside 流动性（用 20 根最高/最低作为参考）
        df_exec["bsl"] = df_exec["high"].rolling(20, min_periods=5).max()
        df_exec["ssl"] = df_exec["low"].rolling(20, min_periods=5).min()
        
        # liquidity_target：用 eq_high/eq_low 均值作为流动性目标
        df_exec["liquidity_target"] = (df_exec["eq_high"] + df_exec["eq_low"]) / 2
        
        # squeeze_on：直接用 SQZMOM 的挤压状态（已在 add_all_indicators 中计算）
        # lowsqz = BB 收缩进 KCL（最紧），midsqz = 收缩进 KCM，highsqz = 收缩进 KCH
        df_exec["squeeze_on"] = df_exec.get("lowsqz", pd.Series(False, index=df_exec.index))
        
        # signal_age：信号年龄计数器（避免 ffill 导致的过度平滑）
        # 当 MSS 信号产生时 age=0，之后每根 K 线 +1，超过 3 根后 runner 拒绝开仓
        from backtest.structure_engine import structure_signal
        raw_signal = structure_signal(df_exec)
        # 用 groupby + cumcount：每次 signal 变化时重置计数
        # signal 为 True 时 age=0,1,2,...；signal 为 False 时 age=999
        signal_group = (raw_signal != raw_signal.shift()).cumsum()
        df_exec["signal_age"] = raw_signal.groupby(signal_group).cumcount()
        df_exec.loc[~raw_signal, "signal_age"] = 999
        
        # 用 ffill() 向前填充所有 SMC 字段的缺失值（冷启动区间）
        _smc_cols = ["eq_high", "eq_low", "last_lower_high", "last_higher_low",
                     "last_swing_low", "last_swing_high", "ob_low", "ob_high",
                     "bsl", "ssl", "liquidity_target", "squeeze_on"]
        df_exec[_smc_cols] = df_exec[_smc_cols].ffill()
        
        # 如果 ffill 后仍有 NaN（数据太少），用 close 兜底
        for col in ["last_lower_high", "last_higher_low", "last_swing_low", "last_swing_high", "ob_low", "ob_high"]:
            df_exec[col] = df_exec[col].fillna(df_exec["close"])
        for col in ["eq_high", "bsl", "liquidity_target"]:
            df_exec[col] = df_exec[col].fillna(df_exec["high"])
        for col in ["eq_low", "ssl"]:
            df_exec[col] = df_exec[col].fillna(df_exec["low"])
        df_exec["squeeze_on"] = df_exec["squeeze_on"].fillna(False)
        
        # ===== 加载 1H 大级别数据，计算 htf_direction =====
        try:
            df_macro = pd.read_csv(macro_path)
            df_macro = add_all_indicators(df_macro, STRATEGY_PARAMS["wvf_std_mult"])
            from strategy.smc import build_macro_context
            macro_ctx = build_macro_context(df_macro)
            htf_dir = macro_ctx.get("allowed_direction", "")
        except Exception:
            htf_dir = ""
        # 将 htf_direction 映射到每根 15m K 线上（整列填充）
        df_exec["htf_direction"] = htf_dir
        print(f"🔍 1H 大级别方向: {htf_dir}")
        
        # 使用新的 backtest.runner.run(df) API
        trades_list = run_backtest(df_exec)
        trades = pd.DataFrame(trades_list)
        
        # 使用 analytics.report.summarize_closed_trades
        if not trades.empty:
            summary = summarize_backtest(trades)
        else:
            summary = {"trades": 0, "win_rate": 0.0, "avg_r": 0.0, "total_r": 0.0, "profit_factor": 0.0}
        
        out = Path(REPORTS_DIR) / "hf_backtest_trades.csv"
        trades.to_csv(out, index=False)
        write_json_report("hf_backtest_summary.json", summary)
        return _json(summary), trades.tail(50), str(out)
    except Exception: return traceback.format_exc(), pd.DataFrame(), "failed"

def _normalize_symbol(symbol): return normalize_swap_symbol(symbol)

def _fetch_live_ohlcv(symbol, timeframe="15m", limit=320):
    """拉取OHLCV（使用requests直连，绕过ccxt SSL问题）"""
    import requests
    sym_raw = _normalize_symbol(symbol)
    sym = sym_raw.split("/")[0] + sym_raw.split("/")[1].split(":")[0]
    tf_map = {
        "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
        "1h": "1H", "2h": "2H", "4h": "4H", "6h": "6Hutc", "12h": "12Hutc",
        "1d": "1Dutc", "3d": "3Dutc", "1w": "1Wutc", "1M": "1Mutc",
    }
    granularity = tf_map.get(timeframe, "15m")
    url = "https://api.bitget.com/api/v2/mix/market/candles"
    params = {"symbol": sym, "productType": "umcbl", "granularity": granularity, "limit": min(limit, 500)}
    resp = requests.get(url, params=params, timeout=15)
    if resp.status_code != 200:
        raise ConnectionError(f"HTTP {resp.status_code}")
    data = resp.json()
    if data.get("code") != "00000":
        raise RuntimeError(f"API Error: {data.get('msg', 'unknown')}")
    bars = data.get("data", [])
    if not bars:
        raise RuntimeError("No data returned")
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

def fetch_live_funding_rate(symbol):
    try:
        import requests
        sym_raw = _normalize_symbol(symbol)
        sym = sym_raw.split("/")[0] + sym_raw.split("/")[1].split(":")[0]
        url = "https://api.bitget.com/api/v2/mix/market/current-fund-rate"
        params = {"symbol": sym}
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == "00000":
                fr = data.get("data", {}).get("fundingRate", "N/A")
                if fr != "N/A":
                    return float(fr) * 100  # 转百分比
        return "N/A"
    except Exception:
        return "N/A"

def build_local_snapshot_and_decision(symbol):
    symbol = (symbol or "BTC/USDT").strip()
    source = "live"
    try:
        df_exec = _fetch_live_ohlcv(symbol, "15m", 320)
        df_macro = _fetch_live_ohlcv(symbol, "1h", 320)
        health_monitor.mark_tick(symbol) # 数据拉取成功，标记心跳
    except Exception:
        source = "sample_fallback"
        df_exec = make_sample_ohlcv(start=100.0)
        df_macro = make_sample_ohlcv(start=102.0)

    df_exec = add_all_indicators(df_exec, STRATEGY_PARAMS["wvf_std_mult"])
    df_macro = add_all_indicators(df_macro, STRATEGY_PARAMS["wvf_std_mult"])
    macro_ctx = build_macro_context(df_macro)
    exec_ctx = build_exec_context(df_exec)
    exec_ctx["data_source"] = source
    exec_ctx["funding_rate"] = fetch_live_funding_rate(symbol)

    curr = df_exec.iloc[-1]
    avg_vol = df_exec["volume"].rolling(20).mean().iloc[-1]
    is_vol = bool(curr["volume"] > avg_vol * 1.5) if avg_vol == avg_vol else False

    # ===== 补充评分系统所需字段 =====
    exec_ctx["htf_direction"] = macro_ctx.get("allowed_direction", "")
    exec_ctx["setup_type"] = "ob" if exec_ctx.get("ob_valid") else ("fvg" if exec_ctx.get("bearish_fvg") or exec_ctx.get("bullish_fvg") else "")
    exec_ctx["squeeze_released"] = str(exec_ctx.get("squeeze", "")).lower() in ("release", "squeeze_release", "released")
    exec_ctx["smc_zone_score"] = float(exec_ctx.get("pivot_strength_high", 0) or 0) + float(exec_ctx.get("pivot_strength_low", 0) or 0)
    exec_ctx["has_valid_zone"] = bool(exec_ctx.get("ob_valid"))
    exec_ctx["liquidity_sweep_confirmed"] = bool(exec_ctx.get("is_bsl_swept") or exec_ctx.get("is_ssl_swept"))
    exec_ctx["liquidity_wrong_side"] = bool((exec_ctx.get("is_bsl_swept") and not exec_ctx.get("is_ssl_swept")) or (exec_ctx.get("is_ssl_swept") and not exec_ctx.get("is_bsl_swept")))
    body = abs(float(curr.get("close", 0)) - float(curr.get("open", 0)))
    hilo = float(curr.get("high", 0)) - float(curr.get("low", 0))
    exec_ctx["body_pct"] = body / hilo if hilo > 0 else 0.0
    exec_ctx["macro_conflict"] = False
    exec_ctx["too_extended"] = False
    exec_ctx["fe_bottom"] = bool(curr.get("is_FE", False))
    exec_ctx["fe_top"] = bool(curr.get("is_Inv_FE", False))
    exec_ctx["same_side_div_count_12"] = 0.0
    exec_ctx["vwap_align"] = None
    exec_ctx["rr"] = 1.0
    exec_ctx["distance_atr"] = 0.0
    exec_ctx["ob_strength"] = float(exec_ctx.get("pivot_strength_high", 0) or 0)
    exec_ctx["fvg_quality"] = 1.0 if (exec_ctx.get("bearish_fvg") or exec_ctx.get("bullish_fvg")) else 0.0
    exec_ctx["displacement"] = float(exec_ctx.get("pivot_strength_low", 0) or 0)
    exec_ctx["liquidity"] = 1.0 if (exec_ctx.get("is_bsl_swept") or exec_ctx.get("is_ssl_swept")) else 0.0
    # ==================================

    # 构建多头和空头各自的评分上下文（方向相关字段分开设置）
    long_ctx = dict(exec_ctx)
    short_ctx = dict(exec_ctx)
    
    # 多头方向相关字段
    long_ctx["divergence_confirmed"] = bool(curr.get("has_bot_div", False))  # 底背离 = 多头
    long_ctx["sqzmom_divergence_dir"] = "Long" if bool(curr.get("has_bot_div", False)) else ""
    long_ctx["sqzmom_divergence_age"] = int(float(curr.get("bot_div_age", 999) or 999))
    long_ctx["sqzmom_divergence_strength"] = float(curr.get("bot_div_strength", 0) or 0)
    long_ctx["sqzmom_white_confirm"] = bool(curr.get("sqzmom_white_reversal_long", False))
    long_ctx["sqzmom_momentum_confirm"] = bool(curr.get("sqzmom_white_reversal_long", False))
    long_ctx["sqzmom_reversal_confirm_long"] = bool(curr.get("sqzmom_white_reversal_long", False))
    long_ctx["sqzmom_reversal_confirm_short"] = False
    long_ctx["sqzmom_dmi_aligned"] = bool(curr.get("dmi_bull", False))
    long_ctx["sqzmom_trigger_ok"] = bool(curr.get("dmi_bull", False))
    long_ctx["dmi_bull"] = bool(curr.get("dmi_bull", False))
    long_ctx["dmi_bear"] = False
    long_ctx["momentum"] = float(curr.get("momentum", 0) or 0)
    long_ctx["liquidity_sweep_confirmed"] = bool(curr.get("is_ssl_swept", False))  # sellside sweep = 多头信号
    long_ctx["liquidity_wrong_side"] = bool(curr.get("is_bsl_swept", False))  # buyside sweep = 空头信号，对多头是反方向
    
    # 空头方向相关字段
    short_ctx["divergence_confirmed"] = bool(curr.get("has_top_div", False))  # 顶背离 = 空头
    short_ctx["sqzmom_divergence_dir"] = "Short" if bool(curr.get("has_top_div", False)) else ""
    short_ctx["sqzmom_divergence_age"] = int(float(curr.get("top_div_age", 999) or 999))
    short_ctx["sqzmom_divergence_strength"] = float(curr.get("top_div_strength", 0) or 0)
    short_ctx["sqzmom_white_confirm"] = bool(curr.get("sqzmom_white_reversal_short", False))
    short_ctx["sqzmom_momentum_confirm"] = bool(curr.get("sqzmom_white_reversal_short", False))
    short_ctx["sqzmom_reversal_confirm_long"] = False
    short_ctx["sqzmom_reversal_confirm_short"] = bool(curr.get("sqzmom_white_reversal_short", False))
    short_ctx["sqzmom_dmi_aligned"] = bool(curr.get("dmi_bear", False))
    short_ctx["sqzmom_trigger_ok"] = bool(curr.get("dmi_bear", False))
    short_ctx["dmi_bull"] = False
    short_ctx["dmi_bear"] = bool(curr.get("dmi_bear", False))
    short_ctx["momentum"] = float(curr.get("momentum", 0) or 0)
    short_ctx["liquidity_sweep_confirmed"] = bool(curr.get("is_bsl_swept", False))  # buyside sweep = 空头信号
    short_ctx["liquidity_wrong_side"] = bool(curr.get("is_ssl_swept", False))  # sellside sweep = 多头信号，对空头是反方向

    l_score, l_thresh, l_reasons = adaptive_signal_score(long_ctx, macro_ctx, "Long", is_vol)
    s_score, s_thresh, s_reasons = adaptive_signal_score(short_ctx, macro_ctx, "Short", is_vol)
    direction = "Long" if l_score >= s_score else "Short"
    sym_strategy = load_symbol_strategy(symbol, SYMBOL_STRATEGY)
    min_rr = sym_strategy.get("min_rr", 2.0)

    sl, tp1, tp2, tp3, rr = calculate_dynamic_tp_sl(direction, curr, df_exec, exec_ctx, min_rr, sym_strategy)
    rr_plan = build_rr_plan(direction, float(curr["close"]), sl, tp1, tp2, tp3)

    # 将 smc_impulse_score 的分数注入 curr 供 V9DecisionKernel 读取
    curr_with_scores = dict(curr)
    curr_with_scores["long_score"] = l_score
    curr_with_scores["short_score"] = s_score

    snapshot = build_signal_snapshot(symbol=symbol, df=df_exec, macro_ctx=macro_ctx, exec_ctx=exec_ctx, long_score=l_score, long_threshold=l_thresh, long_reasons=l_reasons, short_score=s_score, short_threshold=s_thresh, short_reasons=s_reasons, rr_plan=rr_plan, funding_rate=exec_ctx["funding_rate"])
    decision = V9DecisionKernel(params=load_runtime_config()).decide(curr=curr_with_scores, macro_ctx=macro_ctx, exec_ctx=exec_ctx, long_score=l_score, long_threshold=l_thresh, long_reasons=l_reasons, short_score=s_score, short_threshold=s_thresh, short_reasons=s_reasons, min_rr=min_rr, rr=rr, direction=direction, entry=float(curr["close"]), sl=sl, tp1=tp1, tp2=tp2, tp3=tp3, symbol=symbol, timeframe="15m")
    print(f"=== Decision Debug ===")
    print(f"Decision type: {type(decision)}")
    print(f"Approved: {decision.get('approved')}")
    print(f"Reason: {decision.get('reason')}")
    print(f"Source/Version: {decision.get('source', 'unknown')} / {decision.get('version', 'unknown')}")
    print(f"Long score: {l_score:.2f}, Short score: {s_score:.2f}, Direction: {direction}")

    # ---- Observer 事件检测 ----
    observer_events = detect_observer_events(curr, exec_ctx)

    # ---- V37 Final Gate ----
    passed, reason, size_mult = v37_final_gate(decision, {
        "long_score": l_score or exec_ctx.get("long_score", 0),
        "short_score": s_score or exec_ctx.get("short_score", 0),
        **exec_ctx
    })
    if passed and decision.get("approved"):
        # ===== 顺发：Observer 结构事件 =====
                # 【已注释 202607】Observer 详细报告推送（系统打分不走决策，起不到指导作用）
        # if observer_events:
        #     try:
        #         dispatch_observer_snapshot(snapshot, send_all=True)
        #         print(f"[{symbol}] Observer 结构事件顺发完成 ({len(observer_events)} 个)")
        #     except Exception as exc:
        #         print(f"[{symbol}] Observer 顺发异常: {exc}")

        # ===== 顺发：开单信号推送 (Telegram) =====
        _emoji = "📈" if direction == "Long" else "📉"
        _msg = (
            f"{_emoji} [{symbol}] V37 Gate 通过\n"
            f"方向: {direction} | 入场: {float(curr['close']):.2f}\n"
            f"止损: {sl:.2f} | TP1: {tp1:.2f} TP2: {tp2:.2f} TP3: {tp3:.2f}\n"
            f"RR: {rr:.2f} | 评分: L{l_score:.1f} S{s_score:.1f}\n"
            f"size_mult: {size_mult} | Gate: {reason}\n"
            f"形态: {exec_ctx.get('setup_type','?')} | 大级别: {macro_ctx.get('allowed_direction','?')}"
        )
        safe_send_telegram(_msg)

        # ===== 写入全局持仓 =====
        position_manager.update(symbol, {
            "direction": decision["direction"],
            "entry": curr["close"],
            "current_sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "stage": 0,
        })
        # 同时写入 MANAGED_POSITIONS（供原有后台监控线程使用）
        MANAGED_POSITIONS[symbol] = {
            'direction': decision["direction"],
            'entry': curr["close"],
            'tp1': tp1,
            'tp2': tp2,
            'current_sl': sl,
            'stage': 0
        }
        print(f"[{symbol}] V37 Gate 通过 | size_mult={size_mult} | 开单信号已推送")
    else:
        print(f"[{symbol}] V37 Gate 拦截: {reason}")

    return snapshot, decision, source

def direct_observer_signal(symbol):
    try:
        snapshot, decision, source = build_local_snapshot_and_decision((symbol or "BTC/USDT").strip())
        result = dispatch_observer_snapshot(snapshot, send_all=True)
        data = snapshot.to_dict(); data["data_source"] = source
        return result, _json(data)
    except Exception: return "failed", traceback.format_exc()

def strategy_layer_signal(symbol):
    try:
        snapshot, decision, source = build_local_snapshot_and_decision((symbol or "BTC/USDT").strip())
        result = dispatch_strategy_decision(snapshot, decision)
        return result, _json({"approved": decision.get("approved"), "data_source": source, "decision": decision, "snapshot": snapshot.to_dict()})
    except Exception: return "failed", traceback.format_exc()

def execution_layer_status(symbol):
    try:
        event = {"type": "PORTFOLIO_BLOCK", "symbol": (symbol or "BTC/USDT").strip(), "message": "Execution 测试事件"}
        return dispatch_execution_event(event), _json(event)
    except Exception: return "failed", traceback.format_exc()

# ----------------- 后台监控守护线程 -----------------
def background_monitor_worker():
    import requests
    import time
    print("[Monitor] 后台守护线程已启动 (灾难监控 & 追踪止损 5s轮询)...")
    
    def _get_price(sym):
        try:
            sym_raw = normalize_swap_symbol(sym)
            sym_s = sym_raw.split("/")[0] + sym_raw.split("/")[1].split(":")[0]
            url = "https://api.bitget.com/api/v2/mix/market/candles"
            params = {"symbol": sym_s, "productType": "umcbl", "granularity": "1m", "limit": 1}
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == "00000" and data.get("data"):
                    return float(data["data"][0][4])
        except Exception:
            pass
        return None
    
    loop_count = 0
    while True:
        time.sleep(5)  # 【修复1】将 60 秒改为 5 秒，确保不会错过插针行情
        loop_count += 1
        try:
            # 1. 灾难自愈检测 (没必要每5秒查一次，每12次即60秒查一次即可)
            if loop_count % 12 == 0:
                if not health_monitor.is_healthy():
                    stales = health_monitor.check_stale_symbols()
                    if is_real_trading_ready():
                        safe_send_telegram(f"🚨 [系统警告] 数据超时掉线: {stales}")
            
            # 2. 追踪止损检测
            _all_positions_for_trail = position_manager.get()
            if loop_count % 12 == 0 and not _all_positions_for_trail:
                print("[Monitor DEBUG] 当前 position_manager 为空，没有活跃订单。")
            if not _all_positions_for_trail:
                continue
                
            for sym, pos in list(_all_positions_for_trail.items()):
                curr_price = _get_price(sym)
                if curr_price is None:
                    continue
                
                # 调用我们在 risk.py 中写的超强分批止盈逻辑
                action_plan = check_partial_close_and_trail(
                    direction=pos['direction'],
                    current_price=curr_price,
                    entry_price=pos['entry'],
                    current_sl=pos['current_sl'],
                    tp1=pos['tp1'],
                    tp2=pos['tp2'],
                    stage=pos.get('stage', 0)
                )
                
                if action_plan['action'] == 'PARTIAL_CLOSE':
                    msg = f"🏆 [{sym}] 到达目标位! 平仓 {action_plan['close_pct']*100}%，止损移至 {action_plan['new_sl']}"
                    safe_send_telegram(msg)
                    pos['current_sl'] = action_plan['new_sl']
                    pos['stage'] = action_plan['new_stage']
                    # ===== [修复20260817] 回写 position_manager =====
                    try:
                        position_manager.update(sym, dict(pos))
                    except Exception as _pm_e:
                        print(f"[Monitor] position_manager 回写失败: {_pm_e}")

                elif action_plan['action'] == 'TRAIL_ONLY' and abs(pos['current_sl'] - action_plan['new_sl']) > curr_price * 0.001:
                    pos['current_sl'] = action_plan['new_sl']
                    safe_send_telegram(f"🛡️ [{sym}] 追踪止损已推移至 {action_plan['new_sl']}")
                    # ===== [修复20260817] 回写 position_manager =====
                    try:
                        position_manager.update(sym, dict(pos))
                    except Exception as _pm_e:
                        print(f"[Monitor] position_manager 回写失败: {_pm_e}")

        except Exception as e:
            print(f"后台线程异常: {e}")

# 后台守护线程不要在 import app 时自动启动；否则测试/热加载导入时会触发 ccxt 连接。
# 实际 launch 时在 __main__ 中启动。
def start_background_monitor():
    t = threading.Thread(target=background_monitor_worker, daemon=True)
    t.start()
    return t
# --------------------------------------------------

with gr.Blocks(title="SMC Quant System") as demo:
    gr.Markdown("# SMC V11+V9 工程化量化系统")

    with gr.Tab("系统状态"):
        btn = gr.Button("检查配置"); status = gr.Textbox(label="Status"); cfg_box = gr.Code(label="Redacted config", language="json")
        btn.click(system_status, outputs=[status, cfg_box])

    with gr.Tab("策略干运行"):
        symbols = gr.Textbox(label="Symbols", value="BTC/USDT,ETH/USDT"); run_btn = gr.Button("运行信号扫描")
        table = gr.Dataframe(label="Signals"); raw = gr.Code(label="Raw decision JSON", language="json"); saved = gr.Textbox(label="Report")
        run_btn.click(dry_run, inputs=[symbols], outputs=[table, raw, saved])

    with gr.Tab("快速回测"):
        bt_symbol = gr.Textbox(label="Symbol", value="BTC/USDT:USDT"); exec_csv = gr.File(label="Execution CSV 15m", type="filepath"); macro_csv = gr.File(label="Macro CSV 1h", type="filepath")
        # 【修改：滑块上限拉到 50000】
        max_rows = gr.Slider(120, 50000, value=50000, step=1000, label="Max rows (设大即可全量读取)")
        warmup = gr.Slider(50, 500, value=120, step=10, label="Warmup")
        bt_btn = gr.Button("运行快速回测")
        summary = gr.Code(label="Summary", language="json"); trades = gr.Dataframe(label="Recent trades"); out_path = gr.Textbox(label="Output file")
        bt_btn.click(quick_backtest, inputs=[bt_symbol, exec_csv, macro_csv, max_rows, warmup], outputs=[summary, trades, out_path])

    with gr.Tab("信号工具"):
        test_btn = gr.Button("1. 测试 Telegram与微信", variant="secondary"); test_out = gr.Textbox(label="连接测试结果"); test_btn.click(test_telegram, outputs=test_out)
        manual_symbol = gr.Textbox(label="交易对 Symbol", value="BTC/USDT")
        obs_btn = gr.Button("2. Observer 层瞬发信号 (无视打分)", variant="primary"); obs_out = gr.Textbox(label="Observer 推送结果"); shared_raw = gr.Code(label="快照", language="json")
        obs_btn.click(direct_observer_signal, inputs=[manual_symbol], outputs=[obs_out, shared_raw])
        open_btn = gr.Button("3. Strategy 层严谨信号", variant="secondary"); open_out = gr.Textbox(label="Strategy 推送结果")
        open_btn.click(strategy_layer_signal, inputs=[manual_symbol], outputs=[open_out, shared_raw])

    with gr.Tab("持仓管理 (后台自愈)"):
        gr.Markdown("### 模拟注册持仓 (交由后台线程追踪)")
        track_sym = gr.Textbox(label="Symbol", value="BTC/USDT")
        track_dir = gr.Dropdown(choices=["Long", "Short"], label="方向", value="Long")
        track_entry = gr.Number(label="开仓价", value=60000)
        track_tp1 = gr.Number(label="TP1", value=61000)
        track_tp2 = gr.Number(label="TP2", value=62500)
        track_sl = gr.Number(label="初始止损", value=59000)
        
        reg_btn = gr.Button("注入持仓监控", variant="primary")
        reg_out = gr.Textbox(label="结果")
        
        def mock_register_position(sym, d, entry, tp1, tp2, sl):
            MANAGED_POSITIONS[sym] = {'direction': d, 'entry': float(entry), 'tp1': float(tp1), 'tp2': float(tp2), 'current_sl': float(sl), 'stage': 0}
            return f"成功接管: {sym}，后台线程已开始盯盘追踪止损。"
            
        reg_btn.click(mock_register_position, inputs=[track_sym, track_dir, track_entry, track_tp1, track_tp2, track_sl], outputs=[reg_out])

def _start_hf_auto_trader():
    """在后台线程中延迟导入重型模块，不阻塞 Gradio 启动

    HF Space 构建超时（30min）最主要原因是：
      - `import ccxt` / `import hf_auto_trader` 在 __main__ 中同步执行
      - `MicroFeeder("BTCUSDT")` 创建时可能触发网络连接
    解决方案：全部移到 demo.launch() *之后* 的线程中运行。
    """
    import sys, traceback as _tb

    # 清理 ccxt 旧模块（防止热加载冲突）
    for mod_name in list(sys.modules.keys()):
        if "ccxt" in mod_name:
            del sys.modules[mod_name]

    try:
        import ccxt as _ccxt
        _ccxt  # noqa: 只是验证导入成功
    except Exception as exc:
        print(f"[HF] ccxt 导入失败 (非致命): {exc}")

    try:
        import hf_auto_trader
        import asyncio
        from execution.micro.feeder import MicroFeeder

        _feeder = None
        try:
            _feeder = MicroFeeder("BTCUSDT")
            print("[Feeder] MicroFeeder 已创建")
        except Exception as feeder_err:
            print(f"[Feeder] MicroFeeder 创建失败: {feeder_err}")

        async def _run_async_main():
            try:
                if _feeder is not None:
                    print("[Feeder] gather 启动 feeder + main_loop")
                    await asyncio.gather(
                        _feeder.run(),
                        hf_auto_trader.main_loop(),
                    )
                else:
                    await hf_auto_trader.main_loop()
            except Exception as e:
                print(f"[hf_auto_trader] 主循环崩溃: {e}")
                _tb.print_exc()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run_async_main())
    except Exception as e:
        print(f"[HF] 自动扫描启动失败: {e}")
        _tb.print_exc()


if __name__ == "__main__":
    # 【根本修复】不在主线程创建/扰动 asyncio 事件循环。
    # Gradio 内部（uvicorn/Starlette）自建事件循环，主线程预创建循环会导致
    # 退出时 BaseEventLoop.__del__ 争夺已由 Gradio 关闭的 fd，产生：
    #   ValueError: Invalid file descriptor: -1
    # 解决方案：完全删除对主线程事件循环的任何操作。

    start_background_monitor()

    # 启动交易引擎线程（在 demo.launch() 之前，因为 launch() 阻塞不会返回）
    # 线程启动是微秒级的，不阻塞 HF Space 健康检查（端口 7860 由 gradio 监听）
    _hf_thread = threading.Thread(target=_start_hf_auto_trader, daemon=True)
    _hf_thread.start()
    print("[HF] 自动信号扫描线程已启动（不阻塞 Gradio 启动）")

    demo.launch(server_name="0.0.0.0", server_port=7860)
