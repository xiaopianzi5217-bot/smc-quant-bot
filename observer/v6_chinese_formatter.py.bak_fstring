# -*- coding: utf-8 -*-

def direction_cn(d):
    return "做多" if d == "Long" else "做空"

def level_cn(l):
    return {"S":"S级高质量机会","A":"A级可开单机会","B":"B级观察机会","C":"C级普通提醒"}.get(str(l).upper(), str(l))

def format_observer_message(symbol, timeframe, regime, observer_items, plans=None):
    plans = plans or []
    lines = ["【结构提醒】", f"币种：{symbol}", f"周期：{timeframe}", "", "市场状态：", regime.get("summary_cn", "")]
    lines += ["", "触发提醒："]
    for i, item in enumerate(observer_items, 1):
        lines.append(f"{i}. {item.get('type')}：{item.get('text_cn')}")
        if item.get("advice_cn"):
            lines.append(f"   建议：{item.get('advice_cn')}")
    if plans:
        lines += ["", "参考交易计划（仅观察，不代表直接开单）："]
        for plan in plans:
            lines.append(
                f"- {direction_cn(plan['direction'])} 入场 {plan['entry']}，止损 {plan['sl']}，"
                f"止盈1 {plan['tp1']}，止盈2 {plan['tp2']}，止盈3 {plan['tp3']}；"
                f"锚点：{plan.get('anchor_source_cn')}"
            )
    return "\n".join(lines)

def format_trade_message(symbol, timeframe, regime, primary, risk_plan, execution=None):
    execution = execution or {}
    position = risk_plan.get("position") or {}
    lines = [
        "【开单信号】",
        f"币种：{symbol}",
        f"周期：{timeframe}",
        f"方向：{direction_cn(primary.get('direction'))}",
        f"级别：{level_cn(primary.get('level'))}",
        f"综合评分：{primary.get('v6_score')}，基础分：{primary.get('score')}，多空差：{primary.get('score_gap')}",
        "",
        "市场状态：",
        regime.get("summary_cn", ""),
        "",
        "触发原因：",
    ]
    for detail in primary.get("score_detail_cn", []):
        lines.append(f"- {detail}")
    lines += [
        "",
        "交易计划：",
        f"入场：{risk_plan['entry']}",
        f"止损：{risk_plan['sl']}",
        f"止盈1：{risk_plan['tp1']}",
        f"止盈2：{risk_plan['tp2']}",
        f"止盈3：{risk_plan['tp3']}",
        f"止损距离：{risk_plan['risk_distance']}",
        f"止损锚点：{risk_plan.get('anchor_source_cn')}",
    ]
    if position:
        lines += [
            "",
            "仓位建议：",
            f"可用仓位：{position.get('qty')}，名义价值：{position.get('notional')}，"
            f"本单风险：{position.get('risk_cash')}（{position.get('risk_pct')}）",
            f"仓位状态：{position.get('reason_cn')}",
        ]
    if execution:
        lines += ["", f"执行检查：{execution.get('reason_cn')}"]
    lines += ["", "建议：只按计划执行，未到入场区不追单；若价格先打止损区，本信号失效。"]
    return "\n".join(lines)
