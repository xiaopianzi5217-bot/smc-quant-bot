# V56.5 Stable Enhanced Report

## Overall

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

## Candidate Pool

```json
{
  "broad_candidates": 9250,
  "enriched_candidates": 9250,
  "selected_before_overlap_guard": 350,
  "signal_density": 0.26398
}
```

## Target Gap

```json
{
  "trade_count_ok": true,
  "win_rate_ok": true,
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
      "bucket": "(0.389, 0.496]",
      "trades": 35,
      "model_ev_mean": 0.46746,
      "win_rate": 0.6857,
      "avg_r": 0.38823
    },
    {
      "bucket": "(0.496, 0.524]",
      "trades": 35,
      "model_ev_mean": 0.51153,
      "win_rate": 0.5429,
      "avg_r": -0.06758
    },
    {
      "bucket": "(0.524, 0.553]",
      "trades": 35,
      "model_ev_mean": 0.53863,
      "win_rate": 0.4857,
      "avg_r": -0.08723
    },
    {
      "bucket": "(0.553, 0.582]",
      "trades": 34,
      "model_ev_mean": 0.56987,
      "win_rate": 0.6765,
      "avg_r": 0.25559
    },
    {
      "bucket": "(0.582, 0.61]",
      "trades": 35,
      "model_ev_mean": 0.59551,
      "win_rate": 0.5429,
      "avg_r": 0.04842
    },
    {
      "bucket": "(0.61, 0.65]",
      "trades": 35,
      "model_ev_mean": 0.63311,
      "win_rate": 0.6286,
      "avg_r": 0.2534
    },
    {
      "bucket": "(0.65, 0.68]",
      "trades": 34,
      "model_ev_mean": 0.66261,
      "win_rate": 0.6176,
      "avg_r": 0.10971
    },
    {
      "bucket": "(0.68, 0.707]",
      "trades": 35,
      "model_ev_mean": 0.69267,
      "win_rate": 0.4571,
      "avg_r": -0.16352
    },
    {
      "bucket": "(0.707, 0.741]",
      "trades": 35,
      "model_ev_mean": 0.72333,
      "win_rate": 0.5429,
      "avg_r": 0.03949
    },
    {
      "bucket": "(0.741, 0.83]",
      "trades": 35,
      "model_ev_mean": 0.76832,
      "win_rate": 0.6,
      "avg_r": 0.03314
    }
  ],
  "monotonic_winrate_steps": 4,
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
    },
    {
      "scenario": "slippage_plus_1bp_proxy",
      "trades": 348,
      "win_rate": 0.5776,
      "pf": 1.1552,
      "pnl": 24.4925,
      "avg_r": 0.07038,
      "max_dd_r": -23.5128,
      "max_win_r": 1.74,
      "max_loss_r": -1.08,
      "tp1_touch_rate": 0.5718,
      "tp2_touch_rate": 0.3276,
      "tp3_touch_rate": 0.1782,
      "micro_profit_frequency_lt_0p2r": 0.0,
      "micro_loss_frequency_gt_minus_0p2r": 0.0
    },
    {
      "scenario": "tp_minus_5pct",
      "trades": 348,
      "win_rate": 0.5776,
      "pf": 1.12,
      "pnl": 18.7559,
      "avg_r": 0.0539,
      "max_dd_r": -25.4074,
      "max_win_r": 1.6625,
      "max_loss_r": -1.07,
      "tp1_touch_rate": 0.5718,
      "tp2_touch_rate": 0.3276,
      "tp3_touch_rate": 0.1782,
      "micro_profit_frequency_lt_0p2r": 0.0,
      "micro_loss_frequency_gt_minus_0p2r": 0.0
    },
    {
      "scenario": "delay_plus_1bar_proxy",
      "trades": 348,
      "win_rate": 0.5776,
      "pf": 1.172,
      "pnl": 26.9725,
      "avg_r": 0.07751,
      "max_dd_r": -22.3328,
      "max_win_r": 1.75,
      "max_loss_r": -1.09,
      "tp1_touch_rate": 0.5718,
      "tp2_touch_rate": 0.3276,
      "tp3_touch_rate": 0.1782,
      "micro_profit_frequency_lt_0p2r": 0.0,
      "micro_loss_frequency_gt_minus_0p2r": 0.0
    },
    {
      "scenario": "combined_stress",
      "trades": 348,
      "win_rate": 0.5776,
      "pf": 1.0908,
      "pnl": 14.3764,
      "avg_r": 0.04131,
      "max_dd_r": -27.4689,
      "max_win_r": 1.653,
      "max_loss_r": -1.1,
      "tp1_touch_rate": 0.5718,
      "tp2_touch_rate": 0.3276,
      "tp3_touch_rate": 0.1782,
      "micro_profit_frequency_lt_0p2r": 0.0,
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
