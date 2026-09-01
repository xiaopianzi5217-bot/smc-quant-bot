# -*- coding: utf-8 -*-
"""检查数据库中 OPEN 状态的记录 - 验证时间戳单位和字段状态"""
import sqlite3
import time
from v6_data_engine import _get_db_path

conn = sqlite3.connect(str(_get_db_path()))
cur = conn.cursor()

# 查看所有 OPEN 状态的记录
cur.execute("SELECT signal_id, timestamp, exit_reason, exit_timestamp, pnl_r, exit_price FROM trade_snapshots WHERE exit_reason='OPEN'")
rows = cur.fetchall()
print(f'Open trades found: {len(rows)}')
for r in rows[:10]:
    print(f'  signal_id={r[0]}, ts={r[1]} (digits={len(str(r[1]))}), exit_reason={r[2]}, exit_ts={r[3]}, pnl_r={r[4]}, exit_price={r[5]}')

# 检查最大 timestamp
cur.execute("SELECT MAX(timestamp), MIN(timestamp) FROM trade_snapshots")
row = cur.fetchone()
print(f'\nTimestamp range in DB: min={row[1]}, max={row[0]}')
print(f'Current time (sec): {int(time.time())}')
print(f'Current time (ms): {int(time.time()*1000)}')

conn.close()