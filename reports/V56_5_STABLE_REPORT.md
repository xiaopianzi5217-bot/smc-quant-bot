# V56.5 Stable Enhanced Report

## Overall

```json
{
  "trades": 278,
  "win_rate": 0.6475,
  "pf": 1.5634,
  "pnl": 47.6761,
  "avg_r": 0.1715,
  "max_dd_r": -5.8584,
  "max_win_r": 1.75,
  "max_loss_r": -1.07,
  "tp1_touch_rate": 0.6403,
  "tp2_touch_rate": 0.3813,
  "tp3_touch_rate": 0.1942,
  "micro_profit_frequency_lt_0p2r": 0.1151,
  "micro_loss_frequency_gt_minus_0p2r": 0.0
}
```

## Candidate Pool

```json
{
  "broad_candidates": 9256,
  "enriched_candidates": 9256,
  "selected_before_overlap_guard": 280,
  "signal_density": 0.26416
}
```

## Target Gap

```json
{
  "trade_count_ok": false,
  "win_rate_ok": false,
  "stable_pf_ok": true,
  "requested_pf_1p6_ok": false,
  "total_r_ok": true,
  "note": "V56.5 reports both achieved stable target and the original 1.6 PF request. It does not force PF by future leakage or micro-profit tricks."
}
```

## EV Calibration

```json
{
  "buckets": [
    {
      "bucket": "(0.392, 0.5]",
      "trades": 28,
      "model_ev_mean": 0.46787,
      "win_rate": 0.6071,
      "avg_r": 0.14532
    },
    {
      "bucket": "(0.5, 0.537]",
      "trades": 28,
      "model_ev_mean": 0.52063,
      "win_rate": 0.6786,
      "avg_r": 0.09787
    },
    {
      "bucket": "(0.537, 0.568]",
      "trades": 28,
      "model_ev_mean": 0.55377,
      "win_rate": 0.7143,
      "avg_r": 0.26754
    },
    {
      "bucket": "(0.568, 0.596]",
      "trades": 27,
      "model_ev_mean": 0.58085,
      "win_rate": 0.5926,
      "avg_r": 0.0298
    },
    {
      "bucket": "(0.596, 0.623]",
      "trades": 28,
      "model_ev_mean": 0.6056,
      "win_rate": 0.7143,
      "avg_r": 0.37616
    },
    {
      "bucket": "(0.623, 0.653]",
      "trades": 28,
      "model_ev_mean": 0.64059,
      "win_rate": 0.6786,
      "avg_r": 0.11691
    },
    {
      "bucket": "(0.653, 0.681]",
      "trades": 27,
      "model_ev_mean": 0.66647,
      "win_rate": 0.5926,
      "avg_r": 0.12925
    },
    {
      "bucket": "(0.681, 0.712]",
      "trades": 28,
      "model_ev_mean": 0.696,
      "win_rate": 0.5357,
      "avg_r": 0.05406
    },
    {
      "bucket": "(0.712, 0.742]",
      "trades": 28,
      "model_ev_mean": 0.72479,
      "win_rate": 0.6786,
      "avg_r": 0.30897
    },
    {
      "bucket": "(0.742, 0.83]",
      "trades": 28,
      "model_ev_mean": 0.77286,
      "win_rate": 0.6786,
      "avg_r": 0.1825
    }
  ],
  "monotonic_winrate_steps": 5,
  "max_possible_steps": 9,
  "status": "WARN"
}
```

## Stability Curve

```json
{
  "status": "PASS",
  "scenarios": [
    {
      "scenario": "base",
      "trades": 278,
      "win_rate": 0.6475,
      "pf": 1.5634,
      "pnl": 47.6761,
      "avg_r": 0.1715,
      "max_dd_r": -5.8584,
      "max_win_r": 1.75,
      "max_loss_r": -1.07,
      "tp1_touch_rate": 0.6403,
      "tp2_touch_rate": 0.3813,
      "tp3_touch_rate": 0.1942,
      "micro_profit_frequency_lt_0p2r": 0.1151,
      "micro_loss_frequency_gt_minus_0p2r": 0.0
    },
    {
      "scenario": "slippage_plus_1bp_proxy",
      "trades": 278,
      "win_rate": 0.6475,
      "pf": 1.5244,
      "pnl": 44.8961,
      "avg_r": 0.1615,
      "max_dd_r": -6.0284,
      "max_win_r": 1.74,
      "max_loss_r": -1.08,
      "tp1_touch_rate": 0.6403,
      "tp2_touch_rate": 0.3813,
      "tp3_touch_rate": 0.1942,
      "micro_profit_frequency_lt_0p2r": 0.1187,
      "micro_loss_frequency_gt_minus_0p2r": 0.0
    },
    {
      "scenario": "tp_minus_5pct",
      "trades": 278,
      "win_rate": 0.6475,
      "pf": 1.4852,
      "pnl": 41.061,
      "avg_r": 0.1477,
      "max_dd_r": -6.032,
      "max_win_r": 1.6625,
      "max_loss_r": -1.07,
      "tp1_touch_rate": 0.6403,
      "tp2_touch_rate": 0.3813,
      "tp3_touch_rate": 0.1942,
      "micro_profit_frequency_lt_0p2r": 0.1187,
      "micro_loss_frequency_gt_minus_0p2r": 0.0
    },
    {
      "scenario": "delay_plus_1bar_proxy",
      "trades": 278,
      "win_rate": 0.6475,
      "pf": 1.5522,
      "pnl": 46.8761,
      "avg_r": 0.16862,
      "max_dd_r": -5.9184,
      "max_win_r": 1.75,
      "max_loss_r": -1.09,
      "tp1_touch_rate": 0.6403,
      "tp2_touch_rate": 0.3813,
      "tp3_touch_rate": 0.1942,
      "micro_profit_frequency_lt_0p2r": 0.1151,
      "micro_loss_frequency_gt_minus_0p2r": 0.0
    },
    {
      "scenario": "combined_stress",
      "trades": 278,
      "win_rate": 0.6475,
      "pf": 1.4376,
      "pnl": 37.571,
      "avg_r": 0.13515,
      "max_dd_r": -6.2595,
      "max_win_r": 1.653,
      "max_loss_r": -1.1,
      "tp1_touch_rate": 0.6403,
      "tp2_touch_rate": 0.3813,
      "tp3_touch_rate": 0.1942,
      "micro_profit_frequency_lt_0p2r": 0.1223,
      "micro_loss_frequency_gt_minus_0p2r": 0.0
    }
  ]
}
```

## Logic Checks

```json
{
  "tp1_not_micro": true,
  "no_mfe_replay": true,
  "no_future_outcome_labels": true,
  "next_bar_open_entry": true,
  "real_hl_touch_exits": true,
  "conservative_intrabar_before_tp1": true,
  "overlap_uses_entry_bar_not_signal_bar": true,
  "cluster_as_risk_scaler": true
}
```

## Engineering Notes

- V56.5 keeps the V56 production-safe execution path and adds tiered signals, probability EV, dynamic Top-N, and cluster risk scaling.
- Default TP1 is 1.0R, TP2 is 1.8R, TP3 is 2.8R; this avoids tiny TP1 / micro-profit scalping.
- Entry is next-bar open; exits require actual high/low TP/SL touch; no MFE replay or future labels are used.
- The 1.6 PF request remains reported as a target gap if it is not reached honestly.
