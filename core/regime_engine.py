"""
Regime Engine — V53 核心1：硬门控

根据特征判定当前市场状态，并决定是否允许交易。
CHOP ❌  TRANSITION ✔
"""
from typing import Dict, Optional


class RegimeEngine:
    """
    市场状态引擎（硬门控）

    通过 _predict() 判断当前 regime，
    只有 TRANSITION + 高置信度时才允许交易。
    """

    def __init__(self):
        self.min_confidence = 0.75

    def detect(self, features: Dict[str, float]) -> Dict:
        """
        检测市场状态。

        Args:
            features: 特征字典 (含 trend_strength, volatility 等)

        Returns:
            dict: {
                "regime": str,         # "TRANSITION" | "CHOP" | "NEUTRAL"
                "confidence": float,   # 置信度 0~1
                "allow_trade": bool,   # True 仅当 conf >= min_confidence 且 regime == TRANSITION
            }
        """
        regime, conf = self._predict(features)

        # 硬门控：只有 TRANSITION + 高置信度才允许
        allow_trade = (
            conf >= self.min_confidence and
            regime == "TRANSITION"
        )

        return {
            "regime": regime,
            "confidence": conf,
            "allow_trade": allow_trade,
        }

    def _predict(self, f: Dict[str, float]) -> tuple:
        """
        内部预测逻辑。

        score = trend_strength - volatility * 0.5
          > 0.6  → TRANSITION (高置信度)
          < 0.2  → CHOP (高置信度)
          其他   → NEUTRAL (中等置信度)
        """
        trend = f.get("trend_strength", 0.0)
        vol = f.get("volatility", 0.5)

        score = trend - vol * 0.5

        if score > 0.6:
            return "TRANSITION", 0.8
        elif score < 0.2:
            return "CHOP", 0.9
        else:
            return "NEUTRAL", 0.5