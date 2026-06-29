# -*- coding: utf-8 -*-

class V6RegimeGate:
    """行情过滤层：先决定能不能交易，再决定只能观察还是允许开单。"""

    def __init__(self, params=None):
        p = params or {}
        self.adx_min_trade = float(p.get('adx_min_trade', 20.0))
        self.adx_min_observe = float(p.get('adx_min_observe', 14.0))
        self.block_squeeze_building = bool(p.get('block_squeeze_building', True))
        self.block_mud = bool(p.get('block_mud', True))

    def _strong_smc_momentum(self, exec_ctx):
        sweep = bool(exec_ctx.get('is_ssl_swept') or exec_ctx.get('is_bsl_swept'))
        momentum = bool(exec_ctx.get('color_changed') or exec_ctx.get('just_confirmed_bot') or exec_ctx.get('just_confirmed_top') or exec_ctx.get('has_bot_div') or exec_ctx.get('has_top_div'))
        structure = bool(exec_ctx.get('bullish_ob_valid') or exec_ctx.get('bearish_ob_valid') or exec_ctx.get('bullish_fvg') is not None or exec_ctx.get('bearish_fvg') is not None)
        return sweep and momentum and structure

    def evaluate(self, macro_ctx, exec_ctx):
        macro_ctx = macro_ctx or {}
        exec_ctx = exec_ctx or {}
        regime_info = exec_ctx.get('regime_info', {}) or {}
        adx = self._num(exec_ctx.get('adx', exec_ctx.get('ADX_14', regime_info.get('adx', 0))))
        regime = regime_info.get('regime', exec_ctx.get('regime', 'unknown'))
        volatility = regime_info.get('volatility', exec_ctx.get('volatility', 'unknown'))
        squeeze = regime_info.get('squeeze', exec_ctx.get('squeeze', 'unknown'))
        allowed_direction = macro_ctx.get('allowed_direction', 'Both')
        macro_trend = macro_ctx.get('trend', macro_ctx.get('macro_trend', 'unknown'))
        strong_exception = self._strong_smc_momentum(exec_ctx)

        block = []
        observe_only = []

        if self.block_mud and str(regime).lower() in ['mud', 'chaos', 'unknown'] and not strong_exception:
            block.append('行情混乱，禁止开单')
        if self.block_squeeze_building and str(squeeze).lower() in ['squeeze', 'building'] and not strong_exception:
            observe_only.append('波动压缩中，只提醒不开单')
        if adx < self.adx_min_observe and not strong_exception:
            block.append(f'趋势强度过低，ADX={adx:.2f}')
        elif adx < self.adx_min_trade and not strong_exception:
            observe_only.append(f'趋势强度不足，ADX={adx:.2f}')
        if allowed_direction not in ['Long', 'Short', 'Both']:
            block.append(f'宏观方向无效：{allowed_direction}')

        allowed = len(block) == 0
        trade_allowed = allowed and len(observe_only) == 0

        return {
            'allowed': allowed,
            'trade_allowed': trade_allowed,
            'observe_only': allowed and not trade_allowed,
            'allowed_direction': allowed_direction,
            'macro_trend': macro_trend,
            'regime': regime,
            'volatility': volatility,
            'squeeze': squeeze,
            'adx': adx,
            'strong_exception': strong_exception,
            'block_reasons': block,
            'observe_reasons': observe_only,
            'summary_cn': self._summary_cn(regime, volatility, squeeze, adx, macro_trend),
        }

    def _summary_cn(self, regime, volatility, squeeze, adx, macro_trend):
        trend_txt = {'Long': '偏多', 'Short': '偏空', 'Both': '多空均可'}.get(str(macro_trend), str(macro_trend))
        strength = '强' if adx >= 25 else ('中等' if adx >= 20 else '弱')
        return f'趋势{trend_txt}，强度{strength}（ADX={adx:.2f}），波动={volatility}，压缩状态={squeeze}，结构={regime}'

    def _num(self, v, default=0.0):
        try:
            if v is None:
                return default
            return float(v)
        except Exception:
            return default
