# -*- coding: utf-8 -*-
import time

class HealthCheck:
    def __init__(self, max_data_age_sec=300): # 默认缩短到5分钟超时
        self.max_data_age_sec = max_data_age_sec
        self.last_tick = {}

    def mark_tick(self, symbol):
        self.last_tick[symbol] = time.time()

    def report(self):
        now = time.time()
        rows = []
        for symbol, ts in self.last_tick.items():
            age = now - ts
            rows.append({
                "symbol": symbol,
                "age_sec": round(age, 2),
                "status": "OK" if age <= self.max_data_age_sec else "STALE",
            })
        return rows

    def is_healthy(self):
        if not self.last_tick: 
            return True # 系统刚启动，免检测
        return all(r["status"] == "OK" for r in self.report())

    def check_stale_symbols(self):
        return [r["symbol"] for r in self.report() if r["status"] == "STALE"]
