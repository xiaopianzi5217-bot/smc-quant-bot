# utils/adaptive_features.py
import json
import os
from collections import defaultdict


class AdaptiveFeatureWeighter:
    """自适应特征加权：根据历史盈亏动态调整各信号特征的权重。

    支持以下特征类型权重自学习：
      - OB (Order Block)
      - FVG (Fair Value Gap)
      - CHOCH (Change of Character)
      - SQZMOM (Squeeze Momentum)
      - DIVERGENCE (背离)

    用法：
        weighter = AdaptiveFeatureWeighter()
        # 每笔交易结束后更新
        weighter.update(features=["OB", "SQZMOM"], outcome_r=1.5)
        # 计算加权总分
        weighted = weighter.get_weighted_score({"OB": 10, "SQZMOM": 8})
    """

    def __init__(self, window=200, save_path="feature_stats.json"):
        self.window = window
        self.save_path = save_path
        self.feature_stats = self._load_stats()
        self.history = []

    def _load_stats(self):
        """从磁盘加载统计，若文件不存在则返回默认初始权重。"""
        if os.path.exists(self.save_path):
            try:
                with open(self.save_path, 'r') as f:
                    return json.load(f)
            except Exception:
                pass
        # 默认初始权重（基于历史经验）
        return {
            "OB": {"wins": 0, "trades": 0, "avg_r": 0.0, "weight": 1.15},
            "FVG": {"wins": 0, "trades": 0, "avg_r": 0.0, "weight": 1.0},
            "CHOCH": {"wins": 0, "trades": 0, "avg_r": 0.0, "weight": 1.12},
            "SQZMOM": {"wins": 0, "trades": 0, "avg_r": 0.0, "weight": 1.25},
            "DIVERGENCE": {"wins": 0, "trades": 0, "avg_r": 0.0, "weight": 1.35},
        }

    def update(self, features: list, outcome_r: float):
        """用一笔交易的结果更新特征统计。

        Args:
            features: 该笔交易激活的特征名称列表（如 ["OB", "DIVERGENCE"]）
            outcome_r: 该笔交易的最终盈亏 R 倍数
        """
        # 噪音过滤：|outcome_r| < 0.2R 不纳入学习
        if abs(outcome_r) >= 0.2:
            self.history.append((features, outcome_r))
            if len(self.history) > self.window:
                self.history.pop(0)

            for feat in features:
                if feat in self.feature_stats:
                    s = self.feature_stats[feat]
                    s["trades"] += 1
                    if outcome_r > 0.2:  # 仅 >0.2R 才算胜局
                        s["wins"] += 1
                # 递推更新 avg_r：new_avg = (old_avg * (n-1) + new_r) / n
                prev_total = s.get("avg_r", 0) * (s["trades"] - 1)
                s["avg_r"] = (prev_total + outcome_r) / s["trades"]

                win_rate = s["wins"] / s["trades"] if s["trades"] > 0 else 0.5
                # 平滑权重更新：60% 保留旧值 + 40% 新信号
                s["weight"] = 0.6 * s.get("weight", 1.0) + 0.4 * (win_rate * 1.8 + s["avg_r"] * 0.8)

        self._save_stats()

    def _save_stats(self):
        """持久化特征统计到磁盘。"""
        try:
            with open(self.save_path, 'w') as f:
                json.dump(self.feature_stats, f, indent=2)
        except Exception:
            pass

    def get_weighted_score(self, raw_scores: dict) -> float:
        """用自适应权重计算加权总分。

        限制：总乘数上限 1.5，防止连续优秀特征导致分数爆炸越界。

        Args:
            raw_scores: 特征名 -> 原始分值 dict

        Returns:
            加权后的总分
        """
        total = 0.0
        factor = 1.0
        for feat, value in raw_scores.items():
            weight = self.feature_stats.get(feat, {}).get("weight", 1.0)
            total += value * weight
            factor *= weight
        # 乘数上限 1.5，防止分数越界破坏后续阈值
        factor = max(0.8, min(factor, 1.2))
        return round(total * factor, 2)
