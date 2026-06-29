# V56 Production Architecture Report

## Overall

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

## Candidate Pool

```json
{
  "candidates": 9250,
  "selected_before_overlap_guard": 388,
  "signal_density": 0.26398
}
```

## Signal Entropy

```json
{
  "candidate_count": 9250,
  "setup_counts": {
    "TREND_PULLBACK": 3719,
    "WEAK_BOS": 2691,
    "LIQUIDITY_SWEEP": 1724,
    "FVG_TOUCH": 846,
    "ORDERBLOCK_REACTION": 270
  },
  "entropy_bits": 1.9629,
  "max_pattern_share": 0.4021,
  "dominance_warning": false
}
```

## Compression Test

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

## Engineering Notes

- V56 uses five signal sources: liquidity sweep, weak BOS, FVG touch, orderblock reaction, and trend pullback.
- V56 uses Top-N ranking rather than EV hard-gating.
- V56 uses next-bar open entry and real high/low touch exits.
- V56 does not use MFE replay, future outcome labels, profit caps, or tiny-loss floors.
- TP1 is set to 0.85R, TP2 to 1.45R, TP3 to 2.20R, so the system is not relying on micro TP1 scalping.
- Any target that fails is reported as a target gap instead of being forced by unsafe code.
