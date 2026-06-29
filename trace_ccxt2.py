# -*- coding: utf-8 -*-
"""完全追踪ccxt_async的创建"""
import sys
sys.path.insert(0, r'C:\Users\Administrator\Desktop\SMC_Bot')

# 1. 先监视ccxt_async module
import ccxt.async_support as ccxt_async

# 保存原来的bitget类
_orig_bitget_class = ccxt_async.bitget

# 创建一个wrapper类
class _BitgetWrapper:
    def __new__(cls, *args, **kwargs):
        import traceback
        print("*** ccxt_async.bitget() INSTANTIATED! ***")
        traceback.print_stack(limit=15)
        return _orig_bitget_class(*args, **kwargs)

ccxt_async.bitget = _BitgetWrapper

import asyncio
from hf_auto_trader import scan_and_decide

async def test():
    print('Starting scan_and_decide...')
    try:
        result = await scan_and_decide('BTC/USDT')
        print(f'Result: {result is not None}')
    except Exception as e:
        import traceback
        print(f'Error: {e}')
        traceback.print_exc()

asyncio.run(test())
