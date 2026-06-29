# -*- coding: utf-8 -*-

class V6PriorityEngine:
    """权重数据化：权重来自 config/v6_params.json，可用回测优化。"""

    def __init__(self, weights=None, class_params=None):
        self.weights = weights or {}
        cp = class_params or {}
        self.s_level = float(cp.get('s_level', 9.0))
        self.a_level = float(cp.get('a_level', 7.0))
        self.b_level = float(cp.get('b_level', 5.0))
        self.min_open_level = str(cp.get('min_open_level', 'A')).upper()

    def score_candidate(self, candidate, observer_items=None, regime=None):
        observer_items = observer_items or []
        regime = regime or {}
        triggers = list(candidate.get('triggers', []))
        score = float(candidate.get('score', 0.0))
        detail = []

        mapping = {
            'SSL Sweep': 'liquidity_sweep', 'BSL Sweep': 'liquidity_sweep',
            'Near Liquidity': 'near_liquidity', 'Bullish OB': 'valid_ob', 'Bearish OB': 'valid_ob',
            'Near OB': 'near_ob', 'FVG': 'fvg', 'Bottom Divergence': 'divergence', 'Top Divergence': 'divergence',
            'Kline Momentum': 'kline_momentum', 'Score >= Threshold': 'score_confirm', 'Trend Align': 'trend_align',
            'Squeeze Release': 'squeeze_release'
        }
        for t in triggers:
            key = mapping.get(t, t)
            w = float(self.weights.get(key, 0.0))
            score += w
            if w: detail.append(f'{self._cn(t)} +{w:.1f}')

        for item in observer_items:
            add = float(item.get('score_add', 0.0))
            score += add
            if add: detail.append(f"{item.get('type')} +{add:.1f}")

        adx = float(regime.get('adx', 0.0))
        if adx >= 25:
            add = float(self.weights.get('adx_strong', 0.0))
            score += add
            detail.append(f'趋势强度好 ADX={adx:.2f} +{add:.1f}')

        level = self.classify(score)
        return {**candidate, 'v6_score': round(score, 4), 'level': level, 'score_detail_cn': detail, 'open_allowed_by_level': self._open_allowed(level)}

    def rank(self, candidates, observer_items=None, regime=None):
        scored = [self.score_candidate(c, observer_items, regime) for c in candidates]
        return sorted(scored, key=lambda x: x['v6_score'], reverse=True)

    def choose_primary(self, ranked):
        for c in ranked:
            if c.get('open_allowed_by_level'):
                return c
        return None

    def classify(self, score):
        if score >= self.s_level: return 'S'
        if score >= self.a_level: return 'A'
        if score >= self.b_level: return 'B'
        return 'C'

    def _open_allowed(self, level):
        order = {'S':4,'A':3,'B':2,'C':1}
        return order.get(level,0) >= order.get(self.min_open_level,3)

    def _cn(self, t):
        table = {'SSL Sweep':'扫到下方流动性','BSL Sweep':'扫到上方流动性','Bullish OB':'多头订单块有效','Bearish OB':'空头订单块有效','FVG':'价格失衡区','Bottom Divergence':'底部动能背离','Top Divergence':'顶部动能背离','Score >= Threshold':'基础评分达标','Trend Align':'方向顺势','Squeeze Release':'波动释放'}
        return table.get(t, t)
