# -*- coding: utf-8 -*-
"""Check outcome stats and StatisticalEV config"""

import json
import pathlib

# 1. Check outcome_stats.json
p = pathlib.Path('storage/outcome_stats.json')
if p.exists():
    with open(p, 'r', encoding='utf-8') as f:
        d = json.load(f)
    print(f'=== storage/outcome_stats.json ===')
    print(f'Total feature hashes: {len(d)}')
    for h, v in list(d.items())[:15]:
        print(f'  {h[:50]}: trades={v["trade"]} win={v["win"]} loss={v["loss"]} mean_r={v["mean_r"]:.4f} pf={v["pf"]:.4f}')
else:
    print('storage/outcome_stats.json NOT FOUND')

# 2. Check StatisticalEV source
ssev_path = pathlib.Path('strategy/statistical_ev.py')
if ssev_path.exists():
    content = ssev_path.read_text(encoding='utf-8')
    # Extract min_trades and db usage
    import re
    min_trades = re.findall(r'min_trades\s*[:=]\s*(\d+)', content)
    print(f'\n=== strategy/statistical_ev.py ===')
    print(f'min_trades values found: {min_trades}')
    # Find where it loads DB
    db_matches = re.findall(r'(db_path|OutcomeDatabase|storage/outcome_stats)', content)
    print(f'DB references: {db_matches}')
    # Look for the key get_ev call sites
    get_ev_calls = re.findall(r'get_ev\([^)]*\)', content)
    print(f'get_ev calls: {get_ev_calls[:5]}')
else:
    print('strategy/statistical_ev.py NOT FOUND')

# 3. Check v56_5_bucket_ev.json stats
bucket_path = pathlib.Path('data/v56_5_bucket_ev.json')
if bucket_path.exists():
    with open(bucket_path, 'r', encoding='utf-8') as f:
        bd = json.load(f)
    print(f'\n=== data/v56_5_bucket_ev.json ===')
    print(f'Buckets: {len(bd)}')
    stats = {}
    for k, v in bd.items():
        stats[k] = v.get('trades', 0)
    total_trades = sum(stats.values())
    print(f'Total trades across buckets: {total_trades}')
    nonzero = [k for k, c in stats.items() if c > 0]
    print(f'Buckets with trades > 0: {len(nonzero)}')
else:
    print('data/v56_5_bucket_ev.json NOT FOUND')
