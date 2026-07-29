# -*- coding: utf-8 -*-
"""Strategy filter compatibility layer. Safety invariant: DecisionKernel is the only layer that may create approved=True. Strategy filters may keep an approved signal approved or downgrade/block it, but they must never turn HOLD/OBSERVE/REJECTED into approved=True. """
from __future__ import annotations

from typing import Any, Dict, Optional

try:
    from strategy.entry_quality import grade_entry_quality
except Exception:  # pragma: no cover
    from .entry_quality import grade_entry_quality

_FALSE_STRINGS = {"0", "false", "no", "reject", "rejected", "blocked", "hold", "observe", "none"}
_TRUE_STRINGS = {"1", "true", "yes", "ok", "pass", "approved", "allow", "allowed"}


def _safe_bool(v: Any, default: bool = True) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in _FALSE_STRINGS:
            return False
        if s in _TRUE_STRINGS:
            return True
    return bool(v)


def _as_dict(v: Any) -> Dict[str, Any]:
    if isinstance(v, dict):
        return dict(v)
    if hasattr(v, "to_dict"):
        try:
            return dict(v.to_dict())
        except Exception:
            return {}
    return {}


def _looks_like_decision(d: Dict[str, Any]) -> bool:
    return any(k in d for k in ("approved", "decision_approved", "is_approved", "action", "state", "side", "entry_signal", "risk_plan", "primary"))


def _normalize_context(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    ctx: Dict[str, Any] = {}

    if args and isinstance(args[0], str):
        ctx["symbol"] = args[0]
        if len(args) > 1:
            ctx["curr"] = args[1]
            ctx.update(_as_dict(args[1]))
        if len(args) > 2:
            d2 = _as_dict(args[2])
            if _looks_like_decision(d2):
                ctx["decision"] = d2
            else:
                ctx["macro_ctx"] = d2
                ctx.update({k: v for k, v in d2.items() if k not in ctx})
        if len(args) > 3:
            d3 = _as_dict(args[3])
            if _looks_like_decision(d3):
                ctx["decision"] = d3
            else:
                ctx["exec_ctx"] = d3
                ctx.update(d3)
        if len(args) > 4:
            d4 = _as_dict(args[4])
            if _looks_like_decision(d4):
                ctx["decision"] = d4
            else:
                ctx["cfg"] = d4
        if len(args) > 5:
            ctx["cfg"] = _as_dict(args[5]) or ctx.get("cfg", {})
    else:
        for arg in args:
            d = _as_dict(arg)
            if not d:
                continue
            if _looks_like_decision(d):
                ctx.setdefault("decision", d)
            ctx.update(d)

    for key in ("curr", "macro_ctx", "exec_ctx", "decision", "cfg"):
        if key in kwargs:
            d = _as_dict(kwargs[key])
            ctx[key] = d if d else kwargs[key]
            if key in {"curr", "exec_ctx"} and d:
                ctx.update(d)
    for k, v in kwargs.items():
        if k not in {"curr", "macro_ctx", "exec_ctx", "decision", "cfg"}:
            ctx[k] = v

    decision = _as_dict(ctx.get("decision"))
    if decision:
        ctx["decision"] = decision
        for k in ("direction", "rr", "risk_plan", "primary", "action", "side", "state", "reason"):
            if k in decision and k not in ctx:
                ctx[k] = decision[k]
        rp = _as_dict(decision.get("risk_plan"))
        if rp:
            ctx.setdefault("rr", rp.get("rr"))
            ctx.setdefault("direction", rp.get("direction"))
    return ctx


def _decision_allows_entry(decision: Dict[str, Any]) -> bool:
    if not decision:
        return True
    approved = _safe_bool(decision.get("approved"), False)
    decision_approved = _safe_bool(decision.get("decision_approved"), approved)
    is_approved = _safe_bool(decision.get("is_approved"), approved)
    action = str(decision.get("action", "")).strip().upper()
    state = str(decision.get("state") or decision.get("state_name") or "").strip().upper()
    side = str(decision.get("side") or decision.get("entry_signal") or "").strip().upper()
    if action in {"HOLD", "OBSERVE", "NONE"}:
        return False
    if state in {"HOLD", "OBSERVE", "REJECTED", "BLOCKED", "PORTFOLIO_BLOCKED", "STRATEGY_FILTER_BLOCKED"}:
        return False
    if side in {"NONE", "HOLD", ""} and action not in {"BUY", "SELL"}:
        return False
    return approved and decision_approved and is_approved


def check_strategy_filters(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    ctx = _normalize_context(*args, **kwargs)
    decision = _as_dict(ctx.get("decision"))

    if decision and not _decision_allows_entry(decision):
        reason = decision.get("reason") or decision.get("reason_cn") or decision.get("state") or "DECISION_NOT_APPROVED"
        return {
            "approved": False, "allowed": False, "allow_entry": False, "entry_ok": False,
            "ok": False, "blocked": True, "reason": str(reason), "reasons": [str(reason)],
            "reject_reason": str(reason), "entry_grade": "REJECT", "quality_score": 0.0,
            "score": 0.0, "size_mult": 0.0, "rr_min": 0.0, "be_trigger_r": 0.0,
            "trail_trigger_r": 0.0, "partial_tp1_r": 0.0, "partial_tp1_pct": 0.0,
            "v2_filter": True, "source": "decision_guard",
        }

    grade = grade_entry_quality(ctx)
    approved = bool(grade.get("allowed", True))
    reasons = list(grade.get("reasons") or [])
    reason = grade.get("reason", "")

    if _safe_bool(ctx.get("invalid_risk"), False):
        approved = False; reasons.append("REJECT_INVALID_RISK")
    if _safe_bool(ctx.get("spread_bad"), False):
        approved = False; reasons.append("REJECT_SPREAD")
    if _safe_bool(ctx.get("news_pause"), False):
        approved = False; reasons.append("REJECT_NEWS_PAUSE")

    if reasons:
        reason = "|".join([str(x) for x in reasons])
    elif not reason:
        reason = "APPROVED" if approved else "REJECTED"

    return {
        "approved": approved, "allowed": approved, "allow_entry": approved, "entry_ok": approved,
        "ok": approved, "blocked": not approved, "reason": reason,
        "reasons": reasons if reasons else ([reason] if not approved else []),
        "reject_reason": "" if approved else reason,
        "entry_grade": grade.get("grade", "B"), "quality_score": grade.get("score", 60.0),
        "score": grade.get("score", 60.0), "size_mult": grade.get("size_mult", 0.70),
        "rr_min": grade.get("rr_min", 1.10), "be_trigger_r": grade.get("be_trigger_r", 0.80),
        "trail_trigger_r": grade.get("trail_trigger_r", 1.25),
        "partial_tp1_r": grade.get("partial_tp1_r", 1.0),
        "partial_tp1_pct": grade.get("partial_tp1_pct", 0.30),
        "v2_filter": True, "source": "entry_quality",
    }


def mark_strategy_approval(context: Optional[Dict[str, Any]] = None, *args: Any, **kwargs: Any) -> Dict[str, Any]:
    ctx = _normalize_context(context, *args, **kwargs)
    checked = check_strategy_filters(ctx)
    decision = _as_dict(ctx.get("decision"))
    original_allowed = _decision_allows_entry(decision) if decision else _safe_bool(ctx.get("approved"), checked["approved"])
    final_approved = bool(original_allowed and checked["approved"])

    if decision:
        out = dict(decision)
        if final_approved:
            out["approved"] = True
            out["strategy_approved"] = True
            out["allow_entry"] = True
            out["entry_ok"] = True
            out.setdefault("state", "APPROVED")
            out.setdefault("reason", checked["reason"])
        else:
            out["approved"] = False
            out["strategy_approved"] = False
            out["allow_entry"] = False
            out["entry_ok"] = False
            if original_allowed and not checked["approved"]:
                out["state"] = "STRATEGY_FILTER_BLOCKED"
                out["state_name"] = "STRATEGY_FILTER_BLOCKED"
                out["reason"] = checked["reason"]
                out["reason_cn"] = checked["reason"]
            else:
                # Preserve or reconstruct the DecisionKernel non-entry state so
                # UI/audit output does not show state=None for HOLD decisions.
                out.setdefault("state", out.get("action") or "HOLD")
                out.setdefault("state_name", out.get("state"))
                out.setdefault("reason", checked["reason"])
                out.setdefault("reason_cn", out.get("reason"))
        out["v2_filter_result"] = checked
        out["entry_grade"] = checked["entry_grade"]
        out["quality_score"] = checked["quality_score"]
        out["size_mult"] = checked["size_mult"]
        out["approval_reason"] = checked["reason"]
        out["reject_reason"] = checked["reject_reason"] or out.get("reason", "")
        out.setdefault("symbol", ctx.get("symbol", decision.get("symbol", "UNKNOWN")))
        return out

    ctx["strategy_approved"] = final_approved
    ctx["approved"] = final_approved
    ctx["allow_entry"] = final_approved
    ctx["entry_ok"] = final_approved
    ctx["approval_reason"] = checked["reason"]
    ctx["reject_reason"] = checked["reject_reason"]
    ctx["entry_grade"] = checked["entry_grade"]
    ctx["quality_score"] = checked["quality_score"]
    ctx["size_mult"] = checked["size_mult"]
    ctx["v2_filter_result"] = checked
    return ctx


def strategy_filter(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    return check_strategy_filters(*args, **kwargs)


def approve_trade(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    return check_strategy_filters(*args, **kwargs)