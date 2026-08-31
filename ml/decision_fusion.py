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
import math
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

# ════════════════════════════════════════════════════════════
# 方案四增强版：Sigmoid 激活参数
# ════════════════════════════════════════════════════════════
# Sigmoid 形式: act(x) = 1 / (1 + exp(-k * (x - x0)))
#   - x=0 时: act ≈ 1/(1+exp(k*x0))  → 极小值（趋近 0）
#   - x≥x0 时: act 快速爬升
#   - x→1 时: act ≈ 1（饱和）
# floor: 置信度为 0 时保留的最小激活值（防完全失效，但极小）
_SIGMOID_ACT_PARAMS = {
    # calibrator: 平滑参与度上限 0.62，置信度门槛 0.35
    #   - conf=0 → floor=0.01 (接近零)
    #   - conf=1 → cap=0.62 (最大参与度)
    "calibrator": {"k": 8.0, "x0": 0.35, "floor": 0.01, "cap": 0.62},
    # ml_engine: 高 floor 保证 ML 即使不活跃也有基础权重
    "ml_engine":   {"k": 6.0, "x0": 0.25, "floor": 0.40, "cap": 1.0},
    # ev_guard: quality 通过 sigmoid 门槛后激活
    "ev_guard":    {"k": 10.0, "x0": 0.30, "floor": 0.20, "cap": 1.0},
    # feedback: score=0 时保留 30% 基础参与
    "feedback":    {"k": 8.0, "x0": 0.25, "floor": 0.30, "cap": 1.0},
}

# ML 微调参数（方案四增强版）
_ML_TUNING = {
    "ml_inactive_discount": 0.3,       # ML 不活跃时激活因子×0.3
    "ml_level_scale": 0.3,             # ml_active=False 时 ml_level 额外缩放
    "guard_unknown_scale": 0.2,        # guard_quality="unknown/low" 时权重缩放
    "guard_medium_max_w": 0.35,        # guard="medium" 时权重上限（归一化前）
    "guard_unknown_max_w": 0.15,       # guard="unknown" 时权重上限（归一化前）
    "guard_global_max_w": 0.30,        # guard 全局最大权重（归一化前）
    "calib_ml_feed_ratio": 0.0,        # ML 释放权重流给 calibrator 的比例 (0=防止calib垄断)
    "fb_ml_feed_ratio": 1.0,           # ML 释放权重流给 feedback 的比例 (1=全部给feedback)
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
        """基于置信度的动态权重调整（方案四增强版：Sigmoid 激活 + ML 微调）

        核心思想：各来源的权重 = 基础权重 × Sigmoid 置信度激活因子
          - 置信度 = 0 时，激活值据逼近 0（受 floor 保护，极小）
            校准器权重被压缩至 <3%（冷启动安全模式）
          - 置信度 ∈ (0.3~0.5) 门槛后，激活值快速爬升并趋于饱和
          - ML 不活跃时，其权重按比例流给 calibrator / feedback

        流程:
          1. Sigmoid 激活: act(x) = 1 / (1 + exp(-k*(x - x0)))
          2. 计算各源 raw_weight = base_weight * act
          3. ML 微调: ml_active=False → ml_level *= 0.3, 释放权重按 7:3 分给 calib/fb
          4. Guard 质量微调: quality=unknown → guard_w *= 0.2
          5. 归一化确保和 = 1
        """
        # 从基础权重开始（保证 update_weights 的调整仍生效）
        base = dict(self._weights)

        # ── 1. Sigmoid 置信度激活因子 ──
        def _sigmoid_act(conf: float, params: Dict[str, float]) -> float:
            """Sigmoid 激活: conf=0 → floor, conf=1 → cap (默认 1.0)"""
            k = params["k"]
            x0 = params["x0"]
            floor = params["floor"]
            cap = params.get("cap", 1.0)  # 最大参与度上限
            # 标准 Sigmoid
            sig = 1.0 / (1.0 + math.exp(-k * (float(conf) - x0)))
            # 归一化: 确保 conf=0 时 = floor, conf=1 时 ≈ cap
            sig_0 = 1.0 / (1.0 + math.exp(k * x0))
            sig_1 = 1.0 / (1.0 + math.exp(-k * (1.0 - x0)))
            # 重映射到 [floor, cap]
            if sig_1 - sig_0 <= 0:
                return float(floor)
            act = floor + (cap - floor) * (sig - sig_0) / (sig_1 - sig_0)
            return max(floor, min(cap, act))

        # calibrator 激活: 基于 calib_conf
        calib_act = _sigmoid_act(inp.calib_conf, _SIGMOID_ACT_PARAMS["calibrator"])

        # ml_engine 激活: 基于 ml_conf（不在激活阶段乘 ml_inactive_discount）
        #       真正折扣在 Step 3 统一处理，避免双重折扣
        ml_act = _sigmoid_act(inp.ml_conf, _SIGMOID_ACT_PARAMS["ml_engine"])

        # ev_guard 激活: 基于 guard_quality
        guard_quality_map = {
            "high": 1.0,
            "medium": 0.65,
            "low": 0.2,
            "unknown": 0.1,
        }
        guard_qual_act = guard_quality_map.get(str(inp.guard_quality).strip().lower(), 0.2)
        guard_act = _sigmoid_act(guard_qual_act, _SIGMOID_ACT_PARAMS["ev_guard"])

        # feedback 激活: 基于 feedback_score (0-100 → 0-1 归一化)
        fb_score_norm = max(0.0, min(1.0, inp.feedback_score / 100.0))
        fb_act = _sigmoid_act(fb_score_norm, _SIGMOID_ACT_PARAMS["feedback"])

        # ── 2. 计算各源原始权重 (base_weight × activation) ──
        calib_w = base["calibrator"] * calib_act
        ml_w_full = base["ml_engine"] * ml_act  # ML 全量激活权重（未打折）
        guard_w = base["ev_guard"] * guard_act
        fb_w = base["feedback"] * fb_act

        # ── 3. ML 微调: ML 不活跃时，只保留 ml_inactive_discount 比例的权重
        #    其余 (1-ratio) 释放。释放策略受 calibrator 置信度制约：
        #    若 calib_conf 低于激活门槛(x0=0.30)，ML 释放权重全部给 feedback
        #    若 calib_conf 已过门槛，按 7:3 分给 calibrator/feedback ──
        if not inp.ml_active:
            ml_w = ml_w_full * _ML_TUNING["ml_inactive_discount"]      # ML 保留 30%
            ml_release = ml_w_full * (1.0 - _ML_TUNING["ml_inactive_discount"])  # 释放 70%
            # 只有当 calibrator 置信度已超过激活门槛时，才接受 ML 释放的权重
            calib_threshold = _SIGMOID_ACT_PARAMS["calibrator"]["x0"]  # 0.30
            if inp.calib_conf >= calib_threshold:
                calib_w += ml_release * _ML_TUNING["calib_ml_feed_ratio"]
                fb_w += ml_release * _ML_TUNING["fb_ml_feed_ratio"]
            else:
                # calibrator 置信度过低，ML 释放权重全部给 feedback
                fb_w += ml_release
        else:
            ml_w = ml_w_full

        # ── 4. Guard 质量微调: 限制低质量 guard 的最大权重 ──
        guard_quality_str = str(inp.guard_quality).strip().lower()
        if guard_quality_str in ("unknown", "low"):
            guard_w *= _ML_TUNING["guard_unknown_scale"]
            guard_w = min(guard_w, _ML_TUNING["guard_unknown_max_w"])
        elif guard_quality_str == "medium":
            guard_w = min(guard_w, _ML_TUNING["guard_medium_max_w"])
        # Guard 全局上限（归一化前）
        guard_w = min(guard_w, _ML_TUNING["guard_global_max_w"])

        # ── 4b. 校准器满置信时 ML 让出权重（置信度导向） ──
        # 当 calibrator 置信度极高 (≥0.7) 且 ML 活跃时，
        # ML 主动释放一部分权重给 calibrator，确保满置信校准器主导决策。
        # 0.7 → 0% 让出, 1.0 → 35% 让出（线性插值）
        if inp.ml_active and inp.calib_conf >= 0.7:
            calib_steer_ratio = min(0.35, (inp.calib_conf - 0.7) / 0.3 * 0.35)
            steer_amount = ml_w * calib_steer_ratio
            calib_w += steer_amount
            ml_w -= steer_amount
            logger.debug(
                f"DecisionFusion: calib_conf={inp.calib_conf:.2f} >= 0.7, "
                f"ML 让出权重 steer_ratio={calib_steer_ratio:.3f}, amount={steer_amount:.4f}"
            )

        # ── 5. 归一化确保和 = 1 ──
        weights = {
            "calibrator": calib_w,
            "ml_engine": ml_w,
            "ev_guard": guard_w,
            "feedback": fb_w,
        }
        total = sum(weights.values())
        if total <= 0:
            return dict(self._weights)
        weights = {k: v / total for k, v in weights.items()}

        # ── 6. 归一化后 Guard 占比钳制（防止低置信场景下 guard 独裁） ──
        # 即使归一化前已限制，当其他源置信度极低时 guard 仍可能占主导。
        # 此处在归一化后再次兜底，将 guard 超过阈值的部分按比例分给其他源。
        guard_quality_str = str(inp.guard_quality).strip().lower()
        guard_max_ratio = 0.70  # guard 最大允许占比（高置信 quality 时）
        if guard_quality_str in ("unknown", "low"):
            guard_max_ratio = 0.55
        elif guard_quality_str == "medium":
            guard_max_ratio = 0.60

        if weights["ev_guard"] > guard_max_ratio:
            excess = weights["ev_guard"] - guard_max_ratio
            weights["ev_guard"] = guard_max_ratio
            # 将 excess 按比例重分配给其他源
            others_sum = sum(v for k, v in weights.items() if k != "ev_guard")
            if others_sum > 0:
                for k in weights:
                    if k != "ev_guard":
                        weights[k] += excess * weights[k] / others_sum
            else:
                # 极端情况：其他源全为 0，均匀分配
                for k in weights:
                    if k != "ev_guard":
                        weights[k] = excess / 3.0

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