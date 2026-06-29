# -*- coding: utf-8 -*-
"""Position sizing helpers used by V6/V7/V9 execution and grading layers.

This module intentionally exposes both the newer grade-based helper and the
older fixed-fraction/calc_position_size helpers.  Several runners import these
legacy names directly; removing them breaks V6DecisionKernel and the live
runner before any signal can be approved or sent to Telegram.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


DEFAULT_POSITION_SIZING = {
    "enabled": True,
    "grade_risk_multiplier": {"S": 1.0, "A": 0.5, "B": 0.0, "C": 0.0, "D": 0.0},
    "observe_grades": ["B", "C", "D"],
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        v = float(value)
        return default if v != v else v
    except Exception:
        return default


def _grade_letter(value: Any, default: str = "B") -> str:
    grade = str(value or default).strip().upper()
    if not grade:
        return default
    return grade[0]


def _get_grade(decision: Optional[Dict[str, Any]]) -> str:
    primary = (decision or {}).get("primary") or {}
    return _grade_letter(primary.get("grade") or primary.get("level") or "B")


def _position_sizing_cfg(cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    sizing = dict(DEFAULT_POSITION_SIZING)
    sizing.update((cfg or {}).get("position_sizing", {}) or {})
    sizing["grade_risk_multiplier"] = {
        **DEFAULT_POSITION_SIZING["grade_risk_multiplier"],
        **(sizing.get("grade_risk_multiplier") or {}),
    }
    return sizing


def apply_grade_position_sizing(decision: Optional[Dict[str, Any]], cfg: Optional[Dict[str, Any]]):
    """Apply grade-based risk multiplier to an already approved decision.

    The function does not create a trade.  It only scales the risk fields in the
    risk plan and, when the configured grade is observe-only or has zero size,
    converts the decision back to OBSERVE so Strategy alerts/orders are not sent.
    """
    decision = decision or {}
    sizing = _position_sizing_cfg(cfg)
    if not sizing.get("enabled", True):
        decision["position_sizing"] = {"enabled": False}
        return decision

    grade = _get_grade(decision)
    multiplier = _safe_float((sizing.get("grade_risk_multiplier") or {}).get(grade), 0.0)
    observe_grades = {_grade_letter(x) for x in sizing.get("observe_grades", ["B", "C", "D"])}

    decision["position_sizing"] = {
        "enabled": True,
        "grade": grade,
        "risk_multiplier": multiplier,
        "observe_only": grade in observe_grades or multiplier <= 0,
    }

    if decision.get("approved") and (grade in observe_grades or multiplier <= 0):
        decision["approved"] = False
        decision["state_name"] = "OBSERVE"
        decision["state"] = "OBSERVE"
        decision["reason"] = f"信号等级 {grade} 只观察，不开单"
        decision["reason_cn"] = decision["reason"]
        return decision

    risk_plan = decision.get("risk_plan") or {}
    position = risk_plan.get("position") or {}
    for key in ("risk_pct", "account_risk_pct", "size", "qty", "notional", "risk_amount"):
        if key in position and position[key] is not None:
            try:
                position[key] = float(position[key]) * multiplier
            except Exception:
                pass
    if position:
        position["risk_multiplier"] = multiplier
        position["grade"] = grade
        risk_plan["position"] = position
        decision["risk_plan"] = risk_plan
    return decision


def fixed_fraction_position_size(
    equity: Any,
    entry: Any,
    stop_loss: Any,
    risk_per_trade: Any = 0.01,
    max_position_pct: Any = None,
) -> Dict[str, Any]:
    """Build a fixed-fraction position plan.

    Returns the legacy keys expected by V6RiskEngine: ``allowed`` and ``qty``.
    Extra fields are included for reporting and downstream sizing.
    """
    equity_f = _safe_float(equity)
    entry_f = _safe_float(entry)
    stop_f = _safe_float(stop_loss)
    risk_pct = max(0.0, _safe_float(risk_per_trade, 0.01))

    if equity_f <= 0 or entry_f <= 0 or stop_f <= 0:
        return {"allowed": False, "qty": 0.0, "reason_cn": "权益/入场价/止损价无效"}

    risk_per_unit = abs(entry_f - stop_f)
    if risk_per_unit <= 0:
        return {"allowed": False, "qty": 0.0, "reason_cn": "止损距离无效"}

    risk_amount = equity_f * risk_pct
    qty = risk_amount / risk_per_unit if risk_amount > 0 else 0.0
    notional = qty * entry_f

    max_pct = _safe_float(max_position_pct, 0.0) if max_position_pct is not None else 0.0
    if max_pct > 0:
        max_notional = equity_f * max_pct
        if max_notional > 0 and notional > max_notional:
            notional = max_notional
            qty = notional / entry_f

    return {
        "allowed": qty > 0,
        "qty": round(qty, 8),
        "size": round(qty, 8),
        "notional": round(notional, 4),
        "risk_amount": round(risk_amount, 4),
        "risk_pct": risk_pct,
        "entry": round(entry_f, 6),
        "stop_loss": round(stop_f, 6),
        "reason_cn": "固定比例仓位计算完成" if qty > 0 else "风险比例为 0，未开仓",
    }


def calc_position_size(
    balance: Any,
    risk_pct: Any,
    entry: Any,
    stop_loss: Any,
    min_notional: Any = 5.0,
    max_notional: Any = None,
) -> Dict[str, Any]:
    """Legacy live-engine position sizing API.

    ``execution.live_engine`` expects ``ok``, ``size``, ``risk_amount`` and
    ``notional``.  This wrapper keeps that contract while sharing the safer
    fixed-fraction calculation above.
    """
    plan = fixed_fraction_position_size(balance, entry, stop_loss, risk_pct)
    if not plan.get("allowed"):
        return {
            "ok": False,
            "size": 0.0,
            "qty": 0.0,
            "risk_amount": 0.0,
            "notional": 0.0,
            "reason": plan.get("reason_cn", "仓位计算失败"),
        }

    notional = _safe_float(plan.get("notional"), 0.0)
    min_notional_f = _safe_float(min_notional, 0.0)
    if min_notional_f > 0 and notional < min_notional_f:
        return {
            "ok": False,
            "size": 0.0,
            "qty": 0.0,
            "risk_amount": plan.get("risk_amount", 0.0),
            "notional": notional,
            "reason": f"名义价值不足: {round(notional, 4)} < min_notional={min_notional_f}",
        }

    max_notional_f = _safe_float(max_notional, 0.0) if max_notional is not None else 0.0
    if max_notional_f > 0 and notional > max_notional_f:
        size = max_notional_f / _safe_float(entry, 1.0)
        notional = max_notional_f
    else:
        size = _safe_float(plan.get("qty"), 0.0)

    return {
        "ok": size > 0,
        "size": round(size, 8),
        "qty": round(size, 8),
        "risk_amount": plan.get("risk_amount", 0.0),
        "notional": round(notional, 4),
        "reason": "仓位计算通过" if size > 0 else "仓位为 0",
    }
