# -*- coding: utf-8 -*-
"""
V56.5 Stable Production Backtest Runner

鍞竴鍥炴祴鍏ュ彛锛岀洿鎺ヨ皟鐢?final_forge.v56_5_stable_engine銆?鎵€鏈夋棫 V37/V38 鍥炴祴璺緞宸插交搴曠Щ闄ゃ€?"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from utils.safe import safe_float, safe_bool, safe_str

from final_forge.v56_5_stable_engine import (
    V565Config,
    V56_5_Engine,
    load_ohlcv,
    add_v56_indicators,
    summarize_v565,
)

VERSION = "V56_5_RUNNER_20260629"


def run_backtest(
    exec_csv: Any,
    macro_csv: Optional[Any] = None,
    symbol: str = "BTC/USDT",
    warmup: int = 120,
    max_rows: Optional[int] = None,
    min_rr: float = 1.3,
    base_max_hold_bars: int = 96,
    mitigation_required: bool = True,
    fee_bps: float = 6.0,
    slippage_bps: float = 10.0,
    allow_trend_no_structure: bool = False,
    save_reject_audit: bool = True,
    reject_audit_path: str = "reject_audit_v56.csv",
    missed_trade_audit_path: str = "missed_trade_audit_v56.csv",
    target_profile: bool = True,
    target_profit_cap_r: float | None = None,
    **kwargs: Any,
) -> pd.DataFrame:
    cfg = V565Config(
        min_score=float(kwargs.get("min_score", 65.0)),
        tp1_r=float(kwargs.get("tp1_r", 1.0)),
        tp2_r=float(kwargs.get("tp2_r", 1.8)),
        tp3_r=float(kwargs.get("tp3_r", 2.8)),
        max_hold_bars=int(kwargs.get("max_hold_bars", 36)),
    )
    df = add_v56_indicators(load_ohlcv(exec_csv))
    if not df["datetime"].is_monotonic_increasing:
        df = df.sort_values("datetime").reset_index(drop=True)
    if max_rows and int(max_rows) > 0 and int(max_rows) < len(df):
        df = df.tail(int(max_rows) + 580).reset_index(drop=True)

    engine = V56_5_Engine(cfg)
    candidates = engine.generate_candidates(df)
    trades = engine.select_trades(candidates)

    out_path = Path(kwargs.get("out", "data/backtest_v56_5.csv"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    trades.to_csv(out_path, index=False)
    return trades.reset_index(drop=True)


def summarize_backtest(trades: pd.DataFrame) -> Dict[str, Any]:
    return summarize_v565(trades)




