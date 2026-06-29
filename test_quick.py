# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r'C:\Users\Administrator\Desktop\SMC_Bot')
import asyncio

async def test():
    from hf_auto_trader import scan_and_decide
    for sym in ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']:
        result = await scan_and_decide(sym)
        if result:
            print(f'{sym}: direction={result.get("direction")}, approved={result.get("approved")}, score={result.get("score")}, events={len(result.get("observer_events",[]))}')
        else:
            print(f'{sym}: FAILED')

asyncio.run(test())
