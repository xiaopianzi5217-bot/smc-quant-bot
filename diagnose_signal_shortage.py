# -*- coding: utf-8 -*-
"""
V56 信号少诊断脚本
运行: python diagnose_signal_shortage.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd
import numpy as np
from datetime import datetime

# 1. 加载数据
DATA_PATH = os.path.join("data", "BTCUSDT_15M_365d.csv")
print(f"[1/5] 加载数据: {DATA_PATH}")
df_raw = pd.read_csv(DATA_PATH)
print(f"  行数: {len(df_raw)}, 列: {list(df_raw.columns)[:8]}...")

# 数据预处理
df_raw['datetime'] = pd.to_datetime(df_raw['datetime'])
df_raw = df_raw.sort_values('datetime').reset_index(drop=True)

# 2. 加载 V56 引擎和指标
print("\n[2/5] 初始化 V56 引擎...")
from final_forge.v56_production_engine import V56Config, add_v56_indicators, load_ohlcv
cfg = V56Config()

# 添加指标
print("  添加 V56 指标...")
df = add_v56_indicators(load_ohlcv(df_raw))
print(f"  指标列数: {len(df.columns)}")

# 基础统计
print(f"  数据期间: {df['datetime'].iloc[0]} -> {df['datetime'].iloc[-1]}")
print(f"  总K线: {len(df)}, 预热: {cfg.warmup_bars}")

# 3. 手动扫描关键信号状态
print("\n[3/5] 逐K线扫描信号触发统计...")
warmup = max(cfg.warmup_bars, 260)

# 定义信号检查函数
def check_liquidity_sweep_long(i, r):
    """LIQUIDITY_SWEEP_LONG: low < ll20, close > ll20, close > open"""
    conds = [
        r['low'] < r['ll20'],
        r['close'] > r['ll20'],
        r['close'] > r['open'],
    ]
    return sum(conds), conds

def check_liquidity_sweep_short(i, r):
    """LIQUIDITY_SWEEP_SHORT: high > hh20, close < hh20, close < open"""
    conds = [
        r['high'] > r['hh20'],
        r['close'] < r['hh20'],
        r['close'] < r['open'],
    ]
    return sum(conds), conds

def check_weak_bos_long(i, r):
    """WEAK_BOS_LONG: close > hh20, body_pct > 0.45"""
    body_pct = abs(r['close'] - r['open']) / max(r['high'] - r['low'], 1e-10)
    conds = [
        r['close'] > r['hh20'],
        body_pct > 0.45,
    ]
    return sum(conds), conds

def check_weak_bos_short(i, r):
    """WEAK_BOS_SHORT: close < ll20, body_pct > 0.45"""
    body_pct = abs(r['close'] - r['open']) / max(r['high'] - r['low'], 1e-10)
    conds = [
        r['close'] < r['ll20'],
        body_pct > 0.45,
    ]
    return sum(conds), conds

def check_real_choch_long(i, r):
    """REAL_CHOCH_LONG: low < ll20, close > hh20, ema20 > ema50, rsi > 50"""
    conds = [
        r['low'] < r['ll20'],
        r['close'] > r['hh20'],
        r['ema20'] > r['ema50'],
        r['rsi'] > 50,
    ]
    return sum(conds), conds

def check_real_choch_short(i, r):
    """REAL_CHOCH_SHORT: high > hh20, close < ll20, ema20 < ema50, rsi < 50"""
    conds = [
        r['high'] > r['hh20'],
        r['close'] < r['ll20'],
        r['ema20'] < r['ema50'],
        r['rsi'] < 50,
    ]
    return sum(conds), conds

# 信号检查映射
signal_checks = {
    'LIQUIDITY_SWEEP_LONG': check_liquidity_sweep_long,
    'LIQUIDITY_SWEEP_SHORT': check_liquidity_sweep_short,
    'WEAK_BOS_LONG': check_weak_bos_long,
    'WEAK_BOS_SHORT': check_weak_bos_short,
    'REAL_CHOCH_LONG': check_real_choch_long,
    'REAL_CHOCH_SHORT': check_real_choch_short,
}

# 只统计全部满足(3/3或4/4)或差1个条件的K线
results = {}
for sig_name, check_func in signal_checks.items():
    results[sig_name] = {
        'triggered': 0,          # 全部条件满足
        'near_miss_1': 0,        # 差1个条件
        'near_miss_2': 0,        # 差2个条件
        'evaluated': 0,
        'blocked_conditions': {},  # 未通过条件的计数
        'examples_near_miss': [],
        'examples_triggered': [],
    }

# 抽样扫描: 每5根K线检查一根（加速）
step = 5
scan_count = 0

for i in range(warmup, len(df) - 1, step):
    r = df.iloc[i]
    scan_count += 1
    
    for sig_name, check_func in signal_checks.items():
        n_passed, conds = check_func(i, r)
        n_total = len(conds)
        
        stats = results[sig_name]
        stats['evaluated'] += 1
        
        # 记录未满足的条件
        if n_passed < n_total:
            cond_names = [
                ['low < ll20', 'close > ll20', 'close > open'],
                ['high > hh20', 'close < hh20', 'close < open'],
                ['close > hh20', 'body_pct > 0.45'],
                ['close < ll20', 'body_pct > 0.45'],
                ['low < ll20', 'close > hh20', 'ema20 > ema50', 'rsi > 50'],
                ['high > hh20', 'close < ll20', 'ema20 < ema50', 'rsi < 50'],
            ][list(signal_checks.keys()).index(sig_name)]
            
            for j, c in enumerate(conds):
                if not c:
                    cname = cond_names[j]
                    stats['blocked_conditions'][cname] = stats['blocked_conditions'].get(cname, 0) + 1
        
        n_failed = n_total - n_passed
        if n_passed == n_total:
            stats['triggered'] += 1
            if len(stats['examples_triggered']) < 5:
                stats['examples_triggered'].append({
                    'idx': i,
                    'datetime': str(r['datetime']),
                    'close': round(float(r['close']), 2),
                })
        elif n_failed == 1:
            stats['near_miss_1'] += 1
            if len(stats['examples_near_miss']) < 5:
                cond_names = [
                    ['low < ll20', 'close > ll20', 'close > open'],
                    ['high > hh20', 'close < hh20', 'close < open'],
                    ['close > hh20', 'body_pct > 0.45'],
                    ['close < ll20', 'body_pct > 0.45'],
                    ['low < ll20', 'close > hh20', 'ema20 > ema50', 'rsi > 50'],
                    ['high > hh20', 'close < ll20', 'ema20 < ema50', 'rsi < 50'],
                ][list(signal_checks.keys()).index(sig_name)]
                failed = [cond_names[j] for j, c in enumerate(conds) if not c]
                stats['examples_near_miss'].append({
                    'idx': i,
                    'datetime': str(r['datetime']),
                    'close': round(float(r['close']), 2),
                    'failed': failed,
                })
        elif n_failed == 2:
            stats['near_miss_2'] += 1

print(f"  扫描了 {scan_count} 根K线 (间隔 {step} 根)")

# 4. 输出详细报告
print("\n" + "=" * 80)
print("V56 信号少诊断报告")
print("=" * 80)

print(f"\n数据集: {len(df)} 根K线, 预热 {warmup}, 扫描 {scan_count} 根 (抽样 {step}x)")
print(f"期间: {df['datetime'].iloc[warmup]} → {df['datetime'].iloc[-1]}")

print("\n" + "-" * 100)
print(f"{'信号类型':<25} {'触发':<6} {'差1':<6} {'差2':<6} {'触发率':<9} {'差1率':<9} {'差2率':<9} {'主要阻塞'}")
print("-" * 100)

total_triggered = 0
total_near1 = 0
total_near2 = 0

for sig_name, stats in results.items():
    ev = stats['evaluated']
    if ev == 0:
        continue
    trig_rate = stats['triggered'] / ev * 100
    n1_rate = stats['near_miss_1'] / ev * 100
    n2_rate = stats['near_miss_2'] / ev * 100
    
    total_triggered += stats['triggered']
    total_near1 += stats['near_miss_1']
    total_near2 += stats['near_miss_2']
    
    # 主要阻塞条件
    if stats['blocked_conditions']:
        top_block = max(stats['blocked_conditions'].items(), key=lambda x: x[1])[0]
    else:
        top_block = "-"
    
    print(f"{sig_name:<25} {stats['triggered']:<6} {stats['near_miss_1']:<6} {stats['near_miss_2']:<6} "
          f"{trig_rate:<9.4f}% {n1_rate:<9.4f}% {n2_rate:<9.4f}% {top_block}")

print("-" * 100)
print(f"{'合计':<25} {total_triggered:<6} {total_near1:<6} {total_near2:<6}")

print("\n" + "=" * 80)
print("瓶颈分析: 哪个条件失败次数最多?")
print("=" * 80)
all_blockers = {}
for sig_name, stats in results.items():
    for cond, count in stats['blocked_conditions'].items():
        all_blockers[f"{sig_name} → {cond}"] = all_blockers.get(f"{sig_name} → {cond}", 0) + count

sorted_blockers = sorted(all_blockers.items(), key=lambda x: x[1], reverse=True)
print(f"\n{'条件':<50} {'失败次数':<10} {'失败率':<10}")
print("-" * 70)
for cond, count in sorted_blockers[:20]:
    # 计算该信号的总评估数
    sig_part = cond.split(' → ')[0]
    total_ev = results[sig_part]['evaluated']
    rate = count / total_ev * 100 if total_ev > 0 else 0
    print(f"{cond:<50} {count:<10} {rate:<10.2f}%")

# 5. 结论建议
print("\n" + "=" * 80)
print("放宽建议")
print("=" * 80)

print("\n若放宽以下条件（只需满足N-1个条件即可触发）：")
if total_near1 > 0:
    print(f"  💡 差1个条件即可触发的信号: {total_near1} 个（当前触发 {total_triggered} 个）")
    print(f"     放宽后预计信号增加: {total_near1} 个, 总信号: {total_triggered + total_near1}")
if total_near2 > 0:
    print(f"  💡 差2个条件即可触发的信号: {total_near2} 个")
    print(f"     放宽2个条件后: 总信号: {total_triggered + total_near1 + total_near2}")

# 最值得放宽的条件
print("\n🔥 最值得放宽的条件 (按跨信号影响排序):")
from collections import Counter
cross_signal_impact = Counter()
for sig_name, stats in results.items():
    for cond, count in stats['blocked_conditions'].items():
        cross_signal_impact[cond] += count

for cond, count in cross_signal_impact.most_common(10):
    print(f"  {cond}: {count} 次")

print("\n✅ 诊断完成")

# 保存结果
report_path = os.path.join("reports", "signal_shortage_diagnosis.json")
os.makedirs("reports", exist_ok=True)
import json
summary = {
    'dataset': f"{len(df)} bars, {df['datetime'].iloc[0]} to {df['datetime'].iloc[-1]}",
    'scan_count': scan_count,
    'total_triggered': total_triggered,
    'total_near_miss_1': total_near1,
    'total_near_miss_2': total_near2,
    'per_signal': {k: {kk: vv for kk, vv in v.items() if kk not in ('examples_near_miss', 'examples_triggered')} for k, v in results.items()},
    'bottlenecks': [{'condition': k, 'count': v} for k, v in sorted_blockers[:20]],
}
with open(report_path, 'w', encoding='utf-8') as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)
print(f"\n报告已保存: {report_path}")