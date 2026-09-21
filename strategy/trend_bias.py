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


def size_mult_from_bias(
    trade_direction: str,
    bias: Dict[str, Any] | None,
    *,
    regime: str = "",
) -> tuple[float, str]:
    """按偏见强度与对齐度返回仓位乘数 (0~1) 与原因。

    规则（保守、可解释）：
    - 强逆势 |score|>=40 且反向 → 0.0（禁止 LIVE 满仓路径由调用方处理）
    - 弱偏见 / 中性 → 0.5
    - 中等对齐 |score| 25~50 → 0.75
    - 强对齐 |score|>=50 → 1.0
    - RANGE 再乘 0.75（震荡降仓）
    """
    bias = bias or {}
    try:
        bs = float(bias.get("bias_score") or 0.0)
    except Exception:
        bs = 0.0
    d = str(trade_direction or "").strip().lower()
    is_long = d in ("long", "buy")
    is_short = d in ("short", "sell")
    aligned = (is_long and bs >= 25) or (is_short and bs <= -25)
    counter = (is_long and bs <= -40) or (is_short and bs >= 40)
    if counter:
        return 0.0, f"强逆势 bias={bs}"
    if not aligned:
        mult = 0.5
        why = f"偏见弱/中性 bias={bs}"
    elif abs(bs) >= 50:
        mult = 1.0
        why = f"强顺势 bias={bs}"
    else:
        mult = 0.75
        why = f"中等顺势 bias={bs}"
    reg = str(regime or bias.get("regime") or "").upper()
    if reg in ("RANGE", "CHOP"):
        mult *= 0.75
        why += "+RANGE降仓"
    mult = max(0.0, min(1.0, float(mult)))
    return mult, why


def bias_gate_for_live(
    trade_direction: str,
    bias: Dict[str, Any] | None,
    *,
    min_abs: float = 25.0,
) -> tuple[bool, str]:
    """LIVE 是否允许：强逆势直接否；其余交由分数/EV 决定。"""
    bias = bias or {}
    try:
        bs = float(bias.get("bias_score") or 0.0)
    except Exception:
        bs = 0.0
    d = str(trade_direction or "").strip().lower()
    is_long = d in ("long", "buy")
    is_short = d in ("short", "sell")
    if is_long and bs <= -40:
        return False, f"LIVE禁止强逆势做多 bias={bs}"
    if is_short and bs >= 40:
        return False, f"LIVE禁止强逆势做空 bias={bs}"
    if abs(bs) < float(min_abs):
        return True, f"偏见弱放行半仓路径 bias={bs}"  # 不硬杀，由 size_mult 降仓
    return True, f"偏见门控通过 bias={bs}"
