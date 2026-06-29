# -*- coding: utf-8 -*-
import json
import os
from pathlib import Path
from typing import Dict
from core.state import TradeState

def ensure_parent(path: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)

def load_active_trades(path: str) -> Dict[str, TradeState]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        return {symbol: TradeState.from_dict(item) for symbol, item in raw.items()}
    except Exception:
        return {}

def save_active_trades(path: str, trades: Dict[str, TradeState]):
    ensure_parent(path)
    tmp = path + '.tmp'
    payload = {symbol: trade.to_dict() for symbol, trade in trades.items()}
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
