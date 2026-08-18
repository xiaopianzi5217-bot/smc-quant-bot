# -*- coding: utf-8 -*-
"""验证 20260913 四合一修复是否完整生效"""
import io
import sys

lines = io.open('hf_auto_trader.py', encoding='utf-8').read().split('\n')

def find(pred, label):
    for i, l in enumerate(lines):
        if pred(l):
            print(f'  [OK] {label} @ line {i+1}')
            return True
    print(f'  [MISS] {label}')
    return False

print('=== 修复 1: ATR 一致性 ===')
find(lambda l: 'atr' in l and '_atr_val' in l and '【修复20260913】' in l, 'result[atr] 用 _atr_val')

print('\n=== 修复 2: Regime 透传 ===')
find(lambda l: 'regime' in l and '_regime_name' in l and '【修复20260913】' in l, 'result[regime] 用 HTF _regime_name')

print('\n=== 修复 3: EV/score 透传 ===')
find(lambda l: 'expected_value' in l and '_fb_result' in l and '【修复20260913】' in l, 'expected_value 用 FeedbackLoop EV')
find(lambda l: 'score' in l and '_fb_result' in l and 'weighted_score' in l and '【修复20260913】' in l, 'score 用 FeedbackLoop 加权分')
find(lambda l: '_feedback_ev' in l and 'blended_ev' in l and '【修复20260913】' in l, 'check_and_open ev 不被 blended_ev 覆盖')

print('\n=== 修复 4: FeedbackLoop 熔断前置 ===')
find(lambda l: 'FeedbackLoop 熔断必须前置' in l, '熔断前置注释')
find(lambda l: 'FeedbackLoop 熔断' in l and 'should_reject' not in l, '熔断检查日志')

# 验证熔断在路由调用之前
idx_reject = next((i for i, l in enumerate(lines) if 'FeedbackLoop 熔断' in l and 'should_reject' in l), -1)
idx_route = next((i for i, l in enumerate(lines) if 'check_and_open_v6_with_routing(result)' in l and i > idx_reject), -1)
if idx_reject > 0 and idx_route > idx_reject:
    print(f'  [OK] 熔断检查(line {idx_reject+1}) 在路由开仓(line {idx_route+1}) 之前')
else:
    print(f'  [FAIL] 顺序异常: 熔断@{idx_reject+1} 路由@{idx_route+1}')

print('\n=== 语法检查 ===')
import py_compile
try:
    py_compile.compile('hf_auto_trader.py', doraise=True)
    print('  [OK] py_compile 通过')
except Exception as e:
    print(f'  [FAIL] {e}')
    sys.exit(1)
