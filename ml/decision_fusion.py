# -*- coding: utf-8 -*-
"""V60.5 Decision Fusion Layer — 标准融合层

将多路决策源的输出统一融合为最终置信度:

  输入源:
    1. EVRealityGuard (ML EV Check) — {v4.5} 硬拦截 + soft penalty
    2. ProbabilityCalibrator — {v4.6} 校准评分 -> 概率
    3. ML Decision Engine (LightGBM) — {v60} 并行评估
    4. V56.5 引擎 score + Statistical EV (blended)
    5. FeedbackLoop 特征向量 — 闭环调整

融合策略:
  A. 硬拦截 (hard-gate): EVRealityGuard should_enter=False -> BLOCK
     FeedbackLoop should_reject=True -> BLOCK
  B. 加权融合 (soft-fusion):
     fused_prob = w1*calib_prob + w2*ml_prob + w3*guard_prob
     w1 + w2 + w3 = 1, 基于各源置信度动态调整
  C. 最终 EV = fused_prob * avg_win_r - (1-fused_prob) * avg_loss_r
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger("DecisionFusion")

# 默认融合权重（可被模型/校准动态调整）
_DEFAULT_WEIGHTS = {
    "calibrator": 0.35,  # ProbabilityCalibrator (rule-based -> prob)
    "ml_engine": 0.35,   # ML Decision Engine (LightGBM P(win))
    "ev_guard": 0.15,    # EVRealityGuard (ML EV check)
    "feedback": 0.15,    # FeedbackLoop 信号评估
}

# 各源对齐权重下限/上限（避免单源垄断）
_WEIGHT_LIMITS = {
    "calibrator": (0.20, 0.50),
    "ml_engine": (0.20, 0.50),
    "ev_guard": (0.05, 0.25),
    "feedback": (0.05, 0.25),
}


@dataclass
class FusionInput:
    """融合输入源数据"""
    calib_prob: float = 0.50        # ProbabilityCalibrator 校准概率
    calib_conf: float = 0.0         # 校准置信度 (0-1)
    ml_prob: float = 0.50           # ML Decision Engine P(win)
    ml_conf: float = 0.0            # ML 置信度
    ml_active: bool = False         # ML 是否活跃 (非 fallback)
    guard_prob: Optional[float] = None   # EVRealityGuard ML win prob
    guard_ml_ev: Optional[float] = None  # EVRealityGuard ML EV
    guard_quality: str = "unknown"  # "high" / "medium" / "low" / "unknown"
    guard_blocked: bool = False     # 硬拦截
    guard_penalty: float = 0.0      # soft penalty
    feedback_score: float = 0.0     # FeedbackLoop signal score
    feedback_ev: float = 0.0        # FeedbackLoop EV
    feedback_reject: bool = False   # FeedbackLoop 建议拒绝
    v56_score: float = 0.0          # V56.5 原始 score
    blended_ev: float = 0.0         # Statistical EV blend
    direction: str = "Long"         # 方向

    def __post_init__(self):
        # 校验 guard_prob 范围
        if self.guard_prob is None:
            self.guard_prob = None
        else:
            self.guard_prob = max(0.0, min(1.0, float(self.guard_prob)))
        # 校验 ml_prob
        self.ml_prob = max(0.0, min(1.0, float(self.ml_prob)))
        # 校验 calib_prob
        self.calib_prob = max(0.0, min(1.0, float(self.calib_prob)))


@dataclass
class FusionOutput:
    """融合输出"""
    fused_prob: float = 0.0          # 最终融合概率
    fused_ev: float = 0.0            # 最终融合 EV
    fused_conf: float = 0.0          # 最终置信度 (0-1)
    use_fused_prob: bool = False     # 是否使用融合概率 (True=主, False=仍用原通道)
    hard_blocked: bool = False       # 硬拦截
    block_reason: str = ""           # 拦截原因
    source_weights: Dict[str, float] = field(default_factory=dict)  # 各源权重
    source_contributions: Dict[str, float] = field(default_factory=dict)  # 各源贡献
    model_ev: float = 0.0            # model_ev (用于快照)
    p_win_calibrated: float = 0.0    # p_win_calibrated (用于快照)
    details: Dict[str, Any] = field(default_factory=dict)  # 详细调试信息


class DecisionFusionLayer:
    """V60.5 标准融合层"""

    def __init__(self):
        self._weights = dict(_DEFAULT_WEIGHTS)
        self._fusion_enabled = True

    # ════════════════════════════════════════════════════════
    # 公共接口
    # ════════════════════════════════════════════════════════

    def fuse(self, inp: FusionInput) -> FusionOutput:
        """执行标准融合

        Args:
            inp: FusionInput — 多源决策输入

        Returns:
            FusionOutput — 融合结果
        """
        out = FusionOutput()

        # ── Step 1: 硬拦截判定 ──
        # 1a. EVRealityGuard 硬拦截
        if inp.guard_blocked:
            out.hard_blocked = True
            out.block_reason = "EV_REALITY_GUARD_BLOCK"
            out.fused_prob = inp.guard_prob if inp.guard_prob is not None else 0.0
            out.fused_ev = 0.0
            out.fused_conf = 0.0
            out.source_weights = dict(self._weights)
            out.details["hard_block_source"] = "EVRealityGuard"
            logger.info(f"DecisionFusion: EVRealityGuard 硬拦截 -> BLOCK")
            return out

        # 1b. FeedbackLoop 建议拒绝
        if inp.feedback_reject:
            out.hard_blocked = True
            out.block_reason = "FEEDBACK_LOOP_REJECT"
            out.fused_prob = 0.0
            out.fused_ev = 0.0
            out.fused_conf = 0.0
            out.source_weights = dict(self._weights)
            out.details["hard_block_source"] = "FeedbackLoop"
            logger.info(f"DecisionFusion: FeedbackLoop 拒绝 -> BLOCK")
            return out

        # ── Step 2: 归一化各源概率 ──
        # guard_prob: EVRealityGuard ML win prob (None -> 用 calib_prob 替代)
        guard_prob = inp.guard_prob
        if guard_prob is None:
            # 未提供 EVRealityGuard 概率时，用校准概率兜底
            guard_prob = inp.calib_prob

        # ── Step 3: 动态权重调整 ──
        weights = self._dynamic_weights(inp)

        # ── Step 4: 融合计算 ──
        fused_prob = (
            weights["calibrator"] * inp.calib_prob +
            weights["ml_engine"] * inp.ml_prob +
            weights["ev_guard"] * guard_prob +
            weights["feedback"] * self._feedback_to_prob(inp.feedback_score)
        )

        # 如果 ML 降级 (fallback)，将 ml 的权重分给 calibrator
        if not inp.ml_active:
            ml_w = weights["ml_engine"]
            weights["ml_engine"] = ml_w * 0.5
            weights["calibrator"] += ml_w * 0.5
            # 重新计算
            fused_prob = (
                weights["calibrator"] * inp.calib_prob +
                weights["ml_engine"] * inp.ml_prob +
                weights["ev_guard"] * guard_prob +
                weights["feedback"] * self._feedback_to_prob(inp.feedback_score)
            )
            out.details["ml_fallback"] = True

        # 限制范围
        fused_prob = max(0.0, min(1.0, fused_prob))

        # ── Step 5: EV 计算 ──
        # 使用 Statistical EV blend 作为锚定 R:R，但用融合概率重新计算 EV
        # avg_win_r / avg_loss_r 从历史回测数据估算
        avg_win_r = 1.8   # 默认以 V56.5 回测 15m BTC 为准
        avg_loss_r = 1.0  # 默认 loss = 1R

        # 如果提供 blended_ev > 0，可调整 avg_win_r
        if inp.blended_ev > 0:
            # blended_ev ≈ p*win_r - (1-p)*loss_r
            # 假设 p = calib_prob 近似，解出 win_r
            p_est = max(0.3, min(0.7, inp.calib_prob))
            implied_win_r = (inp.blended_ev + (1 - p_est) * avg_loss_r) / p_est
            if 0.5 < implied_win_r < 5.0:
                avg_win_r = implied_win_r

        fused_ev = fused_prob * avg_win_r - (1 - fused_prob) * avg_loss_r

        # ── Step 6: 置信度 ──
        # 融合置信度 = 各源置信度的加权平均
        conf_sources = []
        conf_weights = []
        conf_sources.append(inp.calib_conf if inp.calib_conf > 0 else 0.3)
        conf_weights.append(weights["calibrator"])
        conf_sources.append(inp.ml_conf if inp.ml_conf > 0 else 0.3)
        conf_weights.append(weights["ml_engine"])
        guard_conf = 0.8 if inp.guard_quality == "high" else (0.5 if inp.guard_quality == "medium" else 0.3)
        conf_sources.append(guard_conf)
        conf_weights.append(weights["ev_guard"])
        feedback_conf = min(1.0, abs(inp.feedback_score - 35) / 65) if inp.feedback_score > 0 else 0.1
        conf_sources.append(max(0.1, feedback_conf))
        conf_weights.append(weights["feedback"])

        total_conf_w = sum(conf_weights)
        fused_conf = sum(c * w for c, w in zip(conf_sources, conf_weights)) / total_conf_w if total_conf_w > 0 else 0.3
        fused_conf = max(0.0, min(1.0, fused_conf))

        # ── Step 7: 决定是否使用融合概率 ──
        # 当各源差距较大 (>0.15) 时有融合价值；否则直接用校准概率
        max_diff = max(
            abs(inp.calib_prob - inp.ml_prob),
            abs(inp.calib_prob - guard_prob),
            abs(inp.ml_prob - guard_prob),
        )
        use_fused = max_diff >= 0.05 or fused_conf < 0.9

        # ── Step 8: 填充输出 ──
        out.fused_prob = fused_prob
        out.fused_ev = fused_ev
        out.fused_conf = fused_conf
        out.use_fused_prob = use_fused
        out.source_weights = weights
        out.source_contributions = {
            "calibrator": round(weights["calibrator"] * inp.calib_prob, 4),
            "ml_engine": round(weights["ml_engine"] * inp.ml_prob, 4),
            "ev_guard": round(weights["ev_guard"] * guard_prob, 4),
            "feedback": round(weights["feedback"] * self._feedback_to_prob(inp.feedback_score), 4),
        }
        # 兼容快照字段
        out.model_ev = fused_ev
        out.p_win_calibrated = fused_prob
        out.details.update({
            "max_diff": round(max_diff, 4),
            "guard_prob_used": round(guard_prob, 4),
            "avg_win_r": round(avg_win_r, 4),
            "avg_loss_r": round(avg_loss_r, 4),
        })

        logger.debug(
            f"DecisionFusion: fused_prob={fused_prob:.3f} calib={inp.calib_prob:.3f} "
            f"ml={inp.ml_prob:.3f} guard={guard_prob:.3f} fb={self._feedback_to_prob(inp.feedback_score):.3f} "
            f"w={weights} conf={fused_conf:.3f} ev={fused_ev:.4f} use_fused={use_fused}"
        )

        return out

    def get_weights(self) -> Dict[str, float]:
        """返回当前融合权重 (调试用)"""
        return dict(self._weights)

    def update_weights(self, new_weights: Dict[str, float]):
        """更新融合权重"""
        for k, v in new_weights.items():
            if k in self._weights:
                lo, hi = _WEIGHT_LIMITS.get(k, (0.0, 1.0))
                self._weights[k] = max(lo, min(hi, float(v)))
        logger.info(f"DecisionFusion 权重更新: {self._weights}")

    # ════════════════════════════════════════════════════════
    # 内部方法
    # ════════════════════════════════════════════════════════

    def _dynamic_weights(self, inp: FusionInput) -> Dict[str, float]:
        """动态调整权重

        规则:
          - ML 降级时降低 ml_engine 权重
          - EVRealityGuard quality=low 时降低 guard 权重
          - FeedbackLoop confidence 高时提升 feedback 权重
        """
        weights = dict(self._weights)

        # ML fallback: 降低 ML 权重
        if not inp.ml_active:
            weights["ml_engine"] *= 0.5
            weights["calibrator"] += self._weights["ml_engine"] * 0.5

        # EVRealityGuard quality 低时调整
        if inp.guard_quality == "low":
            weights["ev_guard"] *= 0.5
            # 将权重转移到 calibrator
            weights["calibrator"] += self._weights["ev_guard"] * 0.5

        # FeedbackLoop confidence 高时提升 (通过 feedback_score)
        if inp.feedback_score > 60:
            weights["feedback"] = min(
                _WEIGHT_LIMITS["feedback"][1],
                weights["feedback"] * 1.3
            )

        # 归一化确保权重和 = 1
        total = sum(weights.values())
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}

        return weights

    def _feedback_to_prob(self, feedback_score: float) -> float:
        """将 FeedbackLoop score (0-100) 映射为概率 (0-1)

        score=35 -> 0.5 (中性)
        score=70 -> 0.7 (高置信)
        score=0  -> 0.3 (极低)
        """
        if feedback_score <= 0:
            return 0.30
        # 线性映射: 0~100 -> 0.30~0.80
        prob = 0.30 + (feedback_score / 100.0) * 0.50
        return max(0.0, min(1.0, prob))


# 全局单例
_fusion_layer: Optional[DecisionFusionLayer] = None


def get_decision_fusion() -> DecisionFusionLayer:
    """获取融合层单例"""
    global _fusion_layer
    if _fusion_layer is None:
        _fusion_layer = DecisionFusionLayer()
    return _fusion_layer