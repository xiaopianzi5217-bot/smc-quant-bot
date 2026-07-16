# -*- coding: utf-8 -*-
"""ML Decision Engine — 替代人工权重/阈值的决策管线。

原理：
  1. FeaturePipeline 提取实时特征向量
  2. ProbabilityEngine 推理 → P(win)
  3. EV = P(win) * avg_win_r - (1-P(win)) * avg_loss_r
  4. Decision 基于 EV + confidence

对比：
  旧：smc_quality*0.3 + momentum*0.2 + structure*0.2 + regime_adjust = score
  新：Features → LightGBM → P(win) → EV = 清理权重版

Fallback：
  模型未训练时，降级到人工权重（不改变现有行为）
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from ml.feature_pipeline import get_feature_pipeline
from ml.probability_engine import get_probability_engine

logger = logging.getLogger("MLDecisionEngine")

# 人工权重降级参数（仅在模型未训练时使用）
_FALLBACK_WEIGHTS = {
    "smc_quality": 0.25,
    "fvg_strength": 0.10,
    "ob_strength": 0.15,
    "volume_ratio": 0.05,
    "atr_pct": 0.05,
    "regime": 0.10,
    "sqzmom_state": 0.10,
    "adx": 0.10,
    "rsi": 0.05,
    "entry_hour": 0.05,
}


class MLDecisionEngine:
    """ML 决策引擎 — 两条管线并行。

    主管线（模型已训练）：
      Features → ML → P(win) → EV → Decision

    降级管线（模型未训练）：
      Features → 人工权重 → Score → EV → Decision
    """

    def __init__(self):
        self._feature_pipeline = get_feature_pipeline()
        self._probability_engine = get_probability_engine()
        self._fallback_active = not self._probability_engine.is_ready()

        if self._fallback_active:
            logger.info("ML 决策引擎: 降级到人工权重模式")
        else:
            logger.info("ML 决策引擎: 主管线已就绪")

    # ════════════════════════════════════════════════════════════
    # 公共方法
    # ════════════════════════════════════════════════════════════

    def evaluate(self, exec_ctx: dict, curr_row: dict,
                 regime: str, features_dict: dict,
                 direction: str) -> Tuple[float, float, float, bool]:
        """评估交易机会。

        Args:
            exec_ctx:     build_exec_context 输出
            curr_row:     df_exec.iloc[-1] 的字典
            regime:       "TREND" / "CHOP" / "TRANSITION"
            features_dict: scan_and_decide 中的 _features 字典
            direction:    "Long" / "Short"

        Returns:
            (score, ev, confidence, is_ml_based)
            score:          兼容旧格式的评分 (0-100)
            ev:             Expected Value
            confidence:     置信度 (0-1)
            is_ml_based:    True=ML, False=Fallback
        """
        if self._fallback_active:
            return self._fallback_evaluate(
                exec_ctx, curr_row, regime, features_dict, direction
            )

        try:
            return self._ml_evaluate(
                exec_ctx, curr_row, regime, features_dict, direction
            )
        except Exception as e:
            logger.error(f"ML 评估失败，降级到人工权重: {e}")
            self._fallback_active = True
            return self._fallback_evaluate(
                exec_ctx, curr_row, regime, features_dict, direction
            )

    def dynamic_threshold(self, regime: str, vol_state: str,
                          hour: int) -> Tuple[float, float]:
        """动态阈值计算（替代硬编码的 MIN_SCORE/EV）。

        Args:
            regime:     "TREND" / "CHOP" / "TRANSITION"
            vol_state:  "high_vol" / "normal" / "low_vol"
            hour:       当前小时 (0-23)

        Returns:
            (min_prob, min_ev)
        """
        if self._fallback_active:
            return self._fallback_threshold(regime, vol_state)

        return self._ml_threshold(regime, vol_state, hour)

    def retrain_if_needed(self, force: bool = False) -> bool:
        """检查是否有新数据，尝试重训模型。

        Args:
            force: 是否强制重训

        Returns:
            True 训练成功
        """
        df = self._feature_pipeline.build_training_set(min_samples=30)
        if df is None:
            logger.info("无新训练数据")
            return False

        if force or len(df) > self._probability_engine.get_train_info().get("train_count", 0) * 1.2:
            success = self._probability_engine.train(df, force=True)
            if success:
                self._fallback_active = False
            return success

        return False

    def is_ml_active(self) -> bool:
        return not self._fallback_active

    # ════════════════════════════════════════════════════════════
    # ML 主管线
    # ════════════════════════════════════════════════════════════

    def _ml_evaluate(self, exec_ctx, curr, regime, feats, direction):
        """ML 管线评估。"""
        # 1. 提取特征
        live_df = self._feature_pipeline.get_live_features(
            exec_ctx, curr, regime, feats
        )

        # 2. ML 推理 P(win)
        prob = self._probability_engine.predict(live_df)[0]

        # 3. 方向调整
        if direction == "Short":
            prob = 1.0 - prob  # ML 预测多头概率，空头反转

        # 4. EV
        should_trade, confidence = self._probability_engine.get_decision(prob)
        ev = self._probability_engine.expected_value(prob)

        # 5. 兼容 score（概率 → 0-100 分）
        score = prob * 100

        logger.debug(f"ML 评估: direction={direction} prob={prob:.3f} ev={ev:.4f} "
                     f"confidence={confidence:.3f}")
        return score, ev, confidence, True

    # ════════════════════════════════════════════════════════════
    # Fallback 人工权重
    # ════════════════════════════════════════════════════════════

    def _fallback_evaluate(self, exec_ctx, curr, regime, feats, direction):
        """人工权重降级评估。"""
        # 提取特征数值
        _exec_lq = float(exec_ctx.get("long_quality", 0))
        _exec_sq = float(exec_ctx.get("short_quality", 0))

        smc_quality = float(feats.get("structure_break", False))
        fvg = float(bool(exec_ctx.get("bullish_fvg") or exec_ctx.get("bearish_fvg")))
        ob = float(bool(exec_ctx.get("bullish_ob") or exec_ctx.get("bearish_ob")))
        vol_ratio = float(curr.get("volume_ratio", 1)) if hasattr(curr, 'get') else 1.0
        atr_pct = (
            float(curr.get("ATRr_14", exec_ctx.get("atr", 0))) / max(float(curr.get("close", 1)), 1e-8)
            if hasattr(curr, 'get') else 0.02
        )
        adx_val = float(exec_ctx.get("adx", curr.get("adx", 0))) if hasattr(curr, 'get') else 0
        sqz_state = 1.0 if "squeeze" in str(exec_ctx.get("squeeze", "")).lower() else 0.0

        # 人工评分
        score = (
            smc_quality * 25 +
            fvg * 10 +
            ob * 15 +
            min(vol_ratio, 2.0) * 5 +
            min(atr_pct * 100, 5) * 5 +
            (1 if adx_val > 25 else 0.5 if adx_val > 18 else 0) * 10 +
            sqz_state * 10 +
            (15 if regime in ("TREND", "trend") else 10)
        )
        score = min(100, score)

        if _exec_lq > 0 or _exec_sq > 0:
            score = max(_exec_lq, _exec_sq) if direction == "Long" else max(_exec_sq, _exec_lq)

        ev = (score / 100) * 0.06 - 0.02
        confidence = (score - 35) / 65 if score > 35 else 0.1

        return score, ev, min(confidence, 1.0), False

    def _fallback_threshold(self, regime: str, vol_state: str) -> Tuple[float, float]:
        """人工阈值降级。"""
        _r = regime.upper() if regime else "UNKNOWN"
        _v = vol_state.lower() if vol_state else "normal"

        if "chop" in _r or "range" in _r:
            return 0.50, 0.02
        if "crisis" in _r or "risk" in _r:
            return 0.60, 0.04
        if "high" in _v:
            return 0.55, 0.03
        return 0.45, 0.01

    def _ml_threshold(self, regime: str, vol_state: str,
                      hour: int) -> Tuple[float, float]:
        """ML 动态阈值。"""
        # 模型本身的概率校准已经考虑 regime/vol，这里仅设最低安全线
        _v = vol_state.lower() if vol_state else "normal"
        _r = regime.upper() if regime else "UNKNOWN"

        # 低流动时段（亚洲盘）提阈值
        _night = 0.55 if hour in range(0, 7) else 0.50
        _high_vol = 0.55 if "high" in _v else 0.50
        _choppy = 0.55 if "chop" in _r or "range" in _r else 0.48

        return max(_night, _high_vol, _choppy), 0.0


# 全局单例
_decision_engine: Optional[MLDecisionEngine] = None


def get_ml_decision() -> MLDecisionEngine:
    global _decision_engine
    if _decision_engine is None:
        _decision_engine = MLDecisionEngine()
    return _decision_engine
