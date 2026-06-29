# -*- coding: utf-8 -*-
from pathlib import Path
import json
import sys
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from final_forge.profile import apply_v52_final_profile, summarize_profile

POOL = ROOT / "data" / "v50_candidate_pool.csv"
OUT = ROOT / "data" / "backtest_v52_final_forge_verified.csv"
REPORT = ROOT / "reports" / "V52_FINAL_FORGE_VERIFY.json"

if not POOL.exists():
    raise FileNotFoundError(f"candidate pool not found: {POOL}")

pool = pd.read_csv(POOL)
trades = apply_v52_final_profile(pool)
summary = summarize_profile(trades)
OUT.parent.mkdir(parents=True, exist_ok=True)
REPORT.parent.mkdir(parents=True, exist_ok=True)
trades.to_csv(OUT, index=False)
REPORT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

assert summary["trades"] >= 30, summary
assert summary["win_rate"] >= 0.50, summary
assert 1.20 <= summary["pf"] <= 2.20, summary
assert summary["pnl"] > 0, summary
print("V52_FINAL_FORGE_VERIFY_OK")
print(summary)
print(f"saved: {OUT}")
