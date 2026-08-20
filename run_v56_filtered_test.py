# -*- coding: utf-8 -*-
"""Run V56 backtest with trend filter on TREND_PULLBACK and print summary."""
import sys
sys.path.insert(0, ".")
import pandas as pd
from final_forge.v56_production_engine import run_v56_production_backtest

trades, report = run_v56_production_backtest(
    "data/BTCUSDT_15M_365d.csv",
    output_dir="data/v56_filtered",
)
s = report["overall"]
print(f"=== FILTERED TREND_PULLBACK (trend_strength < 0.35) ===")
print(f"TRADES={s['trades']}  WR={s['win_rate']:.4f}  PF={s['pf']:.4f}  PnL={s['pnl']:.4f}  AvgR={s['avg_r']:.5f}  MaxDD={s['max_dd_r']:.4f}")
print(f"Candidates={report['candidate_summary']['candidates']}  Selected={report['candidate_summary']['selected_before_overlap_guard']}")
print()

# Break down by setup_type
if trades is not None and not trades.empty:
    for st, grp in trades.groupby("setup_type"):
        pnl = pd.to_numeric(grp["pnl_r"], errors="coerce").fillna(0.0)
        wins = float(pnl[pnl > 0].sum())
        losses = abs(float(pnl[pnl < 0].sum()))
        pf = wins / losses if losses > 0 else (999.0 if wins > 0 else 0.0)
        print(f"  {st}: n={len(grp)}  WR={(pnl > 0).mean():.4f}  PF={pf:.4f}  SumR={pnl.sum():.4f}  AvgR={pnl.mean():.5f}")

print()
print("=" * 70)
print("TREND_PULLBACK 细分 trend_strength 分析:")
print("=" * 70)

# Analyze TREND_PULLBACK by trend_strength bucket
tp = trades[trades["setup_type"] == "TREND_PULLBACK"].copy()
if not tp.empty:
    # Median for classification
    med_ts = tp["trend_strength"].median()
    print(f"  TREND_PULLBACK median trend_strength = {med_ts:.4f}")
    
    # Buckets
    buckets = [
        ("(-inf, 0.15)", lambda x: x < 0.15),
        ("[0.15, 0.25)", lambda x: (x >= 0.15) & (x < 0.25)),
        ("[0.25, 0.35]", lambda x: (x >= 0.25) & (x <= 0.35)),
    ]
    for name, cond in buckets:
        sub = tp[pd.to_numeric(tp["trend_strength"], errors="coerce").apply(cond)]
        if len(sub) > 0:
            pnl = pd.to_numeric(sub["pnl_r"], errors="coerce").fillna(0.0)
            wins = float(pnl[pnl > 0].sum())
            losses = abs(float(pnl[pnl < 0].sum()))
            pf = wins / losses if losses > 0 else 0.0
            # Long vs Short
            long = sub[sub["direction"] == "Long"]
            short = sub[sub["direction"] == "Short"]
            long_pnl = pd.to_numeric(long["pnl_r"], errors="coerce").fillna(0.0) if len(long) > 0 else pd.Series(dtype=float)
            short_pnl = pd.to_numeric(short["pnl_r"], errors="coerce").fillna(0.0) if len(short) > 0 else pd.Series(dtype=float)
            print(f"  [{name}]: n={len(sub)}  WR={(pnl > 0).mean():.4f}  PF={pf:.4f}  SumR={pnl.sum():.4f}")
            if len(long) > 0:
                print(f"    Long : n={len(long)}  WR={(long_pnl > 0).mean():.4f}  SumR={long_pnl.sum():.4f}  AvgR={long_pnl.mean():.5f}")
            if len(short) > 0:
                print(f"    Short: n={len(short)}  WR={(short_pnl > 0).mean():.4f}  SumR={short_pnl.sum():.4f}  AvgR={short_pnl.mean():.5f}")
