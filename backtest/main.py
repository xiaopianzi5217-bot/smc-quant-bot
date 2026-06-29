# -*- coding: utf-8 -*-
"""
Compatibility entrypoint for backtest.main.

V56.5 已取代旧 V37/V38 回测系统。本入口直接走 V56_5_Engine。
旧 runner/decision_kernel/alpha_master 依赖链不再需要。
"""
from __future__ import annotations

import argparse
import sys
import os
from pathlib import Path
import importlib.util

# 锁定项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 暴力加载模块函数
def load_module_from_path(module_name, file_path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None:
        raise ImportError(f"无法找到指定文件: {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module

# V56.5: 直接使用稳定引擎，不依赖旧 runner
from final_forge.v56_5_stable_engine import (
    V56_5_Engine,
    V565Config,
    load_ohlcv,
    add_v56_indicators,
    summarize_v565,
)
    
VERSION = "V56_5_ENGINE_STABLE_20260623"


def run_backtest(
    exec_csv: str,
    macro_csv: str | None = None,
    symbol: str = "BTC/USDT",
    warmup: int = 260,
    max_rows: int | None = None,
    **kwargs,
) -> Path:
    """全量回测入口。只走 V56.5 唯一回测路径。"""
    cfg = V565Config(
        min_score=float(kwargs.get("min_score", 65.0)),
        tp1_r=float(kwargs.get("tp1_r", 1.0)),
        tp2_r=float(kwargs.get("tp2_r", 1.8)),
        tp3_r=float(kwargs.get("tp3_r", 2.8)),
        max_hold_bars=int(kwargs.get("max_hold_bars", 36)),
    )
    raw = add_v56_indicators(load_ohlcv(exec_csv))
    if max_rows and int(max_rows) > 0 and int(max_rows) < len(raw):
        raw = raw.tail(int(max_rows) + 580).reset_index(drop=True)

    engine = V56_5_Engine(cfg)
    trades = engine.select_trades(engine.generate_candidates(raw))

    # 收集分桶并二次回测（路径 A：历史分桶 EV）
    buckets = engine.extract_buckets_from_trades(trades)
    engine.load_history_buckets(buckets)
    trades = engine.select_trades(engine.generate_candidates(raw))

    out_path = Path(kwargs.get("out", PROJECT_ROOT / "data" / "backtest_v56_5.csv"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    trades.to_csv(out_path, index=False)
    return out_path


def summarize_backtest(trades_path: str | Path) -> dict:
    """从 CSV 文件加载交易并打印摘要。"""
    import pandas as pd
    trades = pd.read_csv(trades_path)
    return summarize_v565(trades)


def main() -> None:
    parser = argparse.ArgumentParser(description=f"SMC Bot backtest entrypoint ({VERSION})")
    parser.add_argument("--exec-csv", required=True, help="Execution timeframe OHLCV CSV, e.g. 15m")
    parser.add_argument("--macro-csv", default=None, help="(已弃用) V56.5 不使用宏过滤")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--out", default=str(PROJECT_ROOT / "data" / "backtest_v56_5.csv"))
    parser.add_argument("--warmup", type=int, default=260)
    parser.add_argument("--max-rows", type=int, default=None)
    parser.add_argument("--min-score", type=float, default=65.0)
    args = parser.parse_args()

    trades_path = run_backtest(
        args.exec_csv,
        args.macro_csv,
        args.symbol,
        warmup=args.warmup,
        max_rows=args.max_rows,
        min_score=args.min_score,
    )
    print(f"\n[系统] 回测数据已保存: {trades_path}")
    print("\n===== 回测摘要 (V56.5) =====")
    print(summarize_backtest(trades_path))

if __name__ == "__main__":
    main()