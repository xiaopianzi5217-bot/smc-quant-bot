---
title: SMC V11 V9 Quant System
emoji: 🚀
colorFrom: blue
colorTo: green
sdk: gradio
python_version: 3.11
app_file: app.py
pinned: false
---

# SMC V11+V9 工程化量化系统

这是面向 Hugging Face Spaces / 本地服务器的安全工程版。默认运行模式是 `dry_run + sample_data`，不会连接交易所、不会下实盘订单。

## 快速运行

```bash
pip install -r requirements.txt
python main.py
python scripts/smoke_check.py
python app.py
```

## Hugging Face Spaces 部署

1. 新建 Space，SDK 选择 Gradio。
2. 上传本项目全部文件，入口保持 `app.py`。
3. Secrets/Variables 可选配置：
   - `SMC_MODE=dry_run`
   - `SMC_SYMBOLS=BTC/USDT,ETH/USDT`
   - `SMC_RISK_PER_TRADE=0.01`
   - `SMC_MIN_RR=2.0`
4. 不要在公开 Space 里写死交易所 API Key。需要实盘时只放在 Space Secrets，并先完成模拟盘验证。

## 当前完整度

已具备：

- v11 + v9 合并运行入口
- Gradio/Hugging Face 页面入口
- 信号 dry-run
- 快速回测
- 数据质量检查
- 成本模型
- 全局风险保护模块
- 纸面订单 ledger
- 环境变量配置覆盖
- smoke check / HF self-test

仍需通过真实数据和模拟盘继续验证：

- 多市场长周期 walk-forward 统计
- 交易所订单状态双向同步
- 实盘断线恢复和持仓对账
- 资金曲线级熔断参数校准
- 策略阈值的样本外稳定性验证

## 安全提醒

本项目不是投资建议。实盘前必须完成长期回测、样本外测试、模拟盘验证、极端行情测试和人工风控审核。
