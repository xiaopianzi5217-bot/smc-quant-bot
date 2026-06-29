# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
from pathlib import Path
import pandas as pd

from .avs_engine import AlphaValidationEngine


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Alpha Validity Score validation on a trade CSV.")
    parser.add_argument("--trades", required=True, help="Path to backtest trade CSV")
    parser.add_argument("--out-dir", default="outputs", help="Output directory for AVS reports")
    parser.add_argument("--prefix", default="avs", help="Output file prefix")
    args = parser.parse_args()

    df = pd.read_csv(args.trades)
    engine = AlphaValidationEngine(df)
    report = engine.run_full_assessment()
    paths = engine.save_report(args.out_dir, args.prefix)
    print("===== ALPHA VALIDITY SCORE =====")
    print(json.dumps({
        "avs_score": report.get("avs_score"),
        "overfit_score": report.get("overfit_score"),
        "verdict": report.get("verdict"),
        "true_edge_regimes": report.get("true_edge_regimes", [])[:5],
        "fake_clusters": report.get("fake_clusters", [])[:10],
        "saved_paths": paths,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
