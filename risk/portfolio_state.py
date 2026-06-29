# -*- coding: utf-8 -*-
"""Portfolio state helpers for risk guard integration."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, Iterable

from ops.state_store import JsonStateStore
from risk.global_risk import GlobalRiskState


def state_from_dict(data: Dict[str, Any] | None) -> GlobalRiskState:
    data = data or {}
    allowed = {f.name for f in GlobalRiskState.__dataclass_fields__.values()}
    clean = {k: data[k] for k in allowed if k in data}
    return GlobalRiskState(**clean)


class PortfolioStateManager:
    def __init__(self, store_name: str = "portfolio_state.json"):
        self.store = JsonStateStore(store_name)

    def load(self) -> GlobalRiskState:
        return state_from_dict(self.store.load())

    def save(self, state: GlobalRiskState) -> None:
        self.store.save(asdict(state))

    def mark_decisions(self, results: Iterable[Dict[str, Any]]) -> GlobalRiskState:
        state = self.load()
        approved = [r for r in results if r.get("approved")]
        state.open_positions = max(state.open_positions, len(approved))
        self.save(state)
        return state
