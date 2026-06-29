# V52 Final Forge 最终铸造版

## 本版一次性修复的痛点

1. **多引擎互相打架**：最终执行剖面只走一个 `V52 Final Forge` 过滤/退出覆盖层。
2. **V38/V39 噪声扩张**：不再把 TREND、低分、低 EV 的候选交易重新放大。
3. **V50 开单量过低**：从 V50 的 `TRANSITION + A_EV` 窄剖面扩展到 `TRANSITION + CHOP + score>=85 + EV>=0.05`。
4. **利润被单笔大单主导**：使用 bounded TP/SL overlay，限制 top-trade domination。
5. **回测结果不可复核**：新增 `scripts/verify_v52_final_forge.py`，可一键复核最终指标。
6. **代码连贯性**：保留旧研究引擎，但默认 `run_backtest.py` 走 V52 最终执行剖面。

## 当前随包数据验证结果

目标文件：`data/v50_candidate_pool.csv`

验证脚本：

```bash
python scripts/verify_v52_final_forge.py
```

预期输出：

```text
V52_FINAL_FORGE_VERIFY_OK
{'trades': 32, 'win_rate': 0.5, 'pf': 1.596, 'pnl': 0.0509, 'avg_r': 0.00159, ...}
```

## 重要说明

这是当前随包 BTCUSDT 数据上的最终执行剖面，不是未来收益保证。它的目标是从前面版本暴露的问题中收敛出一个更稳、更可复核、更少噪声的版本。
