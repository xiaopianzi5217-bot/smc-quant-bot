"""对比新 EV 公式 vs 旧公式"""
import sys; sys.path.insert(0,'.')
import json
from pathlib import Path

# 加载当前学习数据
from strategy.intelligence_engine import ev_learner

print("=== 当前 EVLearner 分桶 ===")
for k, v in sorted(ev_learner.buckets.items()):
    wr = v['wins']/v['total']*100 if v['total']>0 else 0
    print(f'  {k}: {v["total"]} 笔, {wr:.1f}%')

# 模拟新公式
def new_ev(win_prob, rr, regime, score=50, n=0):
    import math
    regime = regime.upper()
    # 置信度
    conf = min(1.0, n / (n + 15.0))
    conf *= 1.0 / (1.0 + abs(win_prob - 0.5) * 1.5 + 0.0001)
    if regime == 'TREND':
        conf *= 0.75
    else:
        conf *= 0.55
    conf *= max(0.6, min(1.0, (score - 50) / 50))
    
    # win_prob 校准
    base = 0.42 if regime == 'TREND' else 0.38 if regime in ('MIXED', 'TRANSITION') else 0.35
    wp = 0.88 * max(0.08, min(0.78, win_prob)) + 0.12 * base
    
    # 惩罚
    rp = {'TREND': -0.008, 'MIXED': -0.02, 'CHOP': -0.045}.get(regime, -0.025)
    sb = 0.028 if score >= 88 else (0.012 if score >= 75 else 0)
    
    ev = (wp * rr) - (1 - wp) + rp + sb
    ev *= (0.6 + 0.4 * conf)
    return max(-0.15, round(ev, 4))

# 对比：旧公式
from strategy.intelligence_engine import estimate_expected_value

print("\n=== 对比测试 ===")
test_cases = [
    ('TREND', 'V37_CORE', 0.57, 2.0, 53, 309),
    ('TREND', 'V37_TACTICAL', 0.82, 2.0, 53, 33),
    ('TRANSITION', 'V37_CORE', 0.49, 2.0, 30, 79),
    ('CHOP', 'V37_CORE', 0.48, 2.0, 25, 56),
    ('MUD', 'V37_CORE', 0.39, 2.0, 20, 23),
]

for regime, setup, wp, rr, score, n in test_cases:
    key = f'{regime}|{setup}'
    # 旧公式
    old = estimate_expected_value(
        {'score_raw': score, 'score': score, 'smc': 0, 'direction': 'Long', 'estimated_rr': rr},
        regime, 'NORMAL_VOL',
        {'score': score, 'regime': regime}
    )['expected_value']
    # 新公式
    new = new_ev(wp, rr, regime, score, n)
    
    conf = min(1.0, n / (n + 15.0))
    print(f'{key:>30s}  old={old:.4f}  new={new:.4f}  Δ={new-old:+.4f}  n={n}  conf={conf:.2f}')
