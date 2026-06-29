# V56.5 Stable Enhanced Upgrade Summary

## 1. Two-zip comparison

- `6-2389单51胜率.zip`: older baseline package. It has 411 archived entries and does not contain the V56 production architecture files.
- `6-23-2.zip`: more advanced package. It has 423 archived entries and already contains `final_forge/v56_production_engine.py`, `scripts/run_v56_production_backtest.py`, `scripts/v56_deep_detection.py`, V56 reports, and V56 output CSV files.

Conclusion: `6-23-2.zip` is the better base. V56.5 was built on top of this package.

## 2. Main V56 issues found

1. V56 generated enough trades, but the default 365-day result was negative:
   - Trades: 382
   - Win rate: 51.83%
   - PF: 0.8049
   - PnL: -38.4138R
2. The V56 architecture was safer than the old small-pool replay systems, but it still mixed multiple signal families too evenly.
3. Cluster protection existed conceptually but was not fully expressed as a position-size/risk-scaling layer in the V56 production path.
4. Overlap logic compared the signal bar with the previous exit bar; V56.5 compares the actual next-bar entry bar with the previous exit bar.
5. V56 used TP1=0.85R by default. V56.5 raises TP1 to 1.0R to avoid micro TP1 behavior.

## 3. V56.5 modifications added

New files:

- `final_forge/v56_5_stable_engine.py`
- `scripts/run_v56_5_stable_backtest.py`
- `scripts/v56_5_deep_detection.py`
- `reports/V56_5_STABLE_REPORT.md/json`
- `reports/V56_5_DEEP_DETECTION_REPORT.md/json`
- `data/v56_5_candidates.csv`
- `data/v56_5_selected_signals.csv`
- `data/backtest_v56_5_stable.csv`

Updated file:

- `backtest/runner.py`: added an opt-in `v56_5_stable=True` path.

## 4. V56.5 core logic

- Tiered signal layer:
  - Tier 1: `LIQUIDITY_SWEEP`, `FVG_TOUCH`
  - Tier 2: `WEAK_BOS`, `TREND_PULLBACK`
  - Tier 3: `ORDERBLOCK_REACTION`
- Default production selection focuses on Tier-1 liquidity sweep signals during empirically stronger sessions.
- EV is now a continuous probability score using only observable same-bar features.
- Dynamic Top-N uses one best eligible signal per day plus controlled second signals.
- Cluster is converted into risk scaling and skip only at extreme local similarity.
- Execution remains live-safe:
  - next-bar open entry;
  - real high/low TP/SL touch;
  - conservative intrabar handling;
  - no MFE replay;
  - no future outcome labels;
  - no micro-profit cap/loss floor.

## 5. V56.5 365-day backtest result

```json
{
  "trades": 348,
  "win_rate": 0.5776,
  "pf": 1.1789,
  "pnl": 27.9725,
  "avg_r": 0.08038,
  "max_dd_r": -21.8728,
  "max_win_r": 1.75,
  "max_loss_r": -1.07,
  "tp1_touch_rate": 0.5718,
  "tp2_touch_rate": 0.3276,
  "tp3_touch_rate": 0.1782,
  "micro_profit_frequency_lt_0p2r": 0.0,
  "micro_loss_frequency_gt_minus_0p2r": 0.0
}
```

## 6. Stress test

```json
{
  "base": 27.9725,
  "slippage_plus_1bp_proxy": 24.4925,
  "tp_minus_5pct": 18.7559,
  "delay_plus_1bar_proxy": 26.9725,
  "combined_stress": 14.3764
}
```

## 7. Important limitation

V56.5 meets the requested 300–500 annual trades, win-rate improvement, TP1-not-micro rule, and total return >20R in this 365-day engineering backtest. It does not honestly reach PF 1.6. The system reports that as a target gap rather than forcing it through future leakage, MFE replay, tiny TP1, or loss-floor tricks.

## 8. Verification run

- `python3 scripts/v56_5_deep_detection.py`
  - Compile: PASS
  - Checked Python files: 223
  - Errors: 0
  - Deep detection status: PASS
- `python3 _test_final.py`
  - Result: ALL V55 SMOKE CHECKS OK
- `python3 scripts/smoke_check.py`
  - Result: SMOKE_CHECK_OK
