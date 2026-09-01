# -*- coding: utf-8 -*-
""" strategy/trade_management.py V2 Stable Compatible Version 鐩爣锛?1. 灏介噺淇濈暀鏃ч」鐩彲鑳借皟鐢ㄧ殑閫氱敤鍑芥暟鍚嶃€?2. 寮曞叆鍒嗗眰姝㈢泩姝㈡崯鍙傛暟锛?A 鍗曪細鏇磋兘鍚冭秼鍔?B 鍗曪細骞宠　鑳滅巼涓庣泩浜忔瘮 C 鍗曪細蹇繚鏈€佸揩姝㈢泩锛岄伩鍏嶈竟缂樹俊鍙锋嫋绱?3. 涓嶇牬鍧忓師鏈?-1R 鏈€澶т簭鎹熺粨鏋勩€?"""

from __future__ import annotations

from typing import Any, Dict, Optional

from utils.safe import safe_float, safe_bool, safe_str




def _lower(v: Any) -> str:
    try:
        return str(v or "").strip().lower()
    except Exception:
        return ""


def get_management_profile(entry_grade: str = "B", **kwargs: Any) -> Dict[str, Any]:
    """ 鏍规嵁鍏ュ満绛夌骇杩斿洖姝㈢泩姝㈡崯绠＄悊鍙傛暟銆?杩斿洖鍗曚綅锛?- *_r 閮芥槸 R 鍊嶆暟 - pct 鏄钩浠撴瘮渚?"""
    g = str(entry_grade or "B").upper()

    if g == "A":
        return {
            "grade": "A",
            "stop_r": -1.0,
            "be_trigger_r": 0.85,
            "be_offset_r": 0.03,
            "partial_tp1_r": 1.00,
            "partial_tp1_pct": 0.25,
            "partial_tp2_r": 1.80,
            "partial_tp2_pct": 0.25,
            "trail_trigger_r": 1.50,
            "trail_distance_r": 0.70,
            "time_decay_bars": 14,
            "max_hold_bars": 28,
        }

    if g == "C":
        return {
            "grade": "C",
            "stop_r": -1.0,
            "be_trigger_r": 0.60,
            "be_offset_r": 0.02,
            "partial_tp1_r": 0.75,
            "partial_tp1_pct": 0.50,
            "partial_tp2_r": 1.20,
            "partial_tp2_pct": 0.25,
            "trail_trigger_r": 1.00,
            "trail_distance_r": 0.45,
            "time_decay_bars": 8,
            "max_hold_bars": 16,
        }

    return {
        "grade": "B",
        "stop_r": -1.0,
        "be_trigger_r": 0.75,
        "be_offset_r": 0.02,
        "partial_tp1_r": 0.90,
        "partial_tp1_pct": 0.35,
        "partial_tp2_r": 1.50,
        "partial_tp2_pct": 0.25,
        "trail_trigger_r": 1.20,
        "trail_distance_r": 0.55,
        "time_decay_bars": 10,
        "max_hold_bars": 22,
    }


def build_trade_plan(context: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
    """ 鐢熸垚浜ゆ槗绠＄悊璁″垝銆?"""
    ctx: Dict[str, Any] = {}
    if isinstance(context, dict):
        ctx.update(context)
    ctx.update(kwargs)

    grade = str(ctx.get("entry_grade") or ctx.get("grade") or "B").upper()
    profile = get_management_profile(grade)

    entry = safe_float(ctx.get("entry") or ctx.get("entry_price"), 0.0)
    stop = safe_float(ctx.get("stop") or ctx.get("stop_price"), 0.0)
    side = _lower(ctx.get("side") or ctx.get("direction"))

    risk = abs(entry - stop) if entry and stop else safe_float(ctx.get("risk"), 0.0)

    plan = dict(profile)
    plan.update({
        "entry": entry,
        "stop": stop,
        "side": side,
        "risk": risk,
        "v2_management": True,
    })

    if entry and risk:
        if side == "short":
            plan["tp1"] = entry - profile["partial_tp1_r"] * risk
            plan["tp2"] = entry - profile["partial_tp2_r"] * risk
            plan["be_price"] = entry - profile["be_offset_r"] * risk
        else:
            plan["tp1"] = entry + profile["partial_tp1_r"] * risk
            plan["tp2"] = entry + profile["partial_tp2_r"] * risk
            plan["be_price"] = entry + profile["be_offset_r"] * risk

    return plan


def manage_trade(context: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
    """ 閫氱敤鍏煎绠＄悊鍑芥暟锛氳繑鍥炲綋鍓嶅簲閲囧彇鐨勫姩浣溿€?"""
    ctx: Dict[str, Any] = {}
    if isinstance(context, dict):
        ctx.update(context)
    ctx.update(kwargs)

    plan = build_trade_plan(ctx)
    current_r = safe_float(ctx.get("current_r") or ctx.get("unrealized_r"), 0.0)
    bars_held = int(safe_float(ctx.get("bars_held"), 0.0))

    action = "HOLD"
    reason = "NO_ACTION"

    if current_r <= -1.0:
        action = "STOP_LOSS"
        reason = "MAX_RISK_REACHED"
    elif current_r >= plan["partial_tp2_r"]:
        action = "PARTIAL_TP2_OR_TRAIL"
        reason = "TP2_REACHED"
    elif current_r >= plan["partial_tp1_r"]:
        action = "PARTIAL_TP1"
        reason = "TP1_REACHED"
    elif current_r >= plan["be_trigger_r"]:
        action = "MOVE_TO_BE"
        reason = "BE_TRIGGER_REACHED"

    if bars_held >= plan["max_hold_bars"] and current_r < 0.5:
        action = "CLOSE_TIME_DECAY"
        reason = "TIME_DECAY_WEAK_TRADE"

    return {
        "action": action,
        "reason": reason,
        "plan": plan,
        "current_r": current_r,
        "bars_held": bars_held,
    }


# 鏃ч」鐩彲鑳借皟鐢ㄨ繖浜涘埆鍚?
def trade_management_plan(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    return build_trade_plan(*args, **kwargs)


def update_trade_management(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    return manage_trade(*args, **kwargs)


def apply_trade_management(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    return manage_trade(*args, **kwargs)


def get_exit_plan(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    return build_trade_plan(*args, **kwargs)
