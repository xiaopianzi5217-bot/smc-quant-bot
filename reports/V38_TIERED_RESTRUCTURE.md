# V38 机构级信号分层重构说明

## 目标

本次重构针对上一版暴露出的三个核心矛盾：

1. 开单数不足：过度依赖硬过滤和 Cluster Kill，导致交易样本被砍得过少。
2. 胜率不足：所有信号混在一个入口内，低质量探索单和核心单没有区分。
3. PF 不稳定：收益容易被少数 Top 单或少数 cluster 支配。

V38 的目标不是简单放宽所有过滤，而是把交易入口改成分层结构：

- Tier 1：HIGH_PRECISION，高确认核心单，用于保护胜率。
- Tier 2：BALANCED，主交易层，用于提高有效开单数。
- Tier 3：EXPLORATION，小仓探索层，用于扩展覆盖率，同时用低仓位保护 PF。

## 主要修改

### 1. 新增 `core/signal_tier.py`

新增 V38 分层模块，包含：

- `dynamic_ev_threshold()`：按 regime、波动状态、tier 计算动态 EV 阈值。
- `classify_signal_tier()`：把已有 SMC、SQZMOM、EV、RR、scorecard、流动性确认转成 Tier 1/2/3。
- `annotate_signal_with_tier()`：把 tier、rank_score、confirm_count、dynamic threshold 写回 signal。
- `regime_adaptive_entry_params()`：根据 tier/regime 调整等待 K 线数和追价 ATR。
- `tier_exit_profile()`：根据 tier/regime 调整 trailing、分批止盈比例和最大持仓时间。

### 2. 重写 `core/alpha_master_engine.py`

- 将主引擎升级为 `V38TieredMasterEngine`。
- 保留 `V37MasterEngine = V38TieredMasterEngine` 兼容旧导入。
- `choose_signal()` 不再只看硬 base trigger，而是优先选择通过 V38 tier 的候选。
- `tail_filter()` 使用动态 EV 阈值，不再固定一个全局 EV 门槛。
- `risk_budget()` 按 tier、confirmation count、regime 做仓位分配。
- `allocate()` 改为：
  - Tier 1 → CORE
  - Tier 2 → BALANCED
  - Tier 3 → SCALP
  - 低于 tier → PROBE/DUMPSTER

### 3. 重写 `core/decision_kernel.py`

- 决策核接入 `V38TieredMasterEngine`。
- 保持单一 `decide()` 入口，避免 runner、strategy、cluster 多头决策。

### 4. 调整 `strategy/alpha_cluster_guard.py`

- Cluster 从“硬杀死”改成“压缩为主、极端才拒绝”。
- 默认不再 block `SCALP`，避免 Tier 3 全部被挡掉。
- 统计表现较差的 cluster 改为小仓压缩，极端 PF/胜率都很差时才硬拒绝。
- 支持 `V37_` 与 `V38_` cluster 名称兼容映射。

### 5. 重构 `backtest/runner.py`

- 接入 `regime_adaptive_entry_params()`。
- 接入 `tier_exit_profile()`。
- 输出交易中新增：
  - `v38_tier`
  - `v38_tier_name`
  - `v38_dynamic_ev_threshold`
  - `v38_rank_score`
  - `v38_confirm_count`
  - `v38_recovery_allowed`
  - `entry_profile`
  - `exit_trail_atr_mult`
- 优化 HTF macro lookup，避免每根 15m K 线复制和筛选完整 1H DataFrame。

### 6. 调整 `config/alpha_cluster_rules.json`

- 升级为 `AlphaClusterGuard_V38_SOFT_20260618`。
- 允许 `SCALP` 小仓探索。
- 对坏 cluster 默认压缩，而不是直接删除。

### 7. `run_backtest.py`

- 新增 `--skip-avs` 参数，用于快速迭代回测。

## 验证

已执行：

```bash
python -m compileall -q .
python scripts/smoke_check.py
```

结果：

```text
SMOKE_CHECK_OK
```

说明：smoke check 使用短窗口数据，主要验证导入、语法、主链路和报告输出，不代表全年回测收益结论。

## 使用建议

快速测试：

```bash
python run_backtest.py --exec-csv data/BTCUSDT_15M_365d.csv --macro-csv data/BTCUSDT_1H_365d.csv --out data/backtest_v38_tiered.csv --max-rows 1500 --skip-avs
```

完整回测：

```bash
python run_backtest.py --exec-csv data/BTCUSDT_15M_365d.csv --macro-csv data/BTCUSDT_1H_365d.csv --out data/backtest_v38_tiered_full.csv
```

完整回测后重点看：

- `by_setup_type` 中 `V38_CORE / V38_BALANCED / V38_SCALP` 的贡献是否分层清晰。
- `v38_tier_name` 分组下的胜率和 PF。
- Tier 3 是否提高交易数但没有吞噬总 PF。
- `reject_audit_v38.csv` 中 `REJECT_NO_V38_TIER` 是否仍过多。
