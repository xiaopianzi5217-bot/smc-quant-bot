# -*- coding: utf-8 -*-
"""Multi-symbol out-of-sample validation helpers."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

import pandas as pd

from backtest.runner import run_backtest_from_frames, summarize_backtest


def multi_symbol_oos(
    datasets: Dict[str, Dict[str, pd.DataFrame]],
    warmup: int = 120,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Run the lightweight backtest across a dict of symbol datasets.

    datasets format:
        {"BTC/USDT": {"exec": exec_df, "macro": macro_df}}
    """
    results: List[Dict[str, Any]] = []
    trades: List[pd.DataFrame] = []
    for symbol, item in (datasets or {}).items():
        df_exec = item.get("exec") if isinstance(item, dict) else None
        df_macro = item.get("macro") if isinstance(item, dict) else None
        if df_exec is None or df_exec.empty:
            results.append({"symbol": symbol, "error": "missing exec dataframe"})
            continue
        t = run_backtest_from_frames(df_exec=df_exec, df_macro=df_macro, symbol=symbol, warmup=warmup, **kwargs)
        if t is not None and not t.empty:
            t = t.copy()
            t["symbol"] = symbol
            trades.append(t)
        results.append({"symbol": symbol, "summary": summarize_backtest(t)})
    all_trades = pd.concat(trades, ignore_index=True) if trades else pd.DataFrame()
    return {"symbols": results, "trades": all_trades, "summary": summarize_backtest(all_trades)}
