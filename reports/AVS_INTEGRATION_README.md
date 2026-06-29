# V40 Alpha Validity Score Integration

本版本新增 `alpha_validator` 模块，用于在回测完成后自动判断 Alpha 是否真实、哪些 cluster 是伪 alpha、哪些 regime 才是真正 edge。

## 新增/修改文件

- `alpha_validator/__init__.py`
- `alpha_validator/avs_engine.py`
- `alpha_validator/cli.py`
- `scripts/run_alpha_validation.py`
- `run_backtest.py`
- `backtest/runner.py`
- `analytics/report.py`
- `scripts/smoke_check.py`

## 使用方式

完整回测后自动输出 AVS 报告：

```bash
python run_backtest.py --exec-csv data/BTCUSDT_15M_365d.csv --macro-csv data/BTCUSDT_1H_365d.csv --out data/backtest_v40_full.csv --warmup 120
```

单独验证已有交易 CSV：

```bash
python scripts/run_alpha_validation.py --trades data/backtest_v40_full.csv --out-dir outputs --prefix avs_v40_full
```

## 输出文件

- `outputs/avs_report.json`
- `outputs/avs_report.md`
- `outputs/avs_cluster_table.csv`
- `outputs/avs_regime_table.csv`

## 解释

- `AVS_SCORE >= 0.80`：候选真 alpha，可继续做资金分配与扩容测试。
- `0.60 <= AVS_SCORE < 0.80`：弱 alpha，需要更多样本和扰动测试。
- `0.40 <= AVS_SCORE < 0.60`：脆弱 alpha，高过拟合风险。
- `AVS_SCORE < 0.40`：大概率过拟合或无真实 alpha。
