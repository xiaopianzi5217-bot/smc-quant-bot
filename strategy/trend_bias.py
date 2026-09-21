# -*- coding: utf-8 -*-
"""统一趋势偏见评分：HTF + SQZ 柱向 + 结构区 + BOS/CHOCH。

用于提高「顺势开仓」概率，不是对下一根 K 的保证预测。
返回 bias_score ∈ [-100, 100]：正偏多、负偏空；|score| 越大共识越强。
"""
from __future__ import annotations
from typing import Any, Dict, Tuple


def compute_trend_bias(features: Dict[str, Any] | None = None, result: Dict[str, Any] | None = None) -> Dict[str, Any]:
    features = dict(features or {})
    result = dict(result or {})
    src = {**features, **{k: v for k, v in result.items() if k not in features}}

    score = 0.0
    reasons = []

    # 1) HTF regime（权重最高）
    reg = str(src.get("regime") or result.get("regime") or "").upper()
    if reg == "BULL":
        score += 35.0
        reasons.append("HTF_BULL+35")
    elif reg == "BEAR":
        score -= 35.0
        reasons.append("HTF_BEAR-35")
    elif reg in ("RANGE", "CHOP", "UNKNOWN", ""):
        reasons.append("HTF_RANGE+0")
    else:
        reasons.append(f"HTF_{reg}+0")

    # 2) SQZ 柱方向
    hist_sign = int(src.get("sqz_hist_sign") or 0)
    if hist_sign == 0:
        try:
            h = float(src.get("sqz_hist") or src.get("hist") or 0)
            hist_sign = 1 if h > 0 else (-1 if h < 0 else 0)
        except Exception:
            hist_sign = 0
    if hist_sign > 0:
        score += 25.0
        reasons.append("HIST_UP+25")
    elif hist_sign < 0:
        score -= 25.0
        reasons.append("HIST_DN-25")
    else:
        reasons.append("HIST_FLAT+0")

    # 3) 释放状态：已释放同向再加分
    released = bool(src.get("sqz_released"))
    if released and hist_sign > 0:
        score += 10.0
        reasons.append("RELEASED_UP+10")
    elif released and hist_sign < 0:
        score -= 10.0
        reasons.append("RELEASED_DN-10")
    elif not released:
        reasons.append("SQUEEZE+0")

    # 4) 溢价/折价区（若有）
    zone = str(src.get("smc_zone") or src.get("premium_discount") or src.get("pd_zone") or "").upper()
    if zone in ("DISCOUNT", "DISCOUNT_ZONE", "BUY_SIDE"):
        score += 15.0
        reasons.append("DISCOUNT+15")
    elif zone in ("PREMIUM", "PREMIUM_ZONE", "SELL_SIDE"):
        score -= 15.0
        reasons.append("PREMIUM-15")
    else:
        # 布尔特征兜底
        if src.get("in_discount") or src.get("discount"):
            score += 15.0
            reasons.append("DISCOUNT+15")
        elif src.get("in_premium") or src.get("premium"):
            score -= 15.0
            reasons.append("PREMIUM-15")

    # 5) 结构 BOS/CHOCH
    if src.get("bos_bull") or src.get("choch_bull") or src.get("bull_bos") or src.get("bull_choch"):
        score += 15.0
        reasons.append("BOS/CHOCH_BULL+15")
    if src.get("bos_bear") or src.get("choch_bear") or src.get("bear_bos") or src.get("bear_choch"):
        score -= 15.0
        reasons.append("BOS/CHOCH_BEAR-15")
    # structure_break + trend_direction
    td = src.get("trend_direction")
    if td is True or str(td).lower() in ("long", "bull", "up", "1"):
        score += 10.0
        reasons.append("TREND_DIR_LONG+10")
    elif td is False or str(td).lower() in ("short", "bear", "down", "0"):
        # False 在 RANGE 可能无意义，仅当 regime 明确时计
        if reg == "BEAR":
            score -= 10.0
            reasons.append("TREND_DIR_SHORT-10")

    # 钳制
    score = max(-100.0, min(100.0, score))
    direction_bias = "LONG" if score >= 20 else ("SHORT" if score <= -20 else "NEUTRAL")
    strength = "STRONG" if abs(score) >= 50 else ("MED" if abs(score) >= 25 else "WEAK")

    return {
        "bias_score": round(score, 2),
        "direction_bias": direction_bias,
        "bias_strength": strength,
        "bias_reasons": reasons,
        "hist_sign": hist_sign,
        "regime": reg,
        "sqz_released": released,
    }


def direction_aligned(trade_direction: str, bias: Dict[str, Any], min_abs: float = 20.0) -> Tuple[bool, str]:
    """交易方向是否与偏见同向。"""
    d = str(trade_direction or "").strip().lower()
    is_long = d in ("long", "buy")
    is_short = d in ("short", "sell")
    bs = float(bias.get("bias_score") or 0)
    db = str(bias.get("direction_bias") or "NEUTRAL")
    if abs(bs) < float(min_abs):
        return False, f"bias过弱 score={bs}"
    if is_long and bs >= min_abs:
        return True, f"LONG对齐 bias={bs}"
    if is_short and bs <= -min_abs:
        return True, f"SHORT对齐 bias={bs}"
    if is_long and bs < 0:
        return False, f"多单逆偏见 bias={bs} ({db})"
    if is_short and bs > 0:
        return False, f"空单逆偏见 bias={bs} ({db})"
    return False, f"未对齐 dir={trade_direction} bias={bs}"
