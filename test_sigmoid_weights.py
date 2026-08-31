# -*- coding: utf-8 -*-
"""测试 Sigmoid 激活 + ML 微调方案的四增强版权重分配"""
import sys
sys.path.insert(0, '.')
from ml.decision_fusion import DecisionFusionLayer, FusionInput

fusion = DecisionFusionLayer()

print("=" * 60)
print("矩阵测试: 不同 calib_conf 下的权重分配")
print("=" * 60)

calib_confs = [0.0, 0.15, 0.3, 0.5, 0.8]

for conf in calib_confs:
    inp = FusionInput(
        calib_prob=0.5,
        calib_conf=conf,
        ml_prob=0.5,
        ml_conf=0.3,
        ml_active=True,
        guard_prob=0.5,
        guard_quality="unknown",
        feedback_score=0.0,
        direction="Long",
    )
    weights = fusion._dynamic_weights(inp)
    total = sum(weights.values())
    print(f"\ncalib_conf={conf:.2f}:")
    print(f"  calibrator = {weights['calibrator']:.4f} ({weights['calibrator']*100:.1f}%)")
    print(f"  ml_engine  = {weights['ml_engine']:.4f} ({weights['ml_engine']*100:.1f}%)")
    print(f"  ev_guard   = {weights['ev_guard']:.4f} ({weights['ev_guard']*100:.1f}%)")
    print(f"  feedback   = {weights['feedback']:.4f} ({weights['feedback']*100:.1f}%)")
    print(f"  合计: {total:.4f}")

print("\n" + "=" * 60)
print("Test 1 完整融合 (calib_conf=0.0)")
print("=" * 60)

inp = FusionInput(
    calib_prob=0.5,
    calib_conf=0.0,
    ml_prob=0.5,
    ml_conf=0.0,
    ml_active=True,
    guard_prob=0.5,
    guard_quality="unknown",
    feedback_score=0.0,
    direction="Long",
)
out = fusion.fuse(inp)
print(f"源权重: {out.source_weights}")
print(f"融合概率: {out.fused_prob:.4f}")
print(f"融合EV: {out.fused_ev:.4f}")
print(f"融合置信度: {out.fused_conf:.4f}")
print(f"使用融合概率: {out.use_fused_prob}")
print(f"详情: {out.details}")

print("\n" + "=" * 60)
print("Test: ML 不活跃 (ml_active=False, calib_conf=0.0)")
print("=" * 60)

inp2 = FusionInput(
    calib_prob=0.5,
    calib_conf=0.0,
    ml_prob=0.5,
    ml_conf=0.0,
    ml_active=False,
    guard_prob=0.5,
    guard_quality="medium",
    feedback_score=0.0,
    direction="Long",
)
weights2 = fusion._dynamic_weights(inp2)
print(f"权重: {weights2}")
out2 = fusion.fuse(inp2)
print(f"融合概率: {out2.fused_prob:.4f}")
print(f"融合EV: {out2.fused_ev:.4f}")
print(f"使用融合: {out2.use_fused_prob}")

print("\n" + "=" * 60)
print("Test: ML 活跃 + 高置信 (calib_conf=0.8, ml_conf=0.8)")
print("=" * 60)

inp3 = FusionInput(
    calib_prob=0.6,
    calib_conf=0.8,
    ml_prob=0.65,
    ml_conf=0.8,
    ml_active=True,
    guard_prob=0.55,
    guard_quality="high",
    feedback_score=70.0,
    direction="Long",
)
weights3 = fusion._dynamic_weights(inp3)
print(f"权重: {weights3}")
out3 = fusion.fuse(inp3)
print(f"融合概率: {out3.fused_prob:.4f}")
print(f"融合EV: {out3.fused_ev:.4f}")
print(f"使用融合: {out3.use_fused_prob}")