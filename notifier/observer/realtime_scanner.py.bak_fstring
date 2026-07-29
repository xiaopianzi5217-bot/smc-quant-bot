# -*- coding: utf-8 -*-
from __future__ import annotations

import pandas as pd

from utils.time_utils import series_ms_to_bj
from notifier.manager import dispatch_observer_snapshot, dispatch_strategy_decision
from notifier.observer.funding import fetch_funding_rate_safe, normalize_swap_symbol
from notifier.observer.risk_plan import build_rr_plan
from notifier.observer.signal_collector import build_signal_snapshot
from config import STRATEGY_PARAMS, SYMBOL_STRATEGY
from indicators.basic import add_all_indicators
from strategy.smc import build_macro_context, build_exec_context
from strategy.scoring import adaptive_signal_score
from strategy.risk import calculate_dynamic_tp_sl
from utils.symbols import load_symbol_strategy
from decision.v9_decision_kernel import V9DecisionKernel
from ops.env_config import load_runtime_config


def _ohlcv_to_df(ohlcv):
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["datetime"] = series_ms_to_bj(df["timestamp"])
    return df


def fetch_ohlcv_df(exchange, symbol, timeframe, limit=300):
    sym = normalize_swap_symbol(symbol)
    return _ohlcv_to_df(exchange.fetch_ohlcv(sym, timeframe=timeframe, limit=limit))


def _enrich_volume_context(df_exec, curr, exec_ctx):
    avg_vol = df_exec["volume"].rolling(20).mean().iloc[-1]
    volume_ratio = float(curr["volume"] / avg_vol) if avg_vol == avg_vol and avg_vol > 0 else 0.0
    exec_ctx["avg_volume_20"] = float(avg_vol) if avg_vol == avg_vol else 0.0
    exec_ctx["volume_ratio"] = volume_ratio
    exec_ctx["volume_confirmed"] = bool(volume_ratio > 1.5)
    return volume_ratio


def _enrich_near_ob(price, atr, exec_ctx):
    if atr <= 0:
        return
    bull_ob = exec_ctx.get("bullish_ob")
    if bull_ob and isinstance(bull_ob, (list, tuple)) and len(bull_ob) >= 2:
        try:
            ob_max, ob_min = max(float(bull_ob[0]), float(bull_ob[1])), min(float(bull_ob[0]), float(bull_ob[1]))
            if (ob_min - atr) <= price <= (ob_max + atr):
                exec_ctx["near_bullish_ob"] = True
        except Exception:
            pass
    bear_ob = exec_ctx.get("bearish_ob")
    if bear_ob and isinstance(bear_ob, (list, tuple)) and len(bear_ob) >= 2:
        try:
            ob_max, ob_min = max(float(bear_ob[0]), float(bear_ob[1])), min(float(bear_ob[0]), float(bear_ob[1]))
            if (ob_min - atr) <= price <= (ob_max + atr):
                exec_ctx["near_bearish_ob"] = True
        except Exception:
            pass


def build_snapshot_and_decision(exchange, symbol, exec_timeframe="15m", macro_timeframe="1h", limit=300, cfg=None):
    cfg = cfg or load_runtime_config()
    symbol = normalize_swap_symbol(symbol)

    df_exec = fetch_ohlcv_df(exchange, symbol, exec_timeframe, limit=limit)
    df_macro = fetch_ohlcv_df(exchange, symbol, macro_timeframe, limit=limit)

    wvf = (cfg.get("strategy_params") or {}).get("wvf_std_mult", STRATEGY_PARAMS.get("wvf_std_mult", 2.0))
    df_exec = add_all_indicators(df_exec, wvf)
    df_macro = add_all_indicators(df_macro, wvf)

    macro_ctx = build_macro_context(df_macro)
    exec_ctx = build_exec_context(df_exec)
    curr = df_exec.iloc[-1]
    price = float(curr["close"])
    atr = float(curr.get("ATRr_14", curr.get("atr", 0)) or 0)
    exec_ctx["symbol"] = symbol
    _enrich_near_ob(price, atr, exec_ctx)
    volume_ratio = _enrich_volume_context(df_exec, curr, exec_ctx)
    is_vol = bool(volume_ratio > 1.5)

    l_score, l_thresh, l_reasons = adaptive_signal_score(exec_ctx, macro_ctx, "Long", is_vol)
    s_score, s_thresh, s_reasons = adaptive_signal_score(exec_ctx, macro_ctx, "Short", is_vol)

    sym_strategy = load_symbol_strategy(symbol, SYMBOL_STRATEGY)
    direction = "Long" if l_score >= s_score else "Short"
    min_rr = sym_strategy.get("min_rr", (cfg.get("risk") or {}).get("min_rr", 2.0))
    sl, tp1, tp2, tp3, rr = calculate_dynamic_tp_sl(
        direction=direction,
        curr=curr,
        df=df_exec,
        exec_ctx=exec_ctx,
        min_rr=min_rr,
        sym_strategy=sym_strategy,
    )
    rr_plan = build_rr_plan(direction, price, sl, tp1, tp2, tp3)

    funding_rate = fetch_funding_rate_safe(exchange, symbol)
    exec_ctx["funding_rate"] = funding_rate

    snapshot = build_signal_snapshot(
        symbol=symbol,
        df=df_exec,
        macro_ctx=macro_ctx,
        exec_ctx=exec_ctx,
        long_score=l_score,
        long_threshold=l_thresh,
        long_reasons=l_reasons,
        short_score=s_score,
        short_threshold=s_thresh,
        short_reasons=s_reasons,
        rr_plan=rr_plan,
        funding_rate=funding_rate,
    )

    decision = V9DecisionKernel(params=cfg).decide(
        curr=curr,
        macro_ctx=macro_ctx,
        exec_ctx=exec_ctx,
        long_score=l_score,
        long_threshold=l_thresh,
        long_reasons=l_reasons,
        short_score=s_score,
        short_threshold=s_thresh,
        short_reasons=s_reasons,
        min_rr=min_rr,
        rr=rr,
        direction=direction,
        entry=price if 'price' in locals() else float(curr["close"]),
        sl=sl,
        tp1=tp1,
        tp2=tp2,
        tp3=tp3,
        symbol=symbol,
        timeframe=exec_timeframe,
        cfg=cfg,
    )
    return snapshot, decision


def scan_symbol_observer_direct(exchange, symbol, exec_timeframe="15m", macro_timeframe="1h", send_all=False, limit=300, cfg=None):
    snapshot, decision = build_snapshot_and_decision(exchange, symbol, exec_timeframe, macro_timeframe, limit, cfg)
    result = dispatch_observer_snapshot(snapshot, send_all=send_all)
    return {
        "symbol": symbol,
        "layer": "observer",
        "channel": "structure_observation",
        "result": result,
        "snapshot": snapshot.to_dict(),
        "approved": decision.get("approved"),
        "decision_reason": decision.get("reason") or decision.get("reason_cn"),
    }


def scan_symbol_strategy_signal(exchange, symbol, exec_timeframe="15m", macro_timeframe="1h", limit=300, cfg=None):
    snapshot, decision = build_snapshot_and_decision(exchange, symbol, exec_timeframe, macro_timeframe, limit, cfg)
    result = dispatch_strategy_decision(snapshot, decision)
    return {
        "symbol": symbol,
        "layer": "strategy",
        "channel": "approved_opportunity",
        "result": result,
        "approved": decision.get("approved"),
        "decision": decision,
        "snapshot": snapshot.to_dict(),
    }


def scan_symbol_open_via_center(exchange, symbol, exec_timeframe="15m", macro_timeframe="1h", limit=300, cfg=None):
    return scan_symbol_strategy_signal(exchange, symbol, exec_timeframe, macro_timeframe, limit, cfg)


def scan_symbol_and_send_tg( exchange, symbol, exec_timeframe="15m", macro_timeframe="1h", send_all=True, limit=300, cfg=None, include_strategy=True, ):
    """Backward-compatible scanner. Older callers used this name expecting “send TG”. It now sends the Observer snapshot and also attempts the Strategy channel when the central kernel has approved a trade. dispatch_strategy_decision itself will safely skip rejected decisions and return the reason. """
    snapshot, decision = build_snapshot_and_decision(exchange, symbol, exec_timeframe, macro_timeframe, limit, cfg)
    observer_result = dispatch_observer_snapshot(snapshot, send_all=send_all)
    strategy_result = None
    if include_strategy:
        strategy_result = dispatch_strategy_decision(snapshot, decision)
    return {
        "symbol": symbol,
        "layer": "observer+strategy" if include_strategy else "observer",
        "observer_result": observer_result,
        "strategy_result": strategy_result,
        "approved": decision.get("approved"),
        "decision": decision,
        "snapshot": snapshot.to_dict(),
    }


def scan_symbols_and_send_tg(exchange, symbols, exec_timeframe="15m", macro_timeframe="1h", send_all=True, limit=300, cfg=None):
    rows = []
    for symbol in symbols:
        try:
            rows.append(scan_symbol_and_send_tg(exchange, symbol, exec_timeframe, macro_timeframe, send_all, limit, cfg=cfg))
        except Exception as e:
            rows.append({"symbol": symbol, "result": f"扫描失败：{e}"})
    return rows