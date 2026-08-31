# -*- coding: utf-8 -*-
"""测试方案四（贝叶斯收缩）+ 方案五（置信度加权）的冷启动保护逻辑

运行: python -m pytest tests/test_bayesian_shrinkage.py -v
"""
import os
import sys
import math
import tempfile
from unittest.mock import patch

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.probability_calibrator import ProbabilityEngine
from ml.decision_fusion import DecisionFusionLayer, FusionInput


# ════════════════════════════════════════════════════════
# 方案四测试：贝叶斯收缩
# ════════════════════════════════════════════════════════

class TestBayesianShrinkage:
    """验证 EV 在小样本时被收缩回 0 附近"""

    def setup_method(self):
        # 使用临时文件避免污染真实数据
        self.tmp_file = tempfile.NamedTemporaryFile(
            delete=False, suffix=".json"
        )
        self.tmp_path = self.tmp_file.name
        self.tmp_file.close()
        # 删除文件确保是全新的
        if os.path.exists(self.tmp_path):
            os.unlink(self.tmp_path)
        self.engine = ProbabilityEngine(path=self.tmp_path)

    def teardown_method(self):
        if os.path.exists(self.tmp_path):
            os.unlink(self.tmp_path)

    def test_zero_samples_ev_shrinks_to_zero(self):
        """样本=0 时，EV 应该被完全收缩回 0"""
        result = self.engine.calculate_ev(score=72.5, reward=1.8, risk=1.0)
        assert result["ev"] == 0.0, f"Expected EV=0.0, got {result['ev']}"
        assert result["shrinkage"] == 0.0
        assert result["confidence"] == 0.0

    def test_few_samples_strong_shrinkage(self):
        """样本=3 时，EV 应被强烈收缩回 0 附近"""
        # 模拟 3 次样本：全部亏损 -0.7R
        for _ in range(3):
            self.engine.update(score=72.5, profit_r=-0.7)

        result = self.engine.calculate_ev(score=72.5, reward=1.8, risk=1.0)
        raw_ev = result["raw_ev"]
        ev = result["ev"]
        shrinkage = result["shrinkage"]

        # 收缩系数约为 3/153 = 0.0196
        assert shrinkage == round(3 / (3 + 150), 4), f"shrinkage={shrinkage}"

        # EV 应该被大幅压缩但保留方向信息
        assert abs(ev) < abs(raw_ev), f"abs(ev)={abs(ev)} should be < abs(raw_ev)={abs(raw_ev)}"
        assert abs(ev) < 0.02, f"EV should be near zero, got {ev}"

    def test_medium_samples_partial_shrinkage(self):
        """样本=150 时，统计与先验各占 50%"""
        # 模拟 150 次：120 赢 +0.5R，30 输 -1.0R
        for _ in range(120):
            self.engine.update(score=72.5, profit_r=0.5)
        for _ in range(30):
            self.engine.update(score=72.5, profit_r=-1.0)

        result = self.engine.calculate_ev(score=72.5, reward=1.8, risk=1.0)
        shrinkage = result["shrinkage"]

        # 150/(150+150) = 0.5
        assert shrinkage == 0.5, f"shrinkage={shrinkage}"

    def test_many_samples_minimal_shrinkage(self):
        """样本=1000 时，统计权重应接近 87% (1000/1150)"""
        # 模拟 1000 次
        for _ in range(600):
            self.engine.update(score=72.5, profit_r=0.5)
        for _ in range(400):
            self.engine.update(score=72.5, profit_r=-1.0)

        result = self.engine.calculate_ev(score=72.5, reward=1.8, risk=1.0)
        shrinkage = result["shrinkage"]

        # 1000/(1000+150) ≈ 0.87
        assert 0.85 < shrinkage < 0.90, f"shrinkage={shrinkage}"


# ════════════════════════════════════════════════════════
# 方案五测试：置信度加权（S型平滑）
# ════════════════════════════════════════════════════════

class TestConfidenceWeightedFusion:
    """验证决策融合层在低置信度时的权重压缩"""

    def setup_method(self):
        self.fusion = DecisionFusionLayer()
        self.base_input = FusionInput(
            calib_prob=0.62,
            calib_conf=0.0,      # 默认低置信度
            ml_prob=0.55,
            ml_conf=0.8,
            ml_active=True,
            blended_ev=0.15,
            v56_score=68.0,
            direction="Long",
        )

    def test_zero_conf_ev_guard_weight_collapses(self):
        """calib_conf=0 时，calibrator+ev_guard 总权重应被大幅压缩"""
        inp = FusionInput(calib_conf=0.0)
        weights = self.fusion._dynamic_weights(inp)

        # calibrator + ev_guard 的总权重应该非常低
        calib_guard_total = weights["calibrator"] + weights["ev_guard"]
        assert calib_guard_total < 0.15, (
            f"calibrator+ev_guard total={calib_guard_total:.4f} should be < 0.15"
        )

    def test_full_conf_normal_weights(self):
        """calib_conf=1.0 时，权重应该接近默认值"""
        inp = FusionInput(calib_conf=1.0)
        weights = self.fusion._dynamic_weights(inp)

        # calibrator + ev_guard 应该保持接近默认值
        calib_guard_total = weights["calibrator"] + weights["ev_guard"]
        # 83% * 0.50 = 0.415 (因为 conf=1 → participation=1/(1+0.2)=0.83)
        assert 0.35 < calib_guard_total < 0.55, (
            f"calibrator+ev_guard total={calib_guard_total:.4f}"
        )

    def test_smooth_transition_curve(self):
        """权重应该是平滑的 S 形曲线，无阶跃"""
        weights_at_conf = []
        for conf in [0.0, 0.1, 0.2, 0.5, 1.0]:
            inp = FusionInput(calib_conf=conf)
            w = self.fusion._dynamic_weights(inp)
            calib_guard_total = w["calibrator"] + w["ev_guard"]
            weights_at_conf.append(calib_guard_total)

        # 验证单调递增且平滑（无跳变）
        for i in range(1, len(weights_at_conf)):
            assert weights_at_conf[i] > weights_at_conf[i-1], (
                f"Weight should increase monotonically: {weights_at_conf}"
            )
            diff = weights_at_conf[i] - weights_at_conf[i-1]
            assert diff < 0.3, (
                f"Difference too large (step jump): {diff} at idx {i}"
            )

    def test_fusion_steers_to_ml_when_conf_zero(self):
        """calib_conf=0 时，融合结果应主要由 ML 驱动"""
        inp = FusionInput(
            calib_prob=0.40,    # 校准概率偏低
            calib_conf=0.0,     # 但置信度为0
            ml_prob=0.70,       # ML 概率高
            ml_conf=0.8,        # ML 置信度高
            ml_active=True,
            feedback_score=0.0, # 无feedback
            blended_ev=0.20,
        )
        result = self.fusion.fuse(inp)

        # 融合结果应偏向 ML (0.70)，而非校准器 (0.40)
        assert result.fused_prob > 0.55, (
            f"fused_prob={result.fused_prob:.3f} should lean towards ML (0.70)"
        )

        # ML 的权重贡献应该大于 calibrator
        assert result.source_weights["ml_engine"] > result.source_weights["calibrator"], (
            f"ml_engine weight={result.source_weights['ml_engine']:.3f} should > calibrator={result.source_weights['calibrator']:.3f}"
        )

    def test_fusion_steers_to_calib_when_conf_high(self):
        """calib_conf=1.0 时，校准器权重释放回来"""
        inp = FusionInput(
            calib_prob=0.70,    # 校准概率高
            calib_conf=1.0,     # 置信度高
            ml_prob=0.55,       # ML 概率中等
            ml_conf=0.8,
            ml_active=True,
            feedback_score=0.0,
            blended_ev=0.20,
        )
        result = self.fusion.fuse(inp)

        # 融合结果应更接近校准器 (0.70)
        assert result.fused_prob > 0.60, (
            f"fused_prob={result.fused_prob:.3f} should lean towards calibrator (0.70)"
        )