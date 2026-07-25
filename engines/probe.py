# -*- coding: utf-8 -*-
"""
LEGACY COMPATIBILITY WRAPPER — 已废弃

此模块仅为保持向后兼容而保留。
所有新代码应直接使用:
    from execution.probe_manager import probe_manager

将来会完全删除此文件。
"""

from __future__ import annotations
import warnings
from typing import Any, Dict

from execution.probe_manager import probe_manager as _probe_manager


class ProbeEngine:
    """兼容包装器 — 委托给 execution.probe_manager.ProbeManager。"""

    def __init__(self, base_risk: float = 0.08, min_expected_value: float = 0.0):
        self._manager = _probe_manager
        self.base_risk = base_risk
        self.min_expected_value = min_expected_value
        warnings.warn(
            "engines.probe.ProbeEngine 已废弃，请改用 execution.probe_manager.probe_manager",
            DeprecationWarning,
            stacklevel=2,
        )

    def should_trade(self, signal: Dict[str, Any]) -> tuple:
        score = float(signal.get("score", 0))
        confidence = float(signal.get("confidence", 0))
        observer_events = signal.get("observer_events", signal.get("events", []))
        if self._manager.allow_probe(score, confidence, observer_events):
            return True, "PROBE_ALLOW"
        reason = self._manager.reject_reason(score, confidence, observer_events)
        return False, f"PROBE_SKIP_{reason}"

    def process(self, row) -> float:
        return 0.0

    def risk_budget(self, signal, regime, vol_state) -> float:
        return min(self._manager.get_size_multiplier(), 0.32)

    def allocate(self, signal, risk) -> tuple:
        return "PROBE", min(risk, 0.32)

    def update_account(self, pnl_r: float) -> None:
        pass

    def state_dict(self) -> dict:
        return {"status": "DEPRECATED"}
