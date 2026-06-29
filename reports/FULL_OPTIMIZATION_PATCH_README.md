# SMC 量化系统完整优化补丁

本补丁把前面 7 个 Strategy 过滤继续扩展为完整交易质量控制链路。

## 直接复制替换/新增路径

```text
新增/替换：strategy/trade_filters.py
新增/替换：decision/v9_decision_kernel.py
新增/替换：runner/v11_institutional_runner.py
新增/替换：runner/v7_live_runner.py
新增：risk/position_sizing.py
新增：analytics/filter_audit.py
新增：optimizer/strategy_param_optimizer.py
新增：scripts/run_filter_audit_summary.py
新增：scripts/run_strategy_param_optimizer.py
替换：config/v11_full_config.json
替换：config/v7_live_config.json
新增：state/news_pause.json
新增：reports/FULL_OPTIMIZATION_PATCH_README.md
```

## 本次新增 5 个优化

### 1. 回测统计每个过滤器的拦截率和拦截后盈亏

新增 `analytics/filter_audit.py`。

运行中会记录：

```text
reports/filter_audit.csv
```

统计汇总：

```bash
python scripts/run_filter_audit_summary.py
```

输出：

```text
reports/filter_audit_summary.json
```

它能告诉你：哪个过滤器拦截最多、拦截后的平均收益/平均 R 值如何。

### 2. 自动参数优化

新增 `optimizer/strategy_param_optimizer.py`。

运行：

```bash
python scripts/run_strategy_param_optimizer.py
```

输出：

```text
reports/strategy_param_optimization.json
```

当前优化参数：

```text
min_rr
min_volume_ratio
max_structure_atr
```

作用：不是拍脑袋调参数，而是根据历史审计结果找更优参数区间。

### 3. 交易时段过滤

新增配置：

```json
"trading_session": {
  "enabled": true,
  "blocked_utc_hours": [0, 1, 2, 3],
  "allow_if_volume_ratio_above": 1.8
}
```

作用：避开低流动性时段。若该时段成交量显著放大，仍可放行。

### 4. 新闻/极端波动暂停开单

新增配置和文件：

```text
state/news_pause.json
```

手动暂停时改成：

```json
{
  "active": true,
  "reason": "重要数据/新闻前后暂停",
  "until_ts": 0
}
```

同时自动识别：

```text
ATR 过高
单根 K 线振幅过大
成交量极端放大
```

作用：减少新闻插针、极端波动中的被动止损。

### 5. 分层仓位

新增 `risk/position_sizing.py`。

默认：

```text
S 级：1.0 倍风险
A 级：0.5 倍风险
B/C/D：只观察，不开单
```

作用：让系统不是“有信号就同样仓位”，而是按信号质量分配风险。

## 完整开单链路

```text
指标计算
→ SMC/SQZMOM 打分
→ V6/V9 中枢初审
→ Strategy 9 个过滤
→ S/A/B 分层仓位
→ GlobalRiskGuard 全局风控
→ Execution 执行/持仓管理
→ FilterAuditLogger 记录审计数据
→ Optimizer 参数优化
```

## 注意

`reports/filter_audit.csv` 里的 `future_return/future_r` 在实盘运行时默认无法提前知道，所以初始为 0。要做真正“拦截后盈亏”，需要在回测循环里给 `FilterAuditLogger.record(..., future_price=..., future_r=...)` 传入未来价格或最终 R 值。

这版已经预留接口，后面接你现有 backtest engine 时直接传入即可。
