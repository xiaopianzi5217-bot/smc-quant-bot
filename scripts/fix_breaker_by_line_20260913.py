# -*- coding: utf-8 -*-
"""
Fix: Move FeedbackLoop breaker BEFORE route opening.
Uses line-number targeting to avoid encoding issues.
"""
import io
import sys

path = 'hf_auto_trader.py'
lines = io.open(path, encoding='utf-8').read().split('\n')
print(f'Total lines: {len(lines)}')

# Find the line with 'if not check_and_open_v6_with_routing(result):'
target_idx = None
for i, l in enumerate(lines):
    if 'if not check_and_open_v6_with_routing(result):' in l:
        target_idx = i
        break

if target_idx is None:
    print('[FAIL] Target line not found')
    sys.exit(1)

print(f'Found at line {target_idx+1}: {lines[target_idx]}')
print(f'Next: {lines[target_idx+1]}')
print(f'Next2: {lines[target_idx+2]}')
print(f'Next3: {lines[target_idx+3]}')
print(f'Next7: {lines[target_idx+7]}')

# Verify structure: target, return False, blank, FeedbackLoop comment, _fb_res, if should_reject, warning, return False
assert lines[target_idx+1].strip() == 'return False', f'Expected return False, got: {lines[target_idx+1]}'
assert lines[target_idx+2].strip() == '', f'Expected blank, got: {lines[target_idx+2]}'
assert 'FeedbackLoop EV' in lines[target_idx+3], f'Expected FB comment, got: {lines[target_idx+3]}'
assert '_fb_res = result.get' in lines[target_idx+4], f'Expected _fb_res, got: {lines[target_idx+4]}'
assert 'should_reject' in lines[target_idx+5], f'Expected should_reject, got: {lines[target_idx+5]}'
assert 'slog.warning' in lines[target_idx+6], f'Expected warning, got: {lines[target_idx+6]}'
assert lines[target_idx+7].strip() == 'return False', f'Expected return False2, got: {lines[target_idx+7]}'
print('[OK] Structure verified')

# New block: breaker BEFORE routing
new_block = [
    '    # ===== FIX-20260913-FB-BREAKER-BEFORE-ROUTE =====',
    '    _fb_res = result.get("_feedback_result", {})',
    '    if _fb_res.get("should_reject", False):',
    '        slog.warning(f"[{symbol}] FeedbackLoop BREAKER: ev={_fb_res.get(\'ev\', 0):.4f}, confidence={_fb_res.get(\'confidence\', 0):.3f} < threshold={_fb_res.get(\'reject_threshold\', 0.30)}")',
    '        return False',
    '',
    '    if not check_and_open_v6_with_routing(result):',
    '        return False',
]

# Replace lines target_idx .. target_idx+7 (old 8 lines) with new_block (8 lines)
lines = lines[:target_idx] + new_block + lines[target_idx+8:]

io.open(path, 'w', encoding='utf-8').write('\n'.join(lines))
print('[OK] FIX applied: breaker now BEFORE routing')

# Syntax check
import py_compile
try:
    py_compile.compile(path, doraise=True)
    print('[OK] Syntax check passed')
except Exception as e:
    print(f'[FAIL] Syntax error: {e}')
    sys.exit(1)