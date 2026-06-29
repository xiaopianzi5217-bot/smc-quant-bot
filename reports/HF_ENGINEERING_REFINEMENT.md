# HF 工程化精进报告

## 结论

本版本已经从“本地可运行策略包”升级为“可上传 Hugging Face Spaces 的工程化量化框架”。默认 `dry_run + sample_data`，不会触发实盘交易。

## 本次补齐的模块

1. `app.py`：Hugging Face Gradio 入口，支持系统状态、信号干运行、快速回测。
2. `ops/env_config.py`：环境变量配置覆盖，适配 HF Secrets/Variables。
3. `ops/runtime_paths.py`：统一 data/reports/logs/artifacts 路径。
4. `risk/global_risk.py`：组合级风控保护，包括最大持仓、同向持仓、日亏损、回撤、连续亏损。
5. `execution/paper_broker.py`：纸面订单 ledger，避免实盘误下单。
6. `monitoring/runtime_report.py`：统一 JSON/Markdown 报告输出。
7. `scripts/hf_self_test.py`：HF 部署前自检脚本。
8. `README.md`：加入 HF Spaces 元数据和部署说明。
9. `.gitignore`：排除缓存、日志、隐私配置和运行产物。
10. `config/v11_full_config.json`：扩充 HF、安全默认值、风控参数和存储配置。

## 保持不变的部分

- V9 信号评分逻辑不改。
- V9 decision kernel 主链路不改。
- 原始策略依赖 `strategy / indicators` 保留。
- V11 作为升级外壳和工程规范接管运行层。

## 仍建议后续实盘前完成

1. 至少 2-3 年、多币种、多周期的样本内/样本外回测。
2. Walk-forward 参数稳定性验证。
3. 模拟盘连续运行不少于 2-4 周。
4. 交易所订单状态同步、撤单补偿、断线恢复。
5. 实盘前人工 kill-switch 和资金上限。

## 已验证命令

```bash
python scripts/smoke_check.py
python scripts/hf_self_test.py
```
