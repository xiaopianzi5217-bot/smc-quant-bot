# -*- coding: utf-8 -*-
"""
confidence_engine.py — 统一置信度模型（防过拟合核心）

作用：
    对 EV 结果进行置信度调节，样本不足时降低 EV 可信度。
    防止 5 笔交易就声称 EV=0.10 的过拟合问题。

公式：
    confidence = sample_conf * vol_conf * regime_stability
    
    其中：
    - sample_conf = n / (n + 20)    样本越多越可信
    - vol_conf = 1 / (1 + variance)  胜率偏离 0.5 越远越可疑（过拟合信号）
    - regime_stability = 市场状态稳定性（TREND=1.0, CHOP=0.4）
"""

from __future__ import annotations


class ConfidenceEngine:
    """统一置信度模型"""

    @staticmethod
    def score(
        n: int = 0,
        variance: float = 0.0,
        regime_stability: float = 0.7,
    ) -> float:
        """
        计算置信度分数

        参数:
            n: 该分桶的历史交易笔数
            variance: 胜率与 0.5 的绝对差距（过拟合惩罚）
            regime_stability: 市场稳定性 (0~1)

        返回:
            0~1 的置信度
        """
        # 样本置信度：20 笔达 50%，80 笔达 80%
        sample_conf = n / (n + 20.0)

        # 波动惩罚：胜率偏离 0.5 越远越不可信
        vol_conf = 1.0 / (1.0 + max(variance, 0.0001))

        return sample_conf * vol_conf * max(0.0, min(1.0, regime_stability))


# 全局单例
confidence_engine = ConfidenceEngine()
