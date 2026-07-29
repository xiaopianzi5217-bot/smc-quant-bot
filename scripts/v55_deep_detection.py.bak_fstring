# -*- coding: utf-8 -*-
"""V55 deep detection: syntax, fast profile, noise bucket, and PF compression."""
from __future__ import annotations

import json
import py_compile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from final_forge.profile import apply_v55_engineering_profile, summarize_profile, temporal_stability_report, compression_test
from backtest.runner import run_backtest, summarize_backtest


def compile_runtime() -> dict:
    checked = 0
    errors = []
    for p in ROOT.glob("**/*.py"):
        rel = p.relative_to(ROOT)
        if "__pycache__" in rel.parts:
            continue
        try:
            py_compile.compile(str(p), doraise=True)
            checked += 1
        except Exception as exc:
            errors.append({"file": str(rel), "error": f"{type(exc).__name__}: {exc}"})
    return {"checked_py_files": checked, "errors": errors, "status": "PASS" if not errors else "FAIL"}


def noise_bucket(trades: pd.DataFrame) -> dict:
    if trades is None or trades.empty or "pnl_r" not in trades.columns:
        return {"status": "EMPTY"}
    pnl = pd.to_numeric(trades["pnl_r"], errors="coerce").fillna(0.0)
    losses = pnl[pnl < 0]
    return {
        "trades": int(len(pnl)),
        "losing_trade_density": round(float((pnl < 0).mean()), 4),
        "micro_loss_frequency_lt_0p2r": round(float(((pnl < 0) & (pnl > -0.2)).mean()), 4),
        "tail_loss_frequency_le_1r": round(float((pnl <= -1.0).mean()), 4),
        "avg_loss_r": round(float(losses.mean()), 4) if len(losses) else 0.0,
        "median_loss_r": round(float(losses.median()), 4) if len(losses) else 0.0,
    }


def main() -> int:
    report = {"version": "V55_ENGINEERING_REALISTIC_20260623"}
    report["compile"] = compile_runtime()

    pool_path = ROOT / "data" / "backtest_v39_full.csv"
    if pool_path.exists():
        pool = pd.read_csv(pool_path)
        prof = apply_v55_engineering_profile(pool)
        report["candidate_profile_summary"] = summarize_profile(prof)
        report["candidate_profile_stability"] = temporal_stability_report(prof, slices=4)
        report["candidate_profile_compression"] = compression_test(prof)
        report["candidate_profile_noise_bucket"] = noise_bucket(prof)
        out = ROOT / "data" / "backtest_v55_engineering_realistic.csv"
        prof.to_csv(out, index=False)
        report["candidate_profile_output"] = str(out.relative_to(ROOT))
    else:
        report["candidate_profile_summary"] = {"status": "SKIPPED_NO_POOL"}

    # Fast runner path should use the same V55 profile and must not rely on MFE replay.
    try:
        trades = run_backtest(
            ROOT / "data" / "BTCUSDT_15M_365d.csv",
            ROOT / "data" / "BTCUSDT_1H_365d.csv",
            save_reject_audit=False,
            target_profile=True,
        )
        report["runner_fast_summary"] = summarize_backtest(trades)["overall"]
    except Exception as exc:
        report["runner_fast_summary"] = {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}

    report_path = ROOT / "reports" / "V55_DEEP_DETECTION_REPORT.json"
    report_path.parent.mkdir(exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    md_path = ROOT / "reports" / "V55_DEEP_DETECTION_REPORT.md"
    summary = report.get("candidate_profile_summary", {})
    compression = report.get("candidate_profile_compression", {})
    noise = report.get("candidate_profile_noise_bucket", {})
    md = f"""# V55 Deep Detection Report

## Status

- Compile: {report['compile']['status']} ({report['compile']['checked_py_files']} Python files checked)
- Version: {report['version']}

## Candidate Profile Summary

```json
{json.dumps(summary, ensure_ascii=False, indent=2)}
```

## PF Compression Test

```json
{json.dumps(compression, ensure_ascii=False, indent=2)}
```

## Noise Bucket

```json
{json.dumps(noise, ensure_ascii=False, indent=2)}
```

## Engineering Notes

- V55 disables MFE-driven TP1 replay in candidate-pool profile.
- V55 disables default micro profit cap / loss floor.
- V55 widens the stop model and raises TP1/TP2/TP3 R targets to avoid tiny-stop micro-profit trades.
- The bundled candidate pool has only about 155 historical candidates, so it cannot by itself validate a 370–400 trades/year cadence. Use `force_event_backtest=True` and fresh multi-market data for cadence validation.
"""
    md_path.write_text(md, encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(f"saved: {report_path}")
    return 0 if report["compile"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
