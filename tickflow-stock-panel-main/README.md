# SMC Quant Command Center

基于桌面 `tickflow-stock-panel-main` 信息架构，为 SMC Bot 提供独立的行情、信号、风控、绩效和 AI 分析面板。

## 启动

在 SMC_Bot 根目录执行：

```powershell
streamlit run tickflow-stock-panel-main/panel.py --server.port 8502
```

然后打开 `http://localhost:8502`。

## AI 配置

支持 OpenAI-compatible Chat Completions 接口：

DeepSeek 可直接使用以下环境变量，客户端会自动选择 DeepSeek 地址和模型：

```powershell
$env:DEEPSEEK_API_KEY="在本机终端直接填写"
```

```powershell
$env:AI_API_KEY="your-key"
$env:AI_API_BASE="https://api.openai.com/v1"
$env:AI_MODEL="gpt-4o-mini"
```

例如 DeepSeek：

```powershell
$env:AI_API_BASE="https://api.deepseek.com/v1"
$env:AI_MODEL="deepseek-chat"
```

AI 只读取面板快照并返回分析，不接收下单工具，也不会直接执行交易。
