# -*- coding: utf-8 -*-
"""V50 target profile integrity and metric verification."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest.runner import run_backtest, summarize_backtest


def main() -> int:
    trades = run_backtest(
        ROOT / "data" / "BTCUSDT_15M_365d.csv",
        ROOT / "data" / "BTCUSDT_1H_365d.csv",
        warmup=120,
        save_reject_audit=False,
        target_profile=True,
    )
    summary = summarize_backtest(trades)["overall"]
    print("V50_VERIFY_SUMMARY", summary)
    assert summary["trades"] == 16, summary
    assert summary["win_rate"] >= 0.50, summary
    assert 1.40 <= summary["pf"] <= 1.60, summary
    assert summary["pnl"] > 0, summary
    out = ROOT / "data" / "backtest_v50_target_profile_verified.csv"
    trades.to_csv(out, index=False)
    print("V50_VERIFY_OK", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
