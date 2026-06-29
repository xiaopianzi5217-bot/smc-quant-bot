# SMC Quant Bot 分层运行逻辑审查

## 当前结论

本次修复后，通知链路按三层边界运行：

1. Observer 层：只发结构变化，不给开单结论。
2. Strategy 层：只发经过策略中枢测算、打分、风控审批后的可执行机会。
3. Execution 层：只处理开仓后的持仓、订单和风控生命周期事件。

## 与上传版本相比的关键差异

上传版本中 `app.py` 已经尝试导入：

- `dispatch_observer_snapshot`
- `dispatch_strategy_decision`
- `dispatch_execution_event`

但 `notifier/manager.py` 仍是旧版 `dispatch_signal(r)` 混合分发逻辑，没有这些函数，导致 app 启动时会出现 ImportError。

此外，上传版本的 `app.py` 有两个问题：

- 重复导入 Telegram 模块。
- 错误使用 `aiohttp.client_exceptions.cert_errors` 作为发送失败判断条件。这个变量不是你的 `send_errors`，会让报告状态失真。

## 已修复

- 恢复严格三层分发的 `notifier/manager.py`。
- 恢复三层测试入口：Observer / Strategy / Execution。
- `dry_run()` 改为只扫描和保存报告，不直接发 Telegram，避免和 Strategy 层重复推送。
- 保留 `manual_signal_trigger()`，但它现在只是 Strategy 层的兼容入口。
- 删除 app 中无关/重复导入。

## SQZMOM / SMC 源逻辑保留情况

保留：

- `indicators/basic.py` 中仍有 `xtl_val`、`lowsqz`、`midsqz`、`highsqz`。
- `strategy/smc.py` 中仍有 BSL/SSL、Sweep、OB、FVG、pivot、XTL 颜色判断。
- `notifier/observer/signal_collector.py` 中 K 线颜色优先使用 `strategy.smc.get_color_state(xtl_val)`，不是普通阴阳线。
- 背离信号来自 `exec_ctx.get("has_bottom_div")` / `exec_ctx.get("has_top_div")`。

需要注意：当前 SQZMOM 是 Python 复刻逻辑，并非逐字符等同 TradingView Pine 源码。若要做到完全一致，需要把 Pine 源码中的参数、线性回归/动能柱、挤压点颜色定义逐项对齐。

## 开单链路是否符合逻辑

当前开单链路：

数据/指标 -> SMC/SQZMOM 上下文 -> adaptive_signal_score -> V9DecisionKernel -> GlobalRiskGuard -> Strategy 通知 -> Execution 执行/持仓管理

这个方向是正确的。关键点是：Observer 层看到结构变化，不等于可以开单；Strategy 层必须用中枢批准；Execution 层不能再重新生成结构信号。

## 建议继续精修的过滤器

建议加在 Strategy 层，不要加在 Observer 层：

1. 趋势过滤：只在高周期方向允许时开单。
2. RR 过滤：低于最小 RR 的信号不发 Strategy 开单提示。
3. 波动过滤：ATR 过低或异常过高时不发开单信号。
4. 成交量过滤：突破或 Sweep 后没有成交量确认时降权。
5. 冷却过滤：同一品种连续触发时设置 cooldown。
6. 距离过滤：离 BSL/SSL/OB/FVG 过远时不算有效机会。
7. Funding 过滤：永续合约资金费率极端时降低逆向开单权重。
8. 新闻/时段过滤：重大数据公布前后禁止自动开单。
9. 多周期一致性过滤：15m 触发必须得到 1h 或 4h 的方向/结构支持。
10. 重复信号去重：同一结构未失效前不要重复推送开单信号。

## 下一步建议

优先做三件事：

1. 把 real-time exchange 数据接入 `build_signal_snapshot()`，不要长期用 sample OHLCV。
2. 把 Strategy 层输出的 `decision` 写入 journal，便于回测每一个被过滤/被批准信号。
3. 给 `V9DecisionKernel` 增加明确的硬过滤字段，例如 `filters_passed`、`blocked_by`、`confidence_score`。
