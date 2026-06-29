
# -*- coding: utf-8 -*-
from typing import Dict, Any
from backtest.v37_master_engine import V37MasterEngine

class V38MultiAlphaEngine:
    """
    V38 Multi-Alpha Architecture:
    splits V37 output into independent alpha engines
    """

    def __init__(self):
        self.base = V37MasterEngine()

    def run(self, data: Any) -> Dict[str, Any]:
        result = self.base.run(data)

        trades = result.get("trades", [])

        def filter_by(cond):
            return [t for t in trades if cond(t)]

        transition = filter_by(lambda t: t.get("regime") == "TRANSITION" and t.get("grade") == "A_EV")
        core = filter_by(lambda t: t.get("book") == "CORE" and t.get("grade") == "A_EV")
        trend = filter_by(lambda t: t.get("regime") == "TREND")
        probe = filter_by(lambda t: t.get("book") == "PROBE")

        def summarize(ts):
            if not ts:
                return {"trades": 0, "pf": 0, "pnl": 0}
            wins = sum(1 for t in ts if t.get("pnl_r",0) > 0)
            losses = len(ts) - wins
            pf = (wins+1e-9)/(losses+1e-9)
            pnl = sum(t.get("pnl_r",0) for t in ts)
            return {"trades": len(ts), "pf": pf, "pnl": pnl}

        return {
            "overall": result.get("summary", {}),
            "engines": {
                "TRANSITION": summarize(transition),
                "CORE": summarize(core),
                "TREND": summarize(trend),
                "PROBE": summarize(probe),
            },
            "raw_trades": len(trades)
        }
