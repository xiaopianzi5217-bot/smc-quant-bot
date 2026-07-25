# analytics/adaptive_feature_weighter.py
"""
自适应特征权重统一入口。
从 utils.adaptive_features 导入 AdaptiveFeatureWeighter 并暴露全局实例。
"""
from utils.adaptive_features import AdaptiveFeatureWeighter

# 全局单例
feature_weighter = AdaptiveFeatureWeighter()