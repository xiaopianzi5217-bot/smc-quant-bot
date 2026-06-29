# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, "C:/Users/Administrator/Desktop/SMC_Bot")
import asyncio

async def debug():
    from hf_auto_trader import scan_and_decide
    from hf_auto_trader import SYMBOLS
    
    for sym in SYMBOLS:
        result = await scan_and_decide(sym)
        if result:
            d = result["decision"]
            sig = d.get("signal", {})
            print(f"\n{'='*50}")
            print(f"{sym}: {result.get('direction')} | approved={result.get('approved')}")
            print(f"  score={result.get('score')} | EV={result.get('expected_value'):.4f} | RR={result.get('rr')}")
            print(f"  regime={d.get('regime')} | vol={d.get('vol_state')} | book={d.get('book')} | size={d.get('size', 0)*100:.1f}%")
            print(f"  reason={d.get('reason')}")
            print(f"  entry={result.get('entry')} | SL={result.get('sl'):.1f} | TP1={result.get('tp1'):.1f} | TP2={result.get('tp2'):.1f} | TP3={result.get('tp3'):.1f}")
            print(f"  Long: {result.get('long_score')} | Short: {result.get('short_score')}")
            print(f"  Long EV: {result.get('long_ev'):.4f} | Short EV: {result.get('short_ev'):.4f}")
        else:
            print(f"{sym}: FAILED")

asyncio.run(debug())
