# -*- coding: utf-8 -*-
"""
Statistical EV Gate — 优化1: 动态统计阈值代替固定 EV 规则

当前问题:
  MIN_MODEL_EV = -0.28 太宽，很多历史没有优势的信号也能进入。

解决方案:
  根据 regime / confidence / volatility 动态计算 EV 阈值，
  强环境放松、弱环境收紧，替代原来的固定硬地板。

用法:
  from strategy.statistical_ev_gate import StatisticalEVGate
  gate = StatisticalEVGate()
  if gate.allow(model_ev=0.03, regime="TREND", confidence=0.7, volatility=0.02):
      # 通过
  else:
      # 拒绝
"""

from __future__ import annotations

from typing import Dict, Any, Optional


class StatisticalEVGate:
    """动态 EV 阈值门

    根据市场状态动态调整 EV 最低要求：
    - 高波动 → 提高门槛（噪音多，需要更高 EV 补偿）
    - 低置信度 → 提高门槛（样本不足，需要更高 EV 补偿）
    - 强趋势 → 略微降低门槛（趋势延续性提供天然保护）
    """

    def __init__(self, base_ev: float = 0.05):
        """
        参数:
            base_ev: 基础 EV 阈值（默认 0.05）
        """
        self.base_ev = base_ev

    def dynamic_ev_threshold(
        self,
        regime: str = "UNKNOWN",
        confidence: float = 0.5,
        volatility: float = 0.02,
    ) -> float:
        """计算动态 EV 阈值

        参数:
            regime: 市场状态 (TREND / CHOP / TRANSITION / UNKNOWN)
            confidence: 信号置信度 0~1
            volatility: 波动率（如 ATR/价格 百分比）

        返回:
            动态 EV 阈值
        """
        threshold = self.base_ev

        # ---- 波动调整 ----
        # 高波动 → 噪音多，需要更高的 EV 补偿
        if volatility > 0.05:
            threshold += 0.08
        elif volatility > 0.03:
            threshold += 0.05
        elif volatility < 0.01:
            threshold -= 0.02  # 低波动环境，略放松

        # ---- 置信度调整 ----
        # 置信度低 → 历史数据不可靠，需要更高 EV 补偿
        if confidence < 0.3:
            threshold += 0.08
        elif confidence < 0.5:
            threshold += 0.05
        elif confidence < 0.6:
            threshold += 0.03
        elif confidence > 0.85:
            threshold -= 0.02  # 高置信度，略放松

        # ---- 市场状态调整 ----
        regime_upper = str(regime).upper().strip()
        if regime_upper == "TREND":
            threshold -= 0.02  # 趋势延续提供天然保护
        elif regime_upper == "CHOP":
            threshold += 0.03  # 震荡噪音多，需要更高 EV
        elif regime_upper == "TRANSITION":
            threshold += 0.01  # 过渡期中等噪音

        # 保底：不低于 -0.10（防止极端负值）
        return max(-0.10, round(threshold, 4))

    def allow(
        self,
        model_ev: float,
        regime: str = "UNKNOWN",
        confidence: float = 0.5,
        volatility: float = 0.02,
    ) -> bool:
        """判断 EV 是否通过动态阈值

        参数:
            model_ev: 模型预测的 expected value
            regime: 市场状态
            confidence: 信号置信度
            volatility: 波动率

        返回:
            True = 通过，False = 拒绝
        """
        threshold = self.dynamic_ev_threshold(regime, confidence, volatility)
        passed = model_ev >= threshold
        return passed

    def get_threshold_info(
        self,
        model_ev: float,
        regime: str = "UNKNOWN",
        confidence: float = 0.5,
        volatility: float = 0.02,
    ) -> Dict[str, Any]:
        """返回详细的阈值计算信息，用于调试和日志"""
        threshold = self.dynamic_ev_threshold(regime, confidence, volatility)
        passed = model_ev >= threshold
        return {
            "model_ev": round(model_ev, 4),
            "threshold": threshold,
            "base_ev": self.base_ev,
            "regime": regime,
            "confidence": round(confidence, 4),
            "volatility": round(volatility, 4),
            "passed": passed,
            "gap": round(model_ev - threshold, 4),
        }


# 全局单例
_statistical_ev_gate = StatisticalEVGate()


def get_statistical_ev_gate() -> StatisticalEVGate:
    return _statistical_ev_gate
