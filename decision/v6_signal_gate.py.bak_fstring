# -*- coding: utf-8 -*-

class V6SignalGate:
    """统一入口：所有开单候选都必须从这里进入。"""

    def collect_candidates(self, macro_ctx, exec_ctx, long_score, short_score, long_threshold, short_threshold, long_reasons, short_reasons):
        macro_ctx = macro_ctx or {}
        exec_ctx = exec_ctx or {}
        allowed_direction = macro_ctx.get('allowed_direction', 'Both')
        out = []

        if allowed_direction in ['Long', 'Both']:
            triggers = []
            if exec_ctx.get('is_ssl_swept'):
                triggers.append('SSL Sweep')
            if exec_ctx.get('has_bot_div') or exec_ctx.get('just_confirmed_bot') or exec_ctx.get('has_bottom_div') or exec_ctx.get('bottom_divergence'):
                triggers.append('Bottom Divergence')
            if exec_ctx.get('bullish_ob_valid') or exec_ctx.get('bullish_ob') is not None:
                triggers.append('Bullish OB')
            if exec_ctx.get('bullish_fvg') is not None:
                triggers.append('FVG')
            if exec_ctx.get('color_changed') and '藍色' in str(exec_ctx.get('curr_color', '')):
                triggers.append('Kline Momentum')
            if long_score >= long_threshold:
                triggers.append('Score >= Threshold')
            if macro_ctx.get('allowed_direction') in ['Long', 'Both'] or macro_ctx.get('trend') in ['Long', 'Both']:
                triggers.append('Trend Align')
            if triggers and long_score >= max(1.0, float(long_threshold) - 1.0):
                out.append({'direction': 'Long', 'score': float(long_score), 'threshold': float(long_threshold), 'score_gap': float(long_score - short_score), 'triggers': triggers, 'reasons': list(long_reasons or [])})

        if allowed_direction in ['Short', 'Both']:
            triggers = []
            if exec_ctx.get('is_bsl_swept'):
                triggers.append('BSL Sweep')
            if exec_ctx.get('has_top_div') or exec_ctx.get('just_confirmed_top') or exec_ctx.get('top_divergence'):
                triggers.append('Top Divergence')
            if exec_ctx.get('bearish_ob_valid') or exec_ctx.get('bearish_ob') is not None:
                triggers.append('Bearish OB')
            if exec_ctx.get('bearish_fvg') is not None:
                triggers.append('FVG')
            if exec_ctx.get('color_changed') and '紅色' in str(exec_ctx.get('curr_color', '')):
                triggers.append('Kline Momentum')
            if short_score >= short_threshold:
                triggers.append('Score >= Threshold')
            if macro_ctx.get('allowed_direction') in ['Short', 'Both'] or macro_ctx.get('trend') in ['Short', 'Both']:
                triggers.append('Trend Align')
            if triggers and short_score >= max(1.0, float(short_threshold) - 1.0):
                out.append({'direction': 'Short', 'score': float(short_score), 'threshold': float(short_threshold), 'score_gap': float(short_score - long_score), 'triggers': triggers, 'reasons': list(short_reasons or [])})

        return out
