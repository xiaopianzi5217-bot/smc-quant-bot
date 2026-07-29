# -*- coding: utf-8 -*-
"""Unified Telegram dispatch layer with strict signal separation.

Observer  层：只发结构变化，不经过策略中枢，不给开单结论。
Strategy  层：只发可执行机会，必须经过中枢测算、打分、风控审批。
Execution 层：只发持仓/订单生命周期事件，不发原始结构信号。
"""
from notifier.telegram import send_telegram
from notifier.observer.signal_formatter import format_signal_message
from notifier.layers import (
    observer_event_reasons,
    is_observer_event,
    strategy_approval_reason,
    execution_event_reasons,
    is_execution_event,
)


def dispatch_observer_snapshot(snapshot, send_all=False):
    """Observer channel: market-structure changes only; never approves trades."""
    reasons = observer_event_reasons(snapshot)
    if not send_all and not reasons:
        return "跳过：Observer 层没有结构变化"
    return send_telegram(
        format_signal_message(
            snapshot,
            message_type="OBSERVER",
            layer_reasons=reasons,
        )
    )


def dispatch_strategy_decision(snapshot, decision):
    """Strategy channel: only approved central-kernel opportunities are sent."""
    ok, reason = strategy_approval_reason(decision)
    if not ok:
        return f"跳过：Strategy 层未形成可执行机会：{reason}"
    return send_telegram(
        format_signal_message(
            snapshot,
            message_type="STRATEGY",
            layer_reasons=[reason],
            decision=decision,
        )
    )


def dispatch_open_decision(snapshot, decision):
    """Backward-compatible alias: old open signal now maps to Strategy layer."""
    return dispatch_strategy_decision(snapshot, decision)


def dispatch_execution_event(event):
    """Execution channel: position/order lifecycle only."""
    if not is_execution_event(event):
        return "跳过：Execution 层只处理持仓/订单生命周期事件"
    lines = execution_event_reasons(event)
    msg = "<b>🛡 Execution 持仓管理事件</b>\n" + "\n".join(lines)
    extra = event.get("detail") or event.get("raw")
    if extra:
        msg += f"\n\n详情：{extra}"
    return send_telegram(msg[:3900])


def dispatch_signal(r: dict):
    """Backward-compatible adapter for older code paths."""
    ok, reason = strategy_approval_reason(r)
    if ok:
        msg = (
            "🚀 <b>【Strategy 可执行机会】</b>\n"
            f"品种: {r.get('symbol')}\n"
            f"状态: {r.get('state')}\n"
            f"逻辑: {reason}"
        )
        return send_telegram(msg)
    return f"跳过：未获批：{reason}"
