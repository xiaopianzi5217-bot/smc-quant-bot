# -*- coding: utf-8 -*-
"""
Confidence Engine — 多因子可信度评分（V40）

基于样本量、PF、方差、Regime 一致性四个因子的加权几何平均，
输出 0.30 ~ 0.99 的连续可信度评分。

设计原则：
  - 乘法因子：任一因子低分自然降级，但不是硬 0 裁断
  - 加权几何平均：避免单因子 0 拖死整体
  - 输出始终 clamp 到 [0.30, 0.99]

用法：
    from analytics.confidence_engine import ConfidenceEngine
    ce = ConfidenceEngine()
    result = ce.compute(trades=182, pf=2.43, std_r=0.65, same_regime=True)
    print(result.confidence)  # e.g. 0.8741
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List
import math


@dataclass
class ConfidenceResult:
    """多因子可信度评分结果"""
    confidence: float       # 最终可信度 0.30~0.99
    sample_score: float     # 样本量因子 0~1
    pf_score: float         # 盈利因子 0~1
    variance_score: float   # 方差因子 0~1
    regime_score: float     # Regime 一致性 0.75 或 1.0
    reasons: List[str]      # 得分理由


class ConfidenceEngine:
    """多因子可信度评分引擎"""

    # 各因子权重（总和应为 1.0）
    WEIGHTS = {
        "sample": 0.30,
        "pf": 0.30,
        "variance": 0.25,
        "regime": 0.15,
    }

    def __init__(
        self,
        min_sample: int = 20,
        max_sample: int = 200,
    ):
        self.min_sample = min_sample
        self.max_sample = max_sample

    def compute(
        self,
        trades: int,
        pf: float,
        std_r: float = 0.8,
        same_regime: bool = True,
    ) -> ConfidenceResult:
        """计算多因子可信度

        参数:
            trades:    该 regime/该特征下累计交易笔数
            pf:        Profit Factor（总盈利 / 总亏损）
            std_r:     单笔 R 的标准差（衡量稳定性）
            same_regime: 当前 regime 是否与该统计数据的 regime 一致

        返回:
            ConfidenceResult 对象，confidence 为 0.30~0.99
        """
        # ---- 1. Sample Score (0~1) ----
        if trades <= 0:
            sample_score = 0.0
        elif trades < self.min_sample:
            sample_score = trades / self.min_sample * 0.5  # 小于最低样本时线性衰减
        else:
            sample_score = min(1.0, trades / self.max_sample)

        # ---- 2. PF Score (0~1) ----
        # PF < 1.0 无意义，PF 2.5 以上视为满分
        if pf <= 0:
            pf_score = 0.0
        elif pf < 1.0:
            pf_score = pf * 0.3  # 0~1 映射到 0~0.3
        else:
            pf_score = min(1.0, pf / 2.5)

        # ---- 3. Variance Score (0~1) ----
        # std_r 越小越稳定。std_r=0 表示完全稳定，std_r=2.0 以上视为不稳定
        variance_score = 1.0 / (1.0 + std_r)

        # ---- 4. Regime Score ----
        regime_score = 1.0 if same_regime else 0.75

        # ---- 加权几何平均 ----
        w = self.WEIGHTS
        # 用 pow 实现加权几何平均: prod(x_i ^ w_i)
        confidence = (
            math.pow(max(sample_score, 1e-12), w["sample"]) *
            math.pow(max(pf_score, 1e-12), w["pf"]) *
            math.pow(max(variance_score, 1e-12), w["variance"]) *
            math.pow(max(regime_score, 1e-12), w["regime"])
        )

        # Clamp 到合理范围
        confidence = max(0.30, min(confidence, 0.99))

        # ---- 理由 ----
        reasons = []
        if trades > 80:
            reasons.append("HIGH_SAMPLE")
        elif trades > 40:
            reasons.append("MID_SAMPLE")
        if pf > 2.0:
            reasons.append("STRONG_PF")
        elif pf > 1.5:
            reasons.append("MODERATE_PF")
        if variance_score > 0.7:
            reasons.append("LOW_VARIANCE")
        elif variance_score < 0.4:
            reasons.append("HIGH_VARIANCE")
        if regime_score == 1.0:
            reasons.append("REGIME_MATCH")
        else:
            reasons.append("REGIME_MISMATCH")

        return ConfidenceResult(
            confidence=round(confidence, 4),
            sample_score=round(sample_score, 4),
            pf_score=round(pf_score, 4),
            variance_score=round(variance_score, 4),
            regime_score=regime_score,
            reasons=reasons,
        )


# 全局单例
confidence_engine = ConfidenceEngine()
