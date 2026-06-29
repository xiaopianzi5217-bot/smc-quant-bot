# Strategy 层 7 个硬过滤补丁说明

把压缩包里的文件复制到项目根目录对应路径即可。

## 需要新增/替换的位置

```text
新增/替换：strategy/trade_filters.py
替换：decision/v9_decision_kernel.py
替换：runner/v11_institutional_runner.py
替换：runner/v7_live_runner.py
替换：config/v11_full_config.json
替换：config/v7_live_config.json
新增：reports/STRATEGY_FILTER_PATCH_README.md
```

## 现在 Strategy 层包含 7 个过滤

1. 多周期趋势过滤
2. RR 最小值过滤
3. ATR 波动过滤
4. 同币种同方向 cooldown
5. 成交量确认
6. 距离 BSL/SSL/OB/FVG 过滤
7. 重复结构去重

## 新增 5–7 的作用

### 5. 成交量确认

防止“没量硬冲”的假突破、假反转。

当前逻辑：

```text
volume_ratio >= 1.5：强确认
volume_ratio >= 1.15：普通确认
volume_ratio 不足但有扫流动性/OB：允许放行
否则拒绝开单
```

好处：减少低流动性、无参与度的信号。

### 6. 距离 BSL/SSL/OB/FVG 过滤

防止价格已经离有效结构太远才追单，也防止刚开单就贴近反向流动性/OB/FVG。

当前逻辑：

```text
必须靠近顺方向结构：<= 2 ATR
距离反向结构不能太近：>= 0.7 ATR
```

好处：提高入场位置质量，减少追高追空和刚入场就撞阻力/支撑。

### 7. 重复结构去重

同一币种、同一方向、同一组 BSL/SSL/OB/FVG/pivot 结构，在 12 根 K 内只允许触发一次。

好处：减少重复信号、重复推送、重复开单。

## 当前建议参数

```text
min_rr = 2.0
cooldown bars = 8
structure_dedupe bars = 12
min_volume_ratio = 1.15
strong_volume_ratio = 1.5
max_entry_to_support_atr = 2.0
max_entry_to_resistance_atr = 2.0
min_distance_to_opposite_atr = 0.7
```

## 完整开单链路

```text
指标计算
→ SMC/SQZMOM 打分
→ V6/V9 初审
→ Strategy 7 个硬过滤
→ GlobalRiskGuard 全局风控
→ Execution 执行/持仓管理
```
