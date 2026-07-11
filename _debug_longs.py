# -*- coding: utf-8 -*-
import csv

with open('logs/trade_journal.csv', encoding='utf-8') as f:
    rows = list(csv.DictReader(f))

# BTC/USDT 信号时间线
btc = [r for r in rows if r['symbol'] == 'BTC/USDT']
btc.sort(key=lambda r: r['open_time'])

print('=== BTC/USDT 信号时间线 ===')
prev_dir = None
for r in btc:
    d = r['direction']
    dir_icon = 'L' if d == 'Long' else 'S'
    change = ''
    if prev_dir and prev_dir != d:
        change = ' <<< DIRECTION SWITCH'
    prev_dir = d
    
    note = r['note']
    adx = ''
    atr = ''
    vol = ''
    for part in note.split():
        if part.startswith('adx='):
            adx = part.replace('adx=', '')
        elif part.startswith('atr='):
            atr = part.replace('atr=', '')
        elif part.startswith('vol_ratio='):
            vol = part.replace('vol_ratio=', '')
    
    print(f'{r["open_time"][:19]}  {dir_icon}  price={r["open_price"]:>8}  score={r["score"]:>5}  regime={r["regime"]:>10}  rr={r["rr"]:>5}  adx={adx:>4}  atr={atr:>5}  vol={vol:>4}{change}')

print()
print('=== 分析：哪些 Long 信号有问题 ===')

# 找出在下跌趋势/行情中的 Long 信号
btc_longs = [r for r in btc if r['direction'] == 'Long']
for i, r in enumerate(btc_longs):
    prev_same_time = None
    # 检查这个 Long 之前是否有连续的 Short 信号
    idx = btc.index(r)
    shorts_before = 0
    for j in range(max(0, idx-5), idx):
        if btc[j]['direction'] == 'Short':
            shorts_before += 1
    
    note = r['note']
    adx = ''
    for part in note.split():
        if part.startswith('adx='):
            adx = part.replace('adx=', '')
    
    warning = ''
    if float(adx or 0) < 20 and r['regime'] in ('mud', 'transition'):
        warning = ' [WARN: low ADX in mud/transition]'
    if shorts_before >= 2:
        warning += f' [NOTE: {shorts_before} Shorts before this Long]'
    
    print(f'{r["open_time"][:19]}  price={r["open_price"]:>8}  score={r["score"]:>4}  adx={adx:>4}  regime={r["regime"]:>10}  prior_shorts={shorts_before}{warning}')

# 追加分析
print()
print('=== 数据异常检测 ===')
for r in rows:
    if float(r['open_price']) < 1000:
        print(f"ID={r['order_id']}")
        print(f"  symbol={r['symbol']} direction={r['direction']}")
        print(f"  open_time={r['open_time']}")
        print(f"  open_price={r['open_price']} sl={r['sl']} tp1={r['tp1']}")
        print(f"  score={r['score']} regime={r['regime']}")
        print(f"  note={r['note']}")
        print()

