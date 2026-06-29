# Hugging Face 部署说明

推荐上传整个项目目录到 Hugging Face Space，SDK 选择 `Gradio`，入口文件保留 `app.py`。

安全默认值：

- `SMC_MODE=dry_run`
- `SMC_DATA_MODE=sample_data`
- 不会发送实盘订单
- API Key 只能放在 HF Secrets，不能写入源码

上线前建议运行：

```bash
python scripts/smoke_check.py
python scripts/hf_self_test.py
python scripts/deep_audit.py
```

不建议在 Hugging Face 免费 Space 直接跑实盘，因为 Space 可能休眠、重启、丢失本地状态。HF 更适合做观察面板、干运行、快速回测和策略验证。
