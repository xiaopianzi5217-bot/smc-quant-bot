# -*- coding: utf-8 -*-
"""Standalone AVS runner.

Example:
    python scripts/run_alpha_validation.py --trades data/backtest_v39_full.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alpha_validator.cli import main as avs_main


if __name__ == "__main__":
    avs_main()
