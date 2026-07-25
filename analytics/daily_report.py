# analytics/daily_report.py
import time
from collections import defaultdict


class DailyReport:
    def __init__(self):
        self.daily = defaultdict(int)
        self.trades = 0
        self.probes = 0
        self.candidates = 0

    def record_candidate(self):
        self.candidates += 1

    def record_trade(self, mode="NORMAL"):
        self.trades += 1
        if mode == "PROBE":
            self.probes += 1

    def record_reject(self, stage, reason):
        key = f"{stage}:{reason}"
        self.daily[key] += 1

    def generate(self):
        lines = []
        lines.append("========== V56 DAILY REPORT ==========")
        lines.append("")
        lines.append(f"候选信号: {self.candidates}")
        lines.append(f"正式交易: {self.trades - self.probes}")
        lines.append(f"Probe交易: {self.probes}")
        lines.append("")
        lines.append("---- Reject统计 ----")
        total = sum(self.daily.values())
        if total:
            for k, v in sorted(self.daily.items(), key=lambda x: x[1], reverse=True):
                pct = v / total * 100
                lines.append(f"{k}: {v} ({pct:.1f}%)")
        else:
            lines.append("暂无拒绝数据")
        lines.append("")
        lines.append(time.strftime("%Y-%m-%d %H:%M:%S"))
        return "\n".join(lines)


daily_report = DailyReport()