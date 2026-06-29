# -*- coding: utf-8 -*-

class V6SignalObserver:
    """观察层：所有关键结构都提醒，不漏发；每条提醒带文字与数字。"""

    def __init__(self, params=None):
        p = params or {}
        self.near_level_atr_mult = float(p.get('near_level_atr_mult', 0.8))

    def collect(self, curr, exec_ctx):
        price = self._num(curr.get('close'))
        atr = self._atr(curr, price)
        out = []

        # K线颜色/动能状态
        color = exec_ctx.get('kline_color', exec_ctx.get('bar_color', None))
        if color:
            color_cn = {'white': '白色：动能过渡', 'blue': '蓝色：多头动能增强', 'red': '红色：空头动能增强'}.get(str(color).lower(), str(color))
            out.append(self._item('K线动能变化', color_cn, price, None, 0, '观察动能方向变化'))

        # buyside / sellside
        self._near_level(out, '买方流动性', 'buyside_level', curr, exec_ctx, '价格接近上方流动性，警惕扫高后回落或突破加速')
        self._near_level(out, '卖方流动性', 'sellside_level', curr, exec_ctx, '价格接近下方流动性，警惕扫低后反弹或跌破加速')
        self._near_level(out, '买方流动性', 'bsl_level', curr, exec_ctx, '价格接近上方流动性，警惕扫高')
        self._near_level(out, '卖方流动性', 'ssl_level', curr, exec_ctx, '价格接近下方流动性，警惕扫低')

        # OB 订单块
        self._near_zone(out, '多头订单块', 'bullish_ob_low', 'bullish_ob_high', curr, exec_ctx, '接近多头订单块，关注承接')
        self._near_zone(out, '空头订单块', 'bearish_ob_low', 'bearish_ob_high', curr, exec_ctx, '接近空头订单块，关注压制')
        self._near_zone(out, '订单块', 'ob_low', 'ob_high', curr, exec_ctx, '接近订单块，等待确认')

        # 背离
        if exec_ctx.get('has_bottom_div') or exec_ctx.get('bottom_divergence'):
            out.append(self._item('底部动能背离', '下跌动能减弱，可能出现反弹', price, None, 2.3, '等待多头确认'))
        if exec_ctx.get('has_top_div') or exec_ctx.get('top_divergence'):
            out.append(self._item('顶部动能背离', '上涨动能减弱，可能出现回落', price, None, 2.3, '等待空头确认'))

        # FVG
        if exec_ctx.get('fvg_valid') or exec_ctx.get('has_fvg'):
            out.append(self._item('价格失衡区', '出现价格失衡，后续可能回补或延续', price, None, 1.6, '配合方向和订单块判断'))

        return out

    def _near_level(self, out, name, key, curr, ctx, advice):
        price = self._num(curr.get('close'))
        atr = self._atr(curr, price)
        level = ctx.get(key)
        if not self._valid(level): return
        level = float(level)
        dist = abs(price - level)
        if dist <= atr * self.near_level_atr_mult:
            out.append(self._item(name, f'当前价 {price:.6f}，关键位 {level:.6f}，距离 {dist:.6f}（{dist/max(atr,1e-9):.2f} ATR）', price, level, 2.0, advice))

    def _near_zone(self, out, name, low_key, high_key, curr, ctx, advice):
        price = self._num(curr.get('close'))
        atr = self._atr(curr, price)
        low, high = ctx.get(low_key), ctx.get(high_key)
        if not (self._valid(low) and self._valid(high)): return
        low, high = float(low), float(high)
        if low > high: low, high = high, low
        dist = 0.0 if low <= price <= high else min(abs(price-low), abs(price-high))
        if dist <= atr * self.near_level_atr_mult:
            out.append({
                'type': name,
                'text_cn': f'{name}：当前价 {price:.6f}，区间 {low:.6f} - {high:.6f}，距离 {dist:.6f}（{dist/max(atr,1e-9):.2f} ATR）',
                'price': round(price,6), 'level': None, 'zone_low': round(low,6), 'zone_high': round(high,6),
                'distance': round(dist,6), 'score_add': 2.0, 'advice_cn': advice
            })

    def _item(self, name, text, price, level, score_add, advice):
        return {'type': name, 'text_cn': text, 'price': round(float(price),6), 'level': None if level is None else round(float(level),6), 'score_add': score_add, 'advice_cn': advice}

    def _atr(self, curr, price):
        for k in ['ATRr_14','ATR_14','atr','ATR']:
            try:
                v=float(curr.get(k))
                if v>0: return v
            except Exception: pass
        return max(abs(float(price))*0.005, 1e-8)

    def _num(self,v,default=0.0):
        try: return float(v)
        except Exception: return default
    def _valid(self,v):
        try: return v is not None and float(v)>0
        except Exception: return False
