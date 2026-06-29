# V56 Deep Detection Report

## Compile

- Status: PASS
- Checked Python files: 220
- Errors: 0

## 365-Day Backtest Overall

```json
{
  "trades": 382,
  "win_rate": 0.5183,
  "pf": 0.8049,
  "pnl": -38.4138,
  "avg_r": -0.10056,
  "max_dd_r": -53.8056,
  "max_win_r": 1.395,
  "max_loss_r": -1.07,
  "tp1_touch_rate": 0.5183,
  "micro_profit_frequency_lt_0p2r": 0.0,
  "micro_loss_frequency_gt_minus_0p2r": 0.0
}
```

## Candidate / Selection

```json
{
  "candidates": 9250,
  "selected_before_overlap_guard": 388,
  "signal_density": 0.26398
}
```

## Temporal Stability

```json
{
  "slices": [
    {
      "trades": 96,
      "win_rate": 0.6042,
      "pf": 1.2485,
      "pnl": 10.1034,
      "avg_r": 0.10524,
      "max_dd_r": -8.7525,
      "max_win_r": 1.395,
      "max_loss_r": -1.07,
      "tp1_touch_rate": 0.6042,
      "micro_profit_frequency_lt_0p2r": 0.0,
      "micro_loss_frequency_gt_minus_0p2r": 0.0
    },
    {
      "trades": 96,
      "win_rate": 0.5104,
      "pf": 0.7636,
      "pnl": -11.8908,
      "avg_r": -0.12386,
      "max_dd_r": -20.2317,
      "max_win_r": 1.395,
      "max_loss_r": -1.07,
      "tp1_touch_rate": 0.5104,
      "micro_profit_frequency_lt_0p2r": 0.0,
      "micro_loss_frequency_gt_minus_0p2r": 0.0
    },
    {
      "trades": 95,
      "win_rate": 0.4737,
      "pf": 0.6824,
      "pnl": -16.9904,
      "avg_r": -0.17885,
      "max_dd_r": -25.1504,
      "max_win_r": 1.395,
      "max_loss_r": -1.07,
      "tp1_touch_rate": 0.4737,
      "micro_profit_frequency_lt_0p2r": 0.0,
      "micro_loss_frequency_gt_minus_0p2r": 0.0
    },
    {
      "trades": 95,
      "win_rate": 0.4842,
      "pf": 0.6255,
      "pnl": -19.636,
      "avg_r": -0.20669,
      "max_dd_r": -21.0485,
      "max_win_r": 1.395,
      "max_loss_r": -1.07,
      "tp1_touch_rate": 0.4842,
      "micro_profit_frequency_lt_0p2r": 0.0,
      "micro_loss_frequency_gt_minus_0p2r": 0.0
    }
  ]
}
```

## Compression

```json
{
  "trades": 382,
  "win_rate": 0.5183,
  "pf": 0.7431,
  "pnl": -51.1581,
  "avg_r": -0.13392,
  "max_dd_r": -62.0013,
  "max_win_r": 1.3157,
  "max_loss_r": -1.1,
  "tp1_touch_rate": 0.5183,
  "micro_profit_frequency_lt_0p2r": 0.0314,
  "micro_loss_frequency_gt_minus_0p2r": 0.0
}
```

## Target Gap

```json
{
  "trade_count_ok": true,
  "win_rate_ok": false,
  "pf_ok": false,
  "avg_r_ok": false,
  "total_r_ok": false,
  "note": "Targets are reported, not forced. V56 does not use future outcome labels, MFE replay, or micro-profit caps to satisfy target metrics."
}
```

## Logic Checks

- No MFE-driven TP1 replay.
- No future outcome labels used by selection.
- No micro profit cap / loss floor.
- Entry is next-bar open.
- TP/SL exits require real high/low touch.
- Conservative intrabar ordering assumes SL first before TP1 when both happen in the same bar.
