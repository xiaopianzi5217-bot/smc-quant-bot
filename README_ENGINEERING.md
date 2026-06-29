# SMC V11 + V9 Full Quant Final

## 快速运行

```bash
pip install -r requirements.txt
python main.py
```

## 工程检查

```bash
python scripts/smoke_check.py
```

该命令会检查：

1. 全项目 Python 语法编译；
2. 核心模块导入；
3. v11+v9 主入口干运行；
4. 快速回测冒烟。

## 快速回测

```bash
python run_backtest.py --exec-csv data/BTCUSDTUSDT_15m.csv --macro-csv data/BTCUSDTUSDT_1h.csv --out data/backtest_smoke.csv --max-rows 360 --warmup 80
```

## 注意

当前默认是 `dry_run`，不是实盘。实盘前必须完成交易所 API、沙盒测试、订单同步、风控参数、监控告警和长周期回测验证。

详细体检结论见：`reports/ENGINEERING_AUDIT.md`。
