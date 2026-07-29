# -*- coding: utf-8 -*-
"""
V34 Regime Switching Engine（4-Regime 组合切换系统）

核心架构：
1. REGIME 分类器（4 种状态）
2. Portfolio 选择器（每个 Regime 对应一个 Portfolio）
3. Portfolio Allocation（资金分配）
4. Score 系统（局部模型）

设计原则：
✅ "组合切换"而非"策略切换"
✅ 每个 Portfolio 有独立的 entry 规则和仓位权重
✅ 不同市场状态 = 不同模型
❌ 删除：REVERSAL_ONLY 主策略、EARLY_REVERSAL_POOL fallback、
   SCORE_LT_* 全部阈值链、breakout=0 fallback logic、
   by_grade 决策逻辑、cross_regime_grade 参与交易
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd


# ============================================================
# 1. REGIME 分类器
# ============================================================

def classify_regime(row: pd.Series, exec_ctx: Dict[str, Any]) -> str:
    """
    4-Regime 分类器。
    
    优先级：CRISIS_RISK_OFF > TREND > CHOP > TRANSITION
    
    返回: "TREND" | "CHOP" | "TRANSITION" | "CRISIS_RISK_OFF"
    """
    adx = float(row.get("adx", 0) or 0)
    atr_14 = float(row.get("ATRr_14", 0) or 0)
    close = float(row.get("close", 0) or 0)
    atr_spike_ratio = atr_14 / (close * 0.006) if close > 0 else 1.0
    
    # 趋势强度：ADX 归一化到 0~1
    trend_strength = min(1.0, adx / 60.0)
    
    # 波动率扩张检测
    atr_ma = float(row.get("ATRr_50", atr_14) or atr_14)
    
    # 流动性检测
    volume_ratio = float(row.get("volume_ratio", 1.0) or 1.0)
    liquidity_drop = volume_ratio < 0.4
    
    # 趋势不确定性
    plus_di = float(row.get("plus_di", 0) or 0)
    minus_di = float(row.get("minus_di", 0) or 0)
    
    # ===== 优先级 1: CRISIS_RISK_OFF =====
    # ATR 剧烈扩张（>2.5倍基准）或 流动性骤降
    atr_spike_threshold = 2.5
    if atr_spike_ratio > atr_spike_threshold or liquidity_drop:
        return "CRISIS_RISK_OFF"
    
    # ===== 优先级 2: TREND =====
    if trend_strength > 0.6 and adx > 20:
        return "TREND"
    
    # ===== 优先级 3: CHOP =====
    if trend_strength < 0.4:
        return "CHOP"
    
    # ===== 优先级 4: TRANSITION（默认） =====
    return "TRANSITION"


# ============================================================
# 2. Portfolio 定义
# ============================================================

# TREND PORTFOLIO（主赚钱引擎）
# 只允许趋势延续和突破
TREND_PORTFOLIO = {
    "trend_follow": 0.6,
    "breakout": 0.4,
    "reversal": 0.0,  # 禁止反转
}

# TRANSITION PORTFOLIO（核心 alpha 区）
# 回测证明 transition win rate 最高（36%），PF 最好（0.51）
TRANSITION_PORTFOLIO = {
    "micro_reversion": 0.5,
    "liquidity_sweep": 0.5,
}

# CHOP PORTFOLIO（必须极简）
# 不看趋势，不做 breakout，只做均值回归
CHOP_PORTFOLIO = {
    "mean_reversion_only": 1.0,
}

# CRISIS / RISK OFF PORTFOLIO（必须新增）
RISK_OFF_PORTFOLIO = {
    "no_trade": True,
}


# ============================================================
# 3. Portfolio 选择器
# ============================================================

def select_portfolio(regime: str) -> Dict[str, Any]:
    """
    根据 Regime 选择对应的 Portfolio。
    
    返回 Portfolio 配置字典。
    """
    if regime == "TREND":
        return dict(TREND_PORTFOLIO)
    if regime == "TRANSITION":
        return dict(TRANSITION_PORTFOLIO)
    if regime == "CHOP":
        return dict(CHOP_PORTFOLIO)
    if regime == "CRISIS_RISK_OFF":
        return dict(RISK_OFF_PORTFOLIO)
    # 默认安全
    return dict(RISK_OFF_PORTFOLIO)


# ============================================================
# 4. Portfolio Allocation（资金分配）
# ============================================================

REGIME_MULTIPLIER = {
    "TREND": 1.0,
    "TRANSITION": 1.4,
    "CHOP": 0.6,
    "CRISIS_RISK_OFF": 0.0,
}


def calc_position_size(
    base_risk: float,
    regime: str,
    score: float,
    score_min: float = 0.0,
    score_max: float = 100.0,
) -> float:
    """
    计算仓位大小。
    
    position_size = base_risk * regime_multiplier * score_multiplier
    
    Args:
        base_risk: 基础风险（通常 0.01~0.02）
        regime: 当前市场状态
        score: 信号分数（0~100）
        score_min: 分数下限（低于此值不开仓）
        score_max: 分数上限
    
    Returns:
        仓位大小（0.0 ~ 1.0）
    """
    regime_mult = REGIME_MULTIPLIER.get(regime, 0.0)
    
    # 分数乘数：线性映射 0~100 → 0.5~1.0
    score_norm = max(0.0, min(1.0, (score - score_min) / max(score_max - score_min, 1e-9)))
    score_mult = 0.5 + score_norm * 0.5
    
    return base_risk * regime_mult * score_mult


# ============================================================
# 5. Score 系统（局部模型）
# ============================================================

def score_for_regime(
    regime: str,
    row: pd.Series,
    direction: str,
    exec_ctx: Dict[str, Any],
    macro_ctx: Dict[str, Any],
    entry_meta: Dict[str, Any],
    alpha_meta: Dict[str, Any],
) -> float:
    """
    不同市场状态使用不同的评分模型。
    
    关键原则：
    - TREND: 趋势延续 + 突破
    - TRANSITION: 微结构反转 + 流动性扫荡
    - CHOP: 均值回归
    - CRISIS_RISK_OFF: 不开仓
    """
    if regime == "CRISIS_RISK_OFF":
        return 0.0
    
    base_score = float(alpha_meta.get("score", 0.0))
    
    if regime == "TREND":
        return _trend_score(base_score, row, direction, exec_ctx, entry_meta)
    elif regime == "TRANSITION":
        return _transition_score(base_score, row, direction, exec_ctx, entry_meta)
    elif regime == "CHOP":
        return _chop_score(base_score, row, direction, exec_ctx, entry_meta)
    else:
        return base_score


def _trend_score(
    base_score: float,
    row: pd.Series,
    direction: str,
    exec_ctx: Dict[str, Any],
    entry_meta: Dict[str, Any],
) -> float:
    """
    TREND 评分模型。
    
    只允许：
    - Trend continuation（趋势延续）
    - Breakout（突破）
    
    Entry 规则：
    if score > 0.55 and alignment == True:
        LONG / SHORT
    """
    direction = str(direction).title()
    trend_dir = str(exec_ctx.get("trend_direction", "None"))
    
    # 趋势对齐检查
    alignment = (trend_dir == direction)
    
    # 趋势延续加分
    if alignment:
        base_score *= 1.2
    
    # 突破加分
    if bool(row.get("breakout_long", False)) and direction == "Long":
        base_score += 10.0
    if bool(row.get("breakout_short", False)) and direction == "Short":
        base_score += 10.0
    
    # 反转惩罚（TREND 中禁止反转）
    if bool(entry_meta.get("early_reversal_pool", False)):
        base_score *= 0.3
    
    # 归一化到 0~100
    return max(0.0, min(100.0, base_score))


def _transition_score(
    base_score: float,
    row: pd.Series,
    direction: str,
    exec_ctx: Dict[str, Any],
    entry_meta: Dict[str, Any],
) -> float:
    """
    TRANSITION 评分模型。
    
    核心 alpha 区：
    - micro_reversion（微结构反转）
    - liquidity_sweep（流动性扫荡）
    
    Entry 规则：
    if volatility_expansion and trend_confusion:
        trade_allowed = True
    """
    direction = str(direction).title()
    adx = float(row.get("adx", 0) or 0)
    plus_di = float(row.get("plus_di", 0) or 0)
    minus_di = float(row.get("minus_di", 0) or 0)
    volume_ratio = float(row.get("volume_ratio", 1.0) or 1.0)
    
    # 波动率扩张
    vol_expansion = volume_ratio > 1.2
    
    # 趋势混乱（DMI 交叉或 ADX 低）
    trend_confusion = (adx < 23) or (abs(plus_di - minus_di) < 10.0)
    
    # 流动性扫荡加分
    if bool(entry_meta.get("liquidity_sweep_confirmed", False)):
        base_score += 15.0
    
    # 微结构反转加分
    if bool(entry_meta.get("divergence_confirmed", False)):
        base_score += 10.0
    
    # 波动率扩张 + 趋势混乱 = 核心 alpha 环境
    if vol_expansion and trend_confusion:
        base_score *= 1.3
    
    return max(0.0, min(100.0, base_score))


def _chop_score(
    base_score: float,
    row: pd.Series,
    direction: str,
    exec_ctx: Dict[str, Any],
    entry_meta: Dict[str, Any],
) -> float:
    """
    CHOP 评分模型。
    
    强约束：
    - 不看趋势
    - 不做 breakout
    - 只做均值回归
    """
    direction = str(direction).title()
    close = float(row.get("close", 0) or 0)
    ema_20 = float(row.get("ema_20", 0) or 0)
    ema_50 = float(row.get("ema_50", 0) or 0)
    
    # 均值回归信号：价格远离均线
    if ema_20 > 0 and ema_50 > 0:
        dist_from_ema20 = (close - ema_20) / ema_20
        dist_from_ema50 = (close - ema_50) / ema_50
        
        # 价格远离 EMA20 → 回归信号
        if abs(dist_from_ema20) > 0.01:
            if direction == "Long" and dist_from_ema20 < -0.01:
                base_score += 15.0  # 价格低于 EMA20，做多回归
            elif direction == "Short" and dist_from_ema20 > 0.01:
                base_score += 15.0  # 价格高于 EMA20，做空回归
    
    # 突破惩罚（CHOP 中禁止突破）
    if bool(row.get("breakout_long", False)) or bool(row.get("breakout_short", False)):
        base_score *= 0.2
    
    # 反转在 CHOP 中允许（均值回归本质就是反转）
    # 不额外加分也不减分
    
    return max(0.0, min(100.0, base_score))


# ============================================================
# 6. Entry 规则检查
# ============================================================

def check_entry_allowed(
    regime: str,
    direction: str,
    score: float,
    row: pd.Series,
    exec_ctx: Dict[str, Any],
    entry_meta: Dict[str, Any],
) -> Tuple[bool, str]:
    """
    根据 Regime 和 Portfolio 规则检查是否允许入场。
    
    返回 (allowed, reason)
    """
    if regime == "CRISIS_RISK_OFF":
        return False, "CRISIS_RISK_OFF_NO_TRADE"
    
    if regime == "TREND":
        trend_dir = str(exec_ctx.get("trend_direction", "None"))
        alignment = (trend_dir == str(direction).title())
        
        # TREND 要求：score > 55 且 趋势对齐
        if score > 55.0 and alignment:
            return True, "TREND_ALIGNED_SCORE_OK"
        elif score > 55.0 and not alignment:
            return False, "TREND_MISALIGNED"
        else:
            return False, f"TREND_SCORE_TOO_LOW_{score:.0f}"
    
    if regime == "TRANSITION":
        adx = float(row.get("adx", 0) or 0)
        plus_di = float(row.get("plus_di", 0) or 0)
        minus_di = float(row.get("minus_di", 0) or 0)
        volume_ratio = float(row.get("volume_ratio", 1.0) or 1.0)
        
        vol_expansion = volume_ratio > 1.2
        trend_confusion = (adx < 23) or (abs(plus_di - minus_di) < 10.0)
        
        # TRANSITION 核心条件：波动率扩张 + 趋势混乱
        if vol_expansion and trend_confusion:
            return True, "TRANSITION_VOL_EXPANSION_CONFUSION"
        elif score > 60.0:
            return True, "TRANSITION_HIGH_SCORE"
        else:
            return False, "TRANSITION_NO_VOL_EXPANSION"
    
    if regime == "CHOP":
        # CHOP 只做均值回归，分数 > 50 即可
        if score > 50.0:
            return True, "CHOP_MEAN_REVERSION_OK"
        else:
            return False, f"CHOP_SCORE_TOO_LOW_{score:.0f}"
    
    return False, "UNKNOWN_REGIME"


# ============================================================
# 7. 主入口
# ============================================================

def v34_regime_decision(
    row: pd.Series,
    direction: str,
    exec_ctx: Dict[str, Any],
    macro_ctx: Dict[str, Any],
    entry_meta: Dict[str, Any],
    alpha_meta: Dict[str, Any],
    base_risk: float = 0.02,
) -> Dict[str, Any]:
    """
    V34 Regime Switching Engine 主入口。
    
    流程：
    1. 分类 Regime
    2. 选择 Portfolio
    3. 计算局部 Score
    4. 检查 Entry 规则
    5. 计算仓位大小
    
    返回:
        {
            "regime": str,
            "portfolio": dict,
            "score": float,
            "entry_allowed": bool,
            "entry_reason": str,
            "position_size": float,
        }
    """
    # 1. 分类 Regime
    regime = classify_regime(row, exec_ctx)
    
    # 2. 选择 Portfolio
    portfolio = select_portfolio(regime)
    
    # 3. 计算局部 Score
    score = score_for_regime(regime, row, direction, exec_ctx, macro_ctx, entry_meta, alpha_meta)
    
    # 4. 检查 Entry 规则
    entry_allowed, entry_reason = check_entry_allowed(regime, direction, score, row, exec_ctx, entry_meta)
    
    # 5. 计算仓位大小
    position_size = calc_position_size(base_risk, regime, score)
    
    return {
        "regime": regime,
        "portfolio": portfolio,
        "score": round(score, 2),
        "entry_allowed": entry_allowed,
        "entry_reason": entry_reason,
        "position_size": round(position_size, 4),
    }
