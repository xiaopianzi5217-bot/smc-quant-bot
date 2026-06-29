# -*- coding: utf-8 -*-
import sys
from pathlib import Path
_root = Path(__file__).parent.absolute()
sys.path.insert(0, str(_root))

# 测试修复后的_fetch_live_ohlcv
from app import _fetch_live_ohlcv, fetch_live_funding_rate

df = _fetch_live_ohlcv('BTC/USDT', '15m', 100)
close_val = df.iloc[-1]['close']
print(f'BTC 15m: {len(df)} bars, close={close_val:.2f}')

df2 = _fetch_live_ohlcv('BTC/USDT', '1h', 100)
close2 = df2.iloc[-1]['close']
print(f'BTC 1h: {len(df2)} bars, close={close2:.2f}')

fr = fetch_live_funding_rate('BTC/USDT')
print(f'Funding rate: {fr}')
