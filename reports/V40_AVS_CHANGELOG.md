# V40 AVS Integrated Changelog

## 目标

在不破坏 V38 Scorecard 与 V39 Alpha Cluster Guard 主交易链路的前提下，新增“Alpha真实性自动评估模块”，用于自动判断：

- 系统是否存在过拟合风险
- 哪些 cluster 是 fake alpha / fragile alpha
- 哪些 regime 才是真正 edge

## 新增文件

- `alpha_validator/__init__.py`
- `alpha_validator/avs_engine.py`
- `alpha_validator/cli.py`
- `scripts/run_alpha_validation.py`
- `reports/AVS_INTEGRATION_README.md`
- `reports/V40_AVS_CHANGELOG.md`

## 修改文件

- `run_backtest.py`
  - 回测保存 CSV 后自动运行 AVS。
  - 自动保存 `outputs/<prefix>_report.json/md/csv`。
  - 控制台输出 AVS_SCORE、overfit_score、verdict、true_edge_regimes、fake_clusters。

- `backtest/runner.py`
  - Runner 版本号升级为 `V40_AVS_INTEGRATED_20260616`。
  - `summarize_backtest()` 追加 `alpha_validation` 字段。
  - 该字段只做报告输出，不改变交易信号、仓位、出入场逻辑。

- `analytics/report.py`
  - Markdown 报告新增 “Alpha真实性 AVS” 段落。

- `scripts/smoke_check.py`
  - 加入 `alpha_validator.avs_engine` 导入检查。
  - smoke backtest 后自动调用 `scripts/run_alpha_validation.py`。

## 检查结果

已通过：

```bash
python -m compileall -q .
python scripts/smoke_check.py
python scripts/run_alpha_validation.py --trades data/backtest_v39_full.csv --out-dir outputs --prefix avs_existing_full
```

## 设计边界

V40 AVS 不负责“生成交易”，只负责“验证交易结果是否可信”。
这避免了把验证模块变成新的过拟合过滤器。
