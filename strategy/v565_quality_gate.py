# -*- coding: utf-8 -*-
"""
V56.5 原生高质量入场过滤器

核心逻辑：
  1) 低分信号（score<80）用更严格的 RR/小时过滤
  2) 高分信号（score>=80）放宽条件但不放松
  3) 按 regime x hour 动态调整 min_score

设计原则：
  - 只使用 V56.5 候选信号已有字段（score, hour, regime, setup_type, model_ev）
  - 不引入旧系统依赖（smc_quality, ob_valid, dmi 等）
  - 硬拒绝 + 软缩减双轨并用

用法：
  from strategy.v565_quality_gate import v565_quality_gate
  passed, reason, meta = v565_quality_gate(row, config)
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


# ============================================================
# ⚙️ 动态分数门槛表（按 regime × hour）
# -------------------------------------------------------
# 数据来源：V56.5 回测结果（349 笔交易）
# - 小时 0/2/3/17/21：高分时段，可放宽 min_score
# - 小时 4/6/7/16/23：低分时段，需收紧
# - 其余小时：中性
# ============================================================
DEFAULT_REGIME_HOUR_MIN_SCORE: Dict[str, Dict[int, float]] = {
    "trend": {
        # 高分时段（PF>1.5）：降低门槛
        0: 66.0,    # hour=0 PF=2.63 -> 放松
        2: 66.0,    # hour=2 PF=1.98 -> 放松
        3: 66.0,    # hour=3 PF=3.73 -> 放松
        17: 66.0,   # hour=17 PF=1.84 -> 放松
        21: 66.0,   # hour=21 PF=2.51 -> 放松
        # 低分时段（PF<1.0）：收紧
        4: 78.0,    # hour=4 PF=0.98
        6: 78.0,    # hour=6 PF=0.97
        7: 78.0,    # hour=7 PF=0.99
        16: 78.0,   # hour=16 PF=1.10
        23: 78.0,   # hour=23 PF=0.93
        # 默认
        "__default__": 72.0,
    },
    "mixed": {
        0: 66.0,
        2: 66.0,
        3: 66.0,
        17: 66.0,
        21: 66.0,
        4: 78.0,
        6: 78.0,
        7: 78.0,
        16: 78.0,
        23: 78.0,
        "__default__": 74.0,
    },
    "range": {
        0: 66.0,
        2: 66.0,
        3: 66.0,
        17: 66.0,
        21: 66.0,
        4: 78.0,
        6: 78.0,
        7: 78.0,
        16: 78.0,
        23: 78.0,
        "__default__": 72.0,
    },
}


# ============================================================
# ⚙️ 小时-信号质量表：完全禁止的小时
# ============================================================
BLOCKED_HOURS: Tuple[int, ...] = ()


# ============================================================
# ⚙️ model_ev 最低要求（hard floor）
# ============================================================
MIN_MODEL_EV: float = -0.05


# ============================================================
def _get_adaptive_min_score(
    regime: str,
    hour: int,
    score_table: Optional[Dict[str, Dict[int, float]]] = None,
) -> float:
    """获取动态 score 门槛。"""
    table = score_table or DEFAULT_REGIME_HOUR_MIN_SCORE
    regime_lower = regime.lower().strip()
    rt = table.get(regime_lower, table.get("mixed", {}))
    return float(rt.get(int(hour), rt.get("__default__", 72.0)))


def v565_quality_gate(
    row: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    V56.5 高质量入场过滤器。

    参数:
        row: 候选信号字典（必须含 score, hour, regime, model_ev, setup_type）
        config: 可选覆盖参数

    返回:
        (passed, reason, metadata)
    """
    cfg = config or {}
    reasons: list[str] = []
    meta: Dict[str, Any] = {
        "passed_checks": [],
        "failed_checks": [],
        "blocked": False,
        "size_penalty": 1.0,
    }

    score = float(row.get("score", 0.0))
    hour = int(row.get("hour", -1))
    regime = str(row.get("regime", "mixed")).lower().strip()
    model_ev = float(row.get("model_ev", -999.0))

    # ========================================================
    # 1. model_ev 硬地板
    # ========================================================
    ev_min = float(cfg.get("min_model_ev", MIN_MODEL_EV))
    if model_ev < ev_min:
        reasons.append(f"MODEL_EV_TOO_LOW_{model_ev:.4f}<{ev_min:.2f}")
        meta["failed_checks"].append("model_ev")
    else:
        meta["passed_checks"].append("model_ev")

    # ========================================================
    # 2. 动态分数门槛
    # ========================================================
    min_score = _get_adaptive_min_score(regime, hour, cfg.get("regime_hour_min_score"))
    if score < min_score:
        reasons.append(f"SCORE_LOW_{score:.1f}<{min_score:.0f}_REGIME={regime}_HOUR={hour}")
        meta["failed_checks"].append("score")
    else:
        meta["passed_checks"].append("score")

    # ========================================================
    # 3. 低分信号（score<80）额外检查
    # ========================================================
    if score < 80:
        # 3a. 低分 + 不利小时 → 硬拒绝
        hard_hours = set(cfg.get("hard_block_hours", {4, 6, 7, 23}))
        if hour in hard_hours:
            reasons.append(f"HOUR_BLOCKED_{hour}_LOW_SCORE")
            meta["failed_checks"].append("hour_blocked")
            meta["blocked"] = True

        # 3b. 低分 + trend_strength 极端
        trend_strength = float(row.get("trend_strength", 0.0))
        if abs(trend_strength) > 1.8:
            reasons.append(f"TREND_EXTREME_{trend_strength:.2f}_LOW_SCORE")
            meta["failed_checks"].append("trend_extreme")
    else:
        # 高分信号（score>=80）：加分
        meta["passed_checks"].append("high_score_bonus")

    # ========================================================
    # 4. 分数软缩减（不拒绝但降仓位）
    # ========================================================
    size_penalty = 1.0
    if score < 75:
        size_penalty = 0.60
        meta["size_penalty"] = 0.60
    elif score < 78:
        size_penalty = 0.80
        meta["size_penalty"] = 0.80

    # 最终决策
    passed = len(reasons) == 0

    if passed:
        return True, "QUALITY_GATE_V565_PASSED", meta
    else:
        # blocked 的硬拒绝不参与软缩减
        if meta.get("blocked"):
            meta["size_penalty"] = 0.0
        return False, "|".join(reasons[:3]), meta
