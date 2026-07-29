# -*- coding: utf-8 -*-
"""Simple paper broker for Hugging Face/local dry-run.

It records intended orders only. It never touches exchange APIs.
"""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from ops.runtime_paths import ARTIFACTS_DIR, ensure_runtime_dirs


@dataclass
class PaperOrder:
    symbol: str
    side: str
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp3: float
    qty: float = 0.0
    status: str = "paper_submitted"
    created_at: str = ""


class PaperBroker:
    def __init__(self, ledger_path=None):
        ensure_runtime_dirs()
        self.ledger_path = Path(ledger_path or ARTIFACTS_DIR / "paper_orders.jsonl")

    def submit(self, order):
        if isinstance(order, dict):
            o = dict(order)
        else:
            o = asdict(order)
        o.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        o.setdefault("status", "paper_submitted")
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.ledger_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
        return o

    def list_orders(self, limit=100):
        if not self.ledger_path.exists():
            return []
        rows = []
        with open(self.ledger_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows[-int(limit):]
