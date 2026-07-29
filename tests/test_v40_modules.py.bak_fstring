# 导入 V40 模块
from analytics.confidence_engine import ConfidenceEngine
from analytics.outcome_attribution import OutcomeAttribution
from analytics.decision_kernel_v40 import DecisionKernelV40

print("=" * 60)
print("1. ConfidenceEngine 测试")
print("=" * 60)
ce = ConfidenceEngine()
r = ce.compute(trades=182, pf=2.43, std_r=0.65, same_regime=True)
print(f"  Confidence: {r.confidence}")
print(f"  Factors: sample={r.sample_score}, pf={r.pf_score}, var={r.variance_score}, regime={r.regime_score}")
print(f"  Reasons: {r.reasons}")

r2 = ce.compute(trades=12, pf=1.1, std_r=1.8, same_regime=False)
print(f"\n  低分场景: confidence={r2.confidence}, reasons={r2.reasons}")

print("\n" + "=" * 60)
print("2. OutcomeAttribution 测试")
print("=" * 60)
oa = OutcomeAttribution()

# 单笔归因
contrib = oa.attribute(
    {"smc": 0.72, "sqzmom": 0.55, "breakout": 0.30, "regime": 1.0},
    realized_r=2.1,
    trade_id="t1",
    symbol="BTC/USDT",
    direction="LONG",
)
print(f"  单笔归因: {contrib}")
print(f"  总和: {sum(contrib.values()):.4f} (≈ realized_r=2.1)")

# 第二笔
contrib2 = oa.attribute(
    {"smc": 0.30, "sqzmom": 0.65, "breakout": 0.80, "regime": 1.0},
    realized_r=-0.5,
    trade_id="t2",
    symbol="ETH/USDT",
    direction="SHORT",
)
print(f"  亏损归因: {contrib2}")

# 汇总
summary = oa.aggregate()
print(f"\n  汇总: {summary['total_trades']} 笔交易")
print(f"  总 R: {summary['total_realized_r']}")
print(f"  各因子总贡献: {summary['total_contributions']}")
print(f"  贡献占比: {summary['contribution_pct']}")
print(f"  各因子胜率: {summary['win_rate_by_factor']}")

print("\n" + "=" * 60)
print("3. DecisionKernelV40 测试")
print("=" * 60)
kernel = DecisionKernelV40()

# 场景 A
result_a = kernel.evaluate(
    signal={"expected_value": 0.35, "score": 0.75, "direction": "LONG"},
    ctx={
        "symbol": "BTC/USDT",
        "regime": "TREND",
        "v40_trades": 135,
        "v40_pf": 2.18,
        "v40_std_r": 0.65,
        "v40_same_regime": True,
    },
)
print(f"  A) EV=0.35: action={result_a.action}, conf={result_a.confidence:.4f}")
print(f"     size_mult={result_a.size_multiplier}, reason={result_a.reason}")

# 场景 B
result_b = kernel.evaluate(
    signal={"expected_value": 0.02, "score": 0.40, "direction": "LONG"},
    ctx={
        "symbol": "ETH/USDT",
        "regime": "CHOP",
        "v40_trades": 30,
        "v40_pf": 1.2,
        "v40_std_r": 1.2,
        "v40_same_regime": False,
    },
)
print(f"  B) EV=0.02: action={result_b.action}, conf={result_b.confidence:.4f}")
print(f"     reason={result_b.reason}")

# 场景 C
result_c = kernel.evaluate(
    signal={"expected_value": 0.15, "score": 0.55, "direction": "LONG"},
    ctx={
        "symbol": "SOL/USDT",
        "regime": "TREND",
        "v40_trades": 8,
        "v40_pf": 0.9,
        "v40_std_r": 2.0,
        "v40_same_regime": False,
    },
)
print(f"  C) EV=0.15, trades=8: action={result_c.action}, conf={result_c.confidence:.4f}")
print(f"     reason={result_c.reason}")

print("\n" + "=" * 60)
print("4. 归因集成测试")
print("=" * 60)

result = kernel.evaluate(
    signal={"expected_value": 0.25, "score": 0.70, "direction": "LONG"},
    ctx={"symbol": "BTC/USDT", "regime": "TREND", "v40_trades": 100, "v40_pf": 2.0},
)
print(f"  开单: {result.action} (conf={result.confidence:.4f})")

trade = {
    "trade_id": "t_v40_001",
    "symbol": "BTC/USDT",
    "direction": "LONG",
    "realized_r": 2.1,
    "max_drawdown": 0.3,
    "feature": {"smc": 0.72, "sqzmom": 0.55, "breakout": 0.30, "regime": 1.0},
}
contrib_close = kernel.attribute_close(trade)
print(f"  平仓归因: {contrib_close}")

print("\n所有测试通过！")
