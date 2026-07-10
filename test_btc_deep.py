# -*- coding: utf-8 -*-
"""深度排查 BTC 信号断点"""
import asyncio
import sys
import pandas as pd
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.absolute()))

from hf_auto_trader import fetch_ohlcv, STRATEGY_PARAMS
from indicators.basic import add_all_indicators
from strategy.smc import build_exec_context
from final_forge.v56_5_stable_engine import (
    V565Config,
    generate_v56_candidates,
    enrich_v565_candidates,
    select_v565_portfolio,
    execute_v565,
    add_v56_indicators,
    load_ohlcv,
)
from strategy.v565_quality_gate import v565_quality_gate

async def test():
    print("=== 1. 获取数据 ===")
    df = await fetch_ohlcv("BTC/USDT", "15m", 320)
    if df is None or len(df) < 100:
        print("数据不足")
        return
    print(f"数据: {len(df)} 条, 最新价: {df['close'].iloc[-1]:.2f}")

    print()
    print("=== 2. V56 指标计算 ===")
    _loose_cfg = V565Config(min_score=55.0)
    df_v56 = add_v56_indicators(load_ohlcv(df))
    if df_v56 is None or len(df_v56) < 260:
        print(f"V56 指标后数据不足: {len(df_v56) if df_v56 is not None else 'None'}")
        return
    print(f"V56 指标后: {len(df_v56)} 条")

    print()
    print("=== 3. 生成候选信号 ===")
    broad = generate_v56_candidates(df_v56, None)
    if broad is None or broad.empty:
        print("无候选信号")
        return
    print(f"候选信号: {len(broad)} 条")
    print(f"列名: {list(broad.columns)}")
    if 'direction' in broad.columns:
        print(f"方向分布: {broad['direction'].value_counts().to_dict()}")
    if 'score' in broad.columns:
        print(f"Score范围: {broad['score'].min():.1f} ~ {broad['score'].max():.1f}")
    if 'model_ev' in broad.columns:
        print(f"EV范围: {broad['model_ev'].min():.4f} ~ {broad['model_ev'].max():.4f}")

    print()
    print("=== 4. enrich + Quality Gate ===")
    df_exec = add_all_indicators(df, STRATEGY_PARAMS["wvf_std_mult"])
    exec_ctx = build_exec_context(df_exec)
    
    enriched = enrich_v565_candidates(broad, _loose_cfg)
    print(f"Enrich后: {len(enriched)} 条")
    
    gate_passed = []
    gate_reasons = []
    for idx, row in enriched.iterrows():
        passed, reason, meta = v565_quality_gate(row.to_dict())
        gate_passed.append(passed)
        gate_reasons.append(reason)
    enriched["gate_passed"] = gate_passed
    enriched["gate_reason"] = gate_reasons
    
    print(f"Quality Gate 通过: {enriched['gate_passed'].sum()} / {len(enriched)}")
    if not enriched["gate_passed"].any():
        print("全部被拦截!")
        print(f"拦截原因分布: {pd.Series(gate_reasons).value_counts().to_dict()}")
        return
    
    enriched = enriched[enriched["gate_passed"]].copy()
    print(f"通过 Gate 后: {len(enriched)} 条")

    print()
    print("=== 5. Top-N 选择 ===")
    selected = select_v565_portfolio(enriched, _loose_cfg)
    if selected is None or selected.empty:
        print("Top-N 选择后无信号!")
        # 看看 enriched 的内容
        print(f"通过 Gate 的信号 score: {enriched['score'].tolist() if 'score' in enriched.columns else 'N/A'}")
        print(f"通过 Gate 的信号 direction: {enriched['direction'].tolist() if 'direction' in enriched.columns else 'N/A'}")
        return
    print(f"Selected: {len(selected)} 条")
    print(f"选中信号: {selected[['direction','score','setup_type']].to_dict('records') if 'setup_type' in selected.columns else selected.head()}")

    print()
    print("=== 6. 执行交易 ===")
    trades = execute_v565(df_v56, selected, _loose_cfg)
    if trades is None or trades.empty:
        print("执行后无交易")
        return
    print(f"交易: {len(trades)} 条")
    best = trades.sort_values("score", ascending=False).iloc[0]
    print(f"最佳: {best.get('direction')} score={best.get('score'):.1f} ev={best.get('model_ev'):.4f}")

if __name__ == "__main__":
    asyncio.run(test())
