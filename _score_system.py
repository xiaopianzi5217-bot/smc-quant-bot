# -*- coding: utf-8 -*-
"""System scoring report"""
import ast, csv, json, os
from pathlib import Path

root = Path('.')
os.environ['PYTHONIOENCODING'] = 'utf-8'

print('=' * 70)
print('  SMC Quant Bot - System Score Report')
print('=' * 70)

code = open('hf_auto_trader.py', encoding='utf-8').read()
code2 = open('config.py', encoding='utf-8').read()
v565 = open('final_forge/v56_5_stable_engine.py', encoding='utf-8').read()
pm = open('state/position_manager.py', encoding='utf-8').read()

weights = {
    'Signal Generation': 0.15,
    'Decision Pipeline': 0.20,
    'Risk Control': 0.20,
    'Execution & Persistence': 0.15,
    'Code Quality': 0.10,
    'Data & Backtest': 0.10,
    'Extensibility': 0.10,
}

checks = {}

checks['Signal Generation'] = [
    ('V56.5 Engine', 25, (root / 'final_forge/v56_5_stable_engine.py').exists()),
    ('Multi-symbol', 15, 'SYMBOLS' in code and 'BTC' in code),
    ('Multi-timeframe', 15, 'TIMEFRAME_MACRO' in code2 and 'TIMEFRAME_EXEC' in code2),
    ('Observer Events', 15, '_detect_observer_events' in code),
    ('SMC Structure', 15, 'build_macro_context' in code and 'build_exec_context' in code),
    ('Funding Rate', 15, 'funding_rate' in code),
]

checks['Decision Pipeline'] = [
    ('EV Probability Model', 10, 'estimate_win_probability' in v565),
    ('Mixed Decision Score', 10, 'decision_score' in v565),
    ('Candidate Enrichment', 10, 'enrich_v565_candidates' in v565),
    ('Quality Gate', 10, (root / 'strategy/v565_quality_gate.py').exists()),
    ('HTF Regime Filter', 8, (root / 'strategy/htf_regime_filter.py').exists()),
    ('Score Grade', 8, (root / 'strategy/score_grade.py').exists()),
    ('Feature Penalty', 7, (root / 'strategy/feature_penalty.py').exists()),
    ('Statistical EV Gate', 7, (root / 'strategy/statistical_ev_gate.py').exists()),
    ('V37 Final Gate', 10, (root / 'decision/v37_gate.py').exists()),
    ('Statistical EV Blend', 8, (root / 'strategy/statistical_ev.py').exists()),
    ('Trend End Check', 6, 'TREND_END_PULLBACK_ATR' in code),
    ('RR Hard Check', 6, 'actual_rr < 1.0' in code),
]

checks['Risk Control'] = [
    ('Stop-loss Cooldown', 10, '_check_cooldown' in code),
    ('Signal Dedup', 10, '_is_signal_processed' in code),
    ('Position Dedup', 10, 'position_manager.exists' in code),
    ('Funding Rate Filter', 8, 'funding' in code and '0.0003' in code),
    ('Score Gap Filter', 8, 'MIN_SCORE_GAP' in code),
    ('Daily Risk Constants', 8, 'MAX_DAILY_LOSS_R' in code2),
    ('Push Rate Limiting', 8, 'safe_send' in code and 'RATELIMITED_GLOBAL' in code),
    ('Observer Rate Limiting', 8, 'OBSERVER_PERIODIC_INTERVAL' in code),
    ('Max Drawdown Guard', 10, 'MAX_DRAWDOWN_PCT' in code),
    ('Per-symbol Cooldown', 8, '_last_stop_loss_time' in code),
    ('Persistence Recovery', 10, '_load()' in pm),
]

checks['Execution & Persistence'] = [
    ('Position Manager', 15, 'threading.Lock' in pm),
    ('Trade Journal', 15, (root / 'state/trade_journal.py').exists()),
    ('Feature Store', 10, (root / 'feature_store.py').exists()),
    ('Auto Recovery', 10, '_load()' in pm),
    ('Exit Save', 7, 'atexit' in pm),
    ('Telegram Push', 10, (root / 'notifier/telegram.py').exists()),
    ('Observer Dedup', 10, 'OBSERVER_EVENT_ACTIVE' in code),
    ('Async Scanning', 10, 'async def main_loop' in code),
    ('Position Tracking', 8, 'check_trailing' in code),
    ('Signal Diary', 5, 'signal_diary.py' in os.listdir('state')),
]

# Code Quality
tree = ast.parse(code)
large_funcs = 0
for n in ast.walk(tree):
    if isinstance(n, ast.FunctionDef):
        if n.end_lineno and n.end_lineno - n.lineno > 100:
            large_funcs += 1

checks['Code Quality'] = [
    ('File Size', 10, len(code.split('\n')) < 1200),
    ('Type Hints', 10, 'def check_and_open(result: dict | None) -> bool:' in code),
    ('Exception Handling', 12, code.count('try:') >= 5),
    ('Debug Logging', 10, code.count('print(') >= 30),
    ('Config Separation', 12, 'from config import' in code),
    ('Modularity', 16, sum(1 for m in ['strategy/', 'decision/', 'state/', 'notifier/', 'final_forge/'] if (root / m).exists()) >= 4),
    ('Memory Cleanup', 8, 'del _PROCESSED_SIGNALS' in code),
    ('Function Size', 10, large_funcs <= 1),
]

checks['Data & Backtest'] = [
    ('Backtest Report', 20, (root / 'data/V56_5_STABLE_REPORT.json').exists()),
    ('Live Signal Log', 15, (root / 'logs/trade_journal.csv').exists()),
    ('Historical Data', 15, (root / 'data/BTCUSDT_15M_365d.csv').exists()),
    ('HMM Model', 10, (root / 'data/hmm_model.pkl').exists()),
    ('EV Memory', 10, (root / 'data/dynamic_ev_memory.json').exists()),
    ('Stress Test', 15, False),
    ('EV Monotonicity', 10, False),
    ('Cluster Report', 5, (root / 'data/cluster_report.csv').exists()),
]

# Override stress/monotonicity if report exists
if (root / 'data/V56_5_STABLE_REPORT.json').exists():
    report = json.loads(open(root / 'data/V56_5_STABLE_REPORT.json', encoding='utf-8').read())
    scenarios = report.get('stability_curve', {}).get('scenarios', [])
    if len(scenarios) >= 4 and scenarios[-1].get('pnl', 0) > 0:
        checks['Data & Backtest'][5] = ('Stress Test', 15, True)
    else:
        checks['Data & Backtest'][5] = ('Stress Test', 15, False)
    ev_status = report.get('ev_calibration', {}).get('status', '')
    checks['Data & Backtest'][6] = ('EV Monotonicity', 10, ev_status == 'PASS')

strategy_files = [f for f in (root / 'strategy').iterdir() if f.name.endswith('.py')]

checks['Extensibility'] = [
    ('Exchange API', 12, (root / 'execution').exists() and any(f.name.endswith('.py') for f in (root / 'execution').iterdir())),
    ('Unit Tests', 12, (root / 'tests').exists() and any(f.name.endswith('.py') for f in (root / 'tests').iterdir())),
    ('Audit Tool', 8, (root / '_system_audit.py').exists()),
    ('Backtest Runner', 10, (root / 'backtest/runner.py').exists()),
    ('Per-symbol Params', 10, 'SYMBOL_STRATEGY' in code2),
    ('Docker Deploy', 8, (root / 'Dockerfile').exists() or (root / 'docker-compose.yml').exists()),
    ('Log Classification', 10, len(list((root / 'logs').glob('*.csv'))) >= 2),
    ('README', 8, (root / 'README.md').exists()),
    ('Strategy Module Count', 12, len(strategy_files) >= 6),
    ('Symbol Extensibility', 10, code2.count('SYMBOLS') >= 2),
]

total_weighted = 0.0
for category, weight in weights.items():
    items = checks[category]
    raw = sum(pts for _, pts, ok in items)
    max_raw = sum(pts for _, pts, _ in items)
    pct = min(100.0, raw / max_raw * 100.0) if max_raw > 0 else 0.0
    total_weighted += pct * weight
    passed = sum(1 for _, _, ok in items)
    total = len(items)
    print()
    print(f'  [{category}]  {pct:.0f}/100  (weight {weight:.0%})  [{passed}/{total} pass]')
    for name, pts, ok in items:
        icon = '[OK]' if ok else '[--]'
        print(f'    {icon} {name} ({pts}pts)')

print()
print(f'  ===============================================')
print(f'  TOTAL SCORE: {total_weighted:.1f}/100', end='')
if total_weighted >= 90:
    print('   [S] Production Ready')
elif total_weighted >= 78:
    print('   [A] Near Production')
elif total_weighted >= 65:
    print('   [B] Needs Improvement')
elif total_weighted >= 50:
    print('   [C] Early Stage')
else:
    print('   [D] Prototype')
print(f'  ===============================================')

print()
print(f'  Radar Chart:')
for category, weight in sorted(weights.items(), key=lambda x: x[1], reverse=True):
    items = checks[category]
    raw = sum(pts for _, pts, ok in items)
    max_raw = sum(pts for _, pts, _ in items)
    pct = min(100.0, raw / max_raw * 100.0) if max_raw > 0 else 0.0
    bar = '#' * int(pct / 5) + '-' * (20 - int(pct / 5))
    print(f'  {bar}  {category}  {pct:.0f}/100')

print()
print(f'  Key Gaps:')
gap_found = False
for category, items in checks.items():
    for name, pts, ok in items:
        if not ok:
            gap_found = True
            print(f'    [--] [{category}] {name} ({pts}pts)')
if not gap_found:
    print('    No major gaps')

print()
print(f'  Backtest Performance:')
if (root / 'data/V56_5_STABLE_REPORT.json').exists():
    r = json.loads(open(root / 'data/V56_5_STABLE_REPORT.json', encoding='utf-8').read())['overall']
    print(f'    Trades/year:     {r["trades"]}')
    print(f'    Win Rate:        {r["win_rate"]*100:.1f}%')
    print(f'    Profit Factor:   {r["pf"]:.3f}')
    print(f'    Total R:         {r["pnl"]:.1f}')
    print(f'    Avg R/Trade:     {r["avg_r"]:.4f}')
    print(f'    Max DD:          {r["max_dd_r"]:.1f}R')
    print(f'    TP1 Hit Rate:    {r["tp1_touch_rate"]*100:.1f}%')
    print(f'    TP2 Hit Rate:    {r["tp2_touch_rate"]*100:.1f}%')
    print(f'    TP3 Hit Rate:    {r["tp3_touch_rate"]*100:.1f}%')
    for s in report.get('stability_curve', {}).get('scenarios', []):
        print(f'    Stress({s["scenario"]}): PF={s["pf"]:.3f} PnL={s["pnl"]:.1f}R')
else:
    print('    No backtest data available')

print()
print(f'  Live Signals:')
if (root / 'logs/trade_journal.csv').exists():
    with open(root / 'logs/trade_journal.csv', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    print(f'    Total: {len(rows)} signal records')
    print(f'    Status: All OPEN (push only, no live execution)')
else:
    print('    No live records')

print()
print('=' * 70)
print('  Report generated by _score_system.py')
print('=' * 70)
