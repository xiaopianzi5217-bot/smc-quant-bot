# -*- coding: utf-8 -*-
import sys, asyncio
from pathlib import Path
_root = Path(__file__).parent.absolute()
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

async def quick_test():
    from hf_auto_trader import scan_and_decide
    result = await scan_and_decide('BTC/USDT')
    if result:
        print(f'扫描成功')
        print(f'direction: {result.get("direction")}')
        print(f'approved: {result.get("approved")}')
        print(f'score: {result.get("score")}')
        print(f'events: {len(result.get("observer_events", []))} 个')
    else:
        print('扫描失败')

if __name__ == '__main__':
    asyncio.run(quick_test())
