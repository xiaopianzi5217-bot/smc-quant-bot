# V56 Production Architecture Report

## Overall

```json
{
  "trades": 384,
  "win_rate": 0.5208,
  "pf": 0.7651,
  "pnl": -45.8431,
  "avg_r": -0.11938,
  "max_dd_r": -54.735,
  "max_win_r": 1.395,
  "max_loss_r": -1.07,
  "tp1_touch_rate": 0.5208
}
```

## Candidate Pool

```json
{
  "candidates": 7546,
  "selected_before_overlap_guard": 388,
  "signal_density": 0.21535
}
```

## Signal Entropy

```json
{
  "candidate_count": 7546,
  "setup_counts": {
    "LIQUIDITY_SWEEP": 3455,
    "WEAK_BOS": 2459,
    "FVG_TOUCH": 846,
    "TREND_PULLBACK": 502,
    "ORDERBLOCK_REACTION": 270,
    "REAL_CHOCH": 8,
    "ENHANCED_BUY": 6
  },
  "entropy_bits": 1.8478,
  "max_pattern_share": 0.4579,
  "dominance_warning": false
}
```

## Compression Test

```json
{
  "trades": 384,
  "win_rate": 0.5208,
  "pf": 0.7056,
  "pnl": -58.1493,
  "avg_r": -0.15143,
  "max_dd_r": -63.6998,
  "max_win_r": 1.3157,
  "max_loss_r": -1.1,
  "tp1_touch_rate": 0.5208
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
