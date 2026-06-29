# -*- coding: utf-8 -*-
"""Create visual backtest logs from trades CSV."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd
import matplotlib.pyplot as plt


def create_visual_report(trades_csv: str, out_dir: str = "backtest_report") -> str:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    trades = pd.read_csv(trades_csv)
    if trades.empty:
        (out / "summary.txt").write_text("No trades.\n", encoding="utf-8")
        return str(out)

    pnl = pd.to_numeric(trades["pnl_r"], errors="coerce").fillna(0.0)
    equity = pnl.cumsum()
    summary = {
        "trades": len(trades),
        "win_rate": float((pnl > 0).mean()),
        "total_r": float(pnl.sum()),
        "avg_r": float(pnl.mean()),
        "profit_factor": float(pnl[pnl > 0].sum() / abs(pnl[pnl < 0].sum())) if abs(pnl[pnl < 0].sum()) > 0 else 999.0,
    }
    (out / "summary.txt").write_text("\n".join(f"{k}: {v}" for k, v in summary.items()), encoding="utf-8")

    plt.figure(figsize=(10, 4))
    plt.plot(equity.values)
    plt.title("Equity Curve by R")
    plt.xlabel("Trade")
    plt.ylabel("Cumulative R")
    plt.tight_layout()
    plt.savefig(out / "equity_curve.png", dpi=160)
    plt.close()

    if "exit_reason" in trades.columns:
        counts = trades["exit_reason"].fillna("UNKNOWN").value_counts()
        plt.figure(figsize=(10, 4))
        counts.plot(kind="bar")
        plt.title("Exit Reason Distribution")
        plt.xlabel("Exit Reason")
        plt.ylabel("Count")
        plt.tight_layout()
        plt.savefig(out / "exit_reasons.png", dpi=160)
        plt.close()

    if "entry_filter" in trades.columns:
        trades.groupby("entry_filter", dropna=False)["pnl_r"].agg(["count", "mean", "sum"]).sort_values("sum", ascending=False).to_csv(out / "entry_filter_stats.csv")
    if "mitigation_src" in trades.columns:
        trades.groupby("mitigation_src", dropna=False)["pnl_r"].agg(["count", "mean", "sum"]).sort_values("sum", ascending=False).to_csv(out / "mitigation_stats.csv")
    if "stop_hunt_direction" in trades.columns:
        trades.groupby("stop_hunt_direction", dropna=False)["pnl_r"].agg(["count", "mean", "sum"]).sort_values("sum", ascending=False).to_csv(out / "stop_hunt_stats.csv")

    trades.to_csv(out / "visual_trade_log.csv", index=False)
    return str(out)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("trades_csv")
    parser.add_argument("--out-dir", default="backtest_report")
    args = parser.parse_args()
    print(create_visual_report(args.trades_csv, args.out_dir))