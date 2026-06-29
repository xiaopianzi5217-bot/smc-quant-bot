# -*- coding: utf-8 -*-
import itertools
import pandas as pd
from analytics.report import load_journal, parse_context_column, closed_trades, summarize_closed_trades


def journal_filter_optimization(
    journal_path: str,
    min_score_values=(3, 4, 5, 6, 7),
    allowed_regimes=("mud", "transition", "trend"),
    allowed_volatility=("low", "normal", "high"),
):
    """
    基于 journal 的轻量参数优化。

    注意：
    这不是完整逐K回测，而是“交易日志再过滤”：
    假设历史开过的单作为候选样本，测试不同过滤规则下表现。
    用途：快速发现什么市场状态应该过滤掉。
    """
    df = parse_context_column(load_journal(journal_path))
    closes = closed_trades(df)
    if closes.empty:
        return pd.DataFrame()

    # score 可能在 CLOSE 行为空，因为真正 score 在 OPEN 行。
    # 简化处理：如果 CLOSE 行没有 score，则只做 regime / volatility 过滤。
    closes["score"] = pd.to_numeric(closes.get("score", 0), errors="coerce").fillna(0)

    rows = []
    for min_score, regime_subset, vol_subset in itertools.product(
        min_score_values,
        _non_empty_subsets(allowed_regimes),
        _non_empty_subsets(allowed_volatility),
    ):
        sample = closes[
            closes["regime"].fillna("").isin(regime_subset)
            & closes["volatility"].fillna("").isin(vol_subset)
        ].copy()

        if "score" in sample.columns and sample["score"].max() > 0:
            sample = sample[sample["score"] >= min_score]

        stats = summarize_closed_trades(sample)
        rows.append({
            "min_score": min_score,
            "regimes": ",".join(regime_subset),
            "volatility": ",".join(vol_subset),
            **stats,
        })

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out = out[out["trades"] >= 3].copy()
    return out.sort_values(["total_r", "profit_factor", "avg_r"], ascending=False)


def _non_empty_subsets(values):
    values = tuple(values)
    for r in range(1, len(values) + 1):
        for combo in itertools.combinations(values, r):
            yield combo
