# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from final_forge.v56_production_engine import V56Config, run_v56_production_backtest


def main() -> int:
    ap = argparse.ArgumentParser(description="Run V56 production architecture backtest on 15m OHLCV data.")
    ap.add_argument("--exec-csv", default=str(ROOT / "data" / "BTCUSDT_15M_365d.csv"))
    ap.add_argument("--out-dir", default=str(ROOT / "data"))
    ap.add_argument("--extra-second-days", type=int, default=25)
    ap.add_argument("--min-score", type=float, default=55.0)
    args = ap.parse_args()

    cfg = V56Config(extra_second_trade_days=args.extra_second_days, min_score=args.min_score)
    trades, report = run_v56_production_backtest(args.exec_csv, args.out_dir, cfg)
    reports_dir = ROOT / "reports"
    reports_dir.mkdir(exist_ok=True)
    report_path = reports_dir / "V56_PRODUCTION_REPORT.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    md = f"""# V56 Production Architecture Report

## Overall

```json
{json.dumps(report['overall'], ensure_ascii=False, indent=2)}
```

## Candidate Pool

```json
{json.dumps(report['candidate_summary'], ensure_ascii=False, indent=2)}
```

## Signal Entropy

```json
{json.dumps(report['signal_entropy'], ensure_ascii=False, indent=2)}
```

## Compression Test

```json
{json.dumps(report['compression'], ensure_ascii=False, indent=2)}
```

## Target Gap

```json
{json.dumps(report['target_gap'], ensure_ascii=False, indent=2)}
```

## Engineering Notes

- V56 uses five signal sources: liquidity sweep, weak BOS, FVG touch, orderblock reaction, and trend pullback.
- V56 uses Top-N ranking rather than EV hard-gating.
- V56 uses next-bar open entry and real high/low touch exits.
- V56 does not use MFE replay, future outcome labels, profit caps, or tiny-loss floors.
- TP1 is set to 0.85R, TP2 to 1.45R, TP3 to 2.20R, so the system is not relying on micro TP1 scalping.
- Any target that fails is reported as a target gap instead of being forced by unsafe code.
"""
    (reports_dir / "V56_PRODUCTION_REPORT.md").write_text(md, encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
