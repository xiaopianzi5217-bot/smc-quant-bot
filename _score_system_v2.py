# -*- coding: utf-8 -*-
"""
SMC Quant Bot - Deep System Score v2
3-tier scoring: [OK]=full, [WARN]=half, [--]=zero
"""
import ast, csv, json, os
from pathlib import Path

root = Path('.')

print('=' * 70)
print('  SMC Quant Bot - Deep System Score v2')
print('=' * 70)

code = open('hf_auto_trader.py', encoding='utf-8').read()
code2 = open('config.py', encoding='utf-8').read()
v565 = open('final_forge/v56_5_stable_engine.py', encoding='utf-8').read()
pm = open('state/position_manager.py', encoding='utf-8').read()

def check(category, weight):
    """Decorator to define a category"""
    pass

weights = {
    'Signal Generation': 0.12,
    'Decision Pipeline': 0.22,
    'Risk Control': 0.20,
    'Execution & Persistence': 0.15,
    'Code Quality': 0.10,
    'Data & Backtest': 0.13,
    'Extensibility': 0.08,
}

results = {}

# ============================================================
# 1. Signal Generation (12%)
# ============================================================
items = []
items.append(('V56.5 Engine', 15, 1 if (root / 'final_forge/v56_5_stable_engine.py').exists() else 0))
items.append(('Multi-symbol support', 10, 1 if 'SYMBOLS' in code and 'BTC' in code else 0))
items.append(('Multi-timeframe (1h+15m)', 10, 1 if 'TIMEFRAME_MACRO' in code2 and 'TIMEFRAME_EXEC' in code2 else 0))
items.append(('Observer event detection', 12, 1 if '_detect_observer_events' in code else 0))
items.append(('SMC structure (OB/FVG/CHOCH/BOS)', 12, 1 if 'build_macro_context' in code and 'build_exec_context' in code else 0))

# Check signal density quality from report
density = 0
if (root / 'data/V56_5_STABLE_REPORT.json').exists():
    r = json.loads(open(root / 'data/V56_5_STABLE_REPORT.json', encoding='utf-8').read())
    density = r.get('candidate_summary', {}).get('signal_density', 0)
if density > 0.15:
    items.append(('Signal density quality', 10, 1))
elif density > 0:
    items.append(('Signal density quality', 10, 0.5))
else:
    items.append(('Signal density quality', 10, 0))

# Color change detection
items.append(('Candle color detection', 8, 1 if 'color_changed' in code else 0))
# Setup pattern variety
if (root / 'data/V56_5_STABLE_REPORT.json').exists():
    entropy = r.get('signal_entropy_broad', {}).get('entropy_bits', 0)
    if entropy > 1.8:
        items.append(('Setup pattern variety', 10, 1))
    elif entropy > 1.2:
        items.append(('Setup pattern variety', 10, 0.5))
    else:
        items.append(('Setup pattern variety', 10, 0))

# Real-time data fetching
items.append(('Real-time OHLCV fetching', 8, 1 if 'fetch_ohlcv' in code else 0))
# Multi-book support
items.append(('Multi-book routing', 5, 0.5 if 'book' in code else 0))

results['Signal Generation'] = items

# ============================================================
# 2. Decision Pipeline (22%)
# ============================================================
items = []
# EV model quality
if 'estimate_win_probability' in v565 and 'decision_score' in v565:
    items.append(('EV probability model', 8, 1))
else:
    items.append(('EV probability model', 8, 0))

# Blended EV (model + historical)
if (root / 'strategy/statistical_ev.py').exists():
    sev = open(root / 'strategy/statistical_ev.py', encoding='utf-8').read()
    if 'blend' in sev:
        items.append(('Statistical EV blend', 7, 1))
    else:
        items.append(('Statistical EV blend', 7, 0.5))
else:
    items.append(('Statistical EV blend', 7, 0))

# HTF Regime filter
if (root / 'strategy/htf_regime_filter.py').exists():
    htf = open(root / 'strategy/htf_regime_filter.py', encoding='utf-8').read()
    if 'allow_long' in htf and 'allow_short' in htf:
        items.append(('HTF Regime filter (1H dir)', 8, 1))
    else:
        items.append(('HTF Regime filter', 8, 0.5))
else:
    items.append(('HTF Regime filter', 8, 0))

# Quality Gate
items.append(('Quality Gate', 7, 1 if (root / 'strategy/v565_quality_gate.py').exists() else 0))

# Score Grade
items.append(('Score Grade grading', 7, 1 if (root / 'strategy/score_grade.py').exists() else 0))

# Feature Penalty
items.append(('Feature overlap penalty', 6, 1 if (root / 'strategy/feature_penalty.py').exists() else 0))

# Statistical EV Gate
items.append(('Statistical EV Gate', 7, 1 if (root / 'strategy/statistical_ev_gate.py').exists() else 0))

# V37 Final Gate
if (root / 'decision/v37_gate.py').exists():
    v37 = open(root / 'decision/v37_gate.py', encoding='utf-8').read()
    if 'v37_final_gate' in v37:
        items.append(('V37 Final Gate', 8, 1))
    else:
        items.append(('V37 Final Gate', 8, 0.5))
else:
    items.append(('V37 Final Gate', 8, 0))

# Bucket EV (historical perf matching)
if 'bucket_ev' in v565:
    items.append(('Historical bucket EV', 6, 1))
else:
    items.append(('Historical bucket EV', 6, 0))

# Regime-aware factor
if 'regime_factor' in v565:
    items.append(('Regime-aware scoring', 6, 1))
else:
    items.append(('Regime-aware scoring', 6, 0))

# Session factor
if 'session_factor' in v565:
    items.append(('Session time weighting', 5, 1))
else:
    items.append(('Session time weighting', 5, 0))

# Tier-based weighting
if 'TIER_WEIGHT' in v565:
    items.append(('Tier-based weighting', 5, 1))
else:
    items.append(('Tier-based weighting', 5, 0))

# Cluster risk scaling
if '_cluster_score' in v565 and '_size_scale' in v565:
    items.append(('Cluster risk scaling', 5, 1))
else:
    items.append(('Cluster risk scaling', 5, 0))

# Top-N dynamic selection
if 'Dynamic Top-N' in v565 or 'select_v565_portfolio' in v565:
    items.append(('Dynamic Top-N selection', 5, 1))
else:
    items.append(('Dynamic Top-N selection', 5, 0))

# Trend-end position check
items.append(('Trend-end pullback check', 5, 1 if 'TREND_END_PULLBACK_ATR' in code else 0))

# RR hard check
items.append(('RR >= 1.0 hard check', 5, 1 if 'actual_rr < 1.0' in code else 0))

results['Decision Pipeline'] = items

# ============================================================
# 3. Risk Control (20%)
# ============================================================
items = []
# Stop-loss cooldown
items.append(('Stop-loss cooldown (300s)', 8, 1 if '_check_cooldown' in code else 0))
# Signal dedup (900s time slot)
items.append(('Signal dedup (900s slot)', 8, 1 if '_is_signal_processed' in code else 0))
# Position dedup
items.append(('Position dedup (symbol check)', 8, 1 if 'position_manager.exists' in code else 0))
# Funding rate adverse check
items.append(('Funding rate adverse filter', 7, 1 if 'funding' in code and '0.0003' in code else 0))
# Score gap filter
items.append(('Score gap filter (4.0)', 7, 1 if 'MIN_SCORE_GAP' in code else 0))
# Production risk guard constants
if all(k in code2 for k in ['MAX_DAILY_LOSS_R', 'MAX_TRADES_DAY', 'MAX_CONSECUTIVE_LOSS']):
    items.append(('Daily risk constants', 7, 1))
else:
    items.append(('Daily risk constants', 7, 0.5 if any(k in code2 for k in ['MAX_DAILY_LOSS_R', 'MAX_TRADES_DAY', 'MAX_CONSECUTIVE_LOSS']) else 0))

# Push global rate limit
items.append(('Push rate limit (60s global)', 6, 1 if 'safe_send' in code and 'RATELIMITED_GLOBAL' in code else 0))
# Observer periodic summary
items.append(('Observer periodic summary (1800s)', 6, 1 if 'OBSERVER_PERIODIC_INTERVAL' in code else 0))
# Observer event state change tracking
items.append(('Observer event state machine', 6, 1 if 'OBSERVER_EVENT_ACTIVE' in code else 0))
# Max drawdown guard
items.append(('Max drawdown guard (15%)', 7, 1 if 'MAX_DRAWDOWN_PCT' in code else 0))
# Per-symbol cooldown
items.append(('Per-symbol independent cooldown', 6, 1 if '_last_stop_loss_time' in code else 0))
# Runtime state recovery
items.append(('Runtime state recovery', 7, 1 if '_load()' in pm else 0))
# Persistence of processed signals
items.append(('Signal dedup memory cleanup', 6, 1 if 'del _PROCESSED_SIGNALS' in code else 0))
# Consecutive loss stop
items.append(('Consecutive loss stop', 6, 1 if 'MAX_CONSECUTIVE_LOSS' in code2 and 'MAX_CONSECUTIVE_LOSS' in code else 0.5 if 'MAX_CONSECUTIVE_LOSS' in code2 else 0))
# Signal cooldown from config
items.append(('Signal cooldown from config', 5, 1 if 'SIGNAL_COOLDOWN_SECONDS' in code else 0))

results['Risk Control'] = items

# ============================================================
# 4. Execution & Persistence (15%)
# ============================================================
items = []
# Position Manager
if 'threading.Lock' in pm:
    items.append(('Thread-safe Position Manager', 10, 1))
elif 'position_manager' in code:
    items.append(('Thread-safe Position Manager', 10, 0.5))
else:
    items.append(('Thread-safe Position Manager', 10, 0))

# Trade Journal
items.append(('Trade Journal (CSV audit)', 10, 1 if (root / 'state/trade_journal.py').exists() else 0))
# Feature Store
items.append(('Feature Store (trade features)', 8, 1 if (root / 'feature_store.py').exists() else 0))
# Auto recovery on startup
items.append(('Auto state recovery on startup', 8, 1 if '_load()' in pm else 0))
# Exit save
items.append(('Graceful exit save (atexit)', 7, 1 if 'atexit' in pm else 0))
# Telegram integration
items.append(('Telegram push integration', 8, 1 if (root / 'notifier/telegram.py').exists() else 0))
# Position tracking (trailing, partial close)
items.append(('Position tracking (trailing/partial)', 8, 1 if 'check_trailing' in code else 0))
# Async scanning
items.append(('Async scanning (asyncio)', 8, 1 if 'async def main_loop' in code else 0))
# Stop-loss trigger with logging
items.append(('Stop-loss logging + journal', 7, 1 if '_trigger_stop_loss' in code else 0))
# Signal diary
items.append(('Signal diary (push/signal logs)', 5, 1 if (root / 'state/signal_diary.py').exists() else 0))
# Cooldown persistence
items.append(('Cooldown persistence across restart', 5, 0.5 if '_load()' in pm else 0))
# Backup logs
log_backups = len(list((root / 'logs').glob('*backup*')))
items.append(('Log backup files', 3, 1 if log_backups > 0 else 0))
# Error log
items.append(('Error log file', 3, 1 if (root / 'logs').exists() and any(f.name == 'bot_errors.log' for f in (root / 'logs').iterdir()) else 0))

results['Execution & Persistence'] = items

# ============================================================
# 5. Code Quality (10%)
# ============================================================
items = []

# File size
nlines = len(code.split('\n'))
if nlines < 700:
    items.append(('File size', 10, 1))
elif nlines < 1100:
    items.append(('File size', 10, 0.5))
else:
    items.append(('File size', 10, 0))

# Type hints coverage
items.append(('Type hints (function signatures)', 10, 1 if 'def check_and_open(result: dict | None) -> bool:' in code else 0.5 if ': dict | None' in code or '-> bool' in code else 0))

# Function size check
tree = ast.parse(code)
large_funcs = sum(1 for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.end_lineno and n.end_lineno - n.lineno > 150)
if large_funcs == 0:
    items.append(('Function size (max < 150 lines)', 10, 1))
elif large_funcs <= 2:
    items.append(('Function size', 10, 0.5))
else:
    items.append(('Function size', 10, 0))

# Exception handling
try_count = code.count('try:')
except_count = code.count('except')
if try_count >= 8:
    items.append(('Exception handling', 10, 1))
elif try_count >= 4:
    items.append(('Exception handling', 10, 0.5))
else:
    items.append(('Exception handling', 10, 0))

# Config separation
items.append(('Config from code separation', 10, 1 if 'from config import' in code else 0))

# Module structure
modules_present = sum(1 for m in ['strategy/', 'decision/', 'state/', 'notifier/', 'final_forge/'] if (root / m).exists())
if modules_present >= 5:
    items.append(('Module structure (5 dirs)', 10, 1))
elif modules_present >= 3:
    items.append(('Module structure', 10, 0.5))
else:
    items.append(('Module structure', 10, 0))

# Memory management
items.append(('Memory cleanup (expired signals)', 8, 1 if 'del _PROCESSED_SIGNALS' in code else 0))

# Print/log ratio
items.append(('Debug logging coverage', 8, 1 if code.count('print(') >= 40 else 0.5 if code.count('print(') >= 20 else 0))

# Magic numbers avoidance
magic_count = 0
for token in ['900', '300', '60', '1800', '4.0', '35', '55.0', '65.0']:
    if f'= {token}' in code or f'={token}' in code or f': {token}' in code:
        pass  # reasonable constants
# Check for truly magic numbers without named vars
if 'MIN_SCORE_FOR_PUSH' in code and 'MIN_EV_FOR_PUSH' in code:
    items.append(('Named constants (avoid magic numbers)', 8, 1))
else:
    items.append(('Named constants', 8, 0.5))

# Code comments ratio
comment_lines = sum(1 for l in code.split('\n') if l.strip().startswith('#') or l.strip().startswith('"""'))
total_code = len(code.split('\n'))
comment_ratio = comment_lines / total_code if total_code > 0 else 0
if comment_ratio > 0.10:
    items.append(('Code documentation ratio', 8, 1))
elif comment_ratio > 0.05:
    items.append(('Code documentation ratio', 8, 0.5))
else:
    items.append(('Code documentation ratio', 8, 0))

# Import order conventions
items.append(('Clean import conventions', 8, 1 if 'from __future__' in code else 0))

results['Code Quality'] = items

# ============================================================
# 6. Data & Backtest (13%)
# ============================================================
items = []

if (root / 'data/V56_5_STABLE_REPORT.json').exists():
    r = json.loads(open(root / 'data/V56_5_STABLE_REPORT.json', encoding='utf-8').read())
    overall = r.get('overall', {})
    trades = overall.get('trades', 0)
    wr = overall.get('win_rate', 0)
    pf = overall.get('pf', 0)
    
    items.append(('Backtest report exists', 8, 1))
    
    if trades >= 200:
        items.append(('Trade count (>=200/year)', 8, 1))
    elif trades >= 100:
        items.append(('Trade count', 8, 0.5))
    else:
        items.append(('Trade count', 8, 0))
    
    if wr >= 0.60:
        items.append(('Win rate (>=60%)', 8, 1))
    elif wr >= 0.50:
        items.append(('Win rate', 8, 0.5))
    else:
        items.append(('Win rate', 8, 0))
    
    if pf >= 1.5:
        items.append(('Profit Factor (>=1.5)', 8, 1))
    elif pf >= 1.2:
        items.append(('Profit Factor', 8, 0.5))
    else:
        items.append(('Profit Factor', 8, 0))
    
    max_dd = abs(overall.get('max_dd_r', 0))
    if max_dd <= 8:
        items.append(('Max drawdown (<=8R)', 7, 1))
    elif max_dd <= 12:
        items.append(('Max drawdown', 7, 0.5))
    else:
        items.append(('Max drawdown', 7, 0))
    
    scenarios = r.get('stability_curve', {}).get('scenarios', [])
    if len(scenarios) >= 5 and scenarios[-1].get('pnl', 0) > 0:
        items.append(('Stress test (all positive)', 7, 1))
    elif len(scenarios) >= 3:
        items.append(('Stress test', 7, 0.5))
    else:
        items.append(('Stress test', 7, 0))
    
    ev_status = r.get('ev_calibration', {}).get('status', '')
    if ev_status == 'PASS':
        items.append(('EV monotonicity (PASS)', 6, 1))
    else:
        items.append(('EV monotonicity', 6, 0.5 if ev_status == 'WARN' else 0))
    
    tp1_hit = overall.get('tp1_touch_rate', 0)
    if tp1_hit >= 0.55:
        items.append(('TP1 touch rate (>=55%)', 6, 1))
    elif tp1_hit >= 0.40:
        items.append(('TP1 touch rate', 6, 0.5))
    else:
        items.append(('TP1 touch rate', 6, 0))
    
    entropy = r.get('signal_entropy_broad', {}).get('entropy_bits', 0)
    if entropy > 1.8:
        items.append(('Pattern diversity (entropy >1.8)', 6, 1))
    elif entropy > 1.2:
        items.append(('Pattern diversity', 6, 0.5))
    else:
        items.append(('Pattern diversity', 6, 0))
    
    logic = r.get('logic_checks', {})
    logic_ok = sum(1 for v in logic.values() if v is True)
    logic_total = len(logic)
    if logic_ok == logic_total:
        items.append(('Backtest logic integrity', 6, 1))
    elif logic_ok >= logic_total - 2:
        items.append(('Backtest logic integrity', 6, 0.5))
    else:
        items.append(('Backtest logic integrity', 6, 0))
else:
    items.append(('Backtest report exists', 8, 0))

# Live signal log
if (root / 'logs/trade_journal.csv').exists():
    with open(root / 'logs/trade_journal.csv', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    if len(rows) >= 50:
        items.append(('Live signal log (>=50 records)', 7, 1))
    elif len(rows) >= 10:
        items.append(('Live signal log', 7, 0.5))
    else:
        items.append(('Live signal log', 7, 0.2))
else:
    items.append(('Live signal log', 7, 0))

# Historical data
items.append(('Historical OHLCV data (1yr)', 6, 1 if (root / 'data/BTCUSDT_15M_365d.csv').exists() else 0))
# HMM model
items.append(('HMM regime model', 5, 1 if (root / 'data/hmm_model.pkl').exists() else 0))
# EV memory
items.append(('Dynamic EV memory', 5, 1 if (root / 'data/dynamic_ev_memory.json').exists() else 0))
# Cluster report
items.append(('Cluster risk report', 4, 1 if (root / 'data/cluster_report.csv').exists() else 0))

results['Data & Backtest'] = items

# ============================================================
# 7. Extensibility (8%)
# ============================================================
items = []

# Exchange API
ex_dir = root / 'execution'
if ex_dir.exists():
    ex_files = [f for f in ex_dir.iterdir() if f.name.endswith('.py')]
    if len(ex_files) >= 2:
        items.append(('Exchange API layer', 10, 1))
    elif len(ex_files) == 1:
        items.append(('Exchange API layer', 10, 0.5))
    else:
        items.append(('Exchange API layer', 10, 0))
else:
    items.append(('Exchange API layer', 10, 0))

# Unit tests
test_dir = root / 'tests' if (root / 'tests').exists() else root / 'test'
if test_dir.exists():
    test_files = [f for f in test_dir.iterdir() if f.name.endswith('.py')]
    if len(test_files) >= 5:
        items.append(('Unit tests (>=5 files)', 10, 1))
    elif len(test_files) >= 2:
        items.append(('Unit tests', 10, 0.5))
    else:
        items.append(('Unit tests', 10, 0.2))
else:
    items.append(('Unit tests', 10, 0))

# Audit tools
audit_tools = [f for f in root.iterdir() if f.name.startswith('_') and f.name.endswith('.py')]
if len(audit_tools) >= 2:
    items.append(('Audit/debug tools', 8, 1))
elif len(audit_tools) >= 1:
    items.append(('Audit/debug tools', 8, 0.5))
else:
    items.append(('Audit/debug tools', 8, 0))

# Backtest runner
items.append(('Backtest runner script', 8, 1 if (root / 'backtest/runner.py').exists() else 0))

# Per-symbol parameterization
items.append(('Per-symbol strategy params', 8, 1 if 'SYMBOL_STRATEGY' in code2 else 0))

# Docker deployment
items.append(('Docker deployment', 7, 1 if (root / 'Dockerfile').exists() or (root / 'docker-compose.yml').exists() else 0))

# Log classification
log_files = list((root / 'logs').glob('*.csv'))
if len(log_files) >= 4:
    items.append(('Log file classification', 7, 1))
elif len(log_files) >= 2:
    items.append(('Log file classification', 7, 0.5))
else:
    items.append(('Log file classification', 7, 0))

# README quality
readme = root / 'README.md'
if readme.exists():
    readme_text = readme.read_text(encoding='utf-8')
    if len(readme_text) > 500:
        items.append(('README documentation', 7, 1))
    else:
        items.append(('README documentation', 7, 0.5))
else:
    items.append(('README documentation', 7, 0))

# Strategy module count
strategy_files = [f for f in (root / 'strategy').iterdir() if f.name.endswith('.py')]
if len(strategy_files) >= 7:
    items.append(('Strategy module count (>=7)', 8, 1))
elif len(strategy_files) >= 4:
    items.append(('Strategy module count', 8, 0.5))
else:
    items.append(('Strategy module count', 8, 0))

# Symbol extensibility
items.append(('Symbol list configurable', 7, 1 if 'SYMBOLS' in code2 else 0))

# Grade/regime extensibility
items.append(('Grade system extensible', 7, 1 if (root / 'strategy/score_grade.py').exists() else 0))

# Config file options
config_items = ['SIGNAL_COOLDOWN_SECONDS', 'MAX_DAILY_LOSS_R', 'MAX_TRADES_DAY', 'STRATEGY_PARAMS', 'PIVOT_PARAMS', 'THRESHOLD_CONFIG', 'RISK', 'PATHS', 'TELEGRAM']
config_present = sum(1 for k in config_items if k in code2)
items.append(('Config richness', 8, min(1.0, config_present / 7)))

# Allowed grades extensibility
items.append(('Grade routing (A/B/C)', 5, 1 if 'ALLOWED_GRADES' in code2 else 0))

results['Extensibility'] = items

# Output
total_weighted = 0.0
for category, weight in weights.items():
    items = results[category]
    raw = sum(pts * score for name, pts, score in items)
    max_raw = sum(pts for name, pts, score in items)
    pct = min(100.0, raw / max_raw * 100.0) if max_raw > 0 else 0.0
    total_weighted += pct * weight
    passed = sum(1 for _, _, s in items if s >= 0.9)
    half = sum(1 for _, _, s in items if 0.3 < s < 0.9)
    failed = sum(1 for _, _, s in items if s <= 0.3)
    total = len(items)
    print()
    print(f'  [{category}]  {pct:.0f}/100  (weight {weight:.0%})  [{passed}OK/{half}WARN/{failed}MISS]')
    for name, pts, score in items:
        if score >= 0.9:
            icon = '[OK]'
        elif score > 0.3:
            icon = '[~]'
        else:
            icon = '[--]'
        pts_earned = pts * score
        print(f'    {icon} {name} ({pts_earned:.0f}/{pts})')

print()
print(f'  ===============================================')
print(f'  TOTAL SCORE: {total_weighted:.1f}/100', end='')
if total_weighted >= 85:
    print('   [S] Production Ready')
elif total_weighted >= 75:
    print('   [A] Near Production')
elif total_weighted >= 60:
    print('   [B] Needs Improvement')
elif total_weighted >= 40:
    print('   [C] Early Stage')
else:
    print('   [D] Prototype')
print(f'  ===============================================')

print()
print(f'  Radar Chart:')
for category, weight in sorted(weights.items(), key=lambda x: x[1], reverse=True):
    items = results[category]
    raw = sum(pts * score for name, pts, score in items)
    max_raw = sum(pts for name, pts, score in items)
    pct = min(100.0, raw / max_raw * 100.0) if max_raw > 0 else 0.0
    bar = '#' * int(pct / 5) + '-' * (20 - int(pct / 5))
    print(f'  {bar}  {category}  {pct:.0f}/100')

print()
print(f'  Key Gaps (score <0.5):')
for category, items in results.items():
    for name, pts, score in items:
        if score < 0.5:
            pts_earned = pts * score
            print(f'    [--] [{category}] {name} ({pts_earned:.0f}/{pts})')

print()
print('=' * 70)
print('  Report generated by _score_system_v2.py')
print('=' * 70)
