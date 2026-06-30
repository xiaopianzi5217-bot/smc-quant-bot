# -*- coding: utf-8 -*-
"""
V56.5 回测 + Observer 顺发事件记录 + V37 Gate 效果对比

在原 V56.5 稳定回测的候选信号生成与执行之间，插入：
1. _detect_observer_events → 记录每个信号触发时的 Observer 事件列表
2. v37_final_gate → 记录 Gate 通过/拦截及仓位乘数

输出:
  data/backtest_v56_5_with_observer.csv  (trades + observer/v37 字段)
  reports/V56_5_OBSERVER_V37_REPORT.json (汇总报告 + 对比分析)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from final_forge.v56_5_stable_engine import (
    V565Config,
    generate_v56_candidates,
    enrich_v565_candidates,
    select_v565_portfolio,
    execute_v565,
    add_v56_indicators,
    load_ohlcv,
    summarize_v565,
)
from strategy.v565_quality_gate import v565_quality_gate
from decision.v37_gate import v37_final_gate
from indicators.basic import add_all_indicators
from strategy.smc import build_macro_context, build_exec_context
from config import STRATEGY_PARAMS


def _safe_bool(v) -> bool:
    if v is None:
        return False
    if isinstance(v, (bool, int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "y")
    return False


def _safe_float(v, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (ValueError, TypeError):
        return default


def _detect_observer_events_backtest(
    exec_ctx: Dict[str, Any],
    curr_close: float,
    long_score: float = 0.0,
    short_score: float = 0.0,
) -> List[Dict[str, Any]]:
    """
    回测版 Observer 事件检测（轻量，只依赖 exec_ctx，不依赖 curr DataFrame row）。
    与 hf_auto_trader._detect_observer_events 行为一致。
    """
    events: List[Dict[str, Any]] = []

    # SQZMOM 变白
    if _safe_bool(exec_ctx.get("sqzmom_white_reversal_long")):
        events.append({"type": "SQZMOM_WHITE", "dir": "Long", "desc": "SQZMOM 白线（多头动量衰竭）", "key": "sqz_white_long"})
    if _safe_bool(exec_ctx.get("sqzmom_white_reversal_short")):
        events.append({"type": "SQZMOM_WHITE", "dir": "Short", "desc": "SQZMOM 白线（空头动量衰竭）", "key": "sqz_white_short"})

    # 背离
    if _safe_bool(exec_ctx.get("has_bot_div")):
        events.append({"type": "DIVERGENCE_R", "dir": "Long", "desc": "底背离 R", "key": "div_bot"})
    if _safe_bool(exec_ctx.get("has_top_div")):
        events.append({"type": "DIVERGENCE_R", "dir": "Short", "desc": "顶背离 R", "key": "div_top"})

    # SQZMOM 力竭
    curr_color = str(exec_ctx.get("curr_color", ""))
    prev_color = str(exec_ctx.get("prev_color", ""))
    if curr_color and "白色" in curr_color and prev_color and ("红色" in prev_color or "蓝色" in prev_color or "绿色" in prev_color):
        events.append({"type": "SQZMOM_EF", "dir": "N/A", "desc": f"颜色 {prev_color}→{curr_color}，动量耗尽", "key": "sqz_ef"})

    # 接近 OB
    if _safe_bool(exec_ctx.get("near_bullish_ob")):
        events.append({"type": "NEAR_OB", "dir": "Long", "desc": "接近 Bullish OB", "key": "ob_bull"})
    if _safe_bool(exec_ctx.get("near_bearish_ob")):
        events.append({"type": "NEAR_OB", "dir": "Short", "desc": "接近 Bearish OB", "key": "ob_bear"})

    # 流动性
    is_bsl_swept = _safe_bool(exec_ctx.get("is_bsl_swept"))
    is_ssl_swept = _safe_bool(exec_ctx.get("is_ssl_swept"))
    bsl_level = _safe_float(exec_ctx.get("bsl_level", 0))
    ssl_level = _safe_float(exec_ctx.get("ssl_level", 0))
    atr_val = max(_safe_float(exec_ctx.get("atr", 1)), 1e-12)

    if is_bsl_swept:
        events.append({"type": "LIQUIDITY_SWEEP", "dir": "Short", "desc": f"BSL Sweep@{bsl_level:.1f}", "key": "bsl_sweep"})
    elif bsl_level > 0 and curr_close > 0:
        dist_atr = abs(curr_close - bsl_level) / atr_val
        if dist_atr <= 0.75:
            events.append({"type": "NEAR_LIQUIDITY", "dir": "Short", "desc": f"接近 BSL@{bsl_level:.1f}，距离{dist_atr:.2f}ATR", "key": "near_bsl"})
    if is_ssl_swept:
        events.append({"type": "LIQUIDITY_SWEEP", "dir": "Long", "desc": f"SSL Sweep@{ssl_level:.1f}", "key": "ssl_sweep"})
    elif ssl_level > 0 and curr_close > 0:
        dist_atr = abs(curr_close - ssl_level) / atr_val
        if dist_atr <= 0.75:
            events.append({"type": "NEAR_LIQUIDITY", "dir": "Long", "desc": f"接近 SSL@{ssl_level:.1f}，距离{dist_atr:.2f}ATR", "key": "near_ssl"})

    # CHOCH
    swing_high = _safe_float(exec_ctx.get("swing_high", 0))
    swing_low = _safe_float(exec_ctx.get("swing_low", 0))
    if swing_high > 0 and curr_close > swing_high:
        events.append({"type": "CHOCH", "dir": "Long", "desc": f"MSS 突破前高 {swing_high:.1f}", "key": "choch_long"})
    if swing_low > 0 and curr_close < swing_low:
        events.append({"type": "CHOCH", "dir": "Short", "desc": f"MSS 破前低 {swing_low:.1f}", "key": "choch_short"})

    # FVG
    if exec_ctx.get("bullish_fvg") is not None:
        events.append({"type": "FVG", "dir": "Long", "desc": "多头 FVG", "key": "fvg_long"})
    if exec_ctx.get("bearish_fvg") is not None:
        events.append({"type": "FVG", "dir": "Short", "desc": "空头 FVG", "key": "fvg_short"})

    # K线变色
    if _safe_bool(exec_ctx.get("color_changed")):
        events.append({"type": "CANDLE_COLOR", "dir": "Long" if ("bull" in str(curr_color).lower() or "蓝" in str(curr_color)) else "Short", "desc": f"K线变色 {curr_color}", "key": f"color_{curr_color}"})

    # SQZMOM 挤压释放
    squeeze = str(exec_ctx.get("squeeze", ""))
    if squeeze.lower() in ("release", "squeeze_release", "released"):
        events.append({"type": "SQUEEZE_RELEASE", "dir": "N/A", "desc": "SQZMOM 挤压释放", "key": "sqz_release"})

    return events


def run_backtest_with_observer(
    exec_csv: str,
    output_dir: Optional[str] = None,
    config: Optional[V565Config] = None,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    在 V56.5 标准回测基础上，每笔交易记录：
    - observer_events_count / observer_event_types
    - v37_gate_passed / v37_gate_reason / v37_size_mult
    """
    cfg = config or V565Config()
    t0 = time.time()

    # ---------- 读取数据 ----------
    df = add_v56_indicators(load_ohlcv(exec_csv))
    if not df["datetime"].is_monotonic_increasing:
        df = df.sort_values("datetime").reset_index(drop=True)

    # 还需要 exec_ctx 用于 Observer/V37 检测 → 在同一原始 OHLCV 上跑 SMC 管线
    df_smc = add_all_indicators(load_ohlcv(exec_csv).copy(), STRATEGY_PARAMS["wvf_std_mult"])

    # V56.5 管线
    df = add_v56_indicators(load_ohlcv(exec_csv))
    if not df["datetime"].is_monotonic_increasing:
        df = df.sort_values("datetime").reset_index(drop=True)
    if not df_smc["datetime"].is_monotonic_increasing:
        df_smc = df_smc.sort_values("datetime").reset_index(drop=True)

    # SMC 特征列（build_exec_context 输出）
    smc_ctx_keys = [
        "sqzmom_white_reversal_long", "sqzmom_white_reversal_short",
        "has_bot_div", "has_top_div",
        "curr_color", "prev_color", "color_changed",
        "near_bullish_ob", "near_bearish_ob",
        "bullish_ob", "bearish_ob",
        "is_bsl_swept", "is_ssl_swept",
        "bsl_level", "ssl_level",
        "swing_high", "swing_low",
        "bullish_fvg", "bearish_fvg",
        "squeeze",
        "atr", "adx", "rsi",
    ]

    print(f"[数据] df={len(df)} bars, df_smc={len(df_smc)} bars")

    # ---------- 候选信号 ----------
    broad = generate_v56_candidates(df, None)
    candidates = enrich_v565_candidates(broad, cfg)
    print(f"[候选] broad={len(broad)}, enriched={len(candidates)}")

    # ---------- 对每个候选信号注入 Observer + V37 Gate ----------
    enriched_rows: List[Dict[str, Any]] = []
    v37_passed_count = 0
    v37_blocked_count = 0
    observer_event_stats: Dict[str, int] = {}

    # 预计算 build_exec_context 需要的 OHLCV —— df_smc 已含全部指标
    # 对于每个候选 idx，截取到该位置的 df_smc 子集构建上下文
    for _, row in candidates.iterrows():
        rec = row.to_dict()
        sig_idx = int(row["idx"])
        sig_dir = str(row.get("direction", ""))
        sig_score = float(row.get("score", 0.0))
        sig_ev = float(row.get("model_ev", 0.0))

        # ---- 用 build_exec_context 构造 SMC 上下文 ----
        # 需要至少 100+ bars；df_smc 从原始数据计算，长度 ~35040
        if sig_idx < 100:
            continue  # 抛弃暖机阶段候选
        _df_segment = df_smc.iloc[:sig_idx + 1].copy()
        try:
            exec_ctx = build_exec_context(_df_segment)
        except Exception as e:
            print(f"[WARN] sig_idx={sig_idx} build_exec_context error: {e}")
            exec_ctx = {}

        sig_close = _safe_float(df_smc.iloc[sig_idx]["close"])

        # ---- Observer 事件检测 ----
        long_score = sig_score if sig_dir == "Long" else 0.0
        short_score = 0.0 if sig_dir == "Long" else sig_score
        obs_events = _detect_observer_events_backtest(exec_ctx, sig_close, long_score, short_score)
        rec["observer_events_count"] = len(obs_events)
        rec["observer_event_types"] = "|".join(sorted(set(e["type"] for e in obs_events)))
        rec["observer_event_keys"] = "|".join(sorted(set(e["key"] for e in obs_events)))

        for ev in obs_events:
            observer_event_stats[ev["type"]] = observer_event_stats.get(ev["type"], 0) + 1

        # ---- V37 Final Gate ----
        base_decision = {
            "score": sig_score,
            "expected_value": sig_ev,
            "direction": sig_dir,
        }
        v37_ctx = {
            "long_score": long_score,
            "short_score": short_score,
            "regime": str(rec.get("regime", "unknown")),
            "vol_state": exec_ctx.get("volatility", "unknown"),
            "setup_type": str(rec.get("setup_type", "")),
            "rr": float(rec.get("estimated_rr", 0)),
            "entry": float(rec.get("entry", sig_close)),
            "sl": float(rec.get("sl", 0)),
            "tp1": float(rec.get("tp1", 0)),
            "score": sig_score,
            "expected_value": sig_ev,
            "atr": _safe_float(exec_ctx.get("atr", 0), 0),
            "symbol": "BTCUSDT",
        }
        try:
            v37_passed, v37_reason, v37_size_mult = v37_final_gate(base_decision, v37_ctx)
        except Exception as e:
            v37_passed, v37_reason, v37_size_mult = False, f"V37_ERROR:{e}", 0.0

        rec["v37_gate_passed"] = v37_passed
        rec["v37_gate_reason"] = v37_reason
        rec["v37_size_mult"] = v37_size_mult

        if v37_passed:
            v37_passed_count += 1
        else:
            v37_blocked_count += 1

        enriched_rows.append(rec)

    candidates_annotated = pd.DataFrame(enriched_rows)
    print(f"[V37 Gate] 通过={v37_passed_count}, 拦截={v37_blocked_count} (总候选={len(enriched_rows)})")
    print(f"[Observer] 事件分布: {json.dumps(observer_event_stats, ensure_ascii=False)}")

    # ---------- 筛选（V56.5 标准筛选；V37 Gate 只记录不拦截回测执行） ----------
    selected = select_v565_portfolio(candidates_annotated, cfg)
    print(f"[筛选] selected={len(selected)}")

    # ---------- 执行 ----------
    trades = execute_v565(df, selected, cfg)
    print(f"[执行] trades={len(trades)}")

    # ---------- 将 Observer/V37 信息合并到 trades ----------
    if not trades.empty and not candidates_annotated.empty:
        merge_cols = ["idx", "observer_events_count", "observer_event_types", "observer_event_keys",
                      "v37_gate_passed", "v37_gate_reason", "v37_size_mult"]
        trades = trades.merge(
            candidates_annotated[merge_cols],
            on="idx", how="left", suffixes=("", "_cand")
        )

    # ---------- 汇总 ----------
    summary = summarize_v565(trades)

    # Observer 事件在交易中的分布
    obs_in_trades = {
        "total_trades_with_observer": int((trades["observer_events_count"] > 0).sum()) if not trades.empty and "observer_events_count" in trades.columns else 0,
        "avg_observer_events_per_trade": float(trades["observer_events_count"].mean()) if not trades.empty and "observer_events_count" in trades.columns else 0.0,
    }

    report: Dict[str, Any] = {
        "version": "V56_5_OBSERVER_V37_BACKTEST",
        "config": {k: v for k, v in cfg.__dict__.items() if not k.startswith("_")},
        "data": {
            "bars": int(len(df)),
            "start": str(df["datetime"].min()),
            "end": str(df["datetime"].max()),
        },
        "v37_gate_stats": {
            "total_candidates": int(len(enriched_rows)),
            "v37_passed": int(v37_passed_count),
            "v37_blocked": int(v37_blocked_count),
            "v37_pass_rate": round(v37_passed_count / max(1, len(enriched_rows)), 4),
            "selected_after_v56_5_selection": int(len(selected)),
        },
        "observer_event_stats": {
            "total_events_in_candidates": int(sum(observer_event_stats.values())),
            "event_distribution": {k: int(v) for k, v in sorted(observer_event_stats.items(), key=lambda x: -x[1])},
        },
        "observer_in_trades": obs_in_trades,
        "overall": summary,
        "target_gap": {},
    }

    if output_dir is not None:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        candidates_annotated.to_csv(out / "v56_5_candidates_with_observer.csv", index=False)
        selected.to_csv(out / "v56_5_selected_signals.csv", index=False)
        trades.to_csv(out / "backtest_v56_5_with_observer.csv", index=False)
        (out / "V56_5_OBSERVER_V37_REPORT.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )

    elapsed = time.time() - t0
    print(f"[完成] {elapsed:.1f}s")
    return trades, report


def main() -> int:
    ap = argparse.ArgumentParser(description="V56.5 Backtest with Observer events + V37 Gate annotation")
    ap.add_argument("--exec-csv", default=str(ROOT / "data" / "BTCUSDT_15M_365d.csv"))
    ap.add_argument("--out-dir", default=str(ROOT / "data"))
    args = ap.parse_args()

    cfg = V565Config(min_score=60.0)  # 稍微放低门槛让更多候选通过，便于观察 Observer 和 V37 效果
    trades, report = run_backtest_with_observer(args.exec_csv, args.out_dir, cfg)

    print("\n========== 回测汇总 ==========")
    print(json.dumps(report["overall"], ensure_ascii=False, indent=2))
    print("\n========== V37 Gate 统计 ==========")
    print(json.dumps(report["v37_gate_stats"], ensure_ascii=False, indent=2))
    print("\n========== Observer 事件分布 ==========")
    print(json.dumps(report["observer_event_stats"], ensure_ascii=False, indent=2))
    print(f"\n输出文件: {Path(args.out_dir) / 'backtest_v56_5_with_observer.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
