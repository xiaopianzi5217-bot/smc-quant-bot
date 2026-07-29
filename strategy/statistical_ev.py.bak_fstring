# -*- coding: utf-8 -*-
"""
Statistical EV — 优化5: 基于 Outcome Learning 的历史 EV

当前问题：
  model_ev 完全是模型预测，没有利用历史实际结果。
  模型可能持续高估/低估而不自知。

解决方案：
  用 OutcomeDatabase 中的历史实际盈亏计算 Statistical EV，
  与 model_ev 按权重混合（60% 历史 + 40% 模型），
  让历史结果开始影响未来决策。

用法:
  from strategy.statistical_ev import StatisticalEV, statistical_ev
  blended_ev = statistical_ev.blend(model_ev=0.08, features=feature_dict)
  # blended_ev = 0.06 (如果历史 EV=0.04, 权重 60/40)
"""

from __future__ import annotations

import hashlib
from typing import Dict, Any, Optional

from analytics.feature_hash import generate_feature_hash
from analytics.outcome_db import OutcomeDatabase


class StatisticalEV:
    """基于历史结果的 Statistical EV

    从 OutcomeDatabase 读取特征分桶的历史实际 EV，
    与模型预测的 model_ev 混合，输出更可靠的 blended EV。
    """

    def __init__(
        self,
        db: Optional[OutcomeDatabase] = None,
        min_trades: int = 30,
        hist_weight: float = 0.6,
    ):
        """
        参数:
            db: OutcomeDatabase 实例（默认新建）
            min_trades: 历史 EV 可信的最小交易数（默认 30）
            hist_weight: 历史 EV 的权重（默认 0.6，model_ev 权重 = 1 - hist_weight）
        """
        self.db = db or OutcomeDatabase()
        self.min_trades = min_trades
        self.hist_weight = hist_weight

    def get_historical_ev(self, features: Dict[str, Any]) -> Optional[float]:
        """获取特征分桶的历史 Statistical EV

        参数:
            features: 特征字典

        返回:
            历史 EV (mean_r)，如果样本不足返回 None
        """
        if not features:
            return None

        feature_hash = generate_feature_hash(features)
        stats = self.db.get_ev(feature_hash, min_trades=self.min_trades)

        if stats is None:
            return None

        return stats.get("ev")

    def get_historical_stats(self, features: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """获取完整的特征分桶历史统计

        参数:
            features: 特征字典

        返回:
            {
                "ev": float,
                "confidence": float,
                "sample": int,
                "pf": float,
                "win_rate": float,
                "std": float,
                "lower_bound": float,
                "upper_bound": float,
                "sharpe_ratio": float,
            }
            如果样本不足返回 None
        """
        if not features:
            return None

        feature_hash = generate_feature_hash(features)
        return self.db.get_ev(feature_hash, min_trades=self.min_trades)

    def blend(
        self,
        model_ev: float,
        features: Dict[str, Any],
    ) -> float:
        """混合 model_ev 与历史 Statistical EV

        参数:
            model_ev: 模型预测的 expected value
            features: 特征字典（用于查找历史分桶）

        返回:
            混合后的 EV（如果历史数据不足，则返回 model_ev）
        """
        hist_ev = self.get_historical_ev(features)

        if hist_ev is None:
            # 历史数据不足，纯用模型
            return round(model_ev, 4)

        # 混合：hist_weight * 历史 + (1 - hist_weight) * 模型
        blended = self.hist_weight * hist_ev + (1.0 - self.hist_weight) * model_ev
        return round(blended, 4)

    def get_blend_info(
        self,
        model_ev: float,
        features: Dict[str, Any],
    ) -> Dict[str, Any]:
        """返回混合 EV 的详细信息（用于调试和日志）

        参数:
            model_ev: 模型预测的 expected value
            features: 特征字典

        返回:
            {
                "model_ev": float,
                "historical_ev": float | None,
                "blended_ev": float,
                "hist_weight": float,
                "model_weight": float,
                "sample": int,
                "confidence": float | None,
            }
        """
        hist_ev = self.get_historical_ev(features)
        blended = self.blend(model_ev, features)
        stats = self.get_historical_stats(features)

        return {
            "model_ev": round(model_ev, 4),
            "historical_ev": round(hist_ev, 4) if hist_ev is not None else None,
            "blended_ev": blended,
            "hist_weight": self.hist_weight,
            "model_weight": round(1.0 - self.hist_weight, 2),
            "sample": stats.get("sample", 0) if stats else 0,
            "confidence": stats.get("confidence") if stats else None,
        }


# 全局单例
_statistical_ev = StatisticalEV()


def get_statistical_ev() -> StatisticalEV:
    return _statistical_ev
