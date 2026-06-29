# -*- coding: utf-8 -*-
"""V6 risk engine. The previous file at this path was JSON-like content, so importing ``risk.v6_risk_engine`` crashed before the live runner could start. This module restores the Python class expected by ``decision.v6_decision_kernel`` while keeping the same default risk numbers. """
from __future__ import annotations

from typing import Any, Dict, Optional

try:
    from risk.position_sizing import fixed_fraction_position_size
except Exception:  # pragma: no cover
    from .position_sizing import fixed_fraction_position_size


DEFAULT_RISK_CONFIG: Dict[str, Any] = {
    "account_risk_pct": 0.01,
    "max_position_pct": 0.25,
    "max_daily_loss_pct": 0.03,
    "max_total_exposure_pct": 0.60,
    "atr_sl_mult": 1.5,
    "safety_atr_mult": 0.25,
    "tp1_rr": 1.0,
    "tp2_rr": 2.0,
    "tp3_rr": 3.0,
}


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        v = float(value)
        return v if v == v else default
    except Exception:
        return default


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class V6RiskEngine:
    """Build TP/SL and optional position plans for V6DecisionKernel."""

    def __init__(self, cfg: Optional[Dict[str, Any]] = None):
        self.cfg = dict(DEFAULT_RISK_CONFIG)
        if isinstance(cfg, dict):
            self.cfg.update(cfg)

    def _entry_atr(self, curr: Any, exec_ctx: Optional[Dict[str, Any]] = None) -> tuple[float, float]:
        entry = _num(_get(curr, "close", _get(curr, "price", 0.0)), 0.0)
        ctx = exec_ctx or {}
        atr = _num(ctx.get("atr") or ctx.get("ATRr_14") or _get(curr, "ATRr_14", _get(curr, "atr", 0.0)), 0.0)
        if atr <= 0 and entry > 0:
            atr = entry * 0.008
        return entry, atr

    def _build_levels(self, direction: str, curr: Any, exec_ctx: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
        entry, atr = self._entry_atr(curr, exec_ctx)
        atr_sl_mult = max(_num(self.cfg.get("atr_sl_mult"), 1.5), 0.01)
        stop_dist = max(atr * atr_sl_mult, entry * 0.001 if entry > 0 else 0.0)
        tp1_rr = _num(self.cfg.get("tp1_rr"), 1.0)
        tp2_rr = _num(self.cfg.get("tp2_rr"), 2.0)
        tp3_rr = _num(self.cfg.get("tp3_rr"), 3.0)

        if str(direction).lower() == "short":
            sl = entry + stop_dist
            tp1 = entry - stop_dist * tp1_rr
            tp2 = entry - stop_dist * tp2_rr
            tp3 = entry - stop_dist * tp3_rr
        else:
            sl = entry - stop_dist
            tp1 = entry + stop_dist * tp1_rr
            tp2 = entry + stop_dist * tp2_rr
            tp3 = entry + stop_dist * tp3_rr
        return {
            "entry": round(entry, 8), "sl": round(sl, 8), "tp1": round(tp1, 8),
            "tp2": round(tp2, 8), "tp3": round(tp3, 8), "rr": round(tp3_rr, 4),
            "atr": round(atr, 8), "stop_distance": round(stop_dist, 8),
        }

    def build_observer_plan(self, direction: str, curr: Any, exec_ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {"direction": direction, **self._build_levels(direction, curr, exec_ctx), "observer_only": True}

    def build_plan( self, direction: str, curr: Any, exec_ctx: Optional[Dict[str, Any]] = None, equity: Optional[float] = None, level: str = "A", ) -> Dict[str, Any]:
        levels = self._build_levels(direction, curr, exec_ctx)
        equity_f = _num(equity, 0.0)
        position = None
        if equity_f > 0:
            grade_mult = {"S": 1.0, "A": 1.0, "B": 0.5, "C": 0.0, "D": 0.0}.get(str(level or "A").upper()[:1], 1.0)
            position = fixed_fraction_position_size(
                equity=equity_f,
                entry=levels["entry"],
                stop_loss=levels["sl"],
                risk_per_trade=_num(self.cfg.get("account_risk_pct"), 0.01) * grade_mult,
                max_position_pct=self.cfg.get("max_position_pct"),
            )
        return {
            "direction": direction,
            **levels,
            "level": level,
            "position": position,
            "risk_model": "V6RiskEngine.fixed_fraction_atr",
        }