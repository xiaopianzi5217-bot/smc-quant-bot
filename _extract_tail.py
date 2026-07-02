# -*- coding: utf-8 -*-
with open('_fix_engine2.py', 'r', encoding='utf-8') as f:
    fix = f.read()

i = fix.find('tail = r')
j = fix.find('"""', i)
k = fix.find('"""', j + 3)
tail_code = fix[j+3:k]

with open('_tail_extracted.py', 'w', encoding='utf-8') as out:
    out.write(tail_code)

# Verify syntax
try:
    compile(tail_code, '_tail_extracted.py', 'exec')
    print('Syntax OK')
except SyntaxError as e:
    print(f'Syntax error: {e}')

print(f'Extracted {len(tail_code)} chars')
