# -*- coding: utf-8 -*-
"""Local liquidity heatmap derived from pivots, OB/FVG and volume.

注意：这里不是交易所外部清算热力图接口，而是用当前 K 线、未扫高低点、
1H 流动性、OB/FVG、成交量做的本地观察热力层。这样即使没有外部热力图
API，也不会一直显示 N/A。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple
import math


def _sf(v: Any, default=None):
    if v in [None, "", "N/A", "nan", "None"]:
        return default
    try:
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return default
        return x
    except Exception:
        return default


def _fmt_price(v: Any) -> str:
    x = _sf(v)
    if x is None:
        return "N/A"
    if abs(x) >= 100:
        return f"{x:.1f}"
    if abs(x) >= 1:
        return f"{x:.4f}"
    return f"{x:.6f}"


def _range_mid(v: Any):
    if isinstance(v, (tuple, list)) and len(v) >= 2:
        a = _sf(v[0]); b = _sf(v[1])
        if a is not None and b is not None:
            return (a + b) / 2.0
    return _sf(v)


def _dedupe(levels: List[Dict[str, Any]], price: float) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    tol = max(price * 0.0008, 1e-9)  # 0.08% 内认为同一流动性带
    for lv in sorted(levels, key=lambda x: (x["distance_pct"], -x["strength"])):
        if any(abs(lv["price"] - old["price"]) <= tol for old in out):
            continue
        out.append(lv)
    return out


def _append(levels, *, name, price, current_price, kind, strength=1.0):
    p = _sf(price)
    if p is None or p <= 0 or current_price <= 0:
        return
    side = "上方" if p >= current_price else "下方"
    dist = abs(p - current_price) / current_price * 100.0
    levels.append({
        "name": name,
        "price": p,
        "side": side,
        "kind": kind,
        "distance_pct": round(dist, 3),
        "strength": round(float(strength), 2),
    })


def build_local_liquidity_heatmap(df=None, exec_ctx: Dict[str, Any] | None = None, macro_ctx: Dict[str, Any] | None = None, max_levels: int = 6) -> Dict[str, Any]:
    exec_ctx = exec_ctx or {}
    macro_ctx = macro_ctx or {}

    current_price = None
    if df is not None and len(df) > 0:
        try:
            current_price = _sf(df.iloc[-1].get("close"))
        except Exception:
            current_price = None
    current_price = current_price or _sf(exec_ctx.get("price")) or _sf(exec_ctx.get("close"))

    if current_price is None or current_price <= 0:
        return {"levels": "N/A", "analysis": "价格缺失，无法生成本地热力图"}

    levels: List[Dict[str, Any]] = []
    ph = _sf(exec_ctx.get("pivot_strength_high"), 1.0) or 1.0
    pl = _sf(exec_ctx.get("pivot_strength_low"), 1.0) or 1.0

    _append(levels, name="BSL 买方止损池", price=exec_ctx.get("bsl_level"), current_price=current_price, kind="BSL", strength=max(1.0, ph))
    _append(levels, name="SSL 卖方止损池", price=exec_ctx.get("ssl_level"), current_price=current_price, kind="SSL", strength=max(1.0, pl))
    _append(levels, name="1H BSL", price=macro_ctx.get("bsl_1h"), current_price=current_price, kind="1H-BSL", strength=2.0)
    _append(levels, name="1H SSL", price=macro_ctx.get("ssl_1h"), current_price=current_price, kind="1H-SSL", strength=2.0)

    _append(levels, name="Bearish FVG", price=exec_ctx.get("bearish_fvg"), current_price=current_price, kind="FVG", strength=1.4)
    _append(levels, name="Bullish FVG", price=exec_ctx.get("bullish_fvg"), current_price=current_price, kind="FVG", strength=1.4)
    _append(levels, name="Bearish OB", price=_range_mid(exec_ctx.get("bearish_ob")), current_price=current_price, kind="OB", strength=1.6)
    _append(levels, name="Bullish OB", price=_range_mid(exec_ctx.get("bullish_ob")), current_price=current_price, kind="OB", strength=1.6)

    # 从 pivot 列表补充最近的高低点，不依赖外部清算热力接口。
    if df is not None and len(df) > 0:
        try:
            for idx in list(exec_ctx.get("liq_hp") or [])[-4:]:
                i = int(idx)
                if 0 <= i < len(df):
                    vol = _sf(df.iloc[i].get("volume"), 0.0) or 0.0
                    vol_ma = _sf(df["volume"].rolling(20).mean().iloc[i], vol) or vol
                    strength = 1.0 + min(2.0, vol / vol_ma) if vol_ma > 0 else 1.0
                    _append(levels, name="Pivot High", price=df.iloc[i].get("high"), current_price=current_price, kind="PH", strength=strength)
            for idx in list(exec_ctx.get("liq_lp") or [])[-4:]:
                i = int(idx)
                if 0 <= i < len(df):
                    vol = _sf(df.iloc[i].get("volume"), 0.0) or 0.0
                    vol_ma = _sf(df["volume"].rolling(20).mean().iloc[i], vol) or vol
                    strength = 1.0 + min(2.0, vol / vol_ma) if vol_ma > 0 else 1.0
                    _append(levels, name="Pivot Low", price=df.iloc[i].get("low"), current_price=current_price, kind="PL", strength=strength)
        except Exception:
            pass

    levels = _dedupe(levels, current_price)[:max_levels]
    if not levels:
        return {"levels": "N/A", "analysis": "未识别到有效流动性带，等待更多 K 线确认"}

    above = [x for x in levels if x["side"] == "上方"]
    below = [x for x in levels if x["side"] == "下方"]
    nearest_above = min(above, key=lambda x: x["distance_pct"], default=None)
    nearest_below = min(below, key=lambda x: x["distance_pct"], default=None)

    text_parts = []
    for lv in levels[:max_levels]:
        sign = "+" if lv["side"] == "上方" else "-"
        text_parts.append(f"{lv['side']} {_fmt_price(lv['price'])} ({sign}{lv['distance_pct']:.2f}%) {lv['kind']} 强{lv['strength']:.1f}")

    if nearest_above and nearest_below:
        analysis = (
            f"上方最近 {_fmt_price(nearest_above['price'])}，下方最近 {_fmt_price(nearest_below['price'])}。"
            f"价格夹在两侧流动性之间，优先等扫一边后再确认方向。"
        )
    elif nearest_above:
        analysis = f"主要流动性在上方 {_fmt_price(nearest_above['price'])}，谨防先上扫再回落。"
    else:
        analysis = f"主要流动性在下方 {_fmt_price(nearest_below['price'])}，谨防先下扫再反抽。"

    return {
        "levels": " | ".join(text_parts),
        "analysis": analysis,
        "items": levels,
        "nearest_above": nearest_above,
        "nearest_below": nearest_below,
    }
