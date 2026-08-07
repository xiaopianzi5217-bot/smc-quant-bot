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
  - 低质量分数（score < HARD_REJECT_SCORE）硬拒绝，直接 return
  - 通过质量门后统一做风险调整（近阈值 + 流动性），合成 size_penalty

用法：
  from strategy.v565_quality_gate import v565_quality_gate
  passed, reason, meta = v565_quality_gate(row, config)
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from analytics.reject_analytics import reject_analytics
from analytics.trade_funnel import trade_funnel  # V59.7 漏斗统计


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
        # 低分时段（PF<1.0）：收紧（V59.6.1: 78→75 微调）
        4: 75.0,    # hour=4 PF=0.98
        6: 75.0,    # hour=6 PF=0.97
        7: 75.0,    # hour=7 PF=0.99
        16: 75.0,   # hour=16 PF=1.10
        23: 75.0,   # hour=23 PF=0.93
        # 默认
        "__default__": 72.0,
    },
    "mixed": {
        0: 66.0,
        2: 66.0,
        3: 66.0,
        17: 66.0,
        21: 66.0,
        4: 75.0,    # V59.6.1: 78→75
        6: 75.0,    # V59.6.1: 78→75
        7: 75.0,    # V59.6.1: 78→75
        16: 75.0,   # V59.6.1: 78→75
        23: 75.0,   # V59.6.1: 78→75
        "__default__": 74.0,
    },
    "range": {
        0: 66.0,
        2: 66.0,
        3: 66.0,
        17: 66.0,
        21: 66.0,
        4: 75.0,    # V59.6.1: 78→75
        6: 75.0,    # V59.6.1: 78→75
        7: 75.0,    # V59.6.1: 78→75
        16: 75.0,   # V59.6.1: 78→75
        23: 75.0,   # V59.6.1: 78→75
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
MIN_MODEL_EV: float = -0.28


# ============================================================

# ⚙️ 低分硬拒绝阈值
# 注意：实盘 V56 引擎 score 量级为 30~55（非 0-100），
# 实测 trade_journal 最高 54.3、主流 34~54。
# HARD_REJECT_SCORE=25 用于排除最低质量记录，但不会误杀正常信号。
# ============================================================
HARD_REJECT_SCORE: float = 12.0


# ============================================================
def _get_adaptive_min_score(
    regime: str,
    hour: int,
    score_table: Optional[Dict[str, Dict[int, float]]] = None,
    fallback: Optional[float] = None,
) -> float:
    """获取动态 score 门槛。

    如果传入了 fallback（来自 config.min_score），
    且动态表查出来的值 > fallback，则优先用 fallback（让配置覆盖硬编码表）。
    """
    table = score_table or DEFAULT_REGIME_HOUR_MIN_SCORE
    regime_lower = regime.lower().strip()
    rt = table.get(regime_lower, table.get("mixed", {}))
    dynamic_val = float(rt.get(int(hour), rt.get("__default__", 72.0)))

    # V59.3 修复: 动态门槛优先。配置 min_score 只能作为下限提高硬性要求,
    # 不能再把 78 拉低到 55 让 MUD/RANGE 垃圾信号通过。
    if fallback is not None:
        return max(dynamic_val, fallback)

    return dynamic_val


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
    setup_type_str = str(row.get("setup_type", "")).upper()
        # ========================================================
    # 0. Structure Enhancement（高分结构信号）⚡ 优先级最高
    # - 高质量结构信号可作为"加分标记"（meta['override']=True）
    # - 但不再直接通过：仍需通过环境过滤（regime/ADX）和后续质量门检查
    # ========================================================
    #
    # 0a. 流动性惩罚预估算（用于阻止 Override 绕过流动风险）
    # -------------------------------------------------------
    # 如果估算惩罚 >0.4，则禁用 Override（避免顶部追单）
    # 估算逻辑与 Step 4 流动性的惩罚一致
    _liq_penalty_estimate: float = 0.0
    direction = str(row.get("direction", ""))
    if row.get("is_bsl_swept", False):
        _liq_penalty_estimate += 0.25
    if row.get("is_ssl_swept", False):
        _liq_penalty_estimate += 0.25
    sweep_count = int(row.get("sweep_count_20", 0))
    if sweep_count >= 4:
        _liq_penalty_estimate += 0.25
    elif sweep_count >= 2:
        _liq_penalty_estimate += 0.15
    bsl_dist = float(row.get("bsl_dist_atr", 99.0))
    ssl_dist = float(row.get("ssl_dist_atr", 99.0))
    if direction == "Long":
        if ssl_dist < 0.0:
            _liq_penalty_estimate += 0.20
    elif direction == "Short":
        if bsl_dist < 0.0:
            _liq_penalty_estimate += 0.20
    ob_remaining = float(row.get("ob_remaining", 1.0))
    if ob_remaining < 0.2 and _liq_penalty_estimate > 0:
        _liq_penalty_estimate += 0.10
    _liq_penalty_estimate = min(_liq_penalty_estimate, 0.90)

    # ⚡ 流动性惩罚过高时禁用 Override（避免顶部追单）
    _override_disabled = _liq_penalty_estimate > 0.4

    strong_structure = (
        float(row.get("mitigation_strength", 0)) > 0.35 or
        bool(row.get("liquidity_sweep_confirmed", False)) or
        bool(row.get("is_bsl_swept", False)) or
        bool(row.get("is_ssl_swept", False)) or
        "OB" in setup_type_str or
        "FVG" in setup_type_str or
        "LIQUIDITY" in setup_type_str or
        "CHOCH" in setup_type_str or
        bool(row.get("has_choch", False)) or
        bool(row.get("has_bot_div", False)) or
        bool(row.get("has_top_div", False))
    )

    # V59.3 修复: STRUCTURE_OVERRIDE 降权——结构信号不能绕过环境过滤。
    # 之前：OB/FVG/CHOCH + score>=41 + EV>-0.5 就直接通过,
    # 导致大量 RANGE/低ADX 假突破成交。现在需要同时满足:
    #   - score >= 70
    #   - model_ev >= 0.30
    #   - regime 不是 range/mud/chop/sideways
    #   - ADX >= 18
    if strong_structure and score >= 70 and model_ev >= 0.30 and not _override_disabled:
        # 环境过滤: RANGE/MUD 状态时禁止 override 通行（避免假突破）
        if regime in ("range", "mud", "chop", "sideways", "ranging"):
            meta["blocked"] = True
            meta["reason"] = "STRUCTURE_OVERRIDE_BAD_REGIME"
            meta["failed_checks"].append("override_regime_blocked")
            # V59.7 RejectAnalytics 记录
            reject_analytics.record(
                symbol=row.get("symbol", ""),
                signal_id=row.get("signal_id", ""),
                stage="REGIME",
                reason="STRUCTURE_OVERRIDE_BAD_REGIME",
                score=score,
                confidence=row.get("confidence"),
                regime=regime,
                extra={"setup_type": setup_type_str, "adx": round(adx, 1), "model_ev": round(model_ev, 4)},
            )
            return False, "STRUCTURE_OVERRIDE_BAD_REGIME", meta

        # 趋势强度过滤: ADX < 18 时没有明确趋势, 结构信号多为假突破
        if adx < 18:
            meta["blocked"] = True
            meta["reason"] = "STRUCTURE_OVERRIDE_LOW_ADX"
            meta["failed_checks"].append("override_low_adx")
            # V59.7 RejectAnalytics 记录
            reject_analytics.record(
                symbol=row.get("symbol", ""),
                signal_id=row.get("signal_id", ""),
                stage="REGIME",
                reason="STRUCTURE_OVERRIDE_LOW_ADX",
                score=score,
                confidence=row.get("confidence"),
                regime=regime,
                extra={"setup_type": setup_type_str, "adx": round(adx, 1), "model_ev": round(model_ev, 4)},
            )
            return False, "STRUCTURE_OVERRIDE_LOW_ADX", meta



        meta["override"] = True
        meta["size_mult"] = 0.96
        meta["reason"] = "Optimized High Structure"
        meta["liquidity_penalty_estimate"] = round(_liq_penalty_estimate, 4)
        meta["passed_checks"].append("structure_override")
        # V59.6: 不再直接 return —— override 仅作为加分标记，
        # 继续执行后续 gate（model_ev 地板、动态分数门槛、低分硬拒绝、流动性检查）

    # ========================================================
    # 1. model_ev 硬地板
    # ========================================================
    ev_min = float(cfg.get("min_model_ev", MIN_MODEL_EV))
    if model_ev < ev_min:
        reasons.append(f"MODEL_EV_TOO_LOW_{model_ev:.4f}<{ev_min:.2f}")
        meta["failed_checks"].append("model_ev")
        # 【P0 20260730】RejectAnalytics 记录
        reject_analytics.record(
            symbol=row.get("symbol", ""),
            signal_id=row.get("signal_id", ""),
            stage="EV",
            reason="LOW_EV",
            score=score,
            confidence=row.get("confidence"),
            regime=regime,
            extra={"model_ev": round(model_ev, 4), "ev_min": round(ev_min, 4)},
        )
    else:
        meta["passed_checks"].append("model_ev")

    # ========================================================
    # 2. 动态分数门槛
    # ========================================================
    # config.min_score 作为 fallback：如果动态表的值比它高，优先用 config 值
        _config_min_score = cfg.get("min_score")
    _config_min_score_f = float(_config_min_score) if _config_min_score is not None else None
    min_score = _get_adaptive_min_score(regime, hour, cfg.get("regime_hour_min_score"), fallback=_config_min_score_f)
    # V59.5: 记录实际门槛供 gate_snapshot 使用（事后复盘: 哪个条件导致亏损）
    meta["min_score_required"] = round(min_score, 1)
    meta.setdefault("override", False)  # 默认非 override；Step 0 已设则保留
    if score < min_score:
        reasons.append(f"SCORE_LOW_{score:.1f}<{min_score:.0f}_REGIME={regime}_HOUR={hour}")
        meta["failed_checks"].append("score")
        # 【P0 20260730】RejectAnalytics 记录
        reject_analytics.record(
            symbol=row.get("symbol", ""),
            signal_id=row.get("signal_id", ""),
            stage="SCORE",
            reason="LOW_SCORE",
            score=score,
            confidence=row.get("confidence"),
            regime=regime,
            extra={"min_score": round(min_score, 1), "hour": hour},
        )
    else:
        meta["passed_checks"].append("score")

    # ========================================================
    # 3. 低质量分数硬拒绝（score < HARD_REJECT_SCORE）
    # ========================================================
    # V59.3 修复: 取消软缩减通行——低质量信号不能通过"减少仓位"继续交易。
    # 负EV就是负EV, 减半仓仍然亏钱。size_penalty 只能用于已通过质量门但
    # 风险稍高的优质信号, 不能救活不合格信号。
    # V59.6.1+: 硬拒绝直接 return，不再进入 Step 4 风险调整流程。
    if score < HARD_REJECT_SCORE:
        meta["blocked"] = True
        meta["failed_checks"].append("sub_grade_hard_reject")
        meta["size_penalty"] = 0.0
        # V59.7 RejectAnalytics 记录
        reject_analytics.record(
            symbol=row.get("symbol", ""),
            signal_id=row.get("signal_id", ""),
            stage="SCORE",
            reason="SUB_GRADE_HARD_REJECT",
            score=score,
            confidence=row.get("confidence"),
            regime=regime,
            extra={"hard_reject_score": HARD_REJECT_SCORE, "hour": hour},
        )
        return False, f"SUB_GRADE_SCORE_{score:.1f}<{HARD_REJECT_SCORE:.0f}", meta

    # 3b. 通过质量门后的风险因子收集（统一用于 size_penalty 合成）
    risk_penalties: Dict[str, float] = {}

    # 近阈值风险（HARD_REJECT_SCORE <= score < 80 → 微降仓 10%）
    if score < 80:
        risk_penalties["near_threshold"] = 0.10
        meta["passed_checks"].append("near_threshold_pass")
        meta["score_headroom"] = round(score - min_score, 1)
    else:
        # 高分信号（score>=80）：加分
        meta["passed_checks"].append("high_score_bonus")

    # ========================================================
    # 4. 流动性风险惩罚（通过质量门后的风险调整——不拒绝，只减仓）
    # ========================================================
    # V59.6.1+: 与 Step 3b 的近阈值风险统一合成 size_penalty
    liquidity_penalty: float = 0.0
    direction = str(row.get("direction", ""))
    setup_type = str(row.get("setup_type", "")).upper()

    # 4a. 已完成的流动性扫取
    if row.get("is_bsl_swept", False):  # 买方流动性已被扫
        liquidity_penalty += 0.25
        meta["failed_checks"].append("liquidity_bsl_exhausted")
    if row.get("is_ssl_swept", False):  # 卖方流动性已被扫
        liquidity_penalty += 0.25
        meta["failed_checks"].append("liquidity_ssl_exhausted")

    # 4b. 多次扫流动性（sweep_count >= 2 → 反复震荡，流动性已消耗）
    sweep_count = int(row.get("sweep_count_20", 0))
    if sweep_count >= 4:
        liquidity_penalty += 0.25
        meta["failed_checks"].append(f"liquidity_sweep_excessive_{sweep_count}")
    elif sweep_count >= 2:
        liquidity_penalty += 0.15
        meta["failed_checks"].append(f"liquidity_repeated_sweep_{sweep_count}")

    # 4c. BSL/SSL 距离过近（流动性已被价格逼近或突破）
    bsl_dist = float(row.get("bsl_dist_atr", 99.0))
    ssl_dist = float(row.get("ssl_dist_atr", 99.0))
    if direction == "Long":
        if ssl_dist < 0.0:  # 下方流动性已被扫穿（价格已跌破 ll20）
            liquidity_penalty += 0.20
            meta["failed_checks"].append("ssl_breached")
        elif ssl_dist < 0.5:  # 离下方流动性很近
            liquidity_penalty += 0.10
            meta["failed_checks"].append("ssl_near_breach")
        if bsl_dist < 0.5:  # 上方流动性也很近（上下夹击）
            liquidity_penalty += 0.10
            meta["failed_checks"].append("bsl_near")
    elif direction == "Short":
        if bsl_dist < 0.0:  # 上方流动性已被扫穿（价格已突破 hh20）
            liquidity_penalty += 0.20
            meta["failed_checks"].append("bsl_breached")
        elif bsl_dist < 0.5:
            liquidity_penalty += 0.10
            meta["failed_checks"].append("bsl_near_breach")
        if ssl_dist < 0.5:
            liquidity_penalty += 0.10
            meta["failed_checks"].append("ssl_near")

    # 4d. OB 剩余强度过低（价格已远离合理区间）
    ob_remaining = float(row.get("ob_remaining", 1.0))
    if ob_remaining < 0.2 and liquidity_penalty > 0:
        liquidity_penalty += 0.10
        meta["failed_checks"].append("ob_depleted")

    # 记录流动性惩罚值，供 Engine 层使用
    liquidity_penalty = min(liquidity_penalty, 0.90)  # 上限
    meta["liquidity_penalty"] = round(liquidity_penalty, 4)

    # 流动性风险 → 统一记入 risk_penalties（每个惩罚点对应 8% 仓位缩减）
    if liquidity_penalty > 0:
        risk_penalties["liquidity"] = liquidity_penalty * 0.80

    # ========================================================
    # 4e. 合成 size_penalty（统一风险调整层）
    # -------------------------------------------------------
    # 通过质量门的信号，只在这里合并近阈值风险和流动性风险
    # 最终仓位 = 基础仓位 × size_penalty（1.0 = 不减仓）
    # ========================================================
    _total_risk_penalty = min(sum(risk_penalties.values()), 0.85)  # 上限 85% 缩减
    meta["size_penalty"] = round(1.0 - _total_risk_penalty, 4)
    meta["risk_penalties"] = {k: round(v, 4) for k, v in risk_penalties.items()}

    # 最终决策
    passed = len(reasons) == 0

    if passed:
        # V59.7 漏斗统计：质量门通过
        trade_funnel.add("gate_pass")
        if meta.get("override"):
            return True, "QUALITY_GATE_V565_PASSED_STRUCTURE", meta
        return True, "QUALITY_GATE_V565_PASSED", meta
    else:
        # blocked 的硬拒绝不参与软缩减
        if meta.get("blocked"):
            meta["size_penalty"] = 0.0
        return False, "|".join(reasons[:3]), meta