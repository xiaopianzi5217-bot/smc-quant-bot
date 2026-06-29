# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r'C:\Users\Administrator\Desktop\SMC_Bot')
import asyncio

async def test():
    from hf_auto_trader import scan_and_decide
    from hf_auto_trader import SYMBOLS
    
    for sym in SYMBOLS:
        result = await scan_and_decide(sym)
        if result:
            print(f'{sym}: direction={result.get("direction")}, approved={result.get("approved")}, score={result.get("score")}')
        else:
            print(f'{sym}: FAILED')

if __name__ == "__main__":
    asyncio.run(test())

