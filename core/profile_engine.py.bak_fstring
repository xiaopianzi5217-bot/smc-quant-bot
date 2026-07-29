"""
Profile Engine — V52 简化收敛版

基于信号和 regime 计算仓位大小和置信度。
"""
from typing import Dict, Optional


class ProfileEngine:
    """
    仓位画像引擎（收敛版）

    将信号分数映射为具体的仓位大小和置信度。
    """

    def __init__(self):
        self.base_risk = 0.01  # 基础风险比例 (1%)

    def compute(self, signal: Dict, regime: Dict) -> Dict:
        """
        计算仓位画像。

        Args:
            signal: 信号结果 (含 score, valid 等)
            regime: 市场状态 (含 regime, confidence 等)

        Returns:
            dict: {
                "position_size": float,  # 仓位比例 (0~0.03)
                "confidence": float,     # 置信度 (0~1)
            }
        """
        if not signal.get("valid", False):
            return {"position_size": 0.0, "confidence": 0.0}

        confidence = signal.get("score", 0.0)

        # 基础仓位 = base_risk × 信号置信度
        size = self.base_risk * confidence

        # regime scaling — TRANSITION 时放大
        if regime.get("regime") == "TRANSITION":
            size *= 1.4

        # 上限保护
        size = min(size, 0.03)

        return {
            "position_size": float(size),
            "confidence": float(confidence),
        }