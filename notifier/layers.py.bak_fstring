# -*- coding: utf-8 -*-
"""Signal layer rules for the SMC quant system.

The notification stack is deliberately split into three layers:

1. Observer layer: market-structure observations only. It never approves a trade.
2. Strategy layer: executable opportunities only, and only after the decision kernel approves.
3. Execution layer: position/order lifecycle management only. It must not emit raw structure alerts.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


OBSERVER_LAYER = "observer"
STRATEGY_LAYER = "strategy"
EXECUTION_LAYER = "execution"


def observer_event_reasons(snapshot) -> List[str]:
    """Return structural observation reasons from a SignalSnapshot.

    These are allowed to be sent directly because they describe market state,
    not executable trade permission.
    """
    d: Dict[str, Any] = snapshot.to_dict() if hasattr(snapshot, "to_dict") else dict(snapshot or {})
    reasons: List[str] = []

    if d.get("color_changed"):
        reasons.append(f"K线变色：{d.get('candle_color', 'N/A')}")
    if d.get("near_buyside"):
        reasons.append(f"价格接近 Buyside/BSL：{d.get('bsl_level', 'N/A')}")
    if d.get("near_sellside"):
        reasons.append(f"价格接近 Sellside/SSL：{d.get('ssl_level', 'N/A')}")
    if d.get("is_bsl_swept"):
        reasons.append("出现 BSL Sweep")
    if d.get("is_ssl_swept"):
        reasons.append("出现 SSL Sweep")
    if d.get("bullish_divergence"):
        reasons.append("SQZMOM 底背离确认")
    if d.get("bearish_divergence"):
        reasons.append("SQZMOM 顶背离确认")
        
    # 【新增】：OB 顺发事件翻译
    if d.get("near_bullish_ob"):
        reasons.append("价格接近/进入 Bullish OB (潜在做多反转区)")
    if d.get("near_bearish_ob"):
        reasons.append("价格接近/进入 Bearish OB (潜在做空反转区)")

    sqz = str(d.get("squeeze_dots", ""))
    if sqz.startswith("红点") or sqz.startswith("黄点"):
        reasons.append(f"SQZMOM 挤压提示：{sqz}")

    return reasons


def is_observer_event(snapshot) -> bool:
    return bool(observer_event_reasons(snapshot))


def strategy_approval_reason(decision: Dict[str, Any] | None) -> Tuple[bool, str]:
    """Only approved central-kernel decisions may become Strategy alerts."""
    if not decision:
        return False, "没有策略中枢决策结果"
    if not decision.get("approved"):
        return False, decision.get("reason") or decision.get("reason_cn") or "未通过策略中枢审批"
    return True, decision.get("reason") or decision.get("reason_cn") or "通过策略中枢审批"


def execution_event_reasons(event: Dict[str, Any] | None) -> List[str]:
    """Execution layer only reports position/order lifecycle events."""
    event = dict(event or {})
    allowed_types = {
        "POSITION_OPENED",
        "POSITION_CLOSED",
        "POSITION_REDUCED",
        "STOP_MOVED",
        "TP_HIT",
        "SL_HIT",
        "TRAILING_STOP_UPDATED",
        "PORTFOLIO_BLOCK",
        "COOLDOWN_BLOCK",
        "SIZING_BLOCK",
        "EXECUTION_ERROR",
    }
    event_type = str(event.get("type") or event.get("event") or "")
    if event_type not in allowed_types:
        return []
    symbol = event.get("symbol", "N/A")
    reason = event.get("reason") or event.get("message") or event_type
    return [f"{event_type}：{symbol}｜{reason}"]


def is_execution_event(event: Dict[str, Any] | None) -> bool:
    return bool(execution_event_reasons(event))
