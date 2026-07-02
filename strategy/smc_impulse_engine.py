# -*- coding: utf-8 -*-
"""
SMC-Impulse Engine：唯一信号评分器（Single Source of Truth）

评分公式：
    final_score = raw_base + smc_contrib + sqz_contrib + breakout_contrib
    
    其中：
    - raw_base（0~20）：基础动量/价格位置分
    - smc_contrib（0~40）：SMC 结构质量贡献
    - sqz_contrib（0~30）：SQZMOM 动量压力贡献
    - breakout_contrib（0~30）：Breakout 多因子评分贡献

设计原则：
    ✅ Breakout = score gate（多因子累加，非 AND gate）
    ✅ Fallback signal path：breakout_score == 0 时允许微结构信号
    ✅ score = raw + contrib（不压缩、不归一化、不 clamp）
    ✅ 最小化硬编码阈值，用连续函数替代阶梯函数
    ❌ 不做 penalty / compression / normalization / clamp
    ❌ 不做 AND gate / hard filter
    ❌ 不做双重调参（dominance 和 edge amplifier 二选一）

用法：
    from strategy.smc_impulse_engine import smc_impulse_score
    result = smc_impulse_score(ctx)
    score = result["final_score"]  # 0~100 连续
"""

from __future__ import annotations
from typing import Any, Dict, List
import math

from strategy.probabilistic_breakout import breakout_probability

# ============================================================
# Dominance 平滑状态（全局）
# ============================================================
_DOMINANCE_HISTORY: List[str] = []
_DOMINANCE_HISTORY_MAXLEN = 5


def _smooth_dominance(raw_dominance: str) -> str:
    global _DOMINANCE_HISTORY
    _DOMINANCE_HISTORY.append(raw_dominance)
    if len(_DOMINANCE_HISTORY) > _DOMINANCE_HISTORY_MAXLEN:
        _DOMINANCE_HISTORY.pop(0)
    counts = {"momentum": 0, "structure": 0, "balanced": 0}
    for d in _DOMINANCE_HISTORY:
        if d in counts:
            counts[d] += 1
    total = len(_DOMINANCE_HISTORY)
    for dom_type, count in counts.items():
        if count / total >= 0.6:
            return dom_type
    if len(_DOMINANCE_HISTORY) >= 2:
        return _DOMINANCE_HISTORY[-2]
    return raw_dominance


def reset_dominance_history() -> None:
    global _DOMINANCE_HISTORY
    _DOMINANCE_HISTORY = []


from utils.safe import safe_float, safe_bool, safe_str


# ============================================================
# SMC 模块：结构质量评分（0~40）
# ============================================================
def _smc_score(ctx: Dict[str, Any]) -> float:
    """
    SMC 结构质量评分（0~40）
    
    三维度，每维 0~100，加权求和后映射到 0~40。
    使用连续函数替代阶梯函数，减少硬编码阈值。
    """
    # 维度 1：Zone Quality（权重 40%）
    # 连续评分：has_valid_zone(0/25) + liquidity_sweep(0/15) + ob_strength(0~10) + zone_near(0~8) + fvg_bonus(0~8)
    zone = 0.0
    if safe_bool(ctx.get("has_valid_zone", False)):
        zone += 25.0
    if safe_bool(ctx.get("liquidity_sweep", ctx.get("liquidity_sweep_confirmed", False))):
        zone += 15.0
    ob_strength = safe_float(ctx.get("ob_strength", 0.0))
    zone += min(10.0, ob_strength * 15.0)  # 连续：0~0.67 → 0~10
    zone_near = safe_float(ctx.get("zone_near_atr", 9.99))
    if zone_near < 1.5:
        zone += max(0.0, 8.0 * (1.0 - zone_near / 1.5))  # 连续：near=0→+8, near=1.5→0
    # 【修复20260701】FVG 失衡区加分：方向匹配的 FVG 额外 +5~8 分
    direction = safe_str(ctx.get("direction", "")).lower()
    bullish_fvg = ctx.get("bullish_fvg")
    bearish_fvg = ctx.get("bearish_fvg")
    if direction == "long" and bullish_fvg is not None and float(bullish_fvg) > 0:
        zone += 8.0
    elif direction == "short" and bearish_fvg is not None and float(bearish_fvg) > 0:
        zone += 8.0

    # V38: 兼容上游 SMC 质量特征。旧版只看 FVG/OB/sweep，
    # 在没有显式 zone 字段时会把大量真实结构误判为 SMC=0。
    # 这里把 smc_quality_100 映射成结构支撑分，但仍作为连续评分，不做硬拒绝。
    smc_quality_100 = safe_float(ctx.get("smc_quality_100", ctx.get("smc_quality_score", 0.0)), 0.0)
    if smc_quality_100 > 0:
        zone = max(zone, min(40.0, smc_quality_100 * 0.40))
    zone = min(zone, 40.0)

    # 维度 2：Mitigation Strength（权重 30%）
    mitigation = 0.0
    fill_ratio = safe_float(ctx.get("wick_fill_ratio", ctx.get("fill_ratio", 0.0)))
    mitigation += min(15.0, fill_ratio * 20.0)  # 连续：0~0.75 → 0~15
    mitigation_src = safe_str(ctx.get("mitigation_src", "NO_FVG_OB"))
    if mitigation_src != "NO_FVG_OB":
        mitigation += 10.0
    if safe_bool(ctx.get("retest_confirmed", False)):
        mitigation += 5.0
    body_pct = safe_float(ctx.get("body_pct", 0.0))
    mitigation += max(0.0, min(5.0, (body_pct - 0.3) * 40.0))  # 连续：0.3→0, 0.425→5
    mitigation = min(mitigation, 30.0)

    # 维度 3：Structure Alignment（权重 30%）
    alignment = 0.0
    direction = safe_str(ctx.get("direction", "")).lower()
    htf_direction = safe_str(ctx.get("htf_direction", "")).lower()
    if htf_direction and direction:
        if htf_direction == direction:
            alignment += 15.0
        else:
            alignment -= 5.0
    if safe_bool(ctx.get("sweep_direction_match", False)):
        alignment += 10.0
    if safe_bool(ctx.get("momentum_align", False)):
        alignment += 5.0
    if safe_bool(ctx.get("sqzmom_dmi_aligned", ctx.get("dmi_aligned", False))):
        alignment += 5.0
    regime = safe_str(ctx.get("regime", "mud"))
    trend_dir = safe_str(ctx.get("trend_direction", "None")).lower()
    if regime == "trend" and trend_dir == direction:
        alignment += 5.0
    alignment = min(alignment, 30.0)

    # 三维加权求和（0~40）
    score = zone * 0.4 + mitigation * 0.3 + alignment * 0.3
    return max(0.0, min(40.0, score))


# ============================================================
# SQZMOM 模块：动量压力评分（0~30）
# ============================================================
def _sqzmom_score(ctx: Dict[str, Any]) -> float:
    """
    SQZMOM 动量压力评分（0~30）
    
    输入：
    - sqzmom_score（0~44）：来自 _sqzmom_context_score 的原始分数
    - 映射到 0~30 分
    """
    sqzmom_raw = safe_float(ctx.get("sqzmom_score", 0.0), 0.0)
    score = (sqzmom_raw / 44.0) * 30.0
    return max(0.0, min(30.0, score))


# ============================================================
# Breakout 模块：概率评分（0~30，score gate 非 AND gate）
# ============================================================
def _breakout_score(ctx: Dict[str, Any]) -> float:
    """
    Breakout 概率评分（0~30）
    
    调用 Probabilistic Breakout Engine V1 获取 0~100 突破概率，
    然后映射到 0~30 分（保持与 smc_impulse_score 接口兼容）。
    """
    bp = breakout_probability(ctx)
    prob = bp["breakout_prob"]  # 0~100
    score = prob * (30.0 / 100.0)
    return max(0.0, min(30.0, score))


# ============================================================
# 基础动量/价格位置分（0~20）
# ============================================================
def _raw_base_score(ctx: Dict[str, Any]) -> float:
    """
    基础动量/价格位置分（0~20）
    
    这是"fallback signal path"的基础——即使 breakout_score == 0，
    只要有基础动量/价格位置分，系统就不会完全断流。
    """
    score = 0.0
    
    # 1. 价格位置（0~8）
    direction = safe_str(ctx.get("direction", "")).lower()
    close = safe_float(ctx.get("close", 0.0))
    ema20 = safe_float(ctx.get("ema_20", 0.0))
    ema50 = safe_float(ctx.get("ema_50", 0.0))
    
    if direction == "long":
        if close > ema20:
            score += 4.0
        if close > ema50:
            score += 4.0
    elif direction == "short":
        if close < ema20:
            score += 4.0
        if close < ema50:
            score += 4.0
    
    # 2. 动量方向（0~6）
    momentum = safe_float(ctx.get("momentum", 0.0))
    if direction == "long" and momentum > 0:
        score += 6.0
    elif direction == "short" and momentum < 0:
        score += 6.0
    
    # 3. DMI 方向（0~6）
    plus_di = safe_float(ctx.get("plus_di", 0.0))
    minus_di = safe_float(ctx.get("minus_di", 0.0))
    if direction == "long" and plus_di > minus_di:
        score += 6.0
    elif direction == "short" and minus_di > plus_di:
        score += 6.0
    
    return max(0.0, min(20.0, score))


# ============================================================
# 主入口：smc_impulse_score（唯一评分器）
# ============================================================
def smc_impulse_score(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    SMC + SQZMOM + Breakout 融合评分引擎（唯一信号源）
    
    评分流程：
    1. raw_base（0~20）：基础动量/价格位置分
    2. SMC 质量地板：smc_raw < 12 直接拒绝
    3. SQZMOM 确认器：sqz <= 25 时分数打 8 折
    4. Breakout score gate：breakout >= 10 通过
    5. Fallback signal path：breakout == 0 时允许微结构信号
    6. Dominance 加权：momentum/structure/balanced 动态调整权重
    7. final_score = raw_base + smc + sqz + breakout
    
    参数:
        ctx: 包含所有评分所需字段的上下文字典
    
    返回:
        {
            "final_score": float,       # 0~100 最终分数
            "smc": float,               # SMC 结构质量分（0~40）
            "sqzmom": float,            # SQZMOM 动量压力分（0~30）
            "breakout": float,          # Breakout 多因子评分（0~30）
            "raw_base": float,          # 基础动量/价格位置分（0~20）
            "regime": str,              # 当前市场状态
            "weights": dict,            # 动态权重明细
            "smc_passed": bool,         # SMC 地板是否通过
            "sqz_passed": bool,         # SQZMOM 确认是否通过
            "breakout_passed": bool,    # Breakout 是否通过阈值
            "fallback_active": bool,    # 是否启用了 fallback signal path
            "dominance": str,           # 主导因子
            "breakdown": str            # 可读的评分明细
        }
    
    用法:
        result = smc_impulse_score(ctx)
        score = result["final_score"]
        if score >= adaptive_min_score:
            execute_trade()
    """
    # 1. 计算各模块分数
    raw_base = _raw_base_score(ctx)
    smc_raw = _smc_score(ctx)
    sqz = _sqzmom_score(ctx)
    breakout = _breakout_score(ctx)

    # 2. SMC 质量地板降级为软标签。真正的第一道开关在
    # scorecard_system.evaluate_base_trigger(SMC + SQZMOM) 中完成。
    # 这里不再提前 return，避免评分层再次变成“一票否决”。
    smc_passed = smc_raw >= 8.0
    smc_soft_mult = 1.0 if smc_passed else 0.55

    # 3. SQZMOM 确认器
    sqz_passed = sqz > 13.0
    sqz_mult = 1.0 if sqz_passed else 0.9

    # 4. Breakout score gate
    breakout_threshold = 10.0
    breakout_passed = breakout >= breakout_threshold

    # 5. Fallback signal path
    fallback_active = (breakout == 0.0)
    breakout_contrib = 0.0 if fallback_active else breakout

    # ============================================================
    # 6. 主导因子（dominance）判断
    #    使用归一化分数比较：smc_raw(0~40)→0~100, sqz(0~30)→0~100
    # ============================================================
    smc_norm = smc_raw * 2.5
    sqz_norm = sqz * 3.333

    if sqz_norm > 55 and sqz_norm > smc_norm:
        raw_dominance = "momentum"
    elif smc_norm > 50 and smc_norm >= sqz_norm:
        raw_dominance = "structure"
    else:
        raw_dominance = "balanced"

    # 7. 对 dominance 做滑动平均平滑，避免震荡行情中反复跳变
    dominance = _smooth_dominance(raw_dominance)

    # 8. 动态 dominance 权重
    if dominance == "momentum":
        w_smc, w_sqz, w_brk = 0.45, 0.80, 0.40
    elif dominance == "structure":
        w_smc, w_sqz, w_brk = 0.80, 0.45, 0.40
    else:
        w_smc, w_sqz, w_brk = 0.55, 0.60, 0.45

    # 8. 融合评分（dominance 加权）
    # 8. 融合评分（dominance 加权）
    final_score = raw_base + (smc_raw * smc_soft_mult * w_smc) + (sqz * sqz_mult * w_sqz) + (breakout_contrib * w_brk)

        # ==================== 多空对齐 + 奖励 + 信号分层 ====================
    # 多空得分对齐（纠正方向不一致的问题）
    bull_score = (safe_float(ctx.get("smc_quality_score_bull", 0)) +
                  safe_float(ctx.get("sqzmom_bull_strength", 0)) +
                  safe_float(ctx.get("bullish_momentum", 0)))
    bear_score = (safe_float(ctx.get("smc_quality_score_bear", 0)) +
                  safe_float(ctx.get("sqzmom_bear_strength", 0)) +
                  safe_float(ctx.get("bearish_momentum", 0)))
    score_delta = bull_score - bear_score

    # 当多空分差明显时，强制修正方向
    ctx_direction = str(ctx.get("direction", "Neutral"))
    if score_delta > 18:
        aligned_direction = "Long"
    elif score_delta < -18:
        aligned_direction = "Short"
    else:
        aligned_direction = ctx_direction

    # 基础分数保持原有逻辑，不提高门槛。奖励加分项只奖励强信号，不惩罚弱信号。
    bonus = 0.0

    # 奖励0：方向一致性奖励（多空打分与开单方向一致才加分）
    if (aligned_direction == ctx_direction and
        ((aligned_direction == "Long" and score_delta > 5) or
         (aligned_direction == "Short" and score_delta < -5))):
        bonus += 5.0

    # 奖励1：强结构（BOS/CHOCH + Sweep + Retest）
    if (safe_bool(ctx.get("bos_confirmed", False)) or
        safe_bool(ctx.get("choch_confirmed", False))):
        bonus += 8.0

    if safe_bool(ctx.get("liquidity_sweep_confirmed", False)) and safe_bool(ctx.get("retest_confirmed", False)):
        bonus += 7.0

    # 奖励2：高品质 SMC
    smc_quality = safe_float(ctx.get("smc_quality_100", ctx.get("smc_quality_score", 0.0)))
    if smc_quality >= 65:
        bonus += 6.0
    elif smc_quality >= 50:
        bonus += 3.0

    # 奖励3：多时间框架收敛
    htf_align = safe_bool(ctx.get("htf_aligned", False)) or abs(safe_float(ctx.get("htf_direction_strength", 0))) > 0.7
    if htf_align:
        bonus += 9.0

    # bonus 封顶 10 分，防止奖励项喧宾夺主
    bonus = min(bonus, 10.0)
    final_score += bonus

    # ==================== 信号分级 ====================
    # 用 abs(score_delta) 作为方向确定性，参与 A+/A 分级
    if final_score >= 70 and abs(score_delta) >= 20:
        signal_tier = "A+"     # 顶级信号（方向确定性高）
        position_multiplier = 1.8
        confidence_label = "Very High"
    elif final_score >= 58 and abs(score_delta) >= 15:
        signal_tier = "A"      # 高质量（方向较为确定）
        position_multiplier = 1.5
        confidence_label = "High"
    elif final_score >= 44:
        signal_tier = "B"      # 标准信号
        position_multiplier = 1.0
        confidence_label = "Medium"
    else:
        signal_tier = "C"
        position_multiplier = 0.6
        confidence_label = "Low"
    # 9. 构建可读的 breakdown
    sqz_note = "SQZ_CONFIRMED" if sqz_passed else f"SQZ_UNCONFIRMED_x{sqz_mult}"
    brk_note = f"BRK_PASSED({breakout:.1f}>={breakout_threshold})" if breakout_passed else f"BRK_LOW({breakout:.1f}<{breakout_threshold})"
    fb_note = " | FALLBACK_ACTIVE" if fallback_active else ""
    dom_note = f" | DOM={dominance}(w_smc={w_smc},w_sqz={w_sqz},w_brk={w_brk})"
    tier_note = f" | TIER={signal_tier}(bonus={bonus:.1f},mult={position_multiplier})"
    breakdown = (
        f"FINAL={final_score:.1f} | "
        f"RAW={raw_base:.1f} | "
        f"SMC={smc_raw:.1f}x{smc_soft_mult:.2f}={smc_raw*smc_soft_mult:.1f} | "
        f"SQZ={sqz:.1f}x{sqz_mult:.1f}={sqz*sqz_mult:.1f} | "
        f"BRK={breakout:.1f} | "
        f"{sqz_note} | {brk_note}{fb_note}{dom_note}{tier_note}"
    )

    return {
        "final_score": round(final_score, 2),
        "signal_tier": signal_tier,
        "position_multiplier": position_multiplier,
        "confidence_label": confidence_label,
        "bonus": round(bonus, 2),
        "smc": round(smc_raw, 2),
        "sqzmom": round(sqz, 2),
        "breakout": round(breakout, 2),
        "raw_base": round(raw_base, 2),
        "regime": safe_str(ctx.get("regime", "trend")),
        "weights": {"smc": smc_raw * smc_soft_mult * w_smc, "sqzmom": sqz * sqz_mult * w_sqz, "breakout": breakout_contrib * w_brk},
        "smc_passed": bool(smc_passed),
        "sqz_passed": sqz_passed,
        "breakout_passed": breakout_passed,
        "fallback_active": fallback_active,
        "dominance": dominance,
        "breakdown": breakdown,
        "score_delta": round(score_delta, 2),
        "aligned_direction": aligned_direction,
        "bull_score": round(bull_score, 2),
        "bear_score": round(bear_score, 2),
    }
