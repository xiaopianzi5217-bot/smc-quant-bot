# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional
from datetime import datetime
import math
import pandas as pd

from notifier.observer.heatmap import build_local_liquidity_heatmap
from risk.funding_filter import FundingFilterConfig, evaluate_funding_filter


@dataclass
class SignalSnapshot:
    symbol: str
    time: str
    price: Any
    trend_result: str
    suggestion: str
    direction_bias: str
    long_score: float
    short_score: float
    threshold_long: float
    threshold_short: float

    regime: Any = "N/A"
    volatility: Any = "N/A"
    squeeze: Any = "N/A"

    rsi: Any = "N/A"
    macd: Any = "N/A"
    macd_signal: Any = "N/A"
    macd_hist: Any = "N/A"
    macd_status: Any = "N/A"
    adx: Any = "N/A"
    atr: Any = "N/A"
    atr_ratio: Any = "N/A"
    volume_state: Any = "N/A"

    funding_rate: Any = "N/A"
    funding_filter_status: Any = "N/A"
    funding_filter_reason: Any = "N/A"

    heatmap_levels: Any = "N/A"
    heatmap_analysis: Any = "N/A"

    bullish_divergence: bool = False
    bearish_divergence: bool = False
    candle_color: Any = "N/A"
    color_changed: bool = False
    squeeze_dots: Any = "N/A"

    bsl_level: Any = "N/A"
    ssl_level: Any = "N/A"
    near_buyside: bool = False
    near_sellside: bool = False
    is_bsl_swept: bool = False
    is_ssl_swept: bool = False

    ob_valid: bool = False
    bullish_ob_valid: bool = False
    bearish_ob_valid: bool = False
    fvg_valid: bool = False
    ob_range: Any = "N/A"
    bullish_ob_range: Any = "N/A"
    bearish_ob_range: Any = "N/A"
    fvg_level: Any = "N/A"
    pivot_strength: Any = "N/A"

    entry: Any = "N/A"
    sl: Any = "N/A"
    tp1: Any = "N/A"
    tp2: Any = "N/A"
    tp3: Any = "N/A"
    rr1: Any = "N/A"
    rr2: Any = "N/A"
    rr3: Any = "N/A"
    rr: Any = "N/A"
    entry_reason: Any = "N/A"

    # 【修复20260701】顺发信号加入 EV 评分
    long_ev: Any = "N/A"
    short_ev: Any = "N/A"
    ev_gap: Any = "N/A"

    reasons: Optional[List[str]] = None
    raw: Optional[Dict[str, Any]] = None

    def to_dict(self):
        return asdict(self)


def _force_dict(obj):
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict"):
        try:
            return obj.to_dict()
        except Exception:
            return {}
    if hasattr(obj, "__dict__"):
        return vars(obj)
    return {}


def _valid(v: Any) -> bool:
    if v in [None, "", "N/A", "nan", "None"]:
        return False
    try:
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return False
    except Exception:
        pass
    return True


def _sf(v: Any, default=None):
    if not _valid(v):
        return default
    try:
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return default
        return x
    except Exception:
        return default


def _round(v: Any, n=4):
    x = _sf(v)
    return round(x, n) if x is not None else "N/A"


def _fmt_price(v: Any):
    x = _sf(v)
    if x is None:
        return "N/A"
    if abs(x) >= 100:
        return round(x, 2)
    if abs(x) >= 1:
        return round(x, 4)
    return round(x, 6)


def _fmt_range(v: Any):
    if isinstance(v, (tuple, list)) and len(v) >= 2:
        a = _fmt_price(v[0])
        b = _fmt_price(v[1])
        if a != "N/A" and b != "N/A":
            hi = max(float(a), float(b))
            lo = min(float(a), float(b))
            return f"{lo:g}~{hi:g}"
    return _fmt_price(v)


def _nested(src: Dict[str, Any], key: str, default="N/A"):
    if key in src and _valid(src.get(key)):
        return src.get(key)
    ri = src.get("regime_info")
    if isinstance(ri, dict) and key in ri and _valid(ri.get(key)):
        return ri.get(key)
    return default


def _first(keys: List[str], sources: List[Dict[str, Any]], default="N/A"):
    for key in keys:
        for src in sources:
            if not isinstance(src, dict):
                continue
            if key in src and _valid(src.get(key)):
                return src.get(key)
            v = _nested(src, key, None)
            if _valid(v):
                return v
    return default


def _macd_status(hist):
    h = _sf(hist)
    if h is None:
        return "N/A"
    if h > 0:
        return "多方动能占优"
    if h < 0:
        return "空方动能占优"
    return "多空动能接近平衡"


def _volume_state(df, curr):
    try:
        vol = _sf(curr.get("volume"))
        ma = _sf(df["volume"].rolling(20).mean().iloc[-1])
        if vol is None or ma is None or ma <= 0:
            return "N/A"
        r = vol / ma
        if r >= 1.8:
            return f"放量 {r:.2f}x"
        if r >= 1.2:
            return f"温和放量 {r:.2f}x"
        if r <= 0.65:
            return f"缩量 {r:.2f}x"
        return f"正常 {r:.2f}x"
    except Exception:
        return "N/A"


def _squeeze_dot(curr):
    try:
        if bool(curr.get("highsqz")):
            return "红点/强压缩"
        if bool(curr.get("midsqz")):
            return "黄点/中压缩"
        if bool(curr.get("lowsqz")):
            return "绿点/弱压缩"
    except Exception:
        pass
    return "释放/无压缩"


def _near_level(price, level, atr=None, pct_threshold=0.0035):
    p = _sf(price); l = _sf(level)
    if p is None or l is None or p <= 0:
        return False
    tol = max(p * pct_threshold, (_sf(atr, 0.0) or 0.0) * 0.8)
    return abs(p - l) <= tol


def _direction_from_scores(long_score, short_score):
    try:
        l = float(long_score or 0)
        s = float(short_score or 0)
        if l > s:
            return "Long"
        if s > l:
            return "Short"
    except Exception:
        pass
    return "Neutral"


def _suggestion(direction, long_score, short_score, tl, ts, regime, squeeze,
                long_ev=None, short_ev=None, is_approved=False):
    """
    生成明确的交易建议，包含：开不开单、方向、原因。
    结合 分数、EV、是否已批准 做综合判断。
    """
    try:
        lp = float(long_score) / max(float(tl), 1.0) * 100.0
        sp = float(short_score) / max(float(ts), 1.0) * 100.0
    except Exception:
        lp = sp = 0.0

    try:
        lev = float(long_ev or 0)
        sev = float(short_ev or 0)
    except Exception:
        lev = sev = 0.0

    score_gap = abs(lp - sp)

    # ==================== 已批准开单 ====================
    if is_approved:
        if direction == "Long":
            return (
                f"✅ 【建议开多】\n"
                f"原因：多头评分 {lp:.0f}分(阈值{tl:.0f})，EV {lev:+.4f}，"
                f"空头 {sp:.0f}分，分差 {score_gap:.0f}分，AI 判断此方向可执行。\n"
                f"操作：按下方风控计划挂单，不建议追高，等价格回到入场参考附近。"
            )
        else:
            return (
                f"✅ 【建议开空】\n"
                f"原因：空头评分 {sp:.0f}分(阈值{ts:.0f})，EV {sev:+.4f}，"
                f"多头 {lp:.0f}分，分差 {score_gap:.0f}分，AI 判断此方向可执行。\n"
                f"操作：按下方风控计划挂单，不建议追低，等价格回到入场参考附近。"
            )

    # ==================== 未批准但分数接近 ====================
    if direction == "Long" and lp >= 75:
        near_approved = "接近开单门槛" if lp < 85 else "已达标但被其他条件拦截"
        reasons = []
        if lev < -0.20:
            reasons.append(f"EV={lev:.4f}偏低（需>-0.2）")
        if score_gap < 10:
            reasons.append(f"分差仅{score_gap:.0f}分")
        reason_str = f"，原因：{'、'.join(reasons)}" if reasons else ""

        return (
            f"⚠️ 【偏向做多，但暂不开单】\n"
            f"多头 {lp:.0f}分 vs 空头 {sp:.0f}分，{near_approved}{reason_str}。\n"
            f"操作：等回踩下方防守区（SSL/买方OB），或等放量/扫止损确认后再入场，不追高。"
        )

    if direction == "Short" and sp >= 75:
        near_approved = "接近开单门槛" if sp < 85 else "已达标但被其他条件拦截"
        reasons = []
        if sev < -0.20:
            reasons.append(f"EV={sev:.4f}偏低（需>-0.2）")
        if score_gap < 10:
            reasons.append(f"分差仅{score_gap:.0f}分")
        reason_str = f"，原因：{'、'.join(reasons)}" if reasons else ""

        return (
            f"⚠️ 【偏向做空，但暂不开单】\n"
            f"空头 {sp:.0f}分 vs 多头 {lp:.0f}分，{near_approved}{reason_str}。\n"
            f"操作：等反弹上方防守区（BSL/卖方OB），或等放量/扫止损确认后再入场，不追低。"
        )

    # ==================== 分差大但分数不够 ====================
    if direction == "Long" and lp >= 60:
        return (
            f"🔎 【轻微偏多，继续观察】\n"
            f"多头 {lp:.0f}分 vs 空头 {sp:.0f}分，但距开单阈值({tl:.0f}分)还有差距。\n"
            f"操作：等待价格回踩防守区或扫下方止损后，观察评分是否能再提升。"
        )

    if direction == "Short" and sp >= 60:
        return (
            f"🔎 【轻微偏空，继续观察】\n"
            f"空头 {sp:.0f}分 vs 多头 {lp:.0f}分，但距开单阈值({ts:.0f}分)还有差距。\n"
            f"操作：等待价格反弹防守区或扫上方止损后，观察评分是否能再提升。"
        )

    # ==================== 混沌震荡 ====================
    if str(regime) == "mud":
        return (
            f"⏸️ 【混沌震荡，不开单】\n"
            f"最近行情状态为 mud（无方向震荡），多空评分接近。\n"
            f"操作：只观察结构变化，等待扫止损/背离/成交量配合出方向。"
        )

    if str(squeeze) in ["building", "build"]:
        return (
            f"⏸️ 【波动压缩中，不开单】\n"
            f"TTM Squeeze 正在压缩，说明波动在变窄，但不确定往哪边释放。\n"
            f"操作：等待释放方向确认后再入场，不提前重仓。"
        )

    # ==================== 默认 ====================
    return (
        f"⏸️ 【优势不明显，不开单】\n"
        f"多头 {lp:.0f}分 / 空头 {sp:.0f}分，分差 {score_gap:.0f}分，两者均不够突出。\n"
        f"操作：以观察为主，等待评分差距扩大或有流动性触发信号。"
    )


def build_signal_snapshot(*args, **kwargs) -> SignalSnapshot:
    data = args[0] if args and isinstance(args[0], dict) else kwargs

    df = data.get("df")
    curr = {}
    if df is not None and isinstance(df, pd.DataFrame) and not df.empty:
        curr = df.iloc[-1].to_dict()

    macro_ctx = _force_dict(data.get("macro_ctx"))
    exec_ctx = _force_dict(data.get("exec_ctx"))
    rr_plan = _force_dict(data.get("rr_plan"))
    decision = _force_dict(data.get("decision"))
    sources = [data, exec_ctx, macro_ctx, rr_plan, curr, decision]

    long_score = float(data.get("long_score", _first(["long_score"], sources, 0)) or 0)
    short_score = float(data.get("short_score", _first(["short_score"], sources, 0)) or 0)
    threshold_long = _sf(data.get("long_threshold", data.get("threshold_long", _first(["threshold_long", "long_threshold"], sources, 1))), 1) or 1
    threshold_short = _sf(data.get("short_threshold", data.get("threshold_short", _first(["threshold_short", "short_threshold"], sources, 1))), 1) or 1

    price = _first(["price", "close", "last"], sources)
    regime = _first(["regime"], sources)
    volatility = _first(["volatility", "vol"], sources)
    squeeze = _first(["squeeze"], sources)
    atr = _first(["atr", "ATRr_14"], sources)

    direction = _first(["direction_bias", "direction", "side"], sources, None)
    if not _valid(direction) or str(direction).upper() in ["N/A", "NONE"]:
        direction = _direction_from_scores(long_score, short_score)

    # 统一别名：当前策略层的字段名与 TG 展示字段名不同，这里做兼容。
    macd = _first(["macd", "MACD_12_26_9"], sources)
    macd_signal = _first(["macd_signal", "macdsignal", "MACDs_12_26_9"], sources)
    macd_hist = _first(["macd_hist", "macdhist", "MACDh_12_26_9"], sources)

    bsl = _first(["bsl_level", "bsl"], sources)
    ssl = _first(["ssl_level", "ssl"], sources)
    bearish_ob = _first(["bearish_ob", "bearish_ob_range"], sources, None)
    bullish_ob = _first(["bullish_ob", "bullish_ob_range"], sources, None)
    bearish_fvg = _first(["bearish_fvg"], sources, None)
    bullish_fvg = _first(["bullish_fvg"], sources, None)

    funding_rate = _first(["funding_rate"], sources)
    funding_status = _first(["funding_filter_status"], sources, None)
    funding_reason = _first(["funding_filter_reason"], sources, None)
    if _valid(funding_rate) and (not _valid(funding_status) or not _valid(funding_reason)):
        try:
            fr = evaluate_funding_filter(
                direction=str(direction),
                funding_rate=funding_rate,
                config=FundingFilterConfig(enabled=True),
            )
            funding_status = fr.get("status")
            funding_reason = fr.get("reason")
        except Exception:
            pass

    heatmap_levels = _first(["heatmap_levels"], sources, None)
    heatmap_analysis = _first(["heatmap_analysis"], sources, None)
    if not _valid(heatmap_levels) or not _valid(heatmap_analysis):
        # 优先尝试外部爆仓热力图，有数据就用；没有就回退到本地流动性热力图
        try:
            from notifier.observer.liquidation_heatmap import build_liquidation_heatmap
            liq_hm = build_liquidation_heatmap(
                data.get("symbol", "BTC/USDT"),
                price=_sf(price),
                max_levels=5,
            )
            if liq_hm.get("levels", "N/A") not in ("N/A", None, ""):
                heatmap_levels = liq_hm["levels"]
                heatmap_analysis = liq_hm["analysis"]
            else:
                hm = build_local_liquidity_heatmap(df=df, exec_ctx=exec_ctx, macro_ctx=macro_ctx)
                heatmap_levels = hm.get("levels", "N/A")
                heatmap_analysis = hm.get("analysis", "N/A")
        except Exception:
            hm = build_local_liquidity_heatmap(df=df, exec_ctx=exec_ctx, macro_ctx=macro_ctx)
            heatmap_levels = hm.get("levels", "N/A")
            heatmap_analysis = hm.get("analysis", "N/A")

    trend_result = _first(["trend_result", "trend", "macro_trend", "allowed_direction"], sources)
    suggestion = _first(["suggestion"], sources, None)
    if not _valid(suggestion):
        # 【修复】传入 EV 和批准状态，生成明确的建议（开不开、原因）
        _is_approved = bool(_first(["approved", "decision_approved", "is_approved"], sources, False))
        _long_ev = _first(["long_ev"], sources, None)
        _short_ev = _first(["short_ev"], sources, None)
        suggestion = _suggestion(
            direction, long_score, short_score,
            threshold_long, threshold_short,
            regime, squeeze,
            long_ev=_long_ev, short_ev=_short_ev,
            is_approved=_is_approved,
        )

    snapshot = SignalSnapshot(
        symbol=data.get("symbol", _first(["symbol"], sources, "UNKNOWN")),
        time=data.get("time", datetime.utcnow().isoformat()),
        price=_fmt_price(price),

        trend_result=str(trend_result),
        suggestion=str(suggestion),
        direction_bias=str(direction),

        long_score=long_score,
        short_score=short_score,
        threshold_long=threshold_long,
        threshold_short=threshold_short,

        regime=regime,
        volatility=volatility,
        squeeze=squeeze,

        rsi=_round(_first(["rsi", "RSI_14"], sources), 2),
        macd=_round(macd, 4),
        macd_signal=_round(macd_signal, 4),
        macd_hist=_round(macd_hist, 4),
        macd_status=_first(["macd_status"], sources, _macd_status(macd_hist)),
        adx=_round(_first(["adx", "ADX_14"], sources), 2),
        atr=_round(atr, 4),
        atr_ratio=_round(_first(["atr_ratio"], sources), 4),
        volume_state=_first(["volume_state"], sources, _volume_state(df, curr) if df is not None and curr else "N/A"),

        funding_rate=funding_rate,
        funding_filter_status=funding_status if _valid(funding_status) else "N/A",
        funding_filter_reason=funding_reason if _valid(funding_reason) else "N/A",

        heatmap_levels=heatmap_levels,
        heatmap_analysis=heatmap_analysis,

        bullish_divergence=bool(_first(["bullish_divergence", "has_bot_div", "just_confirmed_bot"], sources, False)),
        bearish_divergence=bool(_first(["bearish_divergence", "has_top_div", "just_confirmed_top"], sources, False)),
        candle_color=_first(["candle_color", "curr_color"], sources),
        color_changed=bool(_first(["color_changed"], sources, False)),
        squeeze_dots=_first(["squeeze_dots"], sources, _squeeze_dot(curr)),

        bsl_level=_fmt_price(bsl),
        ssl_level=_fmt_price(ssl),
        near_buyside=bool(_first(["near_buyside"], sources, _near_level(price, bsl, atr))),
        near_sellside=bool(_first(["near_sellside"], sources, _near_level(price, ssl, atr))),
        is_bsl_swept=bool(_first(["is_bsl_swept"], sources, False)),
        is_ssl_swept=bool(_first(["is_ssl_swept"], sources, False)),

        ob_valid=bool(_first(["ob_valid"], sources, _valid(bearish_ob) or _valid(bullish_ob))),
        bullish_ob_valid=bool(_first(["bullish_ob_valid"], sources, _valid(bullish_ob))),
        bearish_ob_valid=bool(_first(["bearish_ob_valid"], sources, _valid(bearish_ob))),
        fvg_valid=bool(_valid(bearish_fvg) or _valid(bullish_fvg)),
        ob_range=_fmt_range(bullish_ob if _valid(bullish_ob) else bearish_ob),
        bullish_ob_range=_fmt_range(bullish_ob),
        bearish_ob_range=_fmt_range(bearish_ob),
        fvg_level=_fmt_range(bullish_fvg if _valid(bullish_fvg) else bearish_fvg),
        pivot_strength=_round(_first(["pivot_strength"], sources), 3),

        entry=_fmt_price(_first(["entry"], sources)),
        sl=_fmt_price(_first(["sl", "stop", "stop_loss"], sources)),
        tp1=_fmt_price(_first(["tp1"], sources)),
        tp2=_fmt_price(_first(["tp2"], sources)),
        tp3=_fmt_price(_first(["tp3"], sources)),
        rr1=_round(_first(["rr1"], sources), 2),
        rr2=_round(_first(["rr2"], sources), 2),
        rr3=_round(_first(["rr3"], sources), 2),
        rr=_round(_first(["rr"], sources), 2),
        entry_reason=_first(["entry_reason"], sources),

        # 【修复20260701】顺发信号加入 EV 评分
        long_ev=_round(_first(["long_ev"], sources), 4),
        short_ev=_round(_first(["short_ev"], sources), 4),
        ev_gap=_round(_first(["ev_gap"], sources,
                             _sf(data.get("long_ev", _first(["long_ev"], sources, 0)), 0) - 
                             _sf(data.get("short_ev", _first(["short_ev"], sources, 0)), 0)), 4),

        reasons=data.get("long_reasons") if direction == "Long" else data.get("short_reasons") or data.get("reasons"),
        raw={
            "input": data,
            "exec_ctx": exec_ctx,
            "macro_ctx": macro_ctx,
            "rr_plan": rr_plan,
        },
    )
    return snapshot


def collect_signal(*args, **kwargs) -> SignalSnapshot:
    return build_signal_snapshot(*args, **kwargs)
