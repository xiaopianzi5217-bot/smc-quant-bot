# V55 Engineering Patch Summary

## 修改目标

基于 V54 回测诊断，V55 的核心目标不是继续提高表面 PF，而是降低结构性乐观偏差，修复 MFE replay、微利润 cap/floor、小止损/低 TP1、噪声桶缺失等问题。

## 主要修改

1. **去 MFE replay 化**
   - `final_forge/profile.py` 新增 `apply_v55_engineering_profile()`。
   - 默认 `use_mfe_tp1_replay=False`。
   - 候选池 profile 不再因为 `mfe_r >= 1R` 自动给一笔 TP1 小盈利。

2. **移除微利润 cap/floor 默认行为**
   - `backtest/runner.py` 中 `target_profit_cap_r` 默认改为 `None`。
   - 不再默认把盈利压成 0.012R/0.02R，也不再默认把尾部亏损硬裁成 -0.02R。

3. **提高交易质量门槛**
   - V55 event gate 默认：
     - `score >= 60`
     - `expected_value >= 0.02`
     - `win_prob >= 0.45`
     - `estimated_rr >= 1.20`
   - fast profile 默认：
     - `score >= 60`
     - `expected_value >= 0.01`
     - `win_prob >= 0.45`
     - `estimated_rr >= 1.10`

4. **避免小止损/低 TP1**
   - `strategy/risk.py`：
     - `min_stop_atr` 从 0.65 提高到 0.95。
     - `tp1_atr` 从 1.00 提高到 1.25。
     - `tp2_atr` 从 1.60 提高到 1.90。
     - `tp3_atr` 从 2.40 提高到 2.80。
   - `backtest/runner_legacy_v31_pre_v37.py`：
     - `min_risk` 从 `0.45 ATR / 0.15%` 提高到 `0.95 ATR / 0.25%`。
     - trend/transition/chop 的 TP1 全部提高到 1.15R–1.25R 区间。

5. **TP1 触发改为真实价格触达**
   - Long 必须 `high >= tp1`。
   - Short 必须 `low <= tp1`。
   - 不再只用 `max_favorable_r >= 1.0` 触发 TP1 状态。

6. **新增 deep detection**
   - 新增 `scripts/v55_deep_detection.py`。
   - 输出：
     - 编译检查
     - fast profile 检查
     - 四段时间稳定性
     - PF 压缩测试
     - noise bucket
   - 报告文件：
     - `reports/V55_DEEP_DETECTION_REPORT.json`
     - `reports/V55_DEEP_DETECTION_REPORT.md`

## 本次检测结果

Bundled 365d candidate profile：

- Trades: 89
- Win rate: 51.69%
- PF: 1.7472
- Total R: 25.6325R
- Avg R/trade: 0.288R

PF 压缩后：

- PF: 1.6164
- Total R: 21.5086R
- Avg R/trade: 0.24167R

## 重要限制

当前压缩包内可验证的候选池只有约 155 条历史候选，因此 fast profile 本身无法验证 370–400 trades/year 的目标。要验证日均约 1 单，需要运行 raw event backtest、扩大信号采样，或接入更多市场/更长周期数据。

V55 没有伪造 70–80% 胜率。当前候选池在去掉 MFE replay 和微利润 cap/floor 后，胜率为 51.69%。这说明 V54 的高 PF 主要来自结构性 profile 处理，而不是稳定的高胜率系统。
