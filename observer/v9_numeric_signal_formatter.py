# -*- coding: utf-8 -*-
"""
v9 中文数字化提醒。
要求：不用英文术语直接砸用户；OB / buyside / sellside 都带文字 + 数字。
"""


def _num(v, nd=4):
    try:
        return round(float(v), nd)
    except Exception:
        return "--"


def _pct_distance(price, level):
    try:
        price = float(price)
        level = float(level)
        if price == 0:
            return "--"
        return round(abs(price - level) / price * 100, 3)
    except Exception:
        return "--"


class V9NumericSignalFormatter:
    def build_alert(self, symbol, timeframe, price, signal, risk_plan=None, regime=None):
        signal = signal or {}
        risk_plan = risk_plan or {}
        regime = regime or {}

        lines = []
        lines.append("【量化结构提醒】")
        lines.append(f"币种：{symbol}")
        lines.append(f"周期：{timeframe}")
        lines.append(f"当前价格：{_num(price)}")
        lines.append("")

        lines.append("【市场环境】")
        lines.append(f"行情类型：{regime.get('regime_name', regime.get('regime', '未知'))}")
        lines.append(f"趋势强度：ADX={regime.get('adx', '--')}")
        lines.append(f"是否允许开单：{'允许' if regime.get('tradable') else '暂不允许'}")
        if regime.get("reason"):
            lines.append(f"原因：{regime.get('reason')}")
        lines.append("")

        lines.append("【结构位置】")
        ob_low = signal.get("ob_low") or signal.get("bullish_ob_low")
        ob_high = signal.get("ob_high") or signal.get("bearish_ob_high")
        if ob_low or ob_high:
            lines.append(f"订单块区间：{_num(ob_low)} - {_num(ob_high)}")
            ref = ob_low if ob_low else ob_high
            lines.append(f"距离订单块：{_pct_distance(price, ref)}%")

        buyside = signal.get("buyside_level") or signal.get("bsl_level") or signal.get("buy_side_level")
        sellside = signal.get("sellside_level") or signal.get("ssl_level") or signal.get("sell_side_level")
        if buyside:
            lines.append(f"买方流动性价格：{_num(buyside)}，距离：{_pct_distance(price, buyside)}%")
        if sellside:
            lines.append(f"卖方流动性价格：{_num(sellside)}，距离：{_pct_distance(price, sellside)}%")

        if signal.get("divergence") or signal.get("has_bottom_div") or signal.get("has_top_div"):
            lines.append("动能变化：出现背离，说明当前推动力可能减弱")
        if signal.get("kline_color"):
            color_map = {"white": "白色：中性过渡", "blue": "蓝色：买盘增强", "red": "红色：卖盘增强"}
            lines.append(f"K线状态：{color_map.get(signal.get('kline_color'), signal.get('kline_color'))}")
        lines.append("")

        lines.append("【评分与建议】")
        lines.append(f"综合评分：{signal.get('score', '--')}")
        lines.append(f"信号等级：{signal.get('grade', '--')}")
        lines.append(f"建议：{signal.get('advice', '等待确认，不追单')}")

        if risk_plan:
            lines.append("")
            lines.append("【交易计划】")
            lines.append(f"方向：{risk_plan.get('direction', '--')}")
            lines.append(f"参考入场：{_num(risk_plan.get('entry'))}")
            lines.append(f"止损：{_num(risk_plan.get('sl'))}")
            lines.append(f"止盈1：{_num(risk_plan.get('tp1'))}")
            lines.append(f"止盈2：{_num(risk_plan.get('tp2'))}")
            if risk_plan.get("tp3") is not None:
                lines.append(f"止盈3：{_num(risk_plan.get('tp3'))}")
            lines.append(f"风险收益比：{risk_plan.get('rr', '--')}")

        return "\n".join(lines)
