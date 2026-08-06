# -*- coding: utf-8 -*-
"""V59.6.1 重构: 硬拒绝与软缩减分离，size_penalty 统一合成"""
import io
import sys

FILE = "strategy/v565_quality_gate.py"

with io.open(FILE, "r", encoding="utf-8") as f:
    src = f.read()

orig = src

# ---- 1. 文件头设计原则 ----
src = src.replace(
    "  - 硬拒绝 + 软缩减双轨并用",
    "  - 低质量分数（score < HARD_REJECT_SCORE）硬拒绝，直接 return\n"
    "  - 通过质量门后统一做风险调整（近阈值 + 流动性），合成 size_penalty",
)

# ---- 2. 门槛表 78 -> 75 ----
src = src.replace(
    "        # 低分时段（PF<1.0）：收紧\n"
    "        4: 78.0,    # hour=4 PF=0.98\n"
    "        6: 78.0,    # hour=6 PF=0.97\n"
    "        7: 78.0,    # hour=7 PF=0.99\n"
    "        16: 78.0,   # hour=16 PF=1.10\n"
    "        23: 78.0,   # hour=23 PF=0.93",
    "        # 低分时段（PF<1.0）：收紧（V59.6.1: 78→75 微调）\n"
    "        4: 75.0,    # hour=4 PF=0.98\n"
    "        6: 75.0,    # hour=6 PF=0.97\n"
    "        7: 75.0,    # hour=7 PF=0.99\n"
    "        16: 75.0,   # hour=16 PF=1.10\n"
    "        23: 75.0,   # hour=23 PF=0.93",
)

# mixed 表
src = src.replace(
    "        4: 78.0,\n"
    "        6: 78.0,\n"
    "        7: 78.0,\n"
    "        16: 78.0,\n"
    "        23: 78.0,\n"
    "        \"__default__\": 74.0,",
    "        4: 75.0,    # V59.6.1: 78→75\n"
    "        6: 75.0,    # V59.6.1: 78→75\n"
    "        7: 75.0,    # V59.6.1: 78→75\n"
    "        16: 75.0,   # V59.6.1: 78→75\n"
    "        23: 75.0,   # V59.6.1: 78→75\n"
    "        \"__default__\": 74.0,",
)

# range 表
src = src.replace(
    "        4: 78.0,\n"
    "        6: 78.0,\n"
    "        7: 78.0,\n"
    "        16: 78.0,\n"
    "        23: 78.0,\n"
    "        \"__default__\": 72.0,",
    "        4: 75.0,    # V59.6.1: 78→75\n"
    "        6: 75.0,    # V59.6.1: 78→75\n"
    "        7: 75.0,    # V59.6.1: 78→75\n"
    "        16: 75.0,   # V59.6.1: 78→75\n"
    "        23: 75.0,   # V59.6.1: 78→75\n"
    "        \"__default__\": 72.0,",
)

# ---- 3. 新增 HARD_REJECT_SCORE 常量 ----
src = src.replace(
    "MIN_MODEL_EV: float = -0.28\n\n\n# ============================================================\ndef _get_adaptive_min_score(",
    "MIN_MODEL_EV: float = -0.28\n\n\n# ============================================================\n"
    "# ⚙️ V59.6.1: 低分硬拒绝阈值（原 78 → 75 微调）\n"
    "# ============================================================\n"
    "HARD_REJECT_SCORE: float = 75.0\n\n\n"
    "# ============================================================\ndef _get_adaptive_min_score(",
)

# ---- 4. 替换 Step 3 逻辑 ----
src = src.replace(
    "    # ========================================================\n"
    "    # 3. 低分信号（score<80）额外检查\n"
    "    # ========================================================\n"
    "    # V59.3 修复: 取消软缩减通行——低质量信号不能通过\"减少仓位\"继续交易。\n"
    "    # 负EV就是负EV, 减半仓仍然亏钱。size_penalty 只能用于已通过质量门但\n"
    "    # 风险稍高的优质信号, 不能救活不合格信号。\n"
    "    if score < 78:\n"
    "        # 硬拒绝: 低分信号不再放行\n"
    "        meta[\"blocked\"] = True\n"
    "        meta[\"failed_checks\"].append(\"sub_grade_hard_reject\")\n"
    "        reasons.append(f\"SUB_GRADE_SCORE_{score:.1f}<78\")\n"
    "    elif score < 80:\n"
    "        # 78~80 区间: 通过但标记需注意（接近阈值, 仓位微缩减至90%）\n"
    "        meta[\"size_penalty\"] = min(meta.get(\"size_penalty\", 1.0), 0.90)\n"
    "        meta[\"passed_checks\"].append(\"near_threshold_pass\")\n"
    "    else:\n"
    "        # 高分信号（score>=80）：加分\n"
    "        meta[\"passed_checks\"].append(\"high_score_bonus\")",
    "    # ========================================================\n"
    "    # 3. 低质量分数硬拒绝（score < HARD_REJECT_SCORE）\n"
    "    # ========================================================\n"
    "    # V59.3 修复: 取消软缩减通行——低质量信号不能通过\"减少仓位\"继续交易。\n"
    "    # 负EV就是负EV, 减半仓仍然亏钱。size_penalty 只能用于已通过质量门但\n"
    "    # 风险稍高的优质信号, 不能救活不合格信号。\n"
    "    # V59.6.1+: 硬拒绝直接 return，不再进入 Step 4 风险调整流程。\n"
    "    if score < HARD_REJECT_SCORE:\n"
    "        meta[\"blocked\"] = True\n"
    "        meta[\"failed_checks\"].append(\"sub_grade_hard_reject\")\n"
    "        meta[\"size_penalty\"] = 0.0\n"
    "        return False, f\"SUB_GRADE_SCORE_{score:.1f}<{HARD_REJECT_SCORE:.0f}\", meta\n"
    "\n"
    "    # 3b. 通过质量门后的风险因子收集（统一用于 size_penalty 合成）\n"
    "    risk_penalties: Dict[str, float] = {}\n"
    "\n"
    "    # 近阈值风险（HARD_REJECT_SCORE <= score < 80 → 微降仓 10%）\n"
    "    if score < 80:\n"
    "        risk_penalties[\"near_threshold\"] = 0.10\n"
    "        meta[\"passed_checks\"].append(\"near_threshold_pass\")\n"
    "        meta[\"score_headroom\"] = round(score - min_score, 1)\n"
    "    else:\n"
    "        # 高分信号（score>=80）：加分\n"
    "        meta[\"passed_checks\"].append(\"high_score_bonus\")",
)

# ---- 5. Step 4 标题 ----
src = src.replace(
    "    # ========================================================\n"
    "    # 4. 流动性惩罚（硬惩罚——降 quality_score，不拒绝）\n"
    "    # ========================================================\n"
    "    liquidity_penalty: float = 0.0",
    "    # ========================================================\n"
    "    # 4. 流动性风险惩罚（通过质量门后的风险调整——不拒绝，只减仓）\n"
    "    # ========================================================\n"
    "    # V59.6.1+: 与 Step 3b 的近阈值风险统一合成 size_penalty\n"
    "    liquidity_penalty: float = 0.0",
)

# ---- 6. 流动性惩罚合成逻辑 ----
src = src.replace(
    "    # 记录流动性惩罚值，供 Engine 层使用\n"
    "    liquidity_penalty = min(liquidity_penalty, 0.90)  # 上限\n"
    "    meta[\"liquidity_penalty\"] = round(liquidity_penalty, 4)\n"
    "\n"
    "    # 应用惩罚：缩小 quality_score（不拒绝，只降分）\n"
    "    # quality_score 降到 < min_score 时会拒绝，但我们不改变 score\n"
    "    # 而是通过 size_penalty 降仓位\n"
    "    if liquidity_penalty > 0:\n"
    "        # 每个惩罚点对应 10% 仓位缩减\n"
    "        liq_size_cut = 1.0 - liquidity_penalty * 0.80\n"
    "        meta[\"size_penalty\"] = min(meta.get(\"size_penalty\", 1.0), liq_size_cut)",
    "    # 记录流动性惩罚值，供 Engine 层使用\n"
    "    liquidity_penalty = min(liquidity_penalty, 0.90)  # 上限\n"
    "    meta[\"liquidity_penalty\"] = round(liquidity_penalty, 4)\n"
    "\n"
    "    # 流动性风险 → 统一记入 risk_penalties（每个惩罚点对应 8% 仓位缩减）\n"
    "    if liquidity_penalty > 0:\n"
    "        risk_penalties[\"liquidity\"] = liquidity_penalty * 0.80\n"
    "\n"
    "    # ========================================================\n"
    "    # 4e. 合成 size_penalty（统一风险调整层）\n"
    "    # -------------------------------------------------------\n"
    "    # 通过质量门的信号，只在这里合并近阈值风险和流动性风险\n"
    "    # 最终仓位 = 基础仓位 × size_penalty（1.0 = 不减仓）\n"
    "    # ========================================================\n"
    "    _total_risk_penalty = min(sum(risk_penalties.values()), 0.85)  # 上限 85% 缩减\n"
    "    meta[\"size_penalty\"] = round(1.0 - _total_risk_penalty, 4)\n"
    "    meta[\"risk_penalties\"] = {k: round(v, 4) for k, v in risk_penalties.items()}",
)

if src == orig:
    print("ERROR: 没有任何替换发生，请检查原始字符串")
    sys.exit(1)

with io.open(FILE, "w", encoding="utf-8", newline="\n") as f:
    f.write(src)

print("OK: 替换完成")
