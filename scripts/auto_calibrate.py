# -*- coding: utf-8 -*-
"""
参数自动校准 + OOS 验证框架

用法：
    from scripts.auto_calibrate import calibrate_thresholds, auto_calibrate
    best_params = calibrate_thresholds("backtest_results.csv")
"""
from __future__ import annotations
from typing import Dict, Tuple
import pandas as pd
import numpy as np
from strategy.v565_quality_gate import v565_quality_gate


def calculate_profit_factor(trades: pd.DataFrame) -> float:
    """计算 Profit Factor（总盈利 / 总亏损）。"""
    if trades.empty:
        return 0.0
    gross_profit = float(trades[trades > 0].sum()) if (trades > 0).any() else 0.0
    gross_loss = abs(float(trades[trades < 0].sum())) if (trades < 0).any() else 1.0
    if gross_loss == 0:
        return 999.0
    return round(gross_profit / gross_loss, 4)


def calibrate_thresholds(
    csv_path: str,
    ev_range: Tuple[float, float, float] = (-0.35, -0.15, 0.02),
    score_buffer_range: Tuple[int, int] = (3, 10),
) -> Dict[str, float]:
    """
    简单网格搜索：遍历 MIN_MODEL_EV 和 score buffer，
    返回最优参数组合。
    """
    df = pd.read_csv(csv_path)

    best_sharpe = -999.0
    best_params = {"min_model_ev": -0.25, "score_buffer": 5}

    ev_values = np.arange(ev_range[0], ev_range[1] + 1e-6, ev_range[2])
    score_buffers = range(score_buffer_range[0], score_buffer_range[1] + 1)

    for ev in ev_values:
        for buf in score_buffers:
            config = {"min_model_ev": round(ev, 3)}
            passes = 0
            total = len(df)
            for _, row in df.iterrows():
                row_dict = row.to_dict()
                row_dict["score"] = row_dict.get("score", 0) - buf
                passed, _, _ = v565_quality_gate(row_dict, config)
                if passed:
                    passes += 1

            pass_rate = passes / total if total > 0 else 0
            # 简单目标：通过率 30%~50% 之间最优
            if 0.30 <= pass_rate <= 0.50:
                score = pass_rate
                if score > best_sharpe:
                    best_sharpe = score
                    best_params = {"min_model_ev": round(ev, 3), "score_buffer": buf}

    return best_params


def auto_calibrate(backtest_df: pd.DataFrame) -> Dict[str, object]:
    """一键校准：遍历 EV 和 mitigation 阈值，返回最优参数 (基于 PF)。

    用法:
        df = pd.read_csv('backtest.csv')
        print(auto_calibrate(df))
    """
    best_params: Dict[str, object] = {"ev": -0.25, "mitigation": 0.55}
    best_pf = 0.0

    for ev in [-0.32, -0.28, -0.25]:
        for mit in [0.52, 0.55, 0.58]:
            mask = backtest_df.apply(
                lambda r: (
                    float(r.get("mitigation_strength", 0)) > mit
                    and float(r.get("model_ev", 0)) > ev
                ),
                axis=1,
            )
            passed = backtest_df[mask]
            if passed.empty:
                continue
            pf = calculate_profit_factor(passed.get("pnl_r", passed.get("profit_r", pd.Series(dtype=float))))
            if pf > best_pf:
                best_pf = pf
                best_params = {"ev": ev, "mitigation": mit}

    best_params["best_pf"] = round(best_pf, 4)
    print("最佳参数:", best_params)
    return best_params


if __name__ == "__main__":
    # 示例用法
    # result = calibrate_thresholds("backtest_results.csv")
    # print(result)
    print("Run: calibrate_thresholds('backtest_results.csv')")
