# -*- coding: utf-8 -*-
"""Compatibility wrapper for the removed V4.3 hard-prune runner.

The old file accidentally contained a GitHub Actions YAML body, which broke
syntax checks and imports.  V38 keeps the public module path alive while routing
calls to the current decoupled scorecard runner.
"""
from __future__ import annotations

from backtest.runner import run_backtest, summarize_backtest, stress_test, load_ohlcv_csv, add_basic_indicators

VERSION = "V4_3_COMPAT_WRAPPER_TO_V38_DECOUPLED_SCORECARD"

__all__ = [
    "VERSION",
    "run_backtest",
    "summarize_backtest",
    "stress_test",
    "load_ohlcv_csv",
    "add_basic_indicators",
]
