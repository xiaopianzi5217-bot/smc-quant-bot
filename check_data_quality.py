
import pandas as pd
import sqlite3

conn = sqlite3.connect('data/ev_training.db')
df = pd.read_sql_query('SELECT * FROM ev_samples', conn)
conn.close()

print('=== 按source分组统计 ===')
for src in df['source'].unique():
    sub = df[df['source'] == src]
    print(f"{src}: n={len(sub)}, avg_pnl={sub['pnl_r'].mean():.4f}, avg_ev={sub['realized_ev'].mean():.4f}, win={(sub['pnl_r']>0).mean():.3f}")

print('\n=== realized_ev 唯一值 ===')
print(df['realized_ev'].unique()[:20])

print('\n=== pnl_r 描述统计 ===')
print(df['pnl_r'].describe())

print('\n=== 按日期分布 ===')
df['date'] = df['datetime'].str[:10]
print(df.groupby('date')['pnl_r'].agg(['count', 'mean']))

print('\n=== 按方向分布 ===')
if 'direction' in df.columns:
    print(df.groupby('direction')['pnl_r'].agg(['count', 'mean']))
