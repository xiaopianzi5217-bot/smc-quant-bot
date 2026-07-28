# -*- coding: utf-8 -*-
"""
Walk-Forward 滚动回测脚本（基于 V56.5 稳定引擎）

用法：
    python -m backtest.walk_forward --exec-csv data/ohlcv_15m.csv --output-dir data/walk_forward/

每滑动 1 个月，用前 6 个月训练期分桶，在后 1 个月测试期执行回测。
输出：月度 OOS 期望值、最大回撤、胜率表 (CSV + JSON)。
"""
from __future__ import annotations

import argparse
import sys
import os
import json
from pathlib import Path
from datetime import datetime
from collections import OrderedDict

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from final_forge.v56_5_stable_engine import (
    V56_5_Engine,
    V565Config,
    load_ohlcv,
    add_v56_indicators,
    summarize_v565,
)


def _month_start(dt):
    return pd.Timestamp(year=dt.year, month=dt.month, day=1, tz=dt.tz)


def _next_month_start(dt):
    m = dt.month + 1
    y = dt.year + (m - 1) // 12
    m = ((m - 1) % 12) + 1
    return pd.Timestamp(year=y, month=m, day=1, tz=dt.tz)


def _months_ago(dt, n):
    total = dt.year * 12 + (dt.month - 1) - n
    y = total // 12
    m = (total % 12) + 1
    return pd.Timestamp(year=y, month=m, day=1, tz=dt.tz)


def _compute_drawdown(trades):
    if trades.empty or 'pnl_r' not in trades.columns:
        return 0.0
    cum = trades['pnl_r'].fillna(0).cumsum()
    if len(cum) < 2:
        return 0.0
    dd = cum.expanding().max() - cum
    return float(dd.max())


def _run_oos_period(engine, oos_data, out_csv=None):
    if oos_data.empty or len(oos_data) < 100:
        return None
    trades = engine.select_trades(engine.generate_candidates(oos_data))
    if trades.empty:
        return None
    total = len(trades)
    wins = trades[trades['pnl_r'] > 0] if 'pnl_r' in trades.columns else pd.DataFrame()
    losses = trades[trades['pnl_r'] <= 0] if 'pnl_r' in trades.columns else pd.DataFrame()
    wr = len(wins) / total if total > 0 else 0.0
    aw = wins['pnl_r'].mean() if not wins.empty else 0.0
    al = losses['pnl_r'].mean() if not losses.empty else 0.0
    tr = trades['pnl_r'].sum() if 'pnl_r' in trades.columns else 0.0
    ae = trades['pnl_r'].mean() if 'pnl_r' in trades.columns else 0.0
    dd = _compute_drawdown(trades)
    pf = abs(aw * len(wins) / (al * len(losses))) if al * len(losses) != 0 else float('inf')
    result = OrderedDict([
        ('oos_month', ''),
        ('total_trades', total),
        ('win_rate', round(wr, 4)),
        ('avg_win_r', round(aw, 4)),
        ('avg_loss_r', round(al, 4)),
        ('avg_ev_r', round(ae, 4)),
        ('total_return_r', round(tr, 4)),
        ('max_drawdown_r', round(dd, 4)),
        ('profit_factor', round(pf, 4)),
    ])
    if out_csv is not None:
        trades.to_csv(out_csv, index=False)
        result['out_csv'] = str(out_csv)
    return result


def run_walk_forward(exec_csv, output_dir='data/walk_forward/', warmup=260,
                     train_months=6, test_months=1, min_score=65.0,
                     tp1_r=1.0, tp2_r=1.8, tp3_r=2.8, max_hold_bars=36,
                     max_rows=None):
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    print(f'[WalkForward] Loading: {exec_csv}')
    raw = add_v56_indicators(load_ohlcv(exec_csv))
    if max_rows and 0 < max_rows < len(raw):
        raw = raw.tail(max_rows + 580).reset_index(drop=True)
    if 'timestamp' not in raw.columns and isinstance(raw.index, pd.DatetimeIndex):
        raw = raw.reset_index().rename(columns={'index': 'timestamp'})
    if 'timestamp' in raw.columns:
        ts_col = pd.to_datetime(raw['timestamp'])
    else:
        ts_col = pd.date_range(start='2020-01-01', periods=len(raw), freq='15min', tz='UTC')
    raw['_ts'] = ts_col
    ds = _month_start(raw['_ts'].iloc[warmup])
    de = _month_start(raw['_ts'].iloc[-1])
    print(f'[WalkForward] Data range: {ds.date()} ~ {de.date()}')
    results = []
    ws = ds
    wi = 0
    while True:
        os_start = ws
        os_end = _next_month_start(os_start)
        for _ in range(test_months - 1):
            os_end = _next_month_start(os_end)
        trs = _months_ago(ws, train_months)
        tre = ws
        if os_start > de:
            break
        train_mask = (raw['_ts'] >= trs) & (raw['_ts'] < tre)
        oos_mask = (raw['_ts'] >= os_start) & (raw['_ts'] < os_end)
        train_data = raw[train_mask].copy()
        oos_data = raw[oos_mask].copy()
        if len(train_data) < 500 or len(oos_data) < 100:
            ws = os_end
            wi += 1
            continue
        print(f'\n--- Window {wi+1} ---')
        print(f'  Train: {trs.date()} ~ {tre.date()} ({len(train_data)} rows)')
        print(f'  OOS:   {os_start.date()} ~ {os_end.date()} ({len(oos_data)} rows)')
        cfg = V565Config(min_score=min_score, tp1_r=tp1_r, tp2_r=tp2_r, tp3_r=tp3_r, max_hold_bars=max_hold_bars)
        engine = V56_5_Engine(cfg)
        tc = engine.generate_candidates(train_data)
        if tc.empty:
            print(f'  [Skip] No candidates from training')
            ws = os_end
            wi += 1
            continue
        train_trades = engine.select_trades(tc)
        if train_trades.empty:
            print(f'  [Skip] No trades from training')
            ws = os_end
            wi += 1
            continue
        tb = engine.extract_buckets_from_trades(train_trades)
        if not tb or all(len(v) == 0 for v in tb.values()):
            print(f'  [Skip] No buckets from training')
            ws = os_end
            wi += 1
            continue
        engine.load_history_buckets(tb)
        total_samp = sum(len(v) for v in tb.values())
        print(f'  Buckets loaded: {total_samp} samples')
        oc = out_dir / f'trades_window_{wi+1}_{os_start.strftime("%Y%m")}.csv'
        mr = _run_oos_period(engine, oos_data, out_csv=oc)
        if mr:
            mr['oos_month'] = os_start.strftime('%Y-%m')
            mr['train_start'] = str(trs.date())
            mr['train_end'] = str(tre.date())
            mr['oos_start'] = str(os_start.date())
            mr['oos_end'] = str(os_end.date())
            mr['train_samples'] = len(train_data)
            mr['oos_samples'] = len(oos_data)
            results.append(mr)
            print(f'  OOS: trades={mr["total_trades"]}, EV={mr["avg_ev_r"]:.4f}, DD={mr["max_drawdown_r"]:.4f}, WR={mr["win_rate"]:.2%}')
        else:
            print('  [Skip] No OOS trades')
        ws = os_end
        wi += 1
        if wi >= 60:
            print('[WalkForward] Reached max 60 windows, stopping')
            break
    sdf = pd.DataFrame(results)
    if not sdf.empty:
        sc = out_dir / f'walk_forward_summary_{ts}.csv'
        sdf.to_csv(sc, index=False)
        print(f'\n{"="*60}')
        print(f'Summary saved: {sc}')
        print(f'  Windows: {len(sdf)}')
        print(f'  Avg EV:  {sdf["avg_ev_r"].mean():.4f}')
        print(f'  Avg WR:  {sdf["win_rate"].mean():.2%}')
        print(f'  Avg DD:  {sdf["max_drawdown_r"].mean():.4f}R')
        print(f'  Avg PF:  {sdf["profit_factor"].mean():.2f}')
        print(f'{"="*60}')
        sj = out_dir / f'walk_forward_summary_{ts}.json'
        sdf.to_json(sj, orient='records', indent=2, force_ascii=False)
        print(f'JSON saved: {sj}')
    else:
        print('[WalkForward] No valid windows')
    return sdf


def main():
    p = argparse.ArgumentParser(description='V56.5 Walk-Forward')
    p.add_argument('--exec-csv', required=True)
    p.add_argument('--output-dir', default='data/walk_forward/')
    p.add_argument('--warmup', type=int, default=260)
    p.add_argument('--train-months', type=int, default=6)
    p.add_argument('--test-months', type=int, default=1)
    p.add_argument('--min-score', type=float, default=65.0)
    p.add_argument('--tp1-r', type=float, default=1.0)
    p.add_argument('--tp2-r', type=float, default=1.8)
    p.add_argument('--tp3-r', type=float, default=2.8)
    p.add_argument('--max-hold-bars', type=int, default=36)
    p.add_argument('--max-rows', type=int, default=None)
    a = p.parse_args()
    run_walk_forward(exec_csv=a.exec_csv, output_dir=a.output_dir, warmup=a.warmup,
                     train_months=a.train_months, test_months=a.test_months,
                     min_score=a.min_score, tp1_r=a.tp1_r, tp2_r=a.tp2_r,
                     tp3_r=a.tp3_r, max_hold_bars=a.max_hold_bars, max_rows=a.max_rows)


if __name__ == '__main__':
    main()
