# V55 Deep Detection Report

## Status

- Compile: PASS (217 Python files checked)
- Version: V55_ENGINEERING_REALISTIC_20260623

## Candidate Profile Summary

```json
{
  "trades": 89,
  "win_rate": 0.5169,
  "pf": 1.7472,
  "pnl": 25.6325,
  "avg_r": 0.28801,
  "max_win_r": 5.1798,
  "max_loss_r": -1.1,
  "max_dd_r": -6.3596,
  "losing_trade_density": 0.4831,
  "micro_loss_frequency": 0.0899
}
```

## PF Compression Test

```json
{
  "trades": 89,
  "win_rate": 0.5169,
  "pf": 1.6164,
  "pnl": 21.5086,
  "avg_r": 0.24167,
  "max_win_r": 4.9113,
  "max_loss_r": -1.13,
  "max_dd_r": -6.7011,
  "losing_trade_density": 0.4831,
  "micro_loss_frequency": 0.0899
}
```

## Noise Bucket

```json
{
  "trades": 89,
  "losing_trade_density": 0.4831,
  "micro_loss_frequency_lt_0p2r": 0.0899,
  "tail_loss_frequency_le_1r": 0.3146,
  "avg_loss_r": -0.7978,
  "median_loss_r": -1.1
}
```

## Engineering Notes

- V55 disables MFE-driven TP1 replay in candidate-pool profile.
- V55 disables default micro profit cap / loss floor.
- V55 widens the stop model and raises TP1/TP2/TP3 R targets to avoid tiny-stop micro-profit trades.
- The bundled candidate pool has only about 155 historical candidates, so it cannot by itself validate a 370–400 trades/year cadence. Use `force_event_backtest=True` and fresh multi-market data for cadence validation.
