import sys
lines = open('hf_auto_trader.py', 'r', encoding='utf-8').readlines()
new_lines = []
inserted = False
for i, l in enumerate(lines):
    new_lines.append(l)
    if 'MAX_DRAWDOWN_PCT' in l and 'return False' in l:
        j = i + 1
        while j < len(lines) and lines[j].strip() == '':
            j += 1
        if j < len(lines) and 'funding' not in lines[j].lower() and not inserted:
            indent = '    '
            new_lines.append('\n')
            new_lines.append(f'{indent}# ---- 资金费率过滤：高费率时不同向开单 ----\n')
            new_lines.append(f'{indent}funding = result.get("funding_rate")\n')
            new_lines.append(f'{indent}if funding is not None and abs(funding) > 0.0005:\n')
            new_lines.append(f'{indent}    if (direction == "Long" and funding > 0.0003) or (direction == "Short" and funding < -0.0003):\n')
            new_lines.append(f'{indent}        print(f"[{symbol}] 资金费率 {funding:.6f} 不利于 {direction}，跳过推送")\n')
            new_lines.append(f'{indent}        return False\n')
            new_lines.append('\n')
            inserted = True
with open('hf_auto_trader.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
print(f'Inserted funding filter: {inserted}')
