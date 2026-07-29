"""
Signal Engine — V52+V53 融合重写

非线性、regime-aware 的信号评分系统。
"""
import numpy as np
from typing import Dict, Optional


class SignalEngine:
    """
    信号引擎（V52+V53 融合）

    - 仅当 regime 允许交易时生成信号
    - 非线性评分（tanh 组合）
    - TRANSITION 状态获得 1.25x 提升
    """

    def generate(self, features: Dict[str, float], regime: Dict) -> Dict:
        """
        生成交易信号。

        Args:
            features: 特征字典（momentum, volatility, compression 等）
            regime: 市场状态结果（来自 RegimeEngine.detect()）

        Returns:
            dict: {
                "valid": bool,        # False 表示信号无效
                "score": float,       # 信号强度 (0~1+)
                "raw_score": float,   # 未经 regime boost 的原始分数
            }
        """
        if not regime.get("allow_trade", False):
            return {"valid": False, "score": 0.0, "raw_score": 0.0}

        raw_score = self._nonlinear_score(features)
        score = raw_score

        # 🔥 regime boost (only TRANSITION)
        if regime.get("regime") == "TRANSITION":
            score *= 1.25

        # 有效性阈值
        valid = score > 0.55

        return {
            "valid": valid,
            "score": float(score),
            "raw_score": float(raw_score),
        }

    def _nonlinear_score(self, f: Dict[str, float]) -> float:
        """
        非线性评分函数。

        组合子项：
            - tanh(momentum * compression): 动量与压缩的非线性交互
            - 0.4 * momentum: 动量线性贡献
            - -0.3 * volatility: 波动率惩罚
            - 0.2 * compression: 压缩线性贡献
        """
        mom = f.get("momentum", 0.0)
        vol = f.get("volatility", 0.5)
        compression = f.get("compression", 0.0)

        score = (
            float(np.tanh(mom * compression)) +
            0.4 * mom -
            0.3 * vol +
            0.2 * compression
        )

        return float(score)