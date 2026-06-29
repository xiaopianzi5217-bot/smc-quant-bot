# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from final_forge.v56_5_stable_engine import V565Config, run_v565_stable_backtest


def main() -> int:
    ap = argparse.ArgumentParser(description="Run V56.5 stable enhanced production backtest on 15m OHLCV data.")
    ap.add_argument("--exec-csv", default=str(ROOT / "data" / "BTCUSDT_15M_365d.csv"))
    ap.add_argument("--out-dir", default=str(ROOT / "data"))
    ap.add_argument("--extra-second-days", type=int, default=25)
    ap.add_argument("--min-score", type=float, default=65.0)
    ap.add_argument("--tp1-r", type=float, default=1.0)
    ap.add_argument("--tp2-r", type=float, default=1.8)
    ap.add_argument("--tp3-r", type=float, default=2.8)
    ap.add_argument("--stop-atr", type=float, default=0.8)
    ap.add_argument("--max-hold-bars", type=int, default=36)
    args = ap.parse_args()

    cfg = V565Config(
        extra_second_trade_days=args.extra_second_days,
        min_score=args.min_score,
        tp1_r=args.tp1_r,
        tp2_r=args.tp2_r,
        tp3_r=args.tp3_r,
        stop_atr=args.stop_atr,
        max_hold_bars=args.max_hold_bars,
    )
    trades, report = run_v565_stable_backtest(args.exec_csv, args.out_dir, cfg)
    reports_dir = ROOT / "reports"
    reports_dir.mkdir(exist_ok=True)
    (reports_dir / "V56_5_STABLE_REPORT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    md = f"""# V56.5 Stable Enhanced Report

## Overall

```json
{json.dumps(report['overall'], ensure_ascii=False, indent=2)}
```

## Candidate Pool

```json
{json.dumps(report['candidate_summary'], ensure_ascii=False, indent=2)}
```

## Target Gap

```json
{json.dumps(report['target_gap'], ensure_ascii=False, indent=2)}
```

## EV Calibration

```json
{json.dumps(report['ev_calibration'], ensure_ascii=False, indent=2)}
```

## Stability Curve

```json
{json.dumps(report['stability_curve'], ensure_ascii=False, indent=2)}
```

## Logic Checks

```json
{json.dumps(report['logic_checks'], ensure_ascii=False, indent=2)}
```

## Engineering Notes

- V56.5 keeps the V56 production-safe execution path and adds tiered signals, probability EV, dynamic Top-N, and cluster risk scaling.
- Default TP1 is 1.0R, TP2 is 1.8R, TP3 is 2.8R; this avoids tiny TP1 / micro-profit scalping.
- Entry is next-bar open; exits require actual high/low TP/SL touch; no MFE replay or future labels are used.
- The 1.6 PF request remains reported as a target gap if it is not reached honestly.
"""
    (reports_dir / "V56_5_STABLE_REPORT.md").write_text(md, encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
