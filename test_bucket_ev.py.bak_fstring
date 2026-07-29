# -*- coding: utf-8 -*-
"""测试 V56_5_Engine 加载回测 bucket_ev 是否生效"""
import sys, json
from pathlib import Path
sys.path.insert(0, ".")

from final_forge.v56_5_stable_engine import V56_5_Engine, V565Config, load_ohlcv, add_v56_indicators
import pandas as pd

cfg = V565Config(min_score=55.0, allowed_hours=tuple(range(24)))
engine = V56_5_Engine(cfg)

bucket_path = Path("data/v56_5_bucket_ev.json")
if bucket_path.exists():
    buckets = json.loads(bucket_path.read_text(encoding="utf-8"))
    engine.load_history_buckets(buckets)
    print(f"bucket_ev 加载成功: {len(buckets)} 个分桶")
    for k, v in sorted(buckets.items()):
        if v["trades"] >= 5:
            print(f"  {k}: trades={v['trades']} wr={v['win_rate']:.2f} bucket_ev={v['bucket_ev']:.4f}")
else:
    print("bucket_ev 文件不存在!")

# 测试 generate_candidates
df_raw = pd.read_csv("data/BTCUSDT_15M_365d.csv", nrows=500)
df_v56 = add_v56_indicators(load_ohlcv(df_raw))
candidates = engine.generate_candidates(df_v56)
print(f"\n候选信号: {len(candidates)}")
if len(candidates) > 0:
    print(f"  score 范围: {candidates['score'].min():.1f} ~ {candidates['score'].max():.1f}")
    if "bucket_ev" in candidates.columns:
        diff = (candidates["bucket_ev"] != candidates["model_ev"]).sum()
        print(f"  bucket_ev 不同(生效): {diff}/{len(candidates)}")
        if diff > 0:
            print(f"  bucket_ev 前5: {candidates['bucket_ev'].head(5).tolist()}")
            print(f"  model_ev 前5:  {candidates['model_ev'].head(5).tolist()}")

# 测试 select_trades
trades = engine.select_trades(candidates)
print(f"\n选择后交易: {len(trades) if trades is not None else 0}")
if trades is not None and len(trades) > 0:
    print(f"  前3笔方向: {trades['direction'].head(3).tolist()}")
    print(f"  前3笔score: {trades['score'].head(3).tolist()}")

print("\n✅ 测试完成")
