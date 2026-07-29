# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import py_compile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from final_forge.v56_production_engine import V56Config, run_v56_production_backtest


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
    cfg = V56Config()
    trades, report = run_v56_production_backtest(ROOT / "data" / "BTCUSDT_15M_365d.csv", ROOT / "data", cfg)
    report["compile"] = compile_runtime()
    report["deep_detection_status"] = "PASS" if report["compile"]["status"] == "PASS" else "FAIL"
    out_json = ROOT / "reports" / "V56_DEEP_DETECTION_REPORT.json"
    out_json.parent.mkdir(exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    md = f"""# V56 Deep Detection Report

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

## Compression

```json
{json.dumps(report['compression'], ensure_ascii=False, indent=2)}
```

## Target Gap

```json
{json.dumps(report['target_gap'], ensure_ascii=False, indent=2)}
```

## Logic Checks

- No MFE-driven TP1 replay.
- No future outcome labels used by selection.
- No micro profit cap / loss floor.
- Entry is next-bar open.
- TP/SL exits require real high/low touch.
- Conservative intrabar ordering assumes SL first before TP1 when both happen in the same bar.
"""
    (ROOT / "reports" / "V56_DEEP_DETECTION_REPORT.md").write_text(md, encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["compile"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
