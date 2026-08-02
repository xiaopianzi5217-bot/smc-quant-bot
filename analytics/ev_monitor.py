from collections import defaultdict


class EVMonitor:

    def __init__(self):
        self.buckets = defaultdict(
            lambda: {
                "samples": 0,
                "wins": 0,
                "total_r": 0.0,
                "ev_total": 0.0,
                "error_total": 0.0,
            }
        )

    def _bucket(self, ev):
        try:
            ev = float(ev)
        except Exception:
            return "NEG"
        if ev < 0:
            return "NEG"
        if ev < 1:
            return "0-1"
        if ev < 2:
            return "1-2"
        return ">2"

    def update(self, ev, pnl_r):
        key = self._bucket(ev)
        b = self.buckets[key]
        b["samples"] += 1
        if pnl_r is not None and pnl_r > 0:
            b["wins"] += 1
        try:
            b["total_r"] += float(pnl_r or 0.0)
        except Exception:
            pass
        try:
            b["ev_total"] += float(ev or 0.0)
        except Exception:
            pass
        try:
            b["error_total"] += float((pnl_r or 0.0) - (ev or 0.0))
        except Exception:
            pass

    def report(self):
        result = {}
        for k, v in self.buckets.items():
            n = v["samples"]
            if n:
                result[k] = {
                    "samples": n,
                    "win_rate": round(v["wins"] / n * 100, 2),
                    "avg_R": round(v["total_r"] / n, 3),
                    "avg_EV": round(v["ev_total"] / n, 3),
                    "EV_error": round(v["error_total"] / n, 3),
                }
            else:
                result[k] = {"samples": 0, "win_rate": 0, "avg_R": 0, "avg_EV": 0, "EV_error": 0}
        return result
