import sqlite3
from datetime import datetime, timezone, timedelta

BJ = timezone(timedelta(hours=8))
f = lambda ts: datetime.fromtimestamp(ts, BJ).strftime('%Y-%m-%d %H:%M:%S') if ts else None

conn = sqlite3.connect('data/v6_research.db')
conn.row_factory = sqlite3.Row
rows = conn.execute(
    'SELECT rowid, signal_id, timestamp, direction, exit_reason, exit_timestamp, pnl_r '
    'FROM trade_snapshots ORDER BY rowid'
).fetchall()
print(f'=== 共 {len(rows)} 行 ===')
for r in rows:
    print(f"row{r['rowid']:2d} | open={f(r['timestamp'])} | dir={str(r['direction']):5s} | "
          f"reason={str(r['exit_reason']):24s} | exit={f(r['exit_timestamp'])} | pnl={r['pnl_r']}")
conn.close()