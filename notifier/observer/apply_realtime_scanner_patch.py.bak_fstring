# -*- coding: utf-8 -*-
from pathlib import Path

p = Path("notifier/observer/realtime_scanner.py")
s = p.read_text(encoding="utf-8")

old = """ decision = V9DecisionKernel(params=cfg).decide( curr=curr, macro_ctx=macro_ctx, exec_ctx=exec_ctx, long_score=l_score, long_threshold=l_thresh, long_reasons=l_reasons, short_score=s_score, short_threshold=s_thresh, short_reasons=s_reasons, min_rr=min_rr, symbol=symbol, timeframe=exec_timeframe, cfg=cfg, )"""

new = """ decision = V9DecisionKernel(params=cfg).decide( curr=curr, macro_ctx=macro_ctx, exec_ctx=exec_ctx, long_score=l_score, long_threshold=l_thresh, long_reasons=l_reasons, short_score=s_score, short_threshold=s_thresh, short_reasons=s_reasons, min_rr=min_rr, rr=rr, direction=direction, entry=price, sl=sl, tp1=tp1, tp2=tp2, tp3=tp3, symbol=symbol, timeframe=exec_timeframe, cfg=cfg, )"""

if old not in s:
    raise SystemExit("Patch target not found. Your realtime_scanner.py may already be patched or has different formatting.")

p.write_text(s.replace(old, new), encoding="utf-8")
print("patched notifier/observer/realtime_scanner.py")