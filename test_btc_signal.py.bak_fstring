# -*- coding: utf-8 -*-
"""测试 BTC 信号生成"""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.absolute()))

from hf_auto_trader import fetch_ohlcv, scan_and_decide

async def test():
    print("=== 测试 BTC 数据获取 ===")
    df = await fetch_ohlcv("BTC/USDT", "15m", 320)
    if df is None:
        print("fetch_ohlcv 返回 None")
        return
    print(f"获取到 {len(df)} 条数据")
    print(f"最新价: {df['close'].iloc[-1]:.2f}")
    
    print()
    print("=== 测试 scan_and_decide ===")
    result = await scan_and_decide("BTC/USDT")
    if result is None:
        print("scan_and_decide 返回 None")
    else:
        print(f"有信号! 方向={result['direction']}, score={result['score']}")
    
    print()
    print("=== 测试 ETH ===")
    result2 = await scan_and_decide("ETH/USDT")
    if result2 is None:
        print("ETH scan_and_decide 返回 None")
    else:
        print(f"ETH有信号! 方向={result2['direction']}, score={result2['score']}")

if __name__ == "__main__":
    asyncio.run(test())
