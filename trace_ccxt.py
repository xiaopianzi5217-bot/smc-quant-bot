# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r'C:\Users\Administrator\Desktop\SMC_Bot')

# Monkey-patch ccxt.async_support to log where it's created
import ccxt.async_support as ccxt_async
original_bitget = ccxt_async.bitget

class TracedBitget(original_bitget):
    def __init__(self, *args, **kwargs):
        import traceback
        print("=== ccxt_async.bitget CREATED ===")
        traceback.print_stack(limit=10)
        super().__init__(*args, **kwargs)

ccxt_async.bitget = TracedBitget

import asyncio
from hf_auto_trader import scan_and_decide

async def test():
    result = await scan_and_decide('BTC/USDT')
    if result:
        print(f'OK: {result.get("direction")}')
    else:
        print('FAILED')

asyncio.run(test())
