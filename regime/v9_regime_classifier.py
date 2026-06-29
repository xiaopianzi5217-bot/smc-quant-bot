# -*- coding: utf-8 -*-
"""
v9 Regime Classifier

修复点：
1. 兼容 build_exec_context() 把 adx/squeeze/atr_ratio 放在 exec_ctx['regime_info'] 的情况。
2. 低 ADX / mud 行情不再“一刀切”全部杀死；只有出现 SMC 流动性扫荡 + 动量确认 + 结构位时，允许交给后续中枢继续过滤。
3. 普通低趋势仍然只提醒不开单，保护未来自动开单安全。
"""


def _f(ctx, key, default=0.0):
    try:
        if ctx is None:
            return float(default)
        return float(ctx.get(key, default))
    except Exception:
        return float(default)


def _s(ctx, key, default=""):
    try:
        if ctx is None:
            return str(default)
        v = ctx.get(key, default)
        return str(default if v is None else v)
    except Exception:
        return str(default)


def _strong_smc_momentum(exec_ctx):
    """强共振例外：只作为“允许进入中枢继续审查”，不是直接开单。"""
    exec_ctx = exec_ctx or {}
    sweep = bool(exec_ctx.get("is_ssl_swept") or exec_ctx.get("is_bsl_swept"))
    div = bool(
        exec_ctx.get("just_confirmed_bot")
        or exec_ctx.get("just_confirmed_top")
        or exec_ctx.get("has_bot_div")
        or exec_ctx.get("has_top_div")
        or exec_ctx.get("has_bottom_div")
        or exec_ctx.get("has_top_divergence")
    )
    momentum = bool(exec_ctx.get("color_changed") or div)
    structure = bool(
        exec_ctx.get("bullish_ob_valid")
        or exec_ctx.get("bearish_ob_valid")
        or exec_ctx.get("bullish_fvg") is not None
        or exec_ctx.get("bearish_fvg") is not None
    )
    return sweep and momentum and structure


class V9RegimeClassifier:
    def __init__(self, params=None):
        params = params or {}
        self.adx_weak = float(params.get("adx_weak", 18))
        self.adx_trend = float(params.get("adx_trend", 24))
        self.adx_strong = float(params.get("adx_strong", 32))
        self.atr_pct_high = float(params.get("atr_pct_high", 0.018))
        self.atr_pct_low = float(params.get("atr_pct_low", 0.004))

    def classify(self, macro_ctx, exec_ctx):
        macro_ctx = macro_ctx or {}
        exec_ctx = exec_ctx or {}
        regime_info = exec_ctx.get("regime_info") or {}

        adx = _f(exec_ctx, "adx", _f(exec_ctx, "ADX_14", _f(regime_info, "adx", 0)))
        atr_pct = _f(exec_ctx, "atr_pct", 0)
        atr_ratio = _f(exec_ctx, "atr_ratio", _f(regime_info, "atr_ratio", 1.0))
        squeeze = _s(exec_ctx, "squeeze", _s(regime_info, "squeeze", "")).lower()
        regime = _s(exec_ctx, "regime", _s(regime_info, "regime", "unknown")).lower()
        trend = str(macro_ctx.get("trend", macro_ctx.get("macro_trend", "unknown")))
        allowed_direction = macro_ctx.get("allowed_direction", "Both")
        strong_exception = _strong_smc_momentum(exec_ctx)

        if squeeze in ["squeeze", "building", "压缩", "波动压缩"] and not strong_exception:
            name = "波动压缩"
            tradable = False
            reason = "波动正在压缩，容易假突破，禁止开单，只提醒"
        elif adx < self.adx_weak and not strong_exception:
            name = "震荡弱趋势"
            tradable = False
            reason = "趋势强度不足，来回止损概率高"
        elif atr_pct >= self.atr_pct_high and adx < self.adx_trend and not strong_exception:
            name = "高波动混乱"
            tradable = False
            reason = "波动过大但方向不清，容易扫损"
        elif strong_exception and regime in ["mud", "chaos", "transition", "unknown"]:
            name = "低趋势强共振"
            tradable = True
            reason = "低趋势环境，但出现SMC扫流动性 + SQZMOM动量/背离 + 结构位共振，允许进入中枢复核"
        elif adx >= self.adx_strong:
            name = "强趋势"
            tradable = True
            reason = "趋势强度较高，允许顺势信号"
        elif adx >= self.adx_trend:
            name = "趋势行情"
            tradable = True
            reason = "趋势强度合格，允许优质信号"
        else:
            name = "过渡行情"
            tradable = bool(strong_exception)
            reason = "强共振例外放行" if strong_exception else "行情处在过渡区，只观察不主动开单"

        return {
            "regime_name": name,
            "tradable": bool(tradable),
            "reason": reason,
            "adx": round(adx, 2),
            "atr_pct": round(atr_pct, 6),
            "atr_ratio": round(atr_ratio, 4),
            "squeeze": squeeze,
            "regime": regime,
            "strong_exception": bool(strong_exception),
            "macro_trend": trend,
            "allowed_direction": allowed_direction,
        }
