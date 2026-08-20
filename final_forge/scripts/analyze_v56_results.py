# -*- coding: utf-8 -*-
"""分析 V56 production backtest 结果"""
import pandas as pd
import os

# 从 SMC_Bot 根目录开始查找
base = r"C:\Users\Administrator\Desktop\SMC_Bot"
csv_path = os.path.join(base, "data", "backtest_v56_production.csv")

if not os.path.exists(csv_path):
    print(f"文件不存在: {csv_path}")
    exit(1)

print(f"使用文件: {csv_path}")

df = pd.read_csv(csv_path)
print(f"总交易数: {len(df)}")
print()

# 按方向统计
print("=== 按方向统计 ===")
for d in df["direction"].unique():
    sub = df[df["direction"] == d]
    pnl = sub["pnl_r"]
    wins = pnl[pnl > 0].sum()
    losses = abs(pnl[pnl < 0].sum())
    pf = wins / losses if losses > 0 else (999 if wins > 0 else 0)
    wr = (pnl > 0).mean() * 100
    print(f"{d}: trades={len(sub)}, WR={wr:.1f}%, PF={pf:.2f}, PNL={pnl.sum():.2f}R")
print()

# 按 setup_type 统计
print("=== 按 setup_type 统计 ===")
if "setup_type" in df.columns:
    for s in df["setup_type"].unique():
        sub = df[df["setup_type"] == s]
        pnl = sub["pnl_r"]
        wins = pnl[pnl > 0].sum()
        losses = abs(pnl[pnl < 0].sum())
        pf = wins / losses if losses > 0 else (999 if wins > 0 else 0)
        wr = (pnl > 0).mean() * 100
        print(f"{s}: trades={len(sub)}, WR={wr:.1f}%, PF={pf:.2f}, PNL={pnl.sum():.2f}R")
print()

# 按退出原因统计
print("=== 按退出原因统计 ===")
if "exit_reason" in df.columns:
    for r in df["exit_reason"].unique():
        sub = df[df["exit_reason"] == r]
        pnl = sub["pnl_r"]
        wins = pnl[pnl > 0].sum()
        losses = abs(pnl[pnl < 0].sum())
        pf = wins / losses if losses > 0 else (999 if wins > 0 else 0)
        wr = (pnl > 0).mean() * 100
        print(f"{r}: trades={len(sub)}, WR={wr:.1f}%, PF={pf:.2f}, PNL={pnl.sum():.2f}R")
print()

# 胜率最高的LS信号特征分析
print("=== LIQUIDITY_SWEEP 信号特征分析 ===")
ls = df[df["setup_type"] == "LIQUIDITY_SWEEP"]
if len(ls) > 0:
    print(f"LS总数: {len(ls)}")
    
    # 按 score 分组
    ls_sorted = ls.sort_values("score", ascending=False)
    print("\n--- 按分数段统计 ---")
    for lo, hi in [(80, 999), (75, 80), (70, 75), (65, 70), (60, 65), (55, 60)]:
        sub = ls_sorted[(ls_sorted["score"] >= lo) & (ls_sorted["score"] < hi)]
        if len(sub) == 0:
            continue
        pnl = sub["pnl_r"]
        wins = pnl[pnl > 0].sum()
        losses = abs(pnl[pnl < 0].sum())
        pf = wins / losses if losses > 0 else (999 if wins > 0 else 0)
        wr = (pnl > 0).mean() * 100
        print(f"score [{lo}-{hi}]: trades={len(sub)}, WR={wr:.1f}%, PF={pf:.2f}, PNL={pnl.sum():.2f}R")

    # 按 RSI 分组
    print("\n--- 按 RSI 分组 (Long only) ---")
    ls_long = ls[ls["direction"] == "Long"]
    if len(ls_long) > 0:
        rsi_col = "rsi" if "rsi" in ls_long.columns else None
        if rsi_col:
            for lo, hi in [(0, 28), (28, 34), (34, 40), (40, 50), (50, 60), (60, 100)]:
                sub = ls_long[(ls_long[rsi_col] >= lo) & (ls_long[rsi_col] < hi)]
                if len(sub) == 0:
                    continue
                pnl = sub["pnl_r"]
                wins = pnl[pnl > 0].sum()
                losses = abs(pnl[pnl < 0].sum())
                pf = wins / losses if losses > 0 else (999 if wins > 0 else 0)
                wr = (pnl > 0).mean() * 100
                print(f"RSI [{lo}-{hi}]: trades={len(sub)}, WR={wr:.1f}%, PF={pf:.2f}, PNL={pnl.sum():.2f}R")

    print("\n--- 按 RSI 分组 (Short only) ---")
    ls_short = ls[ls["direction"] == "Short"]
    if len(ls_short) > 0:
        rsi_col = "rsi" if "rsi" in ls_short.columns else None
        if rsi_col:
            for lo, hi in [(0, 32), (32, 40), (40, 50), (50, 60), (60, 68), (68, 75), (75, 100)]:
                sub = ls_short[(ls_short[rsi_col] >= lo) & (ls_short[rsi_col] < hi)]
                if len(sub) == 0:
                    continue
                pnl = sub["pnl_r"]
                wins = pnl[pnl > 0].sum()
                losses = abs(pnl[pnl < 0].sum())
                pf = wins / losses if losses > 0 else (999 if wins > 0 else 0)
                wr = (pnl > 0).mean() * 100
                print(f"RSI [{lo}-{hi}]: trades={len(sub)}, WR={wr:.1f}%, PF={pf:.2f}, PNL={pnl.sum():.2f}R")

    # 按 trend_strength 分组
    print("\n--- 按 trend_strength 分组 (Long) ---")
    if "trend_strength" in ls_long.columns:
        for lo, hi in [(-999, -0.5), (-0.5, -0.2), (-0.2, 0), (0, 0.2), (0.2, 0.5), (0.5, 999)]:
            sub = ls_long[(ls_long["trend_strength"] >= lo) & (ls_long["trend_strength"] < hi)]
            if len(sub) == 0:
                continue
            pnl = sub["pnl_r"]
            wins = pnl[pnl > 0].sum()
            losses = abs(pnl[pnl < 0].sum())
            pf = wins / losses if losses > 0 else (999 if wins > 0 else 0)
            wr = (pnl > 0).mean() * 100
            print(f"TS [{lo}-{hi}]: trades={len(sub)}, WR={wr:.1f}%, PF={pf:.2f}, PNL={pnl.sum():.2f}R")

    print("\n--- 按 trend_strength 分组 (Short) ---")
    if "trend_strength" in ls_short.columns:
        for lo, hi in [(-999, -0.5), (-0.5, -0.2), (-0.2, 0), (0, 0.2), (0.2, 0.5), (0.5, 999)]:
            sub = ls_short[(ls_short["trend_strength"] >= lo) & (ls_short["trend_strength"] < hi)]
            if len(sub) == 0:
                continue
            pnl = sub["pnl_r"]
            wins = pnl[pnl > 0].sum()
            losses = abs(pnl[pnl < 0].sum())
            pf = wins / losses if losses > 0 else (999 if wins > 0 else 0)
            wr = (pnl > 0).mean() * 100
            print(f"TS [{lo}-{hi}]: trades={len(sub)}, WR={wr:.1f}%, PF={pf:.2f}, PNL={pnl.sum():.2f}R")

# 查看 LS 信号的分数分布
print("\n=== LS 信号分数分布 ===")
ls_score = ls["score"] if len(ls) > 0 else pd.Series()
if len(ls_score) > 0:
    print(f"score: min={ls_score.min():.1f}, max={ls_score.max():.1f}, mean={ls_score.mean():.1f}, median={ls_score.median():.1f}")

# 查看前20个最好和最差交易
print("\n=== 最好/最差交易 TOP 10 ===")
df_sorted = df.sort_values("pnl_r", ascending=False)
print("\n--- 最好 10 笔 ---")
for _, row in df_sorted.head(10).iterrows():
    print(f"{row['direction']} | score={row['score']:.1f} | RSI={row.get('rsi', 0):.1f} | TS={row.get('trend_strength', 0):.2f} | PNL={row['pnl_r']:.2f}R | {row.get('exit_reason', '')}")
print("\n--- 最差 10 笔 ---")
for _, row in df_sorted.tail(10).iterrows():
    print(f"{row['direction']} | score={row['score']:.1f} | RSI={row.get('rsi', 0):.1f} | TS={row.get('trend_strength', 0):.2f} | PNL={row['pnl_r']:.2f}R | {row.get('exit_reason', '')}")