"""V56 修正后验证脚本 - 修复编码"""
import sys
import glob
import json
from pathlib import Path

sys.path.insert(0, 'C:/Users/Administrator/Desktop/SMC_Bot')

import pandas as pd
from final_forge.v56_production_engine import V56Config, run_v56_production_backtest

# 找到正确的15m数据
csv_files = glob.glob('data/*.csv')
exec_csv = None
for f in csv_files:
    if '15M' in f and '365' in f:
        exec_csv = f
        break
if not exec_csv:
    csv_files = sorted(csv_files, key=lambda x: Path(x).stat().st_size, reverse=True)
    exec_csv = csv_files[0] if csv_files else None

print(f"数据: {exec_csv}")
print(f"大小: {Path(exec_csv).stat().st_size / 1024 / 1024:.1f} MB")

cfg = V56Config()
cfg.extra_second_trade_days = 25

trades, report = run_v56_production_backtest(exec_csv, None, cfg)
s = report['overall']

print(f"版本: {report['version']}")
print(f"交易数: {s['trades']}")
print(f"胜率: {s['win_rate']*100:.1f}%")
print(f"盈亏比: {s['pf']:.2f}")
print(f"总利润(R): {s['pnl']:.2f}")
print(f"平均R: {s['avg_r']:.4f}")
print(f"最大回撤(R): {s['max_dd_r']:.2f}")
print(f"最大单笔亏损(R): {s['max_loss_r']:.2f}")
print(f"TP1命中率: {s['tp1_touch_rate']*100:.1f}%")
print("---")
print("信号多样性:")
print(f"  候选数: {report['candidate_summary']['candidates']}")
se = report['signal_entropy']
print(f"  熵(bits): {se['entropy_bits']:.2f}")
print(f"  主导占比: {se['max_pattern_share']*100:.1f}%")
print(f"  setup分布: {se['setup_counts']}")
print("---")
print("分时段表现:")
for idx, sl in enumerate(report['temporal_stability']['slices']):
    print(f"  [第{idx+1}段] 交易{sl['trades']} 胜率{sl['win_rate']*100:.1f}% PF{sl['pf']:.2f} 总R{sl['pnl']:.2f}")
print("---")
print("压缩测试(鲁棒性):")
ct = report['compression']
print(f"  加滑点+TP缩减后: 交易{ct['trades']} 胜率{ct['win_rate']*100:.1f}% PF{ct['pf']:.2f} 总R{ct['pnl']:.2f}")
print("---")
print("目标达标评估:")
tg = report['target_gap']
for k, v in tg.items():
    if isinstance(v, bool):
        print(f"  {k}: {v}")
    else:
        print(f"  {k}: {v}")

# 打印按setup/方向分组的统计 - 用ASCII字符替代Unicode
print("\n=== 按 setup_type + direction 分组表现 ===")
if not trades.empty:
    trades_copy = trades.copy()
    for (st, direction), group in trades_copy.groupby(['setup_type', 'direction']):
        pnl = group['pnl_r'].astype(float)
        wins = (pnl > 0).sum()
        total = len(group)
        pf = pnl[pnl > 0].sum() / abs(pnl[pnl < 0].sum()) if (pnl < 0).any() else (999 if wins > 0 else 0)
        avg = pnl.mean()
        print(f"  {st}_{direction}: N={total} W={wins} WR={wins/total*100:.1f}% PF={pf:.2f} SumR={pnl.sum():.2f} AvgR={avg:.4f}")
    
    # 再查看LIQUIDITY_SWEEP按RSI区间的业绩
    print("\n=== LIQUIDITY_SWEEP Long 按 RSI 分组 ===")
    ls_long = trades_copy[(trades_copy['setup_type'] == 'LIQUIDITY_SWEEP') & (trades_copy['direction'] == 'Long')]
    if len(ls_long) > 0:
        bins = [0, 25, 30, 35, 40, 50, 60, 100]
        labels = ['RSI<25', '25-30', '30-35', '35-40', '40-50', '50-60', 'RSI>60']
        ls_long['rsi_bin'] = pd.cut(ls_long['rsi'], bins=bins, labels=labels)
        for bin_label, g in ls_long.groupby('rsi_bin', observed=True):
            pnl = g['pnl_r'].astype(float)
            n = len(g)
            wins = (pnl > 0).sum()
            pf = pnl[pnl > 0].sum() / abs(pnl[pnl < 0].sum()) if (pnl < 0).any() else (999 if wins > 0 else 0)
            print(f"  {bin_label}: N={n} WR={wins/n*100:.1f}% PF={pf:.2f} SumR={pnl.sum():.2f}")
    
    print("\n=== LIQUIDITY_SWEEP Short 按 RSI 分组 ===")
    ls_short = trades_copy[(trades_copy['setup_type'] == 'LIQUIDITY_SWEEP') & (trades_copy['direction'] == 'Short')]
    if len(ls_short) > 0:
        bins = [0, 40, 50, 60, 65, 70, 75, 100]
        labels = ['RSI<40', '40-50', '50-60', '60-65', '65-70', '70-75', 'RSI>75']
        ls_short['rsi_bin'] = pd.cut(ls_short['rsi'], bins=bins, labels=labels)
        for bin_label, g in ls_short.groupby('rsi_bin', observed=True):
            pnl = g['pnl_r'].astype(float)
            n = len(g)
            wins = (pnl > 0).sum()
            pf = pnl[pnl > 0].sum() / abs(pnl[pnl < 0].sum()) if (pnl < 0).any() else (999 if wins > 0 else 0)
            print(f"  {bin_label}: N={n} WR={wins/n*100:.1f}% PF={pf:.2f} SumR={pnl.sum():.2f}")

# 检查多样性 guard 是否生效
print("\n=== 信号多样性检查 ===")
if not trades.empty:
    setup_counts = trades['setup_type'].value_counts()
    total_all = len(trades)
    for setup, cnt in setup_counts.items():
        pct = cnt / total_all * 100
        print(f"  {setup}: {cnt}笔 ({pct:.1f}%)")

# TREND_PULLBACK 按情况进一步分析（它是最大亏损来源）
print("\n=== TREND_PULLBACK 按 trend_strength 分组分析 ===")
tp_trades = trades_copy[trades_copy['setup_type'] == 'TREND_PULLBACK']
if len(tp_trades) > 0 and 'trend_strength' in tp_trades.columns:
    bins = [-10, -0.8, -0.4, 0, 0.4, 0.8, 10]
    labels_t = ['<-0.8', '-0.8~-0.4', '-0.4~0', '0~0.4', '0.4~0.8', '>0.8']
    tp_trades['ts_bin'] = pd.cut(tp_trades['trend_strength'], bins=bins, labels=labels_t)
    for bin_label, g in tp_trades.groupby('ts_bin', observed=True):
        pnl = g['pnl_r'].astype(float)
        n = len(g)
        wins = (pnl > 0).sum()
        pf = pnl[pnl > 0].sum() / abs(pnl[pnl < 0].sum()) if (pnl < 0).any() else (999 if wins > 0 else 0)
        print(f"  {bin_label}: N={n} WR={wins/n*100:.1f}% PF={pf:.2f} SumR={pnl.sum():.2f} AvgR={pnl.mean():.4f}")

# TREND_PULLBACK 按 rsi 分组
print("\n=== TREND_PULLBACK Long 按 RSI 分组 ===")
tp_long = trades_copy[(trades_copy['setup_type'] == 'TREND_PULLBACK') & (trades_copy['direction'] == 'Long')]
if len(tp_long) > 0 and 'rsi' in tp_long.columns:
    bins = [0, 30, 40, 50, 60, 70, 100]
    labels_b = ['RSI<30', '30-40', '40-50', '50-60', '60-70', 'RSI>70']
    tp_long['rsi_bin'] = pd.cut(tp_long['rsi'], bins=bins, labels=labels_b)
    for bin_label, g in tp_long.groupby('rsi_bin', observed=True):
        pnl = g['pnl_r'].astype(float)
        n = len(g)
        wins = (pnl > 0).sum()
        pf = pnl[pnl > 0].sum() / abs(pnl[pnl < 0].sum()) if (pnl < 0).any() else (999 if wins > 0 else 0)
        print(f"  {bin_label}: N={n} WR={wins/n*100:.1f}% PF={pf:.2f} SumR={pnl.sum():.2f} AvgR={pnl.mean():.4f}")

print("\n=== TREND_PULLBACK Short 按 RSI 分组 ===")
tp_short = trades_copy[(trades_copy['setup_type'] == 'TREND_PULLBACK') & (trades_copy['direction'] == 'Short')]
if len(tp_short) > 0 and 'rsi' in tp_short.columns:
    bins = [0, 30, 40, 50, 60, 70, 100]
    labels_b = ['RSI<30', '30-40', '40-50', '50-60', '60-70', 'RSI>70']
    tp_short['rsi_bin'] = pd.cut(tp_short['rsi'], bins=bins, labels=labels_b)
    for bin_label, g in tp_short.groupby('rsi_bin', observed=True):
        pnl = g['pnl_r'].astype(float)
        n = len(g)
        wins = (pnl > 0).sum()
        pf = pnl[pnl > 0].sum() / abs(pnl[pnl < 0].sum()) if (pnl < 0).any() else (999 if wins > 0 else 0)
        print(f"  {bin_label}: N={n} WR={wins/n*100:.1f}% PF={pf:.2f} SumR={pnl.sum():.2f} AvgR={pnl.mean():.4f}")
