# -*- coding: utf-8 -*-

import pandas as pd
import numpy as np
import os


def load_trades(csv_path: str) -> pd.DataFrame:
    return pd.read_csv(csv_path)


def _adaptive_quantile_buckets(values: pd.Series, base_bins, base_labels):
    """Create quantile-based buckets that always produce exactly len(bins)-1 labels."""
    v = values.dropna()
    if len(v) < 20:
        return pd.cut(values, bins=base_bins, labels=base_labels)
    try:
        # Compute 4 quantile breakpoints
        q = [v.quantile(p) for p in [0.25, 0.50, 0.75]]
        # Deduplicate
        uniq = sorted(set(q))
        # Build bins: [-inf, q1, q2, q3, +inf]
        bins = sorted(set([-999.0] + uniq + [999.0]))
        if len(bins) < 3:
            # Fallback: use simple fixed bins
            return pd.cut(values, bins=base_bins, labels=base_labels)
        # Generate labels
        n = len(bins) - 1
        labels = [str(i) for i in range(n)]
        return pd.cut(values, bins=bins, labels=labels)
    except Exception:
        return pd.cut(values, bins=base_bins, labels=base_labels)


def build_cluster_key(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    df["ev_bucket"] = _adaptive_quantile_buckets(
        df["expected_value"],
        base_bins=[-999, 0, 0.05, 0.10, 0.15, 999],
        base_labels=["NEG", "LOW", "MID", "HIGH", "EXTREME"]
    )

    df["rr_bucket"] = _adaptive_quantile_buckets(
        df["estimated_rr"],
        base_bins=[0, 0.8, 1.2, 1.6, 2.5, 999],
        base_labels=["WEAK", "OK", "GOOD", "STRONG", "EXTREME"]
    )

    df["cluster"] = (
        df["regime"].astype(str) + "_"
        + df["setup_type"].astype(str) + "_"
        + df["ev_bucket"].astype(str) + "_"
        + df["rr_bucket"].astype(str)
    )

    return df


def analyze_clusters(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("cluster")

    result = g.agg(
        trades=("cluster", "count"),
        win_rate=("pnl_r", lambda x: (x > 0).mean()),
        pf=("pnl_r", lambda x: x[x > 0].sum() / abs(x[x < 0].sum() + 1e-9)),
        avg_ev=("expected_value", "mean"),
        avg_rr=("estimated_rr", "mean"),
    ).reset_index()

    result["stability"] = result["win_rate"] * result["pf"]

    result = result.sort_values(by="stability", ascending=False)

    return result


def detect_bad_clusters(df: pd.DataFrame) -> pd.DataFrame:
    bad = df.groupby("cluster").agg(
        trades=("cluster", "count"),
        win_rate=("pnl_r", lambda x: (x > 0).mean()),
        pf=("pnl_r", lambda x: x[x > 0].sum() / abs(x[x < 0].sum() + 1e-9)),
    ).reset_index()

    bad = bad[(bad["pf"] < 1.10) | (bad["win_rate"] < 0.45)]
    return bad


def kill_bad_clusters(trades: pd.DataFrame, min_pf: float = 1.10, min_wr: float = 0.45) -> pd.DataFrame:
    """Kill clusters below thresholds and return the killed trades."""
    df = build_cluster_key(trades)
    grp = df.groupby("cluster").agg(
        trades=("cluster", "count"),
        win_rate=("pnl_r", lambda x: (x > 0).mean()),
        pf=("pnl_r", lambda x: x[x > 0].sum() / abs(x[x < 0].sum() + 1e-9)),
    ).reset_index()
    bad = grp[(grp["pf"] < min_pf) | (grp["win_rate"] < min_wr)]
    bad_names = set(bad["cluster"].tolist())
    killed_mask = df["cluster"].isin(bad_names)
    return trades[killed_mask].copy()


def run_ev_cluster_diagnostic(trades_csv: str, output_dir: str = "outputs"):
    os.makedirs(output_dir, exist_ok=True)

    if not os.path.exists(trades_csv) or os.path.getsize(trades_csv) < 10:
        raise ValueError(f"回测输出文件缺失或为空: {trades_csv}")

    df = load_trades(trades_csv)

    if df.empty:
        raise ValueError(f"回测输出文件没有任何交易记录: {trades_csv}")

    required_cols = ["expected_value", "estimated_rr", "pnl_r", "regime", "setup_type"]
    for c in required_cols:
        if c not in df.columns:
            raise ValueError(f"Missing column: {c}")

    df = build_cluster_key(df)

    report = analyze_clusters(df)
    bad = detect_bad_clusters(df)

    report_path = os.path.join(output_dir, "ev_cluster_report.csv")
    bad_path = os.path.join(output_dir, "ev_bad_clusters.csv")

    report.to_csv(report_path, index=False)
    bad.to_csv(bad_path, index=False)

    print("\n===== EV CLUSTER DIAGNOSTIC =====")
    print(f"Total clusters: {df['cluster'].nunique()}")
    print(f"Bad clusters: {len(bad)}")
    print(f"Saved: {report_path}")
    print(f"Saved: {bad_path}")

    return report, bad


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python ev_cluster_diagnostic.py trades.csv")
        exit()

    run_ev_cluster_diagnostic(sys.argv[1])