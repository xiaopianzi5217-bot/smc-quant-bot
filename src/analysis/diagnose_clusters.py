# -*- coding: utf-8 -*-
"""Cluster diagnostics, temporal decay, and sensitivity analysis."""
from __future__ import annotations

import sys
sys.path.insert(0, '.')
import json
from pathlib import Path
import pandas as pd
import numpy as np

from final_forge.v56_5_stable_engine import V56_5_Engine, V565Config, load_ohlcv, add_v56_indicators
from analysis.ev_cluster_diagnostic import build_cluster_key

# ============================================================
# 1. Full-run trades
# ============================================================
cfg = V565Config()
engine = V56_5_Engine(cfg)
raw = load_ohlcv('data/BTCUSDT_15M_365d.csv')
df_v56 = add_v56_indicators(raw)
print(f"Total data rows: {len(df_v56)}")
trades400 = engine.select_trades(engine.generate_candidates(df_v56))
print(f"Full-run trades: {len(trades400)}")

# Build cluster key
df = build_cluster_key(trades400)

# ============================================================
# 2. Cluster diagnostic
# ============================================================
print("\n" + "=" * 60)
print("1) CLUSTER FIELD DISTRIBUTION")
print("=" * 60)
print("\n--- expected_value stats ---")
print(df['expected_value'].describe())
print("\n--- estimated_rr stats ---")
print(df['estimated_rr'].describe())
print("\n--- win_prob stats ---")
print(df['win_prob'].describe())
print("\n--- ev_bucket ---")
print(df['ev_bucket'].value_counts().sort_index())
print("\n--- rr_bucket ---")
print(df['rr_bucket'].value_counts().sort_index())
print("\n--- regime ---")
print(df['regime'].value_counts())
print("\n--- setup_type ---")
print(df['setup_type'].value_counts())
print("\n--- CLUSTER PERFORMANCE ---")
for c in sorted(df['cluster'].unique()):
    sub = df[df['cluster'] == c]
    wins = (sub['pnl_r'] > 0).sum()
    total = len(sub)
    pf = sub['pnl_r'][sub['pnl_r'] > 0].sum() / abs(sub['pnl_r'][sub['pnl_r'] < 0].sum()) if sub['pnl_r'][sub['pnl_r'] < 0].sum() != 0 else float('inf')
    print(f"  {c:<50s} | n={total:3d} wr={wins/total:.2%} pf={pf:.3f} avg_ev={sub['expected_value'].mean():.4f} avg_pnl={sub['pnl_r'].mean():+.4f}")

# ============================================================
# 3. Temporal decay
# ============================================================
print("\n" + "=" * 60)
print("2) TEMPORAL DECAY (4 slices)")
print("=" * 60)
trades_sorted = trades400.copy()
trades_sorted['opened_at'] = pd.to_datetime(trades_sorted['opened_at'])
trades_sorted = trades_sorted.sort_values('opened_at').reset_index(drop=True)
n = len(trades_sorted)
slices = [trades_sorted.iloc[i*n//4:(i+1)*n//4] for i in range(4)]
for i, sl in enumerate(slices):
    pnl = sl['pnl_r']
    wins = (pnl > 0).sum()
    losses = (pnl < 0).sum()
    pf = pnl[pnl > 0].sum() / abs(pnl[pnl < 0].sum()) if pnl[pnl < 0].sum() != 0 else float('inf')
    span = f"{sl['opened_at'].min().strftime('%m/%d')} - {sl['opened_at'].max().strftime('%m/%d')}"
    print(f"  Slice {i+1}: [{span}] n={len(sl):3d} wr={wins/len(sl):.2%} pf={pf:.3f} avg={pnl.mean():+.4f} sum={pnl.sum():+.4f}")
    if 'regime' in sl.columns:
        print(f"           regime: {sl['regime'].value_counts().to_dict()}")

# ============================================================
# 4. Temporal by hour-of-day
# ============================================================
print("\n--- By hour of day ---")
trades400['hour'] = pd.to_datetime(trades400['opened_at']).dt.hour
for h, grp in trades400.groupby('hour'):
    pnl = grp['pnl_r']
    pf = pnl[pnl > 0].sum() / abs(pnl[pnl < 0].sum()) if pnl[pnl < 0].sum() != 0 else float('inf')
    print(f"  hour={h:2d}: n={len(grp):3d} wr={(pnl>0).mean():.2%} pf={pf:.3f} avg={pnl.mean():+.4f}")

# ============================================================
# 5. Sensitivity: tighten cluster kill thresholds
# ============================================================
print("\n" + "=" * 60)
print("3) CLUSTER KILL SENSITIVITY")
print("=" * 60)

from analysis.ev_cluster_kill_system import kill_bad_clusters, report_cluster_kill

scenarios = [
    ("default (pf<0.8 or wr<0.35)", 0.8, 0.35),
    ("tight (pf<1.0 or wr<0.45)",   1.0, 0.45),
    ("aggressive (pf<1.2 or wr<0.50)", 1.2, 0.50),
    ("extreme (pf<1.5 or wr<0.55)",  1.5, 0.55),
]

for label, pf_thresh, wr_thresh in scenarios:
    killed = kill_bad_clusters(trades400, min_pf=pf_thresh, min_wr=wr_thresh)
    n_killed = killed['cluster'].nunique() if not killed.empty else 0
    survived = trades400[~trades400.index.isin(killed.index)] if not killed.empty else trades400
    pnl_s = survived['pnl_r']
    pf_s = pnl_s[pnl_s > 0].sum() / abs(pnl_s[pnl_s < 0].sum()) if pnl_s[pnl_s < 0].sum() != 0 else float('inf')
    print(f"\n  {label}:")
    print(f"    Killed clusters: {n_killed}, trades removed: {len(killed)}")
    print(f"    Surviving trades: {len(survived)}, wr={(pnl_s>0).mean():.2%}, pf={pf_s:.3f}, avg_pnl={pnl_s.mean():+.4f}, total={pnl_s.sum():+.4f}")
    eq = pnl_s.cumsum()
    max_dd = (eq - eq.cummax()).min()
    print(f"    Max DD: {max_dd:+.4f}")

print("\nDone.")
