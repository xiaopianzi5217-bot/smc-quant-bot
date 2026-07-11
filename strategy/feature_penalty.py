# -*- coding: utf-8 -*-
"""
Feature Penalty — 优化4: 评分重复奖励惩罚

当前问题：
  趋势可能 score 奖励一次、EV奖励一次、cluster奖励一次，
  同一行情因素被重复计入，导致分数虚高。

解决方案：
  检测特征重叠（trend_count / volatility_count），
  当多个特征指向同一因素时，施加惩罚分数。

用法:
  from strategy.feature_penalty import calculate_feature_overlap, apply_feature_penalty
  penalty = calculate_feature_overlap(features)
  final_score = apply_feature_penalty(score, features)
"""

from __future__ import annotations

from typing import Dict, Any, Optional


# ============================================================
# 特征分组定义
# ============================================================
TREND_FEATURES = [
    "ema_trend",
    "adx",
    "structure_break",
    "momentum",
    "trend_direction",
    "ema_alignment",
]

VOLATILITY_FEATURES = [
    "atr_expand",
    "squeeze_release",
    "volume_break",
    "volatility_spike",
    "bb_width_expand",
]

MOMENTUM_FEATURES = [
    "rsi_momentum",
    "macd_cross",
    "price_acceleration",
    "volume_surge",
]

# 分组内最大允许激活数（超过则开始惩罚）
MAX_ALLOWED_PER_GROUP = {
    "trend": 2,
    "volatility": 2,
    "momentum": 2,
}


def calculate_feature_overlap(features: Dict[str, Any]) -> float:
    """计算特征重叠惩罚分数

    参数:
        features: 特征字典（key=特征名, value=bool/int/float 表示是否激活）

    返回:
        惩罚分数（>=0, 从最终 score 中减去）
    """
    penalty = 0.0

    # —— 趋势特征重叠 ——
    trend_count = 0
    for feat in TREND_FEATURES:
        if features.get(feat):
            trend_count += 1
    if trend_count > MAX_ALLOWED_PER_GROUP["trend"]:
        excess = trend_count - MAX_ALLOWED_PER_GROUP["trend"]
        penalty += excess * 5.0  # 每个超出特征惩罚 5 分
    elif trend_count >= 3:
        penalty += 3.0  # 3个以上趋势特征，基础惩罚 3 分

    # —— 波动率特征重叠 ——
    vol_count = 0
    for feat in VOLATILITY_FEATURES:
        if features.get(feat):
            vol_count += 1
    if vol_count > MAX_ALLOWED_PER_GROUP["volatility"]:
        excess = vol_count - MAX_ALLOWED_PER_GROUP["volatility"]
        penalty += excess * 4.0
    elif vol_count >= 3:
        penalty += 2.0

    # —— 动量特征重叠 ——
    mom_count = 0
    for feat in MOMENTUM_FEATURES:
        if features.get(feat):
            mom_count += 1
    if mom_count > MAX_ALLOWED_PER_GROUP["momentum"]:
        excess = mom_count - MAX_ALLOWED_PER_GROUP["momentum"]
        penalty += excess * 3.0
    elif mom_count >= 3:
        penalty += 1.0

    # —— 跨组重叠惩罚 ——
    # 如果趋势 + 波动 + 动量同时大量激活，说明"Everything is aligned"但可能有幻觉
    if trend_count >= 2 and vol_count >= 2 and mom_count >= 2:
        penalty += 3.0  # 全组重叠额外惩罚

    return round(penalty, 2)


def apply_feature_penalty(score: float, features: Dict[str, Any]) -> float:
    """对 score 应用特征重叠惩罚

    参数:
        score: 原始分数
        features: 特征字典

    返回:
        惩罚后的分数
    """
    penalty = calculate_feature_overlap(features)
    final_score = max(0.0, score - penalty)
    return round(final_score, 2)


def get_feature_overlap_summary(features: Dict[str, Any]) -> Dict[str, Any]:
    """返回特征重叠诊断信息"""
    trend_count = sum(1 for f in TREND_FEATURES if features.get(f))
    vol_count = sum(1 for f in VOLATILITY_FEATURES if features.get(f))
    mom_count = sum(1 for f in MOMENTUM_FEATURES if features.get(f))
    penalty = calculate_feature_overlap(features)

    return {
        "trend_count": trend_count,
        "volatility_count": vol_count,
        "momentum_count": mom_count,
        "max_allowed_trend": MAX_ALLOWED_PER_GROUP["trend"],
        "max_allowed_volatility": MAX_ALLOWED_PER_GROUP["volatility"],
        "max_allowed_momentum": MAX_ALLOWED_PER_GROUP["momentum"],
        "penalty": penalty,
    }
