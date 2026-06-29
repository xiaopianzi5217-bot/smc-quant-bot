# -*- coding: utf-8 -*-
"""v7 live runner with Strategy filters, audit logging and grade sizing."""
import json
import time
from pathlib import Path

import pandas as pd

from execution.exchange_adapter import ExchangeAdapter
from execution.live_engine import LiveExecutionEngine
from execution.lifecycle_manager import TradeLifecycleManager
from portfolio.portfolio_manager import PortfolioManager
from journal.trade_logger import TradeLogger
from analytics.filter_audit import FilterAuditLogger
from risk.position_sizing import apply_grade_position_sizing

try:
    from notifier.telegram import send_telegram
    from notifier.manager import dispatch_observer_snapshot, dispatch_strategy_decision
except Exception:
    send_telegram = None
    dispatch_observer_snapshot = None
    dispatch_strategy_decision = None


def notify(text):
    if send_telegram:
        try:
            return send_telegram(text)
        except Exception:
            return None
    print(text)
    return None


def load_config(path="config/v7_live_config.json"):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_runtime(cfg):
    dry_run = str(cfg.get("mode", "dry_run")).lower() != "live"
    exchange = ExchangeAdapter(
        exchange_name=cfg.get("exchange", "bitget"),
        dry_run=dry_run,
        leverage=cfg.get("risk", {}).get("leverage", 1),
    )
    portfolio = PortfolioManager(
        max_open_positions=cfg["risk"].get("max_open_positions", 3),
        max_same_direction_positions=cfg["risk"].get("max_same_direction_positions", 2),
    )
    logger = TradeLogger("data/v7_trade_journal.csv")
    executor = LiveExecutionEngine(cfg, exchange, portfolio, logger, notifier=notify)
    lifecycle = TradeLifecycleManager(cfg, exchange, portfolio, logger, notifier=notify)
    return exchange, portfolio, logger, executor, lifecycle


def fetch_ohlcv_df(exchange, symbol, timeframe, limit=300):
    """Fetch OHLCV, with deterministic dry-run fallback. In dry_run mode ccxt may be unavailable and ``exchange.exchange`` can be None. The old function crashed before V7 could be paper-tested. """
    if getattr(exchange, "exchange", None) is None:
        rows = []
        price = 100.0
        step_min = 60 if str(timeframe).lower().endswith("h") else 15
        end = pd.Timestamp.utcnow().floor(f"{step_min}min")
        start = end - pd.Timedelta(minutes=step_min * (int(limit) - 1))
        for i in range(int(limit)):
            dt = start + pd.Timedelta(minutes=step_min * i)
            drift = ((i % 31) - 15) * 0.02
            open_ = price
            close = max(1.0, price + drift)
            high = max(open_, close) + 0.6
            low = min(open_, close) - 0.6
            vol = 1000 + (i % 17) * 20
            rows.append([int(dt.timestamp() * 1000), open_, high, low, close, vol])
            price = close
    else:
        rows = exchange.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df


def _enrich_volume_context(df_exec, curr, exec_ctx):
    avg_vol = df_exec["volume"].rolling(20).mean().iloc[-1]
    volume_ratio = float(curr["volume"] / avg_vol) if avg_vol == avg_vol and avg_vol > 0 else 0.0
    exec_ctx["avg_volume_20"] = float(avg_vol) if avg_vol == avg_vol else 0.0
    exec_ctx["volume_ratio"] = volume_ratio
    exec_ctx["volume_confirmed"] = bool(volume_ratio > 1.5)
    return volume_ratio


def run_once(cfg, executor, lifecycle, exchange):
    from config import STRATEGY_PARAMS, SYMBOL_STRATEGY
    from indicators.basic import add_all_indicators
    from strategy.smc import build_macro_context, build_exec_context
    from strategy.scoring import adaptive_signal_score
    from strategy.risk import calculate_dynamic_tp_sl
    from notifier.observer.risk_plan import build_rr_plan
    from notifier.observer.signal_collector import build_signal_snapshot
    from decision.v6_decision_kernel import V6DecisionKernel
    from strategy.trade_filters import check_strategy_filters, mark_strategy_approval
    from utils.symbols import load_symbol_strategy

    kernel = V6DecisionKernel()
    results = []
    audit = FilterAuditLogger(cfg.get("audit", {}).get("path", "reports/filter_audit.csv")) if cfg.get("audit", {}).get("enabled", True) else None

    for symbol in cfg.get("symbols", []):
        try:
            df_exec = fetch_ohlcv_df(exchange, symbol, cfg.get("exec_timeframe", "15m"), limit=300)
            df_macro = fetch_ohlcv_df(exchange, symbol, cfg.get("macro_timeframe", "1h"), limit=300)
            df_exec = add_all_indicators(df_exec, STRATEGY_PARAMS["wvf_std_mult"])
            df_macro = add_all_indicators(df_macro, STRATEGY_PARAMS["wvf_std_mult"])

            curr = df_exec.iloc[-1]
            price = float(curr["close"])
            atr = float(curr.get("ATRr_14", curr.get("atr", 0)) or 0)
            lifecycle.manage_position(symbol, price, atr=atr)

            macro_ctx = build_macro_context(df_macro)
            exec_ctx = build_exec_context(df_exec)
            
            # --- 【新增】：OB 距离判断逻辑 ---
            # 判断逻辑：如果价格处于 OB 的最高点和最低点范围，外加 1个 ATR 的缓冲空间内
            if atr > 0:
                bull_ob = exec_ctx.get("bullish_ob")
                if bull_ob and isinstance(bull_ob, (list, tuple)) and len(bull_ob) >= 2:
                    try:
                        ob_max, ob_min = max(float(bull_ob[0]), float(bull_ob[1])), min(float(bull_ob[0]), float(bull_ob[1]))
                        if (ob_min - atr) <= price <= (ob_max + atr):
                            exec_ctx["near_bullish_ob"] = True
                    except Exception: pass
                
                bear_ob = exec_ctx.get("bearish_ob")
                if bear_ob and isinstance(bear_ob, (list, tuple)) and len(bear_ob) >= 2:
                    try:
                        ob_max, ob_min = max(float(bear_ob[0]), float(bear_ob[1])), min(float(bear_ob[0]), float(bear_ob[1]))
                        if (ob_min - atr) <= price <= (ob_max + atr):
                            exec_ctx["near_bearish_ob"] = True
                    except Exception: pass
            # ---------------------------------
            
            exec_ctx["symbol"] = symbol

            volume_ratio = _enrich_volume_context(df_exec, curr, exec_ctx)
            is_vol = bool(volume_ratio > 1.5)

            l_score, l_thresh, l_reasons = adaptive_signal_score(exec_ctx, macro_ctx, "Long", is_vol)
            s_score, s_thresh, s_reasons = adaptive_signal_score(exec_ctx, macro_ctx, "Short", is_vol)

            sym_strategy = load_symbol_strategy(symbol, SYMBOL_STRATEGY)
            direction = "Long" if l_score >= s_score else "Short"
            min_rr = sym_strategy.get("min_rr", cfg.get("risk", {}).get("min_rr", 2.0))
            sl, tp1, tp2, tp3, rr = calculate_dynamic_tp_sl(
                direction, curr, df_exec, exec_ctx, min_rr, sym_strategy
            )
            rr_plan = build_rr_plan(direction, price, sl, tp1, tp2, tp3)
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
                funding_rate=exec_ctx.get("funding_rate"),
            )

            # Observer 顺发只发结构变化；send_observer_all=true 时用于人工调试全量快照。
            tg_cfg = cfg.get("telegram", {})
            if dispatch_observer_snapshot and tg_cfg.get("send_observer", True):
                try:
                    dispatch_observer_snapshot(snapshot, send_all=bool(tg_cfg.get("send_observer_all", False)))
                except Exception as e:
                    print(f"[{symbol}] Observer顺发消息触发异常: {e}")

            decision = kernel.decide(
                curr=curr,
                macro_ctx=macro_ctx,
                exec_ctx=exec_ctx,
                long_score=l_score,
                long_threshold=l_thresh,
                long_reasons=l_reasons,
                short_score=s_score,
                short_threshold=s_thresh,
                short_reasons=s_reasons,
                symbol=symbol,
            )

            if decision.get("approved"):
                filter_result = check_strategy_filters(symbol, curr, macro_ctx, exec_ctx, decision, cfg)
                decision["strategy_filters"] = filter_result
                if not filter_result.get("allowed"):
                    decision["approved"] = False
                    decision["state"] = "STRATEGY_FILTER_BLOCKED"
                    decision["state_name"] = "STRATEGY_FILTER_BLOCKED"
                    decision["reason_cn"] = "Strategy过滤阻止开单: " + "; ".join(filter_result.get("reasons", []))
                    decision["reason"] = decision["reason_cn"]

            decision = apply_grade_position_sizing(decision, cfg)

            if decision.get("approved"):
                if dispatch_strategy_decision and cfg.get("telegram", {}).get("send_approved", True):
                    try:
                        dispatch_strategy_decision(snapshot, decision)
                    except Exception as e:
                        print(f"[{symbol}] Strategy开单提醒触发异常: {e}")
                result = executor.execute_decision(symbol, decision)
                mark_strategy_approval(symbol, curr, decision, cfg, exec_ctx=exec_ctx)
            else:
                result = {"ok": False, "reason": decision.get("reason_cn", decision.get("reason", "not approved"))}

            if audit:
                audit.record(symbol, curr, decision)
            results.append({"symbol": symbol, "result": result})
        except Exception as e:
            msg = f"【v7执行错误】\n币种：{symbol}\n错误：{e}"
            notify(msg)
            results.append({"symbol": symbol, "error": str(e)})

    return results


def main():
    cfg_path = Path("config/v7_live_config.json")
    if not cfg_path.exists():
        cfg_path = Path(__file__).resolve().parents[1] / "config" / "v7_live_config.json"
    cfg = load_config(str(cfg_path))
    exchange, portfolio, logger, executor, lifecycle = build_runtime(cfg)
    notify(
        "【v7交易引擎启动】\n"
        f"模式：{'模拟' if exchange.dry_run else '实盘'}\n"
        f"交易所：{cfg.get('exchange')}\n"
        f"币种：{', '.join(cfg.get('symbols', []))}"
    )
    while True:
        run_once(cfg, executor, lifecycle, exchange)
        time.sleep(int(cfg.get("scan_interval_seconds", 60)))


if __name__ == "__main__":
    main()