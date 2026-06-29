# -*- coding: utf-8 -*-
"""
V37 full integrated runner for SMC_Bot.

This file replaces the old multi-runner/multi-score decision path with a single
V37 institutional decision kernel while reusing the original project's proven
feature engineering, TP/SL planning, entry resolution, cost model, and reporting.

The old runner is preserved as backtest/runner_legacy_v31_pre_v37.py.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import os
from pathlib import Path
import numpy as np
import pandas as pd
from config import VERSION as V37_VERSION

from core.decision_kernel import InstitutionalDecisionKernel
from core.risk_engine import PreTradeRiskEngine

# =========================
# V42 EXPANSION MODE (AUTO)
# =========================
V42_EXPANSION = False  # CLEAN MODE
MIN_EV_STABLE = 0.12
MIN_EV_EXPANSION = 0.02
USE_V56_5 = True

try:
    from . import signals_engine
except Exception:
    import signals_engine

from backtest import runner_legacy_v31_pre_v37 as legacy

# ====================================================
# 🚨 引入 1H 宏观共振外挂
# ====================================================
try:
    from backtest.htf_confluence import compute_htf_macro_score
except ImportError:
    try:
        from htf_confluence import compute_htf_macro_score
    except ImportError:
        def compute_htf_macro_score(df):
            df['htf_macro_score'] = 0.0
            return df
# ====================================================

from strategy.intelligence_engine import grade_from_expected_value
try:
    from strategy.risk import calculate_dynamic_tp_sl, risk_is_acceptable
except Exception:
    from ..strategy.risk import calculate_dynamic_tp_sl, risk_is_acceptable

VERSION = "V55_ENGINEERING_REALISTIC_20260623"

# Keep public helpers compatible with old imports.
load_ohlcv_csv = legacy.load_ohlcv_csv
add_basic_indicators = legacy.add_basic_indicators
build_exec_context = legacy.build_exec_context
build_macro_context = legacy.build_macro_context
_safe_float = legacy._safe_float
_round_trip_cost_r = legacy._round_trip_cost_r
_resolve_entry = legacy._resolve_entry
_normalize_rr_plan = legacy._normalize_rr_plan
_build_trade_exit = legacy._build_trade_exit
legacy_summarize_backtest = legacy.summarize_backtest
stress_test = legacy.stress_test
try:
    deep_diagnostic_test = legacy.deep_diagnostic_test
except Exception:
    def deep_diagnostic_test(trades_df: pd.DataFrame) -> None:
        print("deep_diagnostic_test unavailable in legacy runner")


def _prepare_exec_frame(exec_csv: Any, warmup: int, max_rows: Optional[int]) -> pd.DataFrame:
    raw_exec = exec_csv.copy() if isinstance(exec_csv, pd.DataFrame) else load_ohlcv_csv(exec_csv)
    if max_rows and int(max_rows) > 0 and int(max_rows) < len(raw_exec):
        raw_exec = raw_exec.tail(int(max_rows) + int(warmup) + 260).reset_index(drop=True)
    df_exec = add_basic_indicators(raw_exec)
    # Preserve existing multi-engine signals as features, not as a separate trade path.
    try:
        setup = signals_engine.get_setup_signals(df_exec)
        for col in ["reversal_long", "reversal_short", "breakout_long", "breakout_short", "combo_long", "combo_short"]:
            df_exec[col] = setup.get(col, False).astype(bool)
        df_exec["breakout_vol_z"] = setup.get("breakout_vol_z", 0.0)
        df_exec["breakout_atr_ratio"] = setup.get("breakout_atr_ratio", 0.0)
        df_exec["has_any_setup"] = setup.get("has_any_setup", False).astype(bool)
        print(
            "🧪 V37 Feature Signals | "
            f"REV_LONG={int(df_exec['reversal_long'].sum())} "
            f"REV_SHORT={int(df_exec['reversal_short'].sum())} "
            f"BRK_LONG={int(df_exec['breakout_long'].sum())} "
            f"BRK_SHORT={int(df_exec['breakout_short'].sum())} "
            f"COMBO={int((df_exec['combo_long'] | df_exec['combo_short']).sum())}"
        )
    except Exception as exc:
        print(f"⚠️ V37 setup feature precompute failed, continuing without setup flags: {exc}")
        for col in ["reversal_long", "reversal_short", "breakout_long", "breakout_short", "combo_long", "combo_short", "has_any_setup"]:
            df_exec[col] = False
        df_exec["breakout_vol_z"] = 0.0
        df_exec["breakout_atr_ratio"] = 0.0
    return df_exec


def _prepare_macro_frame(macro_csv: Optional[Any], warmup: int, max_rows: Optional[int]) -> pd.DataFrame:
    if macro_csv is None:
        return pd.DataFrame()
    raw_macro = macro_csv.copy() if isinstance(macro_csv, pd.DataFrame) else load_ohlcv_csv(macro_csv)
    if max_rows and int(max_rows) > 0 and int(max_rows) < len(raw_macro):
        raw_macro = raw_macro.tail(max(300, int(max_rows) // 4 + int(warmup))).reset_index(drop=True)
    
    df = add_basic_indicators(raw_macro)
    
    # ====================================================
    # 🚨 调用外挂计算宏观共振得分
    # ====================================================
    df = compute_htf_macro_score(df)
    
    return df


def run_backtest(
    exec_csv: Any,
    macro_csv: Optional[Any] = None,
    symbol: str = "BTC/USDT",
    warmup: int = 120,
    max_rows: Optional[int] = None,
    min_rr: float = 1.35,
    base_max_hold_bars: int = 96,
    # mitigation_required is kept for API compatibility; V37 uses score/SMC floor instead of hard gate
    mitigation_required: bool = True,
    fee_bps: float = 6.0,
    slippage_bps: float = 10.0,
    # allow_trend_no_structure is for compatibility only
    allow_trend_no_structure: bool = False,
    save_reject_audit: bool = True,
    reject_audit_path: str = "reject_audit_v54.csv",
    missed_trade_audit_path: str = "missed_trade_audit_v54.csv",
    # V55 Engineering Profile: conservative candidate replay without MFE TP1 proxy.
    # Uses audited candidate pool when available; force_event_backtest=True
    # runs the slower raw event engine.
    target_profile: bool = True,
    target_profit_cap_r: float | None = None,
    **kwargs: Any,
) -> pd.DataFrame:
    print(f"\n🧬 Runner Version: {VERSION} | Single Decision Core + Centralized Risk")

    if USE_V56_5:
        print("\n🚀 使用 V56.5 Stable Enhanced 主决策管线...")
        try:
            # Use raw OHLCV directly instead of _prepare_exec_frame,
            # because V56.5 engine needs columns named 'atr' (lowercase) which
            # _prepare_exec_frame's old-style columns (ATRr_14) do not provide.
            from final_forge.v56_5_stable_engine import V56_5_Engine, load_ohlcv, add_v56_indicators

            raw = load_ohlcv(exec_csv)
            df_v56 = add_v56_indicators(raw)
            if max_rows and int(max_rows) > 0 and int(max_rows) < len(df_v56):
                df_v56 = df_v56.tail(int(max_rows) + 580).reset_index(drop=True)
            engine = V56_5_Engine()
            signals = engine.generate_candidates(df_v56)
            trades = engine.select_trades(signals)
            print("\n📊 V56.5 Stable Enhanced 统计概览:")
            print(engine.summarize(trades))
            return trades.reset_index(drop=True)
        except Exception as exc:
            print(f"⚠️ V56.5 主路径失败，回退到旧流程: {exc}")

    # ====================================================
    # V56.5 Stable Enhanced 主线（唯一回测路径）
    # ----------------------------------------------------
    # V38 旧回测系统已弃用。所有回测走 V56.5 引擎。
    # 旧代码保留在 runner_legacy_v31_pre_v37.py。
    # ====================================================
    print("\n🚀 正在启动 V56.5 Stable Enhanced 唯一回测路径...")
    try:
        from final_forge.v56_5_stable_engine import V565Config, run_v565_stable_backtest
        cfg = V565Config(
            min_score=float(kwargs.get("v56_5_min_score", 45.0)),
            tp1_r=float(kwargs.get("v56_5_tp1_r", 1.0)),
            tp2_r=float(kwargs.get("v56_5_tp2_r", 1.8)),
            tp3_r=float(kwargs.get("v56_5_tp3_r", 2.8)),
            max_hold_bars=int(kwargs.get("v56_5_max_hold_bars", 36)),
        )
        data_dir = Path(__file__).resolve().parents[1] / "data"
        trades_v565, report_v565 = run_v565_stable_backtest(exec_csv, data_dir, cfg)
        print("\n📊 V56.5 Stable Enhanced 统计概览:")
        print(report_v565.get("overall", {}))
        return trades_v565.reset_index(drop=True)
    except Exception as exc:
        print(f"❌ V56.5 引擎执行失败: {exc}")
        print("⚠️ V56.5 是唯一回测路径。请检查 final_forge/v56_5_stable_engine.py。")
        return pd.DataFrame()

    df_exec = _prepare_exec_frame(exec_csv, warmup, max_rows)
    df_macro = _prepare_macro_frame(macro_csv, warmup, max_rows)

    print("\n=======================================================")
    print(f"⚠️ 实际参与回测的有效 K 线数量：{len(df_exec)} 根")
    print("=======================================================\n")

    try:
        from strategy.smc_impulse_engine import reset_dominance_history
        reset_dominance_history()
    except Exception:
        pass

    decision_core = InstitutionalDecisionKernel.from_kwargs(
        kwargs,
        project_root=Path(__file__).resolve().parents[1],
    )
    pretrade_risk = PreTradeRiskEngine(
        base_cost_ceiling_r=float(kwargs.get("v38_base_cost_ceiling_r", 0.30)),
        hard_cost_ceiling_r=float(kwargs.get("v38_hard_cost_ceiling_r", 1.15)),
    )

    trades: List[Dict[str, Any]] = []
    reject_rows: List[Dict[str, Any]] = []
    missed_rows: List[Dict[str, Any]] = []
    reject_stats: Dict[str, int] = {}

    def _future_missed_snapshot(idx: int, row: pd.Series, direction: Any, horizons: tuple[int, ...] = (12, 24)) -> Dict[str, Any]:
        """Counterfactual audit: after a rejection, measure future move in ATR/R units."""
        direction_s = str(direction or "").title()
        entry = _safe_float(row.get("close"), 0.0)
        atr = max(_safe_float(row.get("ATRr_14"), entry * 0.006), 1e-12)
        snap: Dict[str, Any] = {}
        if entry <= 0 or direction_s not in {"Long", "Short"}:
            for h in horizons:
                snap[f"future_{h}k_return_r"] = None
                snap[f"future_{h}k_mfe_r"] = None
                snap[f"future_{h}k_mae_r"] = None
            return snap
        for h in horizons:
            end_i = min(len(df_exec) - 1, idx + int(h))
            future = df_exec.iloc[idx + 1 : end_i + 1]
            if future.empty:
                snap[f"future_{h}k_return_r"] = None
                snap[f"future_{h}k_mfe_r"] = None
                snap[f"future_{h}k_mae_r"] = None
                continue
            close_h = _safe_float(future.iloc[-1].get("close"), entry)
            high_h = _safe_float(future["high"].max(), entry) if "high" in future.columns else close_h
            low_h = _safe_float(future["low"].min(), entry) if "low" in future.columns else close_h
            if direction_s == "Long":
                ret = (close_h - entry) / atr
                mfe = (high_h - entry) / atr
                mae = (low_h - entry) / atr
            else:
                ret = (entry - close_h) / atr
                mfe = (entry - low_h) / atr
                mae = (entry - high_h) / atr
            snap[f"future_{h}k_return_r"] = round(float(ret), 4)
            snap[f"future_{h}k_mfe_r"] = round(float(mfe), 4)
            snap[f"future_{h}k_mae_r"] = round(float(mae), 4)
        return snap

    def add_reject(reason: str, row: pd.Series, decision: Dict[str, Any]) -> None:
        reject_stats[reason] = reject_stats.get(reason, 0) + 1
        if not save_reject_audit:
            return
        sig = decision.get("signal", {}) if isinstance(decision, dict) else {}
        meta = sig.get("entry_meta", {}) if isinstance(sig, dict) else {}
        row_idx = int(row.name) if row.name is not None else None
        reject_record = {
            "idx": row_idx,
            "datetime": row.get("datetime"),
            "reason": reason,
            "regime": decision.get("regime"),
            "vol_state": decision.get("vol_state"),
            "direction": sig.get("direction"),
            "score": sig.get("score"),
            "score_raw": sig.get("score_raw"),
            "expected_value": sig.get("expected_value"),
            "win_prob": sig.get("win_prob"),
            "estimated_rr": sig.get("estimated_rr"),
            "ev_grade": sig.get("ev_grade"),
            "size_multiplier": sig.get("size_multiplier"),
            "ev_reasons": sig.get("ev_reasons"),
            "smc": sig.get("smc"),
            "sqzmom": sig.get("sqzmom"),
            "breakout": sig.get("breakout"),
            "dominance": sig.get("dominance"),
            "smc_passed": sig.get("smc_passed"),
            "sqz_passed": sig.get("sqz_passed"),
            "breakout_passed": sig.get("breakout_passed"),
            "fallback_active": sig.get("fallback_active"),
            "mitigation_src": meta.get("mitigation_src"),
            "zone_near_atr": meta.get("zone_near_atr"),
            "vwap_dist_atr": meta.get("vwap_dist_atr"),
            "liquidity_sweep_confirmed": meta.get("liquidity_sweep_confirmed"),
            "liquidity_wrong_side": meta.get("liquidity_wrong_side"),
            "trend_direction": meta.get("trend_direction"),
            "close": row.get("close"),
            "adx": row.get("adx"),
            "volume_ratio": row.get("volume_ratio"),
            "body_pct": row.get("body_pct"),
            "breakdown": sig.get("breakdown"),
            "base_trigger_passed": sig.get("base_trigger_passed"),
            "base_trigger_strength": sig.get("base_trigger_strength"),
            "base_trigger": sig.get("base_trigger"),
            "scorecard_total": sig.get("scorecard_total"),
            "scorecard_summary": sig.get("scorecard_summary"),
            "scorecard_json": sig.get("scorecard_json"),
            "alpha_cluster": sig.get("alpha_cluster"),
            "alpha_cluster_coarse": sig.get("alpha_cluster_coarse"),
            "alpha_cluster_action": sig.get("alpha_cluster_action"),
            "alpha_cluster_reason": sig.get("alpha_cluster_reason"),
            "alpha_cluster_position_mult": sig.get("alpha_cluster_position_mult"),
            "alpha_cluster_stats_json": sig.get("alpha_cluster_stats_json"),
            "alpha_cluster_guard_json": sig.get("alpha_cluster_guard_json"),
        }
        reject_rows.append(reject_record)

        # missed_trade_audit.csv: 只记录有方向的被拒绝信号，便于统计哪些过滤器误杀赚钱机会。
        if row_idx is not None and sig.get("direction") in {"Long", "Short"}:
            missed_record = dict(reject_record)
            missed_record.update(_future_missed_snapshot(row_idx, row, sig.get("direction")))
            missed_rows.append(missed_record)

    # Fast HTF lookup: avoid copying/slicing the 1H dataframe on every 15M bar.
    macro_times = []
    macro_scores = []
    macro_ptr = -1
    if df_macro is not None and not df_macro.empty:
        macro_lookup_df = df_macro.copy()
        if "datetime" not in macro_lookup_df.columns:
            macro_lookup_df = macro_lookup_df.reset_index()
            for col in macro_lookup_df.columns:
                if str(col).lower() in ["index", "date", "time", "timestamp", "level_0"]:
                    macro_lookup_df.rename(columns={col: "datetime"}, inplace=True)
                    break
        if "datetime" in macro_lookup_df.columns:
            macro_lookup_df = macro_lookup_df.sort_values("datetime")
            macro_times = list(macro_lookup_df["datetime"])
            macro_scores = list(macro_lookup_df.get("htf_macro_score", pd.Series(0.0, index=macro_lookup_df.index)))

    i = max(260, int(warmup))
    while i < len(df_exec) - 2:
        row = df_exec.iloc[i]
        signal_ts = row.get("datetime", i)
        exec_ctx = build_exec_context(row)
        macro_ctx = build_macro_context(df_macro, signal_ts)
        
        # ====================================================
        # Fast macro confluence injection
        # ====================================================
        macro_ctx["htf_macro_score"] = 0.0
        if macro_times:
            while macro_ptr + 1 < len(macro_times) and macro_times[macro_ptr + 1] <= signal_ts:
                macro_ptr += 1
            if macro_ptr >= 0:
                macro_ctx["htf_macro_score"] = _safe_float(macro_scores[macro_ptr], 0.0)
        # ====================================================
            
        hist = df_exec.iloc[: i + 1]

        decision = decision_core.decide(row, exec_ctx, macro_ctx)

        if not decision.get("allow", False):
            reason = str(decision.get("reason", "REJECT_UNKNOWN"))
            add_reject(reason, row, decision)
            i += 1
            continue

        signal = decision["signal"]
        entry_meta = signal.get("entry_meta", {})
        direction = signal["direction"]
        regime = decision["regime"]

        # ====================================================
        # V55 ENGINEERING QUALITY GATE
        # V54 used EV>=0 and score>=50, which admitted too many marginal/noise
        # trades and then relied on MFE replay/capped micro-R to look stable.
        # V55 keeps the signal pool broad enough for daily cadence attempts,
        # but requires minimum edge density before execution.
        # ====================================================
        ev_grade_now = str(signal.get("ev_grade", grade_from_expected_value(signal.get("expected_value", 0.0))))
        if bool(target_profile):
            sig_score_now = _safe_float(signal.get("score", 0.0), 0.0)
            sig_ev_now = _safe_float(signal.get("expected_value", 0.0), 0.0)
            sig_wp_now = _safe_float(signal.get("win_prob", 0.0), 0.0)
            sig_rr_now = _safe_float(signal.get("estimated_rr", 0.0), 0.0)
            min_score_now = float(kwargs.get("v55_min_score", 60.0))
            min_ev_now = float(kwargs.get("v55_min_expected_value", 0.02))
            min_wp_now = float(kwargs.get("v55_min_win_prob", 0.45))
            min_rr_now = float(kwargs.get("v55_min_estimated_rr", 1.20))
            if not (str(regime) in ["TREND", "TRANSITION", "CHOP"]
                    and sig_score_now >= min_score_now
                    and sig_ev_now >= min_ev_now
                    and sig_wp_now >= min_wp_now
                    and sig_rr_now >= min_rr_now):
                decision = dict(decision)
                decision["reason"] = "REJECT_V55_ENGINEERING_QUALITY_GATE"
                add_reject(decision["reason"], row, decision)
                i += 1
                continue

        # Alpha Cluster Guard 已在 InstitutionalDecisionKernel 内统一执行。
        # runner 只消费最终 allow/size/signal，不再作为第二个决策大脑。
        size_mult = _safe_float(decision.get("size"), 0.0)

        entry_ok, entry_i, entry, entry_mode = _resolve_entry(
            df_exec, i, direction, row, entry_meta, exec_ctx,
            max_wait_bars=int(kwargs.get("v37_max_wait_bars", 10)),
            max_chase_atr=float(kwargs.get("v37_max_chase_atr", 0.90)),
        )
        if not entry_ok or entry <= 0:
            add_reject(entry_mode, row, decision)
            i += 1
            continue

        dyn_res = calculate_dynamic_tp_sl(direction, row, hist, exec_ctx, min_rr, {})
        sl = dyn_res[0] if isinstance(dyn_res, tuple) else 0.0
        tp1 = dyn_res[1] if isinstance(dyn_res, tuple) else 0.0
        tp2 = dyn_res[2] if isinstance(dyn_res, tuple) else 0.0
        tp3 = dyn_res[3] if isinstance(dyn_res, tuple) else 0.0

        sl, tp1, tp2, tp3, rr = _normalize_rr_plan(direction, entry, sl, tp1, tp2, tp3, row, hist, exec_ctx, min_rr)

        # ====================================================
        # V38 成本逻辑：固定 cost_r 防火墙改为自适应仓位压缩。
        # 旧逻辑 cost_r > 0.30 直接拒绝，导致大量样本无法进入审计池；
        # 新逻辑只在极端成本时硬拒绝，其余进入回测并记录
        # would_have_been_rejected_by_fixed_cost_firewall。
        # ====================================================
        pre_cost_r = _round_trip_cost_r(entry, sl, fee_bps, slippage_bps)
        atr_now = _safe_float(row.get("ATRr_14"), entry * 0.006)
        try:
            avg_atr = _safe_float(hist["ATRr_14"].tail(96).mean(), atr_now)
        except Exception:
            avg_atr = atr_now

        cost_decision = pretrade_risk.evaluate_transaction_cost(pre_cost_r, atr_now, avg_atr)
        adaptive_cost_ceiling = cost_decision.adaptive_cost_ceiling_r
        fixed_cost_firewall_reject = cost_decision.would_have_been_rejected_by_fixed_firewall
        cost_soft_multiplier = cost_decision.position_multiplier

        if not cost_decision.allow:
            decision = dict(decision)
            decision["reason"] = cost_decision.reason
            add_reject(cost_decision.reason, row, decision)
            i += 1
            continue

        if cost_decision.position_multiplier < 1.0:
            decision = dict(decision)
            signal = dict(signal)
            signal["cost_soft_multiplier"] = round(float(cost_soft_multiplier), 4)
            signal["cost_r_pretrade"] = round(float(pre_cost_r), 4)
            signal["adaptive_cost_ceiling_r"] = round(float(adaptive_cost_ceiling), 4)
            signal["would_have_been_rejected_by_fixed_cost_firewall"] = fixed_cost_firewall_reject
            signal["ev_reasons"] = str(signal.get("ev_reasons", "")) + f";{cost_decision.reason}"
            decision["signal"] = signal
            decision["size"] = round(_safe_float(decision.get("size"), 0.0) * cost_soft_multiplier, 6)

        if not risk_is_acceptable(entry, sl, _safe_float(row.get("ATRr_14"), entry * 0.006), max_risk_atr=3.5):
            add_reject("REJECT_RISK_NOT_ACCEPTABLE", row, decision)
            i += 1
            continue

        # ====================================================
        # 🚨 出场逻辑放宽：直接在主循环放大 TREND 的呼吸空间
        # ====================================================
        time_decay_bars = 12 if regime == "TREND" else 9 if regime == "TRANSITION" else 6
        # 原本 TREND 只有 1.35，极其容易在回踩中被洗。直接放大到 3.5。
        trail_mult = 3.5 if regime == "TREND" else 1.15 if regime == "TRANSITION" else 0.95

        exit_info = _build_trade_exit(
            df_exec,
            entry_i,
            direction,
            entry,
            sl,
            tp1,
            tp2,
            tp3,
            max_hold_bars=legacy._adaptive_max_hold_bars(exec_ctx, base_max_hold_bars),
            trail_atr_mult=trail_mult,
            time_drawdown_bars=time_decay_bars,
            tp1_close_pct=float(kwargs.get("v55_tp1_close_pct", 0.30)),
            tp2_close_pct=float(kwargs.get("v55_tp2_close_pct", 0.30)),
            regime=regime,
        )

        raw_pnl_r = _safe_float(exit_info.get("partial_pnl"), 0.0)
        cost_r = _round_trip_cost_r(entry, sl, fee_bps, slippage_bps)

        result_r = raw_pnl_r - cost_r
        position_size = max(0.0, min(1.0, _safe_float(decision.get("size"), 0.0)))
        pnl_r = result_r * position_size
        pnl_r_uncapped = pnl_r
        if bool(target_profile) and target_profit_cap_r is not None and float(target_profit_cap_r) > 0:
            # Optional legacy cap kept only for explicit experiments.  V55 default
            # is None because micro-capping/flooring hides the real trade R.
            pnl_r = min(float(pnl_r), float(target_profit_cap_r))

        exit_i = int(exit_info.get("exit_i", entry_i))
        decision_core.update_account(pnl_r)

        trades.append({
            "symbol": symbol,
            "setup_type": f"V37_{decision.get('book', 'PORTFOLIO')}",
            "direction": direction,
            "signal_at": signal_ts,
            "opened_at": df_exec.iloc[entry_i].get("datetime", entry_i),
            "closed_at": exit_info.get("exit_time"),
            "entry_mode": entry_mode,
            "entry": round(entry, 8),
            "sl": round(sl, 8),
            "tp1": round(tp1, 8),
            "tp2": round(tp2, 8),
            "tp3": round(tp3, 8),
            "exit_price": round(_safe_float(exit_info.get("exit")), 8),
            "exit_reason": exit_info.get("exit_reason"),
            "pnl_r": round(pnl_r, 4),
            "pnl_r_uncapped": round(pnl_r_uncapped, 4),
            "target_profile": bool(target_profile),
            "target_profit_cap_r": round(float(target_profit_cap_r), 4) if target_profit_cap_r is not None else None,
            "trade_r": round(result_r, 4),
            "raw_pnl_r": round(raw_pnl_r, 4),
            "cost_r": round(cost_r, 4),
            "position_size": round(position_size, 6),
            "would_have_been_rejected_by_fixed_cost_firewall": bool(pre_cost_r > float(kwargs.get("v38_base_cost_ceiling_r", 0.30))),
            "fixed_cost_firewall_threshold_r": round(float(kwargs.get("v38_base_cost_ceiling_r", 0.30)), 4),
            "adaptive_cost_ceiling_r": round(float(locals().get("adaptive_cost_ceiling", 0.0)), 4),
            "cost_soft_multiplier": round(float(locals().get("cost_soft_multiplier", 1.0)), 4),
            "rr": round(rr, 4),
            "bars_held": max(0, exit_i - entry_i),
            "score": round(_safe_float(signal.get("score"), 0.0), 2),
            "score_raw": round(_safe_float(signal.get("score_raw"), 0.0), 4),
            "legacy_score_grade": _grade_from_score_v37(_safe_float(signal.get("score"), 0.0)),
            "expected_value": round(_safe_float(signal.get("expected_value"), 0.0), 4),
            "win_prob": round(_safe_float(signal.get("win_prob"), 0.0), 4),
            "estimated_rr": round(_safe_float(signal.get("estimated_rr"), 0.0), 4),
            "grade": signal.get("ev_grade", grade_from_expected_value(signal.get("expected_value", 0.0))),
            "ev_reasons": signal.get("ev_reasons"),
            "size_mult": round(position_size, 6),
            "regime": regime,
            "vol_state": decision.get("vol_state"),
            "book": decision.get("book"),
            "volatility": exec_ctx.get("volatility"),
            "squeeze": exec_ctx.get("squeeze"),
            "trend_direction": exec_ctx.get("trend_direction"),
            "allow_reason": decision.get("reason"),
            "mitigation_src": entry_meta.get("mitigation_src"),
            "vwap_dist_atr": entry_meta.get("vwap_dist_atr"),
            "zone_near_atr": entry_meta.get("zone_near_atr"),
            "liquidity_sweep_confirmed": entry_meta.get("liquidity_sweep_confirmed"),
            "liquidity_wrong_side": entry_meta.get("liquidity_wrong_side"),
            "smc": signal.get("smc"),
            "sqzmom": signal.get("sqzmom"),
            "breakout": signal.get("breakout"),
            "raw_base": signal.get("raw_base"),
            "dominance": signal.get("dominance"),
            "smc_passed": signal.get("smc_passed"),
            "sqz_passed": signal.get("sqz_passed"),
            "breakout_passed": signal.get("breakout_passed"),
            "fallback_active": signal.get("fallback_active"),
            "mfe_r": exit_info.get("mfe_r"),
            "breakdown": signal.get("breakdown"),
            "base_trigger_passed": signal.get("base_trigger_passed"),
            "base_trigger_strength": signal.get("base_trigger_strength"),
            "base_trigger": signal.get("base_trigger"),
            "scorecard_total": signal.get("scorecard_total"),
            "scorecard_summary": signal.get("scorecard_summary"),
            "scorecard_json": signal.get("scorecard_json"),
            "expected_value_before_scorecard": signal.get("expected_value_before_scorecard"),
            "alpha_cluster": signal.get("alpha_cluster"),
            "alpha_cluster_coarse": signal.get("alpha_cluster_coarse"),
            "alpha_cluster_action": signal.get("alpha_cluster_action"),
            "alpha_cluster_reason": signal.get("alpha_cluster_reason"),
            "alpha_cluster_position_mult": signal.get("alpha_cluster_position_mult"),
            "alpha_cluster_stats_json": signal.get("alpha_cluster_stats_json"),
            "alpha_cluster_guard_json": signal.get("alpha_cluster_guard_json"),
            "account_equity_r": round(decision_core.account.equity_r, 4),
            "account_dd_r": round(decision_core.account.drawdown_r, 4),
            "loss_streak": decision_core.account.loss_streak,
        })
        i = max(i + 1, exit_i + 1)

    print("\n🔍 【V54 X光排查报告】 信号拒绝原因统计：")
    for reason, count in sorted(reject_stats.items(), key=lambda x: x[1], reverse=True):
        print(f" ❌ {reason}: {count} 次")
    print("=======================================================\n")

    if save_reject_audit and reject_rows:
        pd.DataFrame(reject_rows).to_csv(reject_audit_path, index=False)
        print(f"📄 V54 Reject Audit 已保存至: {reject_audit_path} | rows={len(reject_rows)}")
    if save_reject_audit and missed_rows:
        pd.DataFrame(missed_rows).to_csv(missed_trade_audit_path, index=False)
        print(f"📄 V54 Missed Trade Audit 已保存至: {missed_trade_audit_path} | rows={len(missed_rows)}")

    df_res = pd.DataFrame(trades)
    if len(df_res) > 0:
        summary_res = summarize_backtest(df_res)
        print("\n📊 V55 Engineering Realistic 统计概览:")
        print(summary_res["overall"])
        print("\n🔥 表现最强的前 10 笔交易明细:")
        best = df_res.sort_values(by="pnl_r", ascending=False).head(10)
        pd.set_option("display.max_columns", None)
        pd.set_option("display.width", 1000)
        print(best[["opened_at", "setup_type", "direction", "pnl_r", "expected_value", "win_prob", "estimated_rr", "score", "score_raw", "grade", "regime", "book", "entry_mode", "exit_reason", "allow_reason"]])
        print("\n⚠️ 开始执行压力测试...")
        stress_test(df_res)
    else:
        print("⚠️ V54 本次没有产生交易。请查看 reject_audit_v54.csv 的拒绝原因与 score_raw 分布。")

    return df_res


def size_multiplier(grade: str, regime: str) -> float:
    """Backward-compatible helper only.

    V37.6 no longer applies this directly to pnl_r. Position sizing must come
    from V37MasterEngine.decide()["size"] so risk accounting has one source of truth.
    """
    return 1.0


def _grade_from_score_v37(score: float) -> str:
    score = _safe_float(score, 0.0)
    if score >= 85:
        return "S"
    if score >= 70:
        return "A"
    if score >= 55:
        return "B"
    if score >= 40:
        return "C"
    return "D"


def summarize_backtest(trades: pd.DataFrame) -> Dict[str, Any]:
    """Dynamic summary compatible with EV grades as well as legacy S/A/B/C/D labels."""
    if trades is None or trades.empty:
        return {"overall": {"trades": 0, "win_rate": 0.0, "pf": 0.0, "pnl": 0.0, "avg_r": 0.0}, "by_grade": {}, "by_state": {}, "by_setup_type": {}, "cross_regime_grade": {}}

    df = trades.copy()
    if "grade" not in df.columns:
        if "expected_value" in df.columns:
            df["grade"] = df["expected_value"].apply(grade_from_expected_value)
        else:
            df["grade"] = df.get("score", pd.Series(0.0, index=df.index)).apply(_grade_from_score_v37)

    def calc_stats(sub: pd.DataFrame) -> Dict[str, Any]:
        if len(sub) == 0:
            return {"trades": 0, "win_rate": 0.0, "pf": 0.0, "pnl": 0.0, "avg_r": 0.0}
        pnl = pd.to_numeric(sub["pnl_r"], errors="coerce").fillna(0.0)
        wins = float(pnl[pnl > 0].sum())
        losses = abs(float(pnl[pnl < 0].sum()))
        pf = wins / losses if losses > 0 else (999.0 if wins > 0 else 0.0)
        return {
            "trades": int(len(sub)),
            "win_rate": round(float((pnl > 0).mean()), 4),
            "pf": round(float(pf), 4),
            "pnl": round(float(pnl.sum()), 4),
            "avg_r": round(float(pnl.mean()), 4),
        }

    overall = calc_stats(df)
    grade_order = ["S_EV_HOT", "A_EV", "B_EV", "C_EV", "D_NEG_EV", "S", "A", "B", "C", "D", "HOT"]
    present = [g for g in grade_order if g in set(df["grade"].dropna().astype(str))]
    extras = [g for g in sorted(set(df["grade"].dropna().astype(str))) if g not in present]
    by_grade = {g: calc_stats(df[df["grade"].astype(str) == g]) for g in present + extras}
    by_state = {str(r): calc_stats(sub) for r, sub in df.groupby("regime")} if "regime" in df.columns else {}
    by_setup_type = {str(st): calc_stats(sub) for st, sub in df.groupby("setup_type")} if "setup_type" in df.columns else {}

    cross: Dict[str, Any] = {}
    if "regime" in df.columns and "grade" in df.columns:
        for (regime, grade), sub in df.groupby(["regime", "grade"]):
            cross[f"{regime}_{grade}"] = calc_stats(sub)

    alpha_validation: Dict[str, Any] = {}
    try:
        from alpha_validator.avs_engine import AlphaValidationEngine

        avs_report = AlphaValidationEngine(df).run_full_assessment()
        alpha_validation = {
            "avs_score": avs_report.get("avs_score"),
            "overfit_score": avs_report.get("overfit_score"),
            "verdict": avs_report.get("verdict"),
            "component_scores": avs_report.get("component_scores", {}),
            "true_edge_regimes": avs_report.get("true_edge_regimes", [])[:5],
            "fake_clusters": avs_report.get("fake_clusters", [])[:10],
            "fragile_clusters": avs_report.get("fragile_clusters", [])[:10],
            "warnings": avs_report.get("warnings", []),
        }
    except Exception as exc:
        alpha_validation = {"error": f"{type(exc).__name__}: {exc}"}

    return {"overall": overall, "by_grade": by_grade, "by_state": by_state, "by_setup_type": by_setup_type, "cross_regime_grade": cross, "alpha_validation": alpha_validation}