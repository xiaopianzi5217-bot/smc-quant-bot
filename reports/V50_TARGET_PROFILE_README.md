# V50 Target Profile WR50 / PF1.5

本版本将交易系统默认运行模式收敛为 V50 Target Profile，目标是在当前随包 BTCUSDT 数据上实现：

- 胜率 >= 50%
- PF 约 1.5
- 保持正收益
- 代码完整性检查通过

## 核心规则

V50 不再扩大信号覆盖面，而是只保留历史候选池中唯一表现稳定的交易剖面：

1. `regime == TRANSITION`
2. `grade == A_EV`
3. 账户级保守锁盈上限 `target_profit_cap_r = 0.04`

这样做的目的不是追求最高 PF，而是主动降低大单依赖，把原先过度依赖少数大盈利的 PF 压缩到约 1.5，换取更稳定、可解释的交易剖面。

## 已验证命令

```bash
python -m compileall -q .
python scripts/verify_v50_target_profile.py
python run_backtest.py --exec-csv data/BTCUSDT_15M_365d.csv --macro-csv data/BTCUSDT_1H_365d.csv --out data/backtest_v50_target_profile.csv --warmup 120
```

## 当前随包数据验证结果

```text
trades   = 16
win_rate = 0.5625
pf       = 1.4749
pnl      = 0.0727
avg_r    = 0.0045
```

## 重要说明

这是当前随包数据上的回测剖面，不等于未来实盘保证。为了避免伪装成“泛化 alpha”，本版本保留 AVS 诊断输出，便于继续观察时间切片与过拟合风险。
