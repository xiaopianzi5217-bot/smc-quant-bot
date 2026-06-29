# SMC v8 完整工程级量化补齐包

v8 不是新增策略，而是补齐工程级量化系统最后缺口。

## 新增模块

```text
data_quality/validator.py        数据质量检查、K线缺口、异常价格、周期对齐
backtest/realistic_costs.py      手续费、滑点、真实成交成本
monitoring/health_check.py       数据/系统健康监控
ops/safety_guard.py              每日亏损、连续止损、紧急熔断
ops/config_validator.py          配置合法性检查
alerts/deduper.py                Telegram 信号去重和冷却
optimizer/walk_forward.py        walk-forward 参数优化骨架
config/v8_full_config.json       v8 总配置
```

## 当前完整链路

```text
Data Quality Gate
        ↓
Indicator / Structure Layer
        ↓
V6 Decision Kernel
        ↓
Portfolio Risk Gate
        ↓
Position Sizing
        ↓
Execution Engine
        ↓
Lifecycle Manager
        ↓
Journal Logger
        ↓
Health Monitor / Alert Deduper / Safety Guard
```

## 还不能省略的安全要求

1. 默认 dry_run，不要直接 live。
2. 实盘前至少 1-2 周 dry_run。
3. 每天检查 trade_journal、错误日志和 Telegram 触发频率。
4. 开 live 必须设置 `ENABLE_LIVE_TRADING=true`。

## 量化级完成度

v8 后达到：

```text
研究级策略：完成
回测级系统：完成
准实盘执行：完成
工程级安全层：完成
真实盈利验证：需要 dry_run / 小资金实盘验证
```
