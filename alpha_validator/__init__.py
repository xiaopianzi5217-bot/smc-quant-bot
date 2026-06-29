# -*- coding: utf-8 -*-
"""Alpha validation package.

This package is deliberately side-effect free so it can be imported by the
backtest runner, report builder, Streamlit UI, or standalone scripts.
"""
from .avs_engine import AlphaValidationEngine, run_alpha_validation

__all__ = ["AlphaValidationEngine", "run_alpha_validation"]
