# -*- coding: utf-8 -*-
"""一次性的 V56.5 回测 + 数据导出"""

import json
import time
from pathlib import Path

from final_forge.v56_5_stable_engine import V565Config, run_v565_stable_backtest, V56_5_Engine, enrich_v565_candidates, select_v565_portfolio, execute_v565, add_v56_indicators, load_ohlcv, generate_v56_candidates

t0 = time.time()

cfg = V565Config(
    min_score=55.0,
    allowed_hours=tuple(range(24)),
)

print("=" * 60)
print("V56.5 回测启动...")
print("=" * 60)

trades, report = run_v565_stable_backtest("data/BTCUSDT_15M_365d.csv", output_dir="data", config=cfg)
elapsed = time.time() - t0

print(f"\n===== 回测完成 ({elapsed:.1f}s) =====")
print(f"总交易数: {report['overall']['trades']}")
print(f"胜率: {report['overall']['win_rate']*100:.1f}%")
print(f"PF: {report['overall']['pf']:.2f}")
print(f"总PnL(R): {report['overall']['pnl']:.2f}")
print(f"平均R: {report['overall']['avg_r']:.4f}")
print(f"最大回撤R: {report['overall']['max_dd_r']:.2f}")
print(f"TP1接触率: {report['overall']['tp1_touch_rate']*100:.1f}%")
print(f"TP2接触率: {report['overall']['tp2_touch_rate']*100:.1f}%")
print(f"TP3接触率: {report['overall']['tp3_touch_rate']*100:.1f}%")

# 打印前 20 笔交易看方向分布
print("\n===== 前 20 笔交易方向分布 =====")
for i, (_, row) in enumerate(trades.head(20).iterrows()):
    print(f"  {i+1}. {row['direction']:>5s} | score={row['score']:.1f} | ev={row['expected_value']:.4f} | pnl_r={row['pnl_r']:+.2f} | exit={row['exit_reason']}")

# 方向统计
dir_counts = trades["direction"].value_counts()
print(f"\n===== 方向统计 =====")
for direction, count in dir_counts.items():
    subset = trades[trades["direction"] == direction]
    wins = (subset["pnl_r"] > 0).sum()
    total = len(subset)
    print(f"  {direction:>5s}: {total} 笔 | 胜率={wins/total*100:.1f}%")

# 提取 bucket EV 用于 FeedbackLoop 预热
print("\n===== 提取 bucket_ev (按 regime x score_bucket) =====")
engine = V56_5_Engine(cfg)
buckets = engine.extract_buckets_from_trades(trades)
print(json.dumps({k: v for k, v in buckets.items()}, ensure_ascii=False, indent=2))

# 保存 bucket_ev 到配置文件
bucket_path = Path("data/v56_5_bucket_ev.json")
bucket_path.write_text(json.dumps(buckets, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nbucket_ev 已保存到 {bucket_path}")

# 打印目标差距
print(f"\n===== 目标差距检查 =====")
tg = report.get("target_gap", {})
for k, v in tg.items():
    print(f"  {k}: {v}")

print(f"\n总计耗时: {elapsed:.1f}s")
print("Done!")
