# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r'C:\Users\Administrator\Desktop\SMC_Bot')

# 模拟 app.py 启动时导入的模块
print('导入 app 模块...')
import app
print('app 导入完成')

# 测试 app 的 fetch 功能
print('测试 app.fetch...')
df = app._fetch_live_ohlcv('BTC/USDT', '15m', 100)
if df is not None:
    close_val = df.iloc[-1]['close']
    print(f'OK: {len(df)} bars, close={close_val:.2f}')
else:
    print('FAILED')
