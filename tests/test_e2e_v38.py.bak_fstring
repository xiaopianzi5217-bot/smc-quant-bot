# -*- coding: utf-8 -*-
"""
P5: V38.5 端到端集成测试。

模拟完整流程：
  收到一行数据 → 决策 → 开单 → 退出 → 更新统计 → 调优建议

覆盖 V38.5 新增的三个模块：
  - OutcomeDatabase（analytics/outcome_db.py）
  - RejectAnalytics（analytics/reject_analytics.py）
  - ExitReplayAnalyzer（analytics/exit_replay_analyzer.py）
"""
import sys, os, json, math, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

# ============================================================
# 1. OutcomeDatabase 端到端
# ============================================================
def test_outcome_db_e2e():
    from analytics.outcome_db import OutcomeDatabase

    tmpf = os.path.join(tempfile.gettempdir(), "test_e2e_outcome.json")
    db = OutcomeDatabase(tmpf)

    # 写入 120 笔正常数据（足够让 skewness 稳定）
    np.random.seed(42)
    for i in range(120):
        r = np.random.normal(0.02, 0.12)
        db.update(f"test_hash_A", r)

    # 写 20 笔另一组
    for i in range(20):
        db.update(f"test_hash_B", np.random.normal(-0.01, 0.10))

    ev_a = db.get_ev("test_hash_A", min_trades=15)
    ev_b = db.get_ev("test_hash_B", min_trades=15)

    assert ev_a is not None, "get_ev should return data for hash A"
    assert ev_b is not None, "get_ev should return data for hash B"
    assert "skewness" in ev_a, "skewness field missing"
    assert "skewness_valid" in ev_a, "skewness_valid field missing"
    assert ev_a["skewness_valid"] == True, "hash A has 120 trades, skewness_valid should be True"
    assert ev_a["skewness"] is not None, "hash A skewness should not be None"

    ev_b_skew = db.get_ev("test_hash_B", min_trades=15)
    # hash B 只有 20 笔 < 100，skewness 应该为 None
    if ev_b_skew:
        assert ev_b_skew["skewness_valid"] == False, "hash B has 20 trades, skewness_valid should be False"
        assert ev_b_skew["skewness"] is None, "hash B skewness should be None"

    # 测试不存在的 hash
    none_ev = db.get_ev("nonexistent_hash")
    assert none_ev is None, "Nonexistent hash should return None"

    # 测试 top/worst
    top = db.get_top_features(top_n=5)
    assert len(top) <= 5
    worst = db.get_worst_features(top_n=5)
    assert len(worst) <= 5

    # 清理
    os.remove(tmpf)
    print("  [OK] OutcomeDatabase e2e")


# ============================================================
# 2. RejectAnalytics 端到端
# ============================================================
def test_reject_analytics_e2e():
    from analytics.reject_analytics import RejectAnalytics

    tmpdir = os.path.join(tempfile.gettempdir(), "test_e2e_reject_logs")
    ra = RejectAnalytics(tmpdir)

    # 模拟一天内多次拒单
    for _ in range(10):
        ra.log("H", "LOW_EV", {"score": 35}, {"expected_value": 0.02})
    for _ in range(3):
        ra.log("H", "NO_BASE_TRIGGER", {})
    for _ in range(5):
        ra.log("4H", "LOW_EV", {"score": 28}, {"expected_value": 0.01})

    stats = ra.get_stats()
    assert stats["total"] >= 18

    db = ra.get_trend_dashboard(days=1)
    assert db["total_period"] >= 18
    assert db["top_reason_today"] != ""

    blacklist = ra.get_feature_blacklist(min_rejects=1)
    assert len(blacklist) >= 1

    heatmap = ra.get_hourly_heatmap()
    assert "hours" in heatmap
    assert "peak_hour" in heatmap

    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)
    print("  [OK] RejectAnalytics e2e")


# ============================================================
# 3. ExitReplayAnalyzer 端到端
# ============================================================
def test_exit_replay_e2e():
    from analytics.exit_replay_analyzer import ExitReplayAnalyzer, ReplayTrade, BatchExitReplayAnalyzer

    # 模拟一笔交易的模拟价格序列（Long 方向，看涨）
    np.random.seed(42)
    entry_price = 100.0
    risk = 2.0  # 入场 - 初始止损
    atr = 0.8
    price_history = [entry_price + np.random.randn() * atr * 0.5 + 0.05 for _ in range(60)]

    trade = ReplayTrade(
        trade_id="test_001",
        symbol="BTC/USDT",
        entry=entry_price,
        risk=risk,
        atr=atr,
        regime="TREND",
        impulse_strength=0.6,
        price_history=price_history,
        real_r=0.12,
    )

    # 单笔回放
    analyzer = ExitReplayAnalyzer(trade)

    # simulate
    result = analyzer.simulate(trail_atr_mult=5.0, lock_trigger_r=1.9)
    assert hasattr(result, "simulated_r"), "simulate() should return ReplayResult"
    assert hasattr(result, "max_drawdown")
    assert hasattr(result, "exit_bar")

    # grid_search
    grid = analyzer.grid_search()
    assert "best_result" in grid
    assert "total_simulations" in grid
    assert grid["total_simulations"] > 0

    # atr_sensitivity
    sens = analyzer.atr_sensitivity(atr_range=[2.0, 3.0, 4.0])
    assert "best_atr_mult" in sens
    assert "results" in sens
    assert len(sens["results"]) == 3

    # 批量分析
    trade2 = ReplayTrade(
        trade_id="test_002",
        symbol="ETH/USDT",
        entry=50.0,
        risk=1.5,
        atr=0.6,
        regime="CHOP",
        impulse_strength=0.3,
        price_history=[50.0 + np.random.randn() * 0.4 for _ in range(40)],
        real_r=-0.05,
    )

    batch = BatchExitReplayAnalyzer([trade, trade2])
    regime_analysis = batch.analyze_by_regime()
    assert "by_regime_suggestions" in regime_analysis
    assert "suggested_update" in regime_analysis

    report = batch.generate_report()
    assert report["total_trades"] == 2
    assert "regime_analysis" in report

    print("  [OK] ExitReplayAnalyzer e2e")


# ============================================================
# 4. 三者联动：模拟完整的"信号→开单→退出→统计→调优"流程
# ============================================================
def test_full_pipeline_e2e():
    """
    端到端完整流程，模拟：
    1. 传入一行 OHLCV 数据 + 执行上下文
    2. V37MasterEngine 决策
    3. 假设开单
    4. 用 OutcomeDatabase 记录盈亏
    5. 用 RejectAnalytics 记录拒单原因
    6. 用 ExitReplayAnalyzer 做退场调优
    """
    from analytics.outcome_db import OutcomeDatabase
    from analytics.reject_analytics import RejectAnalytics

    # 创建 V37MasterEngine
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    # 使用 core/ 下的新版（已确认是唯一留存版本）
    from core.alpha_master_engine import V37MasterEngine

    tmp_db = os.path.join(tempfile.gettempdir(), "test_e2e_full_outcome.json")
    tmp_rej = os.path.join(tempfile.gettempdir(), "test_e2e_full_reject.json")

    db = OutcomeDatabase(tmp_db)
    ra = RejectAnalytics(tmp_rej)
    engine = V37MasterEngine()

    # 模拟 30 行数据
    np.random.seed(123)
    rows = []
    close = 100.0
    for i in range(30):
        close += np.random.randn() * 0.3
        rows.append({
            "datetime": f"2025-01-{i+1:02d}",
            "open": close - 0.1,
            "high": close + abs(np.random.randn()) * 0.4,
            "low": close - abs(np.random.randn()) * 0.4,
            "close": close,
            "volume": int(max(100, np.random.randn() * 200 + 1000)),
            "adx": np.random.uniform(12, 35),
            "ATRr_14": 0.5 + abs(np.random.randn()) * 0.2,
            "momentum": np.random.randn() * 0.3,
            "momentum_slope": np.random.randn() * 0.1,
            "vwap_48": close + np.random.randn() * 0.2,
            "volume_ratio": np.random.uniform(0.5, 2.5),
            "sqzmom_reversal_confirm_long": np.random.rand() > 0.7,
            "sqzmom_reversal_confirm_short": np.random.rand() > 0.7,
            "dmi_bull": np.random.rand() > 0.5,
            "dmi_bear": np.random.rand() > 0.5,
            "plus_di": np.random.uniform(15, 35),
            "minus_di": np.random.uniform(15, 35),
            "squeeze_released": np.random.rand() > 0.8,
            "fvg_direction": "Long" if np.random.rand() > 0.5 else "Short",
            "fvg_mid": close + np.random.randn() * 0.3,
            "ob_direction": "Long" if np.random.rand() > 0.5 else "Short",
            "ob_mid": close + np.random.randn() * 0.3,
            "reversal_long": np.random.rand() > 0.8,
            "reversal_short": np.random.rand() > 0.8,
            "breakout_long": np.random.rand() > 0.85,
            "breakout_short": np.random.rand() > 0.85,
            "combo_long": np.random.rand() > 0.9,
            "combo_short": np.random.rand() > 0.9,
            "sellside_sweep": np.random.rand() > 0.85,
            "buyside_sweep": np.random.rand() > 0.85,
            "smc_quality_score_bull": np.random.uniform(0, 80),
            "smc_quality_score_bear": np.random.uniform(0, 80),
        })

    exec_ctx = {
        "atr_pct": 0.008,
        "atr": 0.5,
        "trend_direction": "Long",
    }
    macro_ctx = {
        "allowed_direction": "Long",
    }

    trades_count = 0
    reject_count = 0

    for row in rows:
        row = pd.Series(row)
        decision = engine.decide(row, exec_ctx, macro_ctx)

        if decision.get("allow"):
            # 模拟开单：用模拟的 realized_r
            realized_r = np.random.normal(0.05, 0.15)
            db.update("e2e_test_feature", realized_r)
            engine.update_account(realized_r)
            trades_count += 1
        else:
            reason = decision.get("reason", "UNKNOWN")
            sig = decision.get("signal", {})
            ra.log("H", reason, {"score": sig.get("score_raw", 0)}, {"expected_value": sig.get("expected_value", 0)})
            reject_count += 1

    # 断言：流程正常跑完，没有报错
    assert trades_count + reject_count == 30, f"Total decisions mismatch: {trades_count} + {reject_count} != 30"

    # 检查 OutcomeDatabase 有记录
    ev_result = db.get_ev("e2e_test_feature", min_trades=1)
    if trades_count > 0:
        assert ev_result is not None, "Should have EV result for test feature"

    # 检查 RejectAnalytics
    stats = ra.get_stats()
    assert stats["total"] == reject_count

    # 检查 AccountState
    state = engine.state_dict()
    assert state["trade_count"] == trades_count

    # 清理
    os.remove(tmp_db)
    import shutil
    shutil.rmtree(tmp_rej, ignore_errors=True)
    print("  [OK] Full pipeline e2e")


# ============================================================
if __name__ == "__main__":
    print("=== P5: V38.5 End-to-End Integration Tests ===")
    test_outcome_db_e2e()
    test_reject_analytics_e2e()
    test_exit_replay_e2e()
    test_full_pipeline_e2e()
    print("\n=== All E2E tests passed ===")
