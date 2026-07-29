# -*- coding: utf-8 -*-
from typing import Any, Dict, List, Optional

import pandas as pd

from backtest.runner import (
    load_ohlcv_csv,
    run_backtest_from_frames,
    summarize_backtest,
)


def _slice_pair(
    df_exec: pd.DataFrame,
    df_macro: pd.DataFrame,
    start: int,
    end: int,
):
    exec_part = df_exec.iloc[start:end].copy().reset_index(drop=True)

    if len(exec_part) == 0:
        return exec_part, df_macro.iloc[0:0].copy()

    start_time = exec_part["datetime"].min()
    end_time = exec_part["datetime"].max()

    macro_part = df_macro[
        (df_macro["datetime"] >= start_time) &
        (df_macro["datetime"] <= end_time)
    ].copy().reset_index(drop=True)

    if len(macro_part) < 50:
        macro_part = df_macro.copy().reset_index(drop=True)

    return exec_part, macro_part


def walk_forward_validate(
    exec_csv: str,
    macro_csv: str,
    symbol: str = "BTC/USDT:USDT",
    train_rows: int = 1800,
    test_rows: int = 600,
    step_rows: Optional[int] = None,
    warmup: int = 120,
    fee_bps: float = 6.0,
    slippage_bps: float = 2.0,
    funding_rate_pct: float = 0.0,
    funding_extreme_pct: float = 0.01,
    enable_funding_filter: bool = True,
    block_adverse_funding: bool = True,
    max_hold_bars: int = 96,
) -> Dict[str, Any]:
    """
    Walk-forward：
    - 前 train_rows 作为训练窗口
    - 后 test_rows 作为样本外测试窗口
    - 这里先不做复杂参数优化，先验证固定策略在滚动样本外是否稳定
    """
    step_rows = step_rows or test_rows

    df_exec = load_ohlcv_csv(exec_csv)
    df_macro = load_ohlcv_csv(macro_csv)

    folds = []
    all_oos_trades = []

    start = 0
    fold_id = 1

    while True:
        train_start = start
        train_end = train_start + train_rows
        test_start = train_end
        test_end = test_start + test_rows

        if test_end > len(df_exec):
            break

        train_exec, train_macro = _slice_pair(df_exec, df_macro, train_start, train_end)
        test_exec, test_macro = _slice_pair(df_exec, df_macro, test_start, test_end)

        train_trades = run_backtest_from_frames(
            df_exec=train_exec,
            df_macro=train_macro,
            symbol=symbol,
            warmup=warmup,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            funding_rate_pct=funding_rate_pct,
            funding_extreme_pct=funding_extreme_pct,
            enable_funding_filter=enable_funding_filter,
            block_adverse_funding=block_adverse_funding,
            max_hold_bars=max_hold_bars,
        )

        test_trades = run_backtest_from_frames(
            df_exec=test_exec,
            df_macro=test_macro,
            symbol=symbol,
            warmup=warmup,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            funding_rate_pct=funding_rate_pct,
            funding_extreme_pct=funding_extreme_pct,
            enable_funding_filter=enable_funding_filter,
            block_adverse_funding=block_adverse_funding,
            max_hold_bars=max_hold_bars,
        )

        train_summary = summarize_backtest(train_trades)
        test_summary = summarize_backtest(test_trades)

        fold = {
            "fold": fold_id,
            "train_start_i": train_start,
            "train_end_i": train_end,
            "test_start_i": test_start,
            "test_end_i": test_end,
            "train_summary": train_summary,
            "test_summary": test_summary,
        }

        folds.append(fold)

        if test_trades is not None and len(test_trades) > 0:
            test_trades = test_trades.copy()
            test_trades["fold"] = fold_id
            all_oos_trades.append(test_trades)

        fold_id += 1
        start += step_rows

    if all_oos_trades:
        oos_trades = pd.concat(all_oos_trades, ignore_index=True)
    else:
        oos_trades = pd.DataFrame()

    return {
        "symbol": symbol,
        "folds": folds,
        "oos_trades": oos_trades,
        "oos_summary": summarize_backtest(oos_trades),
    }


def walk_forward_report_to_frames(result: Dict[str, Any]):
    fold_rows = []

    for f in result.get("folds", []):
        ts = f.get("test_summary", {})
        tr = f.get("train_summary", {})

        fold_rows.append({
            "fold": f.get("fold"),
            "train_trades": tr.get("trades"),
            "train_win_rate": tr.get("win_rate"),
            "train_net_total_pct": tr.get("net_total_pct"),
            "train_max_drawdown_pct": tr.get("max_drawdown_pct"),
            "test_trades": ts.get("trades"),
            "test_win_rate": ts.get("win_rate"),
            "test_net_total_pct": ts.get("net_total_pct"),
            "test_max_drawdown_pct": ts.get("max_drawdown_pct"),
            "test_profit_factor": ts.get("profit_factor"),
        })

    fold_df = pd.DataFrame(fold_rows)
    oos_trades = result.get("oos_trades", pd.DataFrame())

    return fold_df, oos_trades