# SMC v7 Live Trading Engine

## 目标

这版把系统从“信号系统”升级为“可执行交易系统”。

包含：

- 实盘执行引擎
- dry_run 安全模式
- 仓位计算
- 多币种持仓管理
- 部分止盈
- 止损后冷却
- TP1 后移动止损到开仓价
- TP2 后移动止损保护利润
- 交易日志
- Telegram 开单/管理提醒

## 上传目录

把这些目录上传到 HuggingFace / VPS 项目根目录：

```text
config/
execution/
risk/
portfolio/
journal/
runner/
```

同时项目里必须已有：

```text
decision/v6_decision_kernel.py
indicators/
strategy/
notifier/
config.py
```

## 默认安全模式

默认配置：

```json
"mode": "dry_run"
```

不会真实下单。

## 开启实盘的三个条件

必须同时满足：

```text
1. config/v7_live_config.json 里 mode = live
2. 环境变量 ENABLE_LIVE_TRADING=true
3. 配置交易所 API Key
```

环境变量：

```text
EXCHANGE_API_KEY
EXCHANGE_SECRET
EXCHANGE_PASSWORD  # bitget / okx 常用
ENABLE_LIVE_TRADING=true
```

## 启动

```bash
python runner/v7_live_runner.py
```

## 重要说明

这不是盈利保证。正式实盘前必须先 dry_run 至少 1-2 周，确认：

- 信号是否正常
- 仓位是否正确
- TP/SL 是否合理
- 日志是否完整
- Telegram 是否不漏发
