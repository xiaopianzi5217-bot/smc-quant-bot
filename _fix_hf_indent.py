# -*- coding: utf-8 -*-
with open('hf_auto_trader.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix the indentation issue around lines 668-680
# The problematic section is "entry = result["entry"]" through "reason = result["reason"]"
# Plus the msg block after it

old = '''    entry = result["entry"]
    sl = result["sl"]
    tp1 = result["tp1"]
        tp2 = result["tp2"]
    tp3 = result["tp3"]
    rr = result["rr"]
    regime = str(result.get("regime", "unknown"))
    vol_state = str(result.get("vol_state", "unknown"))
    book = result["book"]
    size = result["size"]
    reason = result["reason"]'''

new = '''    entry = result["entry"]
    sl = result["sl"]
    tp1 = result["tp1"]
    tp2 = result["tp2"]
    tp3 = result["tp3"]
    rr = result["rr"]
    regime = str(result.get("regime", "unknown"))
    vol_state = str(result.get("vol_state", "unknown"))
    book = result["book"]
    size = result["size"]
    reason = result["reason"]'''

if old in content:
    content = content.replace(old, new)
    print("Fixed indentation")
else:
    print("CANNOT FIND old pattern!")
    # Debug: find the actual text
    idx = content.find('entry = result["entry"]')
    if idx >= 0:
        print(f'Found at {idx}: {repr(content[idx:idx+400])}')

with open('hf_auto_trader.py', 'w', encoding='utf-8') as f:
    f.write(content)

try:
    compile(content, 'hf_auto_trader.py', 'exec')
    print('SYNTAX OK')
except SyntaxError as e:
    print(f'SYNTAX ERROR: {e}')
    # Show context around error
    lines = content.split('\n')
    if e.lineno:
        for i in range(max(0, e.lineno-3), min(len(lines), e.lineno+2)):
            print(f'  {i+1}: {repr(lines[i])}')
