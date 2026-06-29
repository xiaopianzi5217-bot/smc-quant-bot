# -*- coding: utf-8 -*-
import csv
import json
from datetime import datetime, timezone
from pathlib import Path


class TradeLogger:
    def __init__(self, path="data/v7_trade_journal.csv"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fields = [
            "ts", "event", "symbol", "direction", "state", "price", "size",
            "entry", "sl", "tp1", "tp2", "tp3", "score", "priority",
            "risk_amount", "notional", "message", "raw"
        ]
        if not self.path.exists():
            with self.path.open("w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=self.fields).writeheader()

    def log(self, event, symbol="", direction="", state="", price="", size="", entry="", sl="", tp1="", tp2="", tp3="", score="", priority="", risk_amount="", notional="", message="", raw=None):
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "symbol": symbol,
            "direction": direction,
            "state": state,
            "price": price,
            "size": size,
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "score": score,
            "priority": priority,
            "risk_amount": risk_amount,
            "notional": notional,
            "message": message,
            "raw": json.dumps(raw or {}, ensure_ascii=False),
        }
        with self.path.open("a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=self.fields).writerow(row)
        return row
