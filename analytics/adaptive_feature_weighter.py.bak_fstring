# -*- coding: utf-8 -*-
"""
自适应特征权重统一入口。
从 utils.adaptive_features 导入 AdaptiveFeatureWeighter 并暴露全局实例。
"""
from utils.adaptive_features import AdaptiveFeatureWeighter

# 全局单例
feature_weighter = AdaptiveFeatureWeighter()

# 在导入后立即改写默认权重
# 确保新增的 LIQUIDITY / VOLATILITY / REGIME / VWAP 存在初始权重
_DEFAULT_WEIGHTS = {
    "SQZMOM": 1.0,
    "OB": 1.0,
    "FVG": 1.0,
    "CHOCH": 1.0,
    "DIVERGENCE": 1.35,
    "LIQUIDITY": 1.0,
    "VOLATILITY": 1.0,
    "REGIME": 1.0,
    "VWAP": 1.0,
}

# 如果单例中尚未包含这些特征，注入默认权重
for feat, w in _DEFAULT_WEIGHTS.items():
    if feat not in feature_weighter.feature_stats:
        feature_weighter.feature_stats[feat] = {
            "wins": 0, "trades": 0, "avg_r": 0.0, "weight": w
        }

# 确认样本保护所需字段
if not hasattr(feature_weighter, "samples"):
    feature_weighter.samples = {}


def get_weight(feature: str) -> float:
    """获取单个特征当前权重（供 V56.5 Engine 使用）。"""
    return feature_weighter.feature_stats.get(feature, {}).get("weight", 1.0)


def update_feature(feature: str, outcome_r: float) -> None:
    """更新单个特征的统计（含样本保护）。

    Args:
        feature: 映射后的特征名（如 'LIQUIDITY' 而非 'BSL_SWEEP'）
        outcome_r: 该笔交易的盈亏 R 倍数
    """
    if feature not in feature_weighter.feature_stats:
        return

    # 样本保护：累计 <30 笔时不更新权重
    feature_weighter.samples[feature] = feature_weighter.samples.get(feature, 0) + 1
    if feature_weighter.samples[feature] < 30:
        return

    s = feature_weighter.feature_stats[feature]
    if abs(outcome_r) >= 0.2:
        s["trades"] += 1
        if outcome_r > 0.2:
            s["wins"] += 1
        prev_total = s.get("avg_r", 0) * (s["trades"] - 1)
        s["avg_r"] = (prev_total + outcome_r) / s["trades"]
        win_rate = s["wins"] / s["trades"] if s["trades"] > 0 else 0.5
        new_weight = 0.6 * s.get("weight", 1.0) + 0.4 * (win_rate * 1.8 + s["avg_r"] * 0.8)
        # 分层限幅：Structure 层 [0.70, 1.30]；新增层 [0.85, 1.15]
        _new_features = {"LIQUIDITY", "VOLATILITY", "REGIME", "VWAP"}
        if feature in _new_features:
            new_weight = max(0.85, min(new_weight, 1.15))
        else:
            new_weight = max(0.70, min(new_weight, 1.30))
        s["weight"] = new_weight
