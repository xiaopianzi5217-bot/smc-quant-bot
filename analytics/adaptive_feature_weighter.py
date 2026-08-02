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

    # 样本保护与分阶段限幅策略：
    # 0-99 笔：仅记录样本，不调整权重（数据收集阶段）
    # 100-499 笔：轻微调整，权重相对当前值波动不超过 ±5%
    # 500-999 笔：放宽限制，允许更大调整（最多 ±20%），但仍保持分层上下界
    # >=1000 笔：按原始逻辑更新（但仍受全局上下界限制）
    feature_weighter.samples[feature] = feature_weighter.samples.get(feature, 0) + 1

    s = feature_weighter.feature_stats[feature]
    if abs(outcome_r) < 0.2:
        # 对非常小的 outcome_r 不纳入样本统计
        return

    # 更新统计量（trade/win/avg_r）在所有阶段都进行
    s["trades"] += 1
    if outcome_r > 0.2:
        s["wins"] += 1
    prev_total = s.get("avg_r", 0) * (s["trades"] - 1)
    s["avg_r"] = (prev_total + outcome_r) / s["trades"]
    win_rate = s["wins"] / s["trades"] if s["trades"] > 0 else 0.5

    current = s.get("weight", 1.0)
    # 基础候选权重（原始计算逻辑）
    candidate = 0.6 * current + 0.4 * (win_rate * 1.8 + s["avg_r"] * 0.8)

    samples = feature_weighter.samples[feature]
    # 分层限幅基础
    _new_features = {"LIQUIDITY", "VOLATILITY", "REGIME", "VWAP"}
    if feature in _new_features:
        global_min, global_max = 0.85, 1.15
    else:
        global_min, global_max = 0.70, 1.30

    if samples < 100:
        # 仅记录，不更新权重
        return
    elif samples < 500:
        # 轻微调整 ±5%
        lower = max(global_min, current * 0.95)
        upper = min(global_max, current * 1.05)
        new_weight = max(lower, min(candidate, upper))
    elif samples < 1000:
        # 中度调整 ±20%（受全局上下界约束）
        lower = max(global_min, current * 0.80)
        upper = min(global_max, current * 1.20)
        new_weight = max(lower, min(candidate, upper))
    else:
        # >=1000：放开到全局上下界
        new_weight = max(global_min, min(candidate, global_max))

    s["weight"] = new_weight
