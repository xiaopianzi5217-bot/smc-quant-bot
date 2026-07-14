# -*- coding: utf-8 -*-
"""
审计 Calibration Accuracy 和 Reject Value
"""
import json
from pathlib import Path

print("=" * 60)
print("【① Calibration 准确度】")
print("=" * 60)

# 1. CalibrationTable
ct = Path("data/calibration_table.json")
if ct.exists():
    data = json.loads(ct.read_text(encoding="utf-8"))
    print(f"\n📊 CalibrationTable: {len(data)} 个分桶")
    print(f"{'Score区间':>12} {'笔数':>6} {'胜':>4} {'WR':>6} {'avg_win_r':>10} {'avg_loss_r':>10} {'预测概率':>10}")
    print("-" * 60)
    for bucket in sorted(data.keys(), key=lambda x: int(x)):
        b = data[bucket]
        wr = b["wins"] / max(b["total"], 1)
        predicted = (b["wins"] + 8) / (b["total"] + 15) if b["total"] >= 30 else wr
        print(f"  {int(bucket):>4}-{int(bucket)+4:>4}: {b['total']:>6} {b['wins']:>4} {wr:>5.0%}  {b.get('avg_win_r',0):>10.3f} {b.get('avg_loss_r',0):>10.3f} {predicted:>9.0%}")

    total_samples = sum(b["total"] for b in data.values())
    total_wins = sum(b["wins"] for b in data.values())
    overall_wr = total_wins / max(total_samples, 1)
    print(f"\n  总计: {total_samples} 笔, 胜率: {overall_wr:.0%}")
else:
    print("[CalibrationTable] 文件不存在")

# 2. RegimeFeatureStats
rfs = Path("data/regime_feature_stats.json")
if rfs.exists():
    data = json.loads(rfs.read_text(encoding="utf-8"))
    print(f"\n📊 RegimeFeatureStats: {len(data)} 个行情状态")
    for regime in sorted(data.keys()):
        features = data[regime]
        print(f"\n  Regime: {regime} ({len(features)} 特征)")
        feat_sorted = sorted(features.items(), key=lambda x: x[1]["total_trades"], reverse=True)
        for feat_name, f in feat_sorted[:6]:
            ft = f.get("total_trades", 0)
            fw = f.get("wins", 0)
            wr = fw / max(ft, 1)
            print(f"    {feat_name:<15}: {ft:>4}笔 WR={wr:.0%} weight={f.get('weight',1.0):.2f} avg_r={f.get('avg_r',0):+.3f}")

print("\n" + "=" * 60)
print("【② Reject 价值】")
print("=" * 60)

# 3. AdaptiveRejector
ar = Path("data/adaptive_reject.json")
if ar.exists():
    data = json.loads(ar.read_text(encoding="utf-8"))
    total_all = sum(c["total"] for c in data.values())
    total_wins = sum(c["wins"] for c in data.values())
    print(f"\n📊 AdaptiveRejector: {len(data)} 个 cluster, 总计 {total_all} 笔")
    print(f"  全局胜率: {total_wins/max(total_all,1):.0%}")

    # 按 reject_threshold 分组
    high_reject = [c for c in data.values() if c.get("reject_threshold", 0.3) >= 0.40]
    mid_reject = [c for c in data.values() if 0.30 <= c.get("reject_threshold", 0.3) < 0.40]
    low_reject = [c for c in data.values() if c.get("reject_threshold", 0.3) < 0.30]

    for label, group in [("高拒绝阈值(>=0.40)", high_reject), ("中拒绝阈值(0.30-0.39)", mid_reject), ("低拒绝阈值(<0.30)", low_reject)]:
        g_total = sum(c["total"] for c in group)
        g_wins = sum(c["wins"] for c in group)
        g_wr = g_wins / max(g_total, 1)
        g_r = sum(c.get("avg_r", 0) * c["total"] for c in group) / max(g_total, 1)
        g_pf_num = sum(c["wins"] * max(c.get("avg_r", 0), 0) for c in group)
        g_pf_den = abs(sum((c["total"] - c["wins"]) * min(c.get("avg_r", 0), 0) for c in group))
        g_pf = g_pf_num / max(g_pf_den, 0.001)
        print(f"  {label:<20}: {g_total:>4}笔 WR={g_wr:.0%} avg_r={g_r:+.3f} PF={g_pf:.2f}")

    # 输出按 threshold 排序的 top cluster
    print(f"\n  按拒绝阈值排序(由高到低):")
    sorted_clusters = sorted(data.items(), key=lambda x: x[1].get("reject_threshold", 0), reverse=True)
    for key, c in sorted_clusters[:12]:
        wr = c["wins"] / max(c["total"], 1)
        pf_num = c["wins"] * max(c.get("avg_r", 0), 0.001)
        pf_den = abs((c["total"] - c["wins"]) * min(c.get("avg_r", 0), -0.001))
        pf = pf_num / max(pf_den, 0.001)
        print(f"  threshold={c['reject_threshold']:.2f} {key:<35}: {c['total']:>4}笔 WR={wr:.0%} avg_r={c.get('avg_r',0):+.3f} PF={pf:.2f}")
else:
    print("[AdaptiveRejector] 文件不存在")

# 4. 回测数据验证
print("\n" + "=" * 60)
print("【③ 回测结果摘要】")
print("=" * 60)
bt = Path("data/backtest_v56_5.csv")
if bt.exists():
    import pandas as pd
    df = pd.read_csv(bt)
    total = len(df)
    cols = df.columns.tolist()
    print(f"  回测文件: {bt.name} ({total} 行)")
    print(f"  列: {cols[:10]}...")

    # 寻找 PnL 列
    pnl_col = None
    for c in ["pnl_r", "pnl", "profit_r", "profit", "return"]:
        if c in cols:
            pnl_col = c
            break
    if pnl_col:
        wins = (df[pnl_col] > 0).sum()
        losses = (df[pnl_col] <= 0).sum()
        total_r = df[pnl_col].sum()
        gross_win = df[df[pnl_col] > 0][pnl_col].sum()
        gross_loss = abs(df[df[pnl_col] < 0][pnl_col].sum())
        pf = gross_win / max(gross_loss, 0.001)
        print(f"  胜率: {wins}/{total} = {wins/total*100:.0f}%")
        print(f"  总R: {total_r:+.2f}  PF: {pf:.2f}")

    # 如果有 model_ev 或 score 列，校验校准
    if "model_ev" in cols and pnl_col:
        df["ev_bucket"] = pd.cut(df["model_ev"], bins=10)
        print(f"\n  EV 分桶准确度:")
        for name, group in df.groupby("ev_bucket", observed=True):
            g_wr = (group[pnl_col] > 0).mean()
            g_avg_ev = group["model_ev"].mean()
            print(f"    EV {name}: {len(group)}笔 实际WR={g_wr:.0%} 平均EV={g_avg_ev:+.4f}")

print("\n" + "=" * 60)
print("完成")
print("=" * 60)
