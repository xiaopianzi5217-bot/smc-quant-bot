# -*- coding: utf-8 -*-
"""Backward-compatible import path for the institutional alpha master engine.

The implementation was moved to ``core.alpha_master_engine`` so backtest is no
longer the owner of decision logic.  Existing imports from
``backtest.v37_master_engine`` remain valid.
"""
from __future__ import annotations

from core.alpha_master_engine import *  # noqa: F401,F403
