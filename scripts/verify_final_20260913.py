# -*- coding: utf-8 -*-
"""Verify all 20260913 fixes are present"""
import io

lines = io.open('hf_auto_trader.py', encoding='utf-8').read().splitlines()

checks = [
    ('FIX1 ATR uses _atr_val', '_atr_val'),
    ('FIX2 Regime uses _regime_name', '_regime_name'),
    ('FIX3 EV uses fb_result', '_fb_result[\"ev\"]'),
    ('FIX4 Score uses weighted_score', '_fb_result[\"weighted_score\"]'),
    ('FIX5 v6_weighted_score field', 'v6_weighted_score'),
    ('FIX6 ev not overridden by blended', '_feedback_ev'),
    ('FIX7 breaker before routing', 'FB-BREAKER-BEFORE-ROUTE'),
    ('FIX8 pm write uses weighted score', 'v6_weighted_score'),
    ('FIX9 regime not best.get', 'FIX-20260913'),
]

passed = 0
for name, keyword in checks:
    hits = [(i+1) for i, l in enumerate(lines) if keyword in l]
    if hits:
        print(f'[PASS] {name} -> line {hits[0]}')
        passed += 1
    else:
        print(f'[FAIL] {name}')

print(f'\nResult: {passed}/{len(checks)} passed')
print(f'Total lines: {len(lines)}')
