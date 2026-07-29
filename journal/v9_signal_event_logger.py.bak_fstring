# -*- coding: utf-8 -*-
"""
v9 Signal Event Logger
把每一个提醒、拒绝、开单、平仓都记录成训练数据。
"""

import csv
import json
from pathlib import Path
from datetime import datetime, timezone


class V9SignalEventLogger:
    def __init__(self, path="data/v9_signal_events.csv"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fields = [
            "ts", "event_type", "symbol", "timeframe", "direction",
            "grade", "score", "priority", "approved", "reason",
            "entry", "sl", "tp1", "tp2", "tp3", "regime",
            "raw_json",
        ]
        if not self.path.exists():
            with self.path.open("w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=self.fields).writeheader()

    def log(self, event_type, symbol, timeframe, payload):
        payload = payload or {}
        primary = payload.get("primary") or payload.get("signal") or {}
        risk = payload.get("risk_plan") or {}
        regime = payload.get("regime") or {}
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "symbol": symbol,
            "timeframe": timeframe,
            "direction": primary.get("direction") or risk.get("direction"),
            "grade": primary.get("grade"),
            "score": primary.get("score"),
            "priority": primary.get("priority"),
            "approved": payload.get("approved"),
            "reason": payload.get("reason"),
            "entry": risk.get("entry"),
            "sl": risk.get("sl"),
            "tp1": risk.get("tp1"),
            "tp2": risk.get("tp2"),
            "tp3": risk.get("tp3"),
            "regime": regime.get("regime_name") or regime.get("regime"),
            "raw_json": json.dumps(payload, ensure_ascii=False, default=str),
        }
        with self.path.open("a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=self.fields).writerow(row)
        return row
