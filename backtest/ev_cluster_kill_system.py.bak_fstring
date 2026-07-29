# -*- coding: utf-8 -*-

import pandas as pd
import numpy as np


# ============================================================
# 1️⃣ 读取数据
# ============================================================
def load_data(path: str) -> pd.DataFrame:
    return pd.read_csv(path)


# ============================================================
# 2️⃣ 构建cluster
# ============================================================
def build_cluster(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    df["cluster"] = (
        df["regime"].astype(str) + "_" +
        df["setup_type"].astype(str)
    )

    return df


# ============================================================
# 3️⃣ cluster统计
# ============================================================
def cluster_stats(df: pd.DataFrame) -> pd.DataFrame:

    g = df.groupby("cluster")

    out = g.agg(
        trades=("pnl_r", "count"),
        win_rate=("pnl_r", lambda x: (x > 0).mean()),
        pf=("pnl_r", lambda x: x[x > 0].sum() / (abs(x[x < 0].sum()) + 1e-9)),
        avg_ev=("expected_value", "mean"),
        avg_rr=("estimated_rr", "mean"),
    ).reset_index()

    return out


# ============================================================
# 4️⃣ ❌ Bad Cluster识别（核心）
# ============================================================
def detect_bad_clusters(stats: pd.DataFrame) -> pd.DataFrame:

    bad_clusters = stats[
        (stats["pf"] < 1.10) |
        (stats["win_rate"] < 0.45) |
        (stats["avg_ev"] < 0.0)
    ].copy()

    return bad_clusters


# ============================================================
# 5️⃣ EV → 仓位映射（核心升级）
# ============================================================
def ev_to_position(ev: float, rr: float, win_rate: float) -> float:

    base = 0.10

    edge = max(0.0, ev)

    confidence = win_rate * rr

    size = base + 0.8 * edge + 0.3 * confidence

    # clamp
    return max(0.0, min(size, 0.50))


# ============================================================
# 6️⃣ Tail Risk 控制（核心升级）
# ============================================================
def tail_risk_adjust(df: pd.DataFrame) -> pd.DataFrame:

    df = df.copy()

    df["is_tail_loss"] = df["pnl_r"] < df["pnl_r"].quantile(0.1)

    df["risk_penalty"] = np.where(
        df["is_tail_loss"],
        0.5,
        1.0
    )

    df["adjusted_pnl"] = df["pnl_r"] * df["risk_penalty"]

    return df


# ============================================================
# 7️⃣ Cluster Kill + PF提升主逻辑
# ============================================================
def run_cluster_kill(trades_csv: str, output_path: str = "cluster_report.csv"):

    df = load_data(trades_csv)

    required = ["pnl_r", "expected_value", "estimated_rr", "regime", "setup_type"]

    for c in required:
        if c not in df.columns:
            raise ValueError(f"Missing column: {c}")

    df = build_cluster(df)

    # step1: cluster stats
    stats = cluster_stats(df)

    # step2: bad cluster
    bad_clusters = detect_bad_clusters(stats)

    bad_cluster_names = set(bad_clusters["cluster"].tolist())

    # step3: kill bad clusters
    df["cluster_killed"] = df["cluster"].apply(
        lambda x: 0 if x in bad_cluster_names else 1
    )

    df_alive = df[df["cluster_killed"] == 1].copy()

    # step4: EV → position mapping
    df_alive["position_size"] = df_alive.apply(
        lambda r: ev_to_position(
            r["expected_value"],
            r["estimated_rr"],
            (r["pnl_r"] > 0)
        ),
        axis=1
    )

    # step5: tail control
    df_final = tail_risk_adjust(df_alive)

    # step6: PF recalculation
    wins = df_final[df_final["adjusted_pnl"] > 0]["adjusted_pnl"].sum()
    losses = abs(df_final[df_final["adjusted_pnl"] < 0]["adjusted_pnl"].sum())

    pf = wins / (losses + 1e-9)

    print("\n================ CLUSTER KILL REPORT ================")
    print(f"Total trades: {len(df)}")
    print(f"Alive trades: {len(df_final)}")
    print(f"Killed clusters: {len(bad_cluster_names)}")
    print(f"PF after kill: {pf:.4f}")

    df_final.to_csv(output_path, index=False)

    return df_final, stats, bad_clusters