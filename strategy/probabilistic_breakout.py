# -*- coding: utf-8 -*-
"""
Probabilistic Breakout Engine V1

突破概率评分（0~100），替代旧的 AND gate / binary filter。

设计原则：
    ✅ 连续概率空间（0→100），非 0/1 二元
    ✅ 四因子加权：ATR + Volume + Squeeze + Momentum
    ✅ Regime-aware 动态权重
    ✅ 每个子模块输出 0~100 分

用法：
    from strategy.probabilistic_breakout import breakout_probability
    bp = breakout_probability(ctx)
    breakout_score = bp["breakout_prob"]  # 0~100
    if breakout_score >= 60:
        # 突破信号
"""

from __future__ import annotations
from typing import Any, Dict
import math


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        out = float(v)
        return default if math.isnan(out) or math.isinf(out) else out
    except Exception:
        return default


def _safe_str(v: Any, default: str = "") -> str:
    try:
        if v is None:
            return default
        return str(v)
    except Exception:
        return default


# ============================================================
# 子模块 1：ATR 波动释放评分（0~100）
# ============================================================
def _atr_score(ctx: Dict[str, Any]) -> float:
    """
    ATR 波动释放评分（0~100）
    
    输入：
    - atr_pct: ATR / Close 百分比
    - 或 atr / close 计算
    
    评分：
    - > 3.0%: 90（极端波动）
    - > 2.0%: 70（高波动）
    - > 1.0%: 50（正常波动）
    - <= 1.0%: 30（低波动）
    """
    atr_pct = _safe_float(ctx.get("atr_pct", 0.0))
    if atr_pct > 0.03:
        return 90.0
    elif atr_pct > 0.02:
        return 70.0
    elif atr_pct > 0.01:
        return 50.0
    else:
        return 30.0


# ============================================================
# 子模块 2：Volume 量能评分（0~100）
# ============================================================
def _volume_score(ctx: Dict[str, Any]) -> float:
    """
    Volume 量能评分（0~100）
    
    输入：
    - volume_ratio: 当前量 / 20日均量
    
    评分：
    - > 1.5: 90（巨量爆发）
    - > 1.2: 70（放量）
    - > 1.0: 50（正常量）
    - <= 1.0: 30（缩量）
    """
    vol = _safe_float(ctx.get("volume_ratio", 1.0))
    if vol > 1.5:
        return 90.0
    elif vol > 1.2:
        return 70.0
    elif vol > 1.0:
        return 50.0
    else:
        return 30.0


# ============================================================
# 子模块 3：Squeeze 压缩释放评分（0~100）
# ============================================================
def _squeeze_score(ctx: Dict[str, Any]) -> float:
    """
    Squeeze 压缩释放评分（0~100）
    
    输入：
    - squeeze: "tight" / "mid" / "none"（字符串）
    - 或 squeeze_level: 3 / 2 / 1 / 0（整数）
    
    评分：
    - tight / level >= 3: 85（深度压缩）
    - mid / level == 2: 60（中度压缩）
    - none / level <= 1: 40（无压缩）
    """
    squeeze = _safe_str(ctx.get("squeeze", "none")).lower()
    squeeze_level = int(_safe_float(ctx.get("squeeze_level", 0), 0))
    
    if squeeze == "tight" or squeeze_level >= 3:
        return 85.0
    elif squeeze == "mid" or squeeze_level == 2:
        return 60.0
    else:
        return 40.0


# ============================================================
# 子模块 4：Momentum 方向动量评分（0~100）
# ============================================================
def _momentum_score(ctx: Dict[str, Any]) -> float:
    """
    Momentum 方向动量评分（0~100）
    
    输入：
    - sqzmom_score: 0~44（来自 _sqzmom_context_score）
    
    评分：
    - 直接映射 sqzmom_score 到 0~100
    - sqzmom_score 范围 0~44，乘以 100/44 ≈ 2.27
    """
    sqz = _safe_float(ctx.get("sqzmom_score", 0.0))
    return min(max(sqz * (100.0 / 44.0), 0.0), 100.0)


# ============================================================
# 主函数：breakout_probability
# ============================================================
def breakout_probability(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    Probabilistic Breakout Engine V1
    
    四因子加权评分，输出突破概率（0~100）。
    
    权重根据市场状态动态调整：
    - trend:      ATR=0.3, Vol=0.2, Squeeze=0.2, Mom=0.3
    - transition: ATR=0.25, Vol=0.25, Squeeze=0.25, Mom=0.25
    - mud:        ATR=0.2, Vol=0.3, Squeeze=0.2, Mom=0.3
    
    参数:
        ctx: 上下文字典，需包含：
            - atr_pct (float): ATR / Close
            - volume_ratio (float): 量比
            - squeeze (str) 或 squeeze_level (int): 压缩状态
            - sqzmom_score (float): SQZMOM 原始分
            - regime (str): 市场状态
    
    返回:
        {
            "breakout_prob": float,     # 0~100 突破概率
            "components": {
                "atr": float,           # ATR 分
                "volume": float,        # 量能分
                "squeeze": float,       # 压缩分
                "momentum": float       # 动量分
            },
            "weights": {
                "atr": float,
                "volume": float,
                "squeeze": float,
                "momentum": float
            }
        }
    """
    # 1. 计算各子模块分数
    atr = _atr_score(ctx)
    vol = _volume_score(ctx)
    squeeze = _squeeze_score(ctx)
    momentum = _momentum_score(ctx)

    # 2. Regime-aware 动态权重
    regime = _safe_str(ctx.get("regime", "trend")).lower()
    if regime == "trend":
        w_atr, w_vol, w_sqz, w_mom = 0.3, 0.2, 0.2, 0.3
    elif regime == "transition":
        w_atr, w_vol, w_sqz, w_mom = 0.25, 0.25, 0.25, 0.25
    else:  # mud
        w_atr, w_vol, w_sqz, w_mom = 0.2, 0.3, 0.2, 0.3

    # 3. 加权求和
    prob = (
        atr * w_atr +
        vol * w_vol +
        squeeze * w_sqz +
        momentum * w_mom
    )

    return {
        "breakout_prob": round(prob, 2),
        "components": {
            "atr": round(atr, 2),
            "volume": round(vol, 2),
            "squeeze": round(squeeze, 2),
            "momentum": round(momentum, 2),
        },
        "weights": {
            "atr": w_atr,
            "volume": w_vol,
            "squeeze": w_sqz,
            "momentum": w_mom,
        },
    }
