# V2 Scorecard Architecture — 评分引擎改造文档

## 一、现状诊断

### 当前数据流

```
OHLCV → smc.py(build_exec/macro_context) → ctx_builder.py → 
  smc_impulse_engine.py(4模块直接加) → scoring.py(转发) → 
    V565_gate + V9DecisionKernel
```

### V37 核心问题

| 问题 | 说明 |
|------|------|
| 分数膨胀 | raw_base(0~20) + smc(0~40) + sqz(0~30) + breakout(0~30) + bonus(0~10) = 0~130，虽 clamped 到 100 但模块间无归一化 |
| Bonus 喧宾夺主 | 方向一致+5、BOS/CHOCH+8、HTF对齐+9，bonus封顶10——低分信号靠bonus跳级，破坏了评分纯洁性 |
| SMC 权重固定 | zone 40% + mitigation 30% + alignment 30%，regime 不影响 SMC 权重 |
| Breakout 粗糙 | 4因子阶梯函数(30/50/70/90)，不是连续概率，breakout贡献0~30缺乏区分度 |
| 分级硬编码 | A+≥70+delta≥20 / A≥58+delta≥15 / B≥44 / C<44，与V9 threshold=35脱节 |
| 评测反馈缺失 | 评分层与EV层分离——评分不知道自己的信号最终EV是多少，无法自我校准 |

---

## 二、V2 三层架构

```
                    ┌─────────────────────────────┐
                    │  3. EV-Confidence Layer     │  ← 新增
                    │  根据历史回测校准评分        │
                    └──────────┬──────────────────┘
                               │
                    ┌──────────▼──────────────────┐
                    │  2. Quality Layer           │  ← 重写
                    │  结构质量 × 环境适配         │
                    │  输出 0~100（连续）          │
                    └──────────┬──────────────────┘
                               │
                    ┌──────────▼──────────────────┐
                    │  1. Base Layer              │  ← 精简
                    │  原始信号强度（动量+价格）   │
                    │  输出 0~40（不参与归一化）    │
                    └──────────┬──────────────────┘
                               │
                    ┌──────────▼──────────────────┐
                    │  V56.5 Gate + V9 Kernel     │  ← 已有，不改
                    │  消费最终 score (0~100)     │
                    └─────────────────────────────┘
```

### 第1层：Base Layer（精简动量层）

**职责**：提供原始信号强度基线，区分"有信号 vs 无信号"。

- 输入：`momentum`, `ema_20/50`, `plus_di/minus_di`, 仅方向相关字段
- **剔除**：bonus、SMC 结构、breakout 概率、HTF 对齐
- 输出：0~40（保持与旧版 raw_base 0~20 的兼容性，**实际空间 0~40**）
- 阈值：base < 12 → 评分直接封顶 35（不让低动量信号靠结构奖励跳级）

```
base_score = 
  price_position(0~12) + momentum_align(0~14) + dmi_confirm(0~14)
clamped to [0, 40]
if base_score < 12: final_score = min(final_score, 35)
```

### 第2层：Quality Layer（质量适配层）

**职责**：根据结构质量 + 环境适配，将 base_score 映射到 0~100。

**2a. Structure Quality Score (0~70)**

从 0~70 映射到整个 score range 的权重因子。

```
structure = 
  zone_quality(0~20) +            # OB/FVG 有效性 + 强度
  mitigation_quality(0~15) +      # 回测确认 + 实体吞噬
  sweep_confirm(0~15) +          # 流动性扫荡 + 方向匹配
  structure_alignment(0~10) +    # BOS/CHOCH + HTF 共识
  setup_direction(0~10)          # setuptype 方向匹配
clamped to [0, 70]
```

**2b. Environment Multiplier (0.6~1.4)**

```
env_mult = 
  regime_factor(0.8~1.2) +       # TREND=1.2, MUD=0.8
  vol_factor(0.9~1.1) +          # HIGH_VOL=0.9, NORMAL=1.0
  squeeze_factor(0.95~1.2)       # TIGHT=1.2(突破概率高), NONE=0.95
clamped to [0.6, 1.4]
```

**2c. 融合公式**

```
quality_score = structure * env_mult        # 0~70 * 0.6~1.4 = 0~98
final_score = base_score + quality_score    # 0~40 + 0~98 = 0~138
final_score = min(final_score, 100)         # clamped to 100
```

### 第3层：EV-Confidence Layer（新增）

**职责**：用历史回测结果校准评分，让评分知道"同样分数在历史中的实际 EV 如何"。

```
ev_calibrated_score = final_score + bias_correction(regime, score_bucket)

bias_correction = 
  从 ev_learner 获取 (regime, score_bucket) 的历史 EV 偏差
  score_bucket = LOW(<35) / MID(35~54) / HIGH(55~74) / VERY_HIGH(>=75)
  偏差正值 → 分数上调（模型低估）
  偏差负值 → 分数下调（模型高估）
  调整幅度封顶 ±8 分
```

**回测集成**：

```
每笔交易平仓后：
  ev_learner.record_trade(
    regime=..., 
    score_bucket=..., 
    won=..., 
    ev=predicted_ev, 
    realized_r=actual_r
  )
```

---

## 三、与 V56.5 Gate + V9 Kernel 的衔接

```
V2 Scorecard final_score (0~100)
  ↓
V56.5 Gate (min_score=55, 结构特权score>=41)
  ↓ passed
V9 Kernel (threshold=35)
  ↓ approved
开单
```

**重要**：V2 评分输出的分数范围应与旧版保持一致（0~100），这样 V56.5 和 V9 的阈值无需调整。

旧版实测分数分布（已验证）：
- 旧版 smc_impulse_score：42~46 分在真实数据上常见
- 旧版 42 分信号通过 V56.5 结构特权 + V9 → 开单
- V2 需确保同样市场条件下输出可比分数

---

## 四、文件结构

```
strategy/
  ├── smc_impulse_engine.py     # 旧版 → 删除或保留为 V1 兼容
  ├── scoring.py                 # 适配器 → 修改为调用 V2
  ├── v2_scorecard.py           # 【新增】V2 评分引擎
  └── v565_quality_gate.py      # 不变
decision/
  └── v9_decision_kernel.py     # 不变
```

### v2_scorecard.py 接口设计

```python
def v2_scorecard(ctx: dict, config: dict = None) -> dict:
    """
    V2 Scorecard Engine
    
    参数:
        ctx: 评分上下文（由 ctx_builder 填充，与旧版接口兼容）
        config: 可选覆盖参数
    
    返回:
        {
            "final_score": float,           # 0~100（与旧版兼容）
            "base_score": float,            # 0~40
            "quality_score": float,         # 0~70 原始结构分
            "env_mult": float,              # 环境乘数
            "ev_adjustment": float,         # EV校准调整值
            "structure_breakdown": {...},   # 结构各维度明细
            "regime": str,                  # 市场状态
            "breakdown": str,               # 可读评分明细
        }
    """
```

---

## 五、迁移计划

### Phase 1：代码构建（当前）
1. 创建 `v2_scorecard.py`（Base + Quality 层）
2. 修改 `scoring.py` 适配器（可选新旧切换）
3. 并行运行 V1 vs V2，对比分数分布

### Phase 2：验证
4. 回测同一历史区间，对比 V1 vs V2 分数分布
5. 检查分桶一致性（确保 V56.5/V9 阈值适用）
6. 修复任何分数漂移

### Phase 3：上线
7. V2 替换 V1
8. 删除旧版 `smc_impulse_engine.py` 中死代码
9. 监控线上分数分布，必要时微调参数

---

## 六、关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 三层架构 | Base → Quality → EV-Confidence | 分离"原始信号"和"结构质量"，EV 校准做最后一层微调 |
| 无 bonus | 所有奖励集成到 structure 权重中 | 消除 bonus 跳级问题 |
| quality 用乘数而非加数 | `quality_score = structure * env_mult` | 环境好坏放大/缩小结构质量，而非简单加减 |
| EV 校准 ≤ ±8 分 | 小幅度调整 | 防止校准过度主导评分 |
| 分数范围 0~100 | 与旧版一致 | V56.5/V9 阈值不变 |
| 并行运行 | V1 和 V2 同时输出分数但不切换 | 验证分数分布对齐后再上线 |
