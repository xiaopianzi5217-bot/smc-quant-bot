""" Feature importance learning engine — Outcome → Feature weight update

用法:
    learner = FeatureLearningEngine()

    # 开单时记录特征快照
    learner.record_features(signal_id, features_dict)

    # 平仓时更新权重
    learner.update(signal_id, pnl_r)

    # 获取当前特征权重用于评分
    w = learner.get_weight("ob_quality")
"""
import json
import os
import time
from pathlib import Path
from typing import Dict, Optional


class FeatureLearningEngine:
    """特征学习引擎：Outcome → Feature Weight 闭环

    设计原则:
    - 每个特征有独立的 weight
    - 正收益 → 增强有效特征的 weight
    - 负收益 → 降低无效特征的 weight
    - weight 有上下限 [0.2, 3.0]，防止爆炸
    - 持久化到 JSON 文件，进程重启不丢失
    """

    def __init__(self, weight_file: str = "analytics/feature_weights.json"):
        self.weight_file = Path(weight_file)
        self.weight_file.parent.mkdir(parents=True, exist_ok=True)
        self.default_weights = {
            "smc_score": 1.0,
            "ob_quality": 1.0,
            "fvg_quality": 1.0,
            "sqzmom_state": 1.0,
            "volume_ratio": 1.0,
            "atr_pct": 1.0,
            "vwap_distance": 1.0,
            "regime": 1.0,
            "liquidity_sweep": 1.0,
            "cho_ch": 1.0,
            "divergence": 1.0,
            "momentum": 1.0,
            "structure_break": 1.0,
            "ema_alignment": 1.0,
            "squeeze_release": 1.0,
        }
        self.weights: Dict[str, float] = {}
        # signal_id -> {features: dict, timestamp: float}
        self._open_features: Dict[str, dict] = {}
        self._load()

    def _load(self):
        if self.weight_file.exists():
            try:
                data = json.loads(self.weight_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self.weights = data
                    return
            except Exception as e:
                print(f"[FeatureLearningEngine] 加载失败: {e}")
        self.weights = self.default_weights.copy()
        self._save()

    def _save(self):
        try:
            self.weight_file.write_text(
                json.dumps(self.weights, indent=4, ensure_ascii=False),
                encoding="utf-8"
            )
        except Exception as e:
            print(f"[FeatureLearningEngine] 保存失败: {e}")

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------
    def record_features(self, signal_id: str, features: Dict[str, float]):
        """开单时记录特征快照

        Args:
            signal_id: 信号唯一 ID（与 SignalTracker 一致）
            features: 特征名 -> 特征值（归一化到 0~1 或对评分的贡献值）
                      例如: {"ob_quality": 0.85, "fvg_quality": 0.0, "sqzmom_state": 0.3}
        """
        self._open_features[signal_id] = {
            "features": features,
            "timestamp": time.time(),
        }

    def update(self, signal_id: str, pnl_r: float):
        """平仓时更新特征权重

        Args:
            signal_id: 信号 ID
            pnl_r: 盈亏 R（正数 = 盈利，负数 = 亏损）
        """
        record = self._open_features.pop(signal_id, None)
        if record is None:
            return

        features = record.get("features", {})
        if not features:
            return

        learning_rate = 0.02
        # 用 tanh 压缩极端 R 值，防止单笔巨亏/巨赚过度扭曲权重
        compressed_r = max(-2.0, min(2.0, pnl_r))

        for name, value in features.items():
            if name not in self.weights:
                # 新特征：使用默认权重
                default = self.default_weights.get(name, 1.0)
                self.weights[name] = default
                continue

            # 核心学习规则:
            #   weight += learning_rate * value * pnl_r
            # 正收益 + 高特征值 → weight ↑
            # 正收益 + 低特征值 → weight 微增
            # 负收益 + 高特征值 → weight ↓（这个特征在亏钱）
            # 负收益 + 低特征值 → weight 微降
            impact = value * compressed_r
            self.weights[name] += learning_rate * impact

            # 防止权重爆炸
            self.weights[name] = max(0.2, min(3.0, self.weights[name]))

        self._save()

    def get_weight(self, name: str) -> float:
        """获取特征权重"""
        return self.weights.get(name, self.default_weights.get(name, 1.0))

    def get_all_weights(self) -> Dict[str, float]:
        """获取所有权重（用于调试和可视化）"""
        return dict(self.weights)

    def get_weighted_score(self, raw_scores: Dict[str, float]) -> float:
        """计算加权总分

        Args:
            raw_scores: 特征名 -> 原始分数

        Returns:
            加权后的总分
        """
        total = 0.0
        for name, value in raw_scores.items():
            total += value * self.get_weight(name)
        return total

    def get_feature_contribution(self, raw_scores: Dict[str, float]) -> Dict[str, float]:
        """计算每个特征对最终分数的贡献（调试用）

        Returns:
            特征名 -> 贡献值 (value * weight)
        """
        return {
            name: value * self.get_weight(name)
            for name, value in raw_scores.items()
        }

    def reset(self):
        """重置所有权重为默认值"""
        self.weights = self.default_weights.copy()
        self._save()


# 全局单例
_learner: Optional[FeatureLearningEngine] = None


def get_feature_learner() -> FeatureLearningEngine:
    global _learner
    if _learner is None:
        _learner = FeatureLearningEngine()
    return _learner
