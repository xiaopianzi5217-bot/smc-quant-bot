# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import py_compile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from final_forge.v56_5_stable_engine import V565Config, run_v565_stable_backtest


def compile_runtime() -> dict:
    checked = 0
    errors = []
    for p in ROOT.glob("**/*.py"):
        if "__pycache__" in p.parts:
            continue
        try:
            py_compile.compile(str(p), doraise=True)
            checked += 1
        except Exception as exc:
            errors.append({"file": str(p.relative_to(ROOT)), "error": f"{type(exc).__name__}: {exc}"})
    return {"checked_py_files": checked, "errors": errors, "status": "PASS" if not errors else "FAIL"}


def main() -> int:
    cfg = V565Config()
    trades, report = run_v565_stable_backtest(ROOT / "data" / "BTCUSDT_15M_365d.csv", ROOT / "data", cfg)
    report["compile"] = compile_runtime()
    logic = report.get("logic_checks", {})
    target = report.get("target_gap", {})
    report["deep_detection_status"] = "PASS" if report["compile"]["status"] == "PASS" and logic.get("tp1_not_micro") and target.get("trade_count_ok") and target.get("total_r_ok") else "WARN"

    out_json = ROOT / "reports" / "V56_5_DEEP_DETECTION_REPORT.json"
    out_json.parent.mkdir(exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    md = f"""# V56.5 Deep Detection Report

## Compile

- Status: {report['compile']['status']}
- Checked Python files: {report['compile']['checked_py_files']}
- Errors: {len(report['compile']['errors'])}

## 365-Day Backtest Overall

```json
{json.dumps(report['overall'], ensure_ascii=False, indent=2)}
```

## Candidate / Selection

```json
{json.dumps(report['candidate_summary'], ensure_ascii=False, indent=2)}
```

## Temporal Stability

```json
{json.dumps(report['temporal_stability'], ensure_ascii=False, indent=2)}
```

## Stability Curve

```json
{json.dumps(report['stability_curve'], ensure_ascii=False, indent=2)}
```

## EV Calibration

```json
{json.dumps(report['ev_calibration'], ensure_ascii=False, indent=2)}
```

## Target Gap

```json
{json.dumps(report['target_gap'], ensure_ascii=False, indent=2)}
```

## Logic Checks

```json
{json.dumps(report['logic_checks'], ensure_ascii=False, indent=2)}
```
"""
    (ROOT / "reports" / "V56_5_DEEP_DETECTION_REPORT.md").write_text(md, encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["compile"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
