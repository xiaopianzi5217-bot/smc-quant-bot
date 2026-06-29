# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _bool(v: Any, default: bool = False) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in {"true", "1", "yes", "y", "ok", "pass", "approved"}
    return bool(v)


def mark_strategy_approval(
    signal: Any = None,
    decision: Any = None,
    cfg: Any = None,
    *args,
    **kwargs,
) -> Dict[str, Any]:
    """
    兼容旧调用和新调用。

    旧调用：
        mark_strategy_approval()

    新调用：
        mark_strategy_approval(signal, decision, cfg)

    作用：
        只负责把 V9/V11 决策结果整理成统一 approved/state/reason，
        不再因为缺少 decision/cfg 直接抛异常。
    """

    symbol = (
        kwargs.get("symbol")
        or _get(signal, "symbol")
        or _get(decision, "symbol")
        or "UNKNOWN"
    )

    if decision is None:
        return {
            "symbol": symbol,
            "approved": False,
            "state": "NO_DECISION",
            "reason": "mark_strategy_approval called without decision; skipped instead of error",
        }

    approved = _bool(
        _get(decision, "approved", _get(decision, "allow", _get(decision, "passed", False)))
    )

    reason = (
        _get(decision, "reason")
        or _get(decision, "reject_reason")
        or _get(decision, "message")
        or ("approved" if approved else "decision_not_approved")
    )

    state = "APPROVED" if approved else "REJECTED"

    return {
        "symbol": symbol,
        "approved": approved,
        "state": state,
        "reason": str(reason),
        "decision": decision if isinstance(decision, dict) else None,
    }


def mark_approval(*args, **kwargs):
    return mark_strategy_approval(*args, **kwargs)


def strategy_approval(*args, **kwargs):
    return mark_strategy_approval(*args, **kwargs)