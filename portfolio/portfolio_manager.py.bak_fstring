# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
import json


@dataclass
class Position:
    symbol: str
    direction: str
    size: float
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp3: float
    opened_at: str
    state: str = "OPEN"
    remaining_size: Optional[float] = None
    filled_qty: Optional[float] = None
    avg_fill_price: Optional[float] = None
    exchange_order_id: Optional[str] = None
    client_order_id: Optional[str] = None
    order_status: str = "UNKNOWN"
    tp1_done: bool = False
    tp2_done: bool = False
    tp3_done: bool = False
    realized_r: float = 0.0
    source: str = "DecisionKernel"
    last_sync_at: Optional[str] = None

    def __post_init__(self):
        if self.remaining_size is None:
            self.remaining_size = float(self.size)
        if self.filled_qty is None:
            self.filled_qty = float(self.size)
        if self.avg_fill_price is None:
            self.avg_fill_price = float(self.entry)
        if self.last_sync_at is None:
            self.last_sync_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self):
        return asdict(self)


class PortfolioManager:
    def __init__(self, max_open_positions=3, max_same_direction_positions=2, state_path: str | None = "state/live_portfolio_state.json"):
        self.max_open_positions = int(max_open_positions)
        self.max_same_direction_positions = int(max_same_direction_positions)
        self.positions: Dict[str, Position] = {}
        self.loss_cooldowns: Dict[str, Dict[str, Any]] = {}
        self.zone_cooldowns: Dict[str, Dict[str, Any]] = {}
        self.state_path = Path(state_path) if state_path else None
        self.load_state()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def save_state(self):
        if not self.state_path:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "positions": {k: v.to_dict() for k, v in self.positions.items()},
                "loss_cooldowns": self.loss_cooldowns,
                "zone_cooldowns": self.zone_cooldowns,
                "updated_at": self._now(),
            }
            self.state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        except Exception:
            pass

    def load_state(self):
        if not self.state_path or not self.state_path.exists():
            return
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.positions = {}
            for symbol, raw in (payload.get("positions") or {}).items():
                if isinstance(raw, dict):
                    self.positions[symbol] = Position(**raw)
            self.loss_cooldowns = payload.get("loss_cooldowns") or {}
            self.zone_cooldowns = payload.get("zone_cooldowns") or {}
        except Exception:
            self.positions = {}
            self.loss_cooldowns = {}
            self.zone_cooldowns = {}

    def reconcile_from_exchange(self, exchange_positions: Dict[str, Any] | list | None):
        if not exchange_positions:
            return {"ok": True, "message": "no exchange positions supplied"}
        return {"ok": False, "message": "exchange reconciliation requires adapter-specific mapping", "raw": exchange_positions}

    def open_positions(self):
        return [p for p in self.positions.values() if p.state == "OPEN" and float(p.remaining_size or 0) > 0]

    def get_position(self, symbol):
        return self.positions.get(symbol)

    def can_open(self, symbol, direction):
        if symbol in self.positions and self.positions[symbol].state == "OPEN" and float(self.positions[symbol].remaining_size or 0) > 0:
            return False, "该币种已有持仓"
        positions = self.open_positions()
        if len(positions) >= self.max_open_positions:
            return False, "达到最大同时持仓数量"
        same_dir = [p for p in positions if p.direction == direction]
        if len(same_dir) >= self.max_same_direction_positions:
            return False, "同方向持仓过多"
        return True, "允许开仓"

    def add_position(self, symbol, direction, size, plan, order: Optional[Dict[str, Any]] = None):
        order = order or {}
        avg_price = order.get("average") or order.get("avg_fill_price") or order.get("price") or plan.get("entry")
        filled = order.get("filled") or order.get("amount") or size
        p = Position(
            symbol=symbol,
            direction=direction,
            size=float(filled),
            remaining_size=float(filled),
            filled_qty=float(filled),
            avg_fill_price=float(avg_price),
            entry=float(avg_price),
            sl=float(plan["sl"]),
            tp1=float(plan["tp1"]),
            tp2=float(plan["tp2"]),
            tp3=float(plan["tp3"]),
            opened_at=self._now(),
            exchange_order_id=str(order.get("id")) if order.get("id") is not None else None,
            client_order_id=str(order.get("clientOrderId")) if order.get("clientOrderId") is not None else None,
            order_status=str(order.get("status") or "UNKNOWN"),
            last_sync_at=self._now(),
        )
        self.positions[symbol] = p
        self.save_state()
        return p

    def reduce_position(self, symbol, close_size):
        p = self.positions.get(symbol)
        if not p:
            return None
        close_size = max(0.0, float(close_size or 0.0))
        p.remaining_size = max(0.0, float(p.remaining_size or p.size) - close_size)
        p.last_sync_at = self._now()
        if p.remaining_size <= 1e-12:
            p.state = "CLOSED"
            p.remaining_size = 0.0
        self.save_state()
        return p

    def close_position(self, symbol):
        p = self.positions.get(symbol)
        if p:
            p.state = "CLOSED"
            p.remaining_size = 0.0
            p.last_sync_at = self._now()
            self.save_state()
        return p

    def mark_loss_cooldown(self, symbol, minutes):
        self.loss_cooldowns[symbol] = {
            "until_ts": datetime.now(timezone.utc).timestamp() + float(minutes) * 60,
            "reason": "止损后冷却",
        }
        self.save_state()

    def is_in_loss_cooldown(self, symbol):
        item = self.loss_cooldowns.get(symbol)
        if not item:
            return False, "无冷却"
        if datetime.now(timezone.utc).timestamp() <= item["until_ts"]:
            return True, item.get("reason", "冷却中")
        self.loss_cooldowns.pop(symbol, None)
        self.save_state()
        return False, "冷却结束"