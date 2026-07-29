"""V2 Scorecard Engine — 三层评分架构

数据流:
  OHLCV → smc.py(ctx_builder) → v2_scorecard.py → V565 gate → V9 kernel
                                      ↑
                              intelligence_engine.py (EV校准)

架构:
  Layer 1: Base Layer (0~40)     — 纯动量/价格位置，无结构奖励
  Layer 2: Quality Layer (0~70×环境系数) — 结构质量×环境适配
  Layer 3: EV-Confidence Layer   — 历史回测偏差校准 (±8分)

设计原则:
  ✅ 三层分离，每层有明确上限
  ✅ 无 bonus 跳级 — 所有结构奖励在 quality 层内部处理
  ✅ 环境用乘数而非加数 — regime/vol/squeeze 放大/缩小结构质量
  ✅ EV 校准 ≤ ±8 分 — 小幅度调整，防止过度主导
  ❌ 不引入硬编码阈值（所有阈值可从 config 覆盖）
  ❌ 不处理 gate/filter/reject — 只输出分数

用法:
  from strategy.v2_scorecard import v2_scorecard
  result = v2_scorecard(ctx)
  score = result["final_score"]  # 0~100
"""

from __future__ import annotations
from typing import Any, Dict, Optional, Tuple
import math

from utils.safe import safe_float, safe_bool, safe_str


# ============================================================
# 第1层：Base Layer（纯动量/价格位置，无结构奖励）
# ============================================================
def _base_layer(ctx: Dict[str, Any]) -> float:
    """
    原始信号强度基线（0~40）
    
    仅使用方向相关的动量/价格位置/DMI 字段，
    不引入任何 SMC 结构、breakout 概率、HTF 对齐。
    
    子项:
      - price_position (0~12): 价格在 EMA 网络中的位置
      - momentum_align (0~10): 动量方向与信号方向一致性
      - dmi_confirm   (0~10): DMI 方向确认
    """
    direction = safe_str(ctx.get("direction", "")).lower()
    if direction not in ("long", "short"):
        return 0.0

    score = 0.0

    # ── 1a. 价格位置（0~12）─────────────────────
    close = safe_float(ctx.get("close", 0.0))
    ema_20 = safe_float(ctx.get("ema_20", 0.0))
    ema_50 = safe_float(ctx.get("ema_50", 0.0))
    high_20 = safe_float(ctx.get("high_20", 0.0))
    low_20 = safe_float(ctx.get("low_20", 0.0))

    if direction == "long":
        if ema_20 > 0 and close > ema_20:
            score += 4.0
        if ema_50 > 0 and close > ema_50:
            score += 4.0
        if low_20 > 0 and high_20 > low_20:
            pos_ratio = (close - low_20) / (high_20 - low_20)
            if pos_ratio < 0.35:
                score += 4.0
    else:
        if ema_20 > 0 and close < ema_20:
            score += 4.0
        if ema_50 > 0 and close < ema_50:
            score += 4.0
        if low_20 > 0 and high_20 > low_20:
            pos_ratio = (close - low_20) / (high_20 - low_20)
            if pos_ratio > 0.65:
                score += 4.0

    # ── 1b. 动量方向对齐（0~10）──────────────────
    momentum = safe_float(ctx.get("momentum", 0.0))
    if direction == "long" and momentum > 0:
        score += min(10.0, momentum * 1.5)
    elif direction == "short" and momentum < 0:
        score += min(10.0, abs(momentum) * 1.5)

    # ── 1c. DMI 方向确认（0~10）─────────────────
    plus_di = safe_float(ctx.get("plus_di", 0.0))
    minus_di = safe_float(ctx.get("minus_di", 0.0))
    di_diff = plus_di - minus_di
    if direction == "long" and di_diff > 0:
        score += min(10.0, di_diff * 0.6)
    elif direction == "short" and di_diff < 0:
        score += min(10.0, abs(di_diff) * 0.6)

    return min(40.0, max(0.0, score))


# ============================================================
# 第2a层：Structure Quality Score（0~70）
# ============================================================
def _structure_quality(ctx: Dict[str, Any]) -> float:
    """
    结构质量评分（0~70）
    
    五项加权：
      - zone_quality       (0~20): OB/FVG 有效性 + 强度
      - mitigation_quality (0~15): 回测确认 + 实体吞噬
      - sweep_confirm      (0~15): 流动性扫荡 + 方向匹配
      - structure_align    (0~10): BOS/CHOCH + HTF 共识
      - setup_direction    (0~10): setup_type 方向匹配
    """
    score = 0.0

    # ── 2a-1. Zone Quality（0~20）────────────────
    zone = 0.0
    if safe_bool(ctx.get("has_valid_zone", False)):
        zone += 8.0
    ob_strength = safe_float(ctx.get("ob_strength", 0.0))
    if ob_strength > 0.6:
        zone += 7.0
    elif ob_strength > 0.3:
        zone += 4.0
    direction = safe_str(ctx.get("direction", "")).lower()
    if direction == "long" and ctx.get("bullish_fvg") is not None:
        zone += 5.0
    elif direction == "short" and ctx.get("bearish_fvg") is not None:
        zone += 5.0
    zone = min(zone, 20.0)
    score += zone

    # ── 2a-2. Mitigation Quality（0~15）──────────
    mitigation = 0.0
    fill_ratio = safe_float(ctx.get("wick_fill_ratio", ctx.get("fill_ratio", 0.0)))
    mitigation += min(7.0, fill_ratio * 10.0)
    if safe_bool(ctx.get("retest_confirmed", False)):
        mitigation += 4.0
    body_pct = safe_float(ctx.get("body_pct", 0.0))
    if body_pct > 0.5:
        mitigation += 4.0
    elif body_pct > 0.3:
        mitigation += 2.0
    mitigation = min(mitigation, 15.0)
    score += mitigation

    # ── 2a-3. Sweep Confirm（0~15）───────────────
    sweep = 0.0
    liquidity_sweep = safe_bool(ctx.get("liquidity_sweep_confirmed", False))
    if liquidity_sweep:
        sweep += 7.0
    if direction == "long" and safe_bool(ctx.get("is_ssl_swept", False)):
        sweep += 5.0
    elif direction == "short" and safe_bool(ctx.get("is_bsl_swept", False)):
        sweep += 5.0
    if liquidity_sweep and safe_bool(ctx.get("retest_confirmed", False)):
        sweep += 3.0
    sweep = min(sweep, 15.0)
    score += sweep

    # ── 2a-4. Structure Alignment（0~10）─────────
    alignment = 0.0
    if safe_bool(ctx.get("bos_confirmed", False)) or safe_bool(ctx.get("choch_confirmed", False)):
        alignment += 4.0
    htf_allowed = safe_str(ctx.get("htf_allowed", "Both"))
    if htf_allowed == direction.capitalize():
        alignment += 4.0
    mit_src = safe_str(ctx.get("mitigation_src", "NO_FVG_OB"))
    if mit_src != "NO_FVG_OB":
        alignment += 2.0
    alignment = min(alignment, 10.0)
    score += alignment

    # ── 2a-5. Setup Direction Match（0~10）───────
    setup = 0.0
    setup_type = safe_str(ctx.get("setup_type", ""))
    if "ob" in setup_type.lower():
        setup += 4.0
    elif "fvg" in setup_type.lower():
        setup += 4.0
    if safe_bool(ctx.get("setup_direction_match", False)):
        setup += 3.0
    if safe_bool(ctx.get("liquidity_sweep_confirmed", False)) and (
        "ob" in setup_type.lower() or "fvg" in setup_type.lower()
    ):
        setup += 3.0
    setup = min(setup, 10.0)
    score += setup

    return min(70.0, max(0.0, score))


# ============================================================
# 第2b层：Environment Multiplier（0.6~1.4）
# ============================================================
def _environment_multiplier(ctx: Dict[str, Any]) -> float:
    """
    环境乘数（0.6~1.4）
    
    三项因子累加基值 1.0：
      - regime_factor  (-0.15~+0.15): TREND→+0.15, MUD→-0.15
      - vol_factor     (-0.10~+0.10): HIGH_VOL→-0.10
      - squeeze_factor (-0.05~+0.20): TIGHT→+0.20, NONE→-0.05
    """
    mult = 1.0

    # ── Regime Factor（-0.15~+0.15）─────────────
    regime = safe_str(ctx.get("regime", "mud")).lower()
    if regime == "trend":
        mult += 0.15
    elif regime in ("chop", "mud"):
        mult -= 0.15
    elif regime == "transition":
        mult += 0.05

    # ── Volatility Factor（-0.10~+0.10）─────────
    vol_state = safe_str(ctx.get("vol_state", "normal")).lower()
    if vol_state == "high_vol":
        mult -= 0.10
    elif vol_state == "low_vol":
        mult += 0.05

    # ── Squeeze Factor（-0.05~+0.20）────────────
    squeeze = safe_str(ctx.get("squeeze", "none")).lower()
    if squeeze == "tight":
        mult += 0.20
    elif squeeze == "building":
        mult += 0.08
    elif squeeze == "none":
        mult -= 0.05

    return max(0.6, min(1.4, mult))


# ============================================================
# 第3层：EV-Confidence Calibration（±8分）
# ============================================================
def _ev_calibration(
    final_score: float,
    regime: str,
    ctx: Dict[str, Any],
    ev_learner=None,
) -> Tuple[float, str]:
    """
    EV 校准（±8分）
    
    用历史回测结果校准评分：
      - 从 ev_learner 获取 (regime, score_bucket) 桶的历史 EV 偏差
      - 偏差正值 → 分数上调（模型低估）
      - 偏差负值 → 分数下调（模型高估）
      - 调整幅度封顶 ±8 分
    
    返回:
        (adjusted_score, calibration_note)
    """
    if ev_learner is None:
        return final_score, "EV_CALIB_DISABLED"

    if final_score >= 75:
        score_bucket = "VERY_HIGH"
    elif final_score >= 55:
        score_bucket = "HIGH"
    elif final_score >= 35:
        score_bucket = "MID"
    else:
        score_bucket = "LOW"

    setup_type = safe_str(ctx.get("setup_type", "V37_CORE"))

    bias = ev_learner.get_ev_bias(regime, f"{regime}|{score_bucket}", min_samples=15)
    if bias is None:
        bias = ev_learner.get_ev_bias(regime, setup_type, min_samples=10)

    if bias is None:
        return final_score, f"NO_CALIB_DATA({regime}|{score_bucket})"

    adjustment = bias * 100.0
    adjustment = max(-8.0, min(8.0, adjustment))
    adjusted = final_score + adjustment
    adjusted = max(0.0, min(100.0, adjusted))

    note = f"EV_CALIB({regime}|{score_bucket},bias={bias:.4f},adj={adjustment:+.1f})"
    return adjusted, note


# ============================================================
# 主入口：v2_scorecard
# ============================================================
def v2_scorecard(
    ctx: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
    ev_learner=None,
) -> Dict[str, Any]:
    """
    V2 Scorecard Engine
    
    参数:
        ctx: 评分上下文（由 ctx_builder 填充，与旧版接口兼容）
        config: 可选覆盖参数
        ev_learner: EVLearner 实例
    
    返回:
        {
            "final_score": float,           # 0~100（与旧版兼容）
            "base_score": float,            # 0~40
            "quality_score": float,         # 0~70
            "env_mult": float,              # 环境乘数
            "quality_weighted": float,
            "ev_adjustment": float,
            "calibration_note": str,
            "regime": str,
            "breakdown": str,
        }
    """
    cfg = config or {}

    base_score = _base_layer(ctx)
    quality_score = _structure_quality(ctx)
    env_mult = _environment_multiplier(ctx)
    quality_weighted = quality_score * env_mult
    regime = safe_str(ctx.get("regime", "mixed")).upper()
    raw_final = base_score + quality_weighted
    final_score = min(100.0, max(0.0, raw_final))

    # ── Base Layer 低保 ─────────────────────────
    # 当 base_score < 12（低动量）时：
    #   1) 结构分打 7 折（避免无方向动量时纯靠结构跳级）
    #   2) final_score 封顶 35
    if base_score < 12.0:
        quality_weighted = quality_weighted * 0.7
        final_score = min(100.0, max(0.0, base_score + quality_weighted))
        final_score = min(final_score, 35.0)

    ev_adjustment = 0.0
    cal_note = "EV_CALIB_DISABLED"
    if cfg.get("ev_calibration", {}).get("enabled", True):
        calibrated_score, cal_note = _ev_calibration(final_score, regime, ctx, ev_learner)
        ev_adjustment = calibrated_score - final_score
        final_score = calibrated_score

    # ── Breakdown 用最终值 ────────────────────
    bd_weighted = quality_score * env_mult
    bd_raw = base_score + bd_weighted
    if base_score < 12.0:
        bd_weighted = bd_weighted * 0.7
        bd_raw = base_score + bd_weighted
    bd_note = f" (LOW_BASE_30PCT_OFF)" if base_score < 12.0 else ""
    breakdown = (
        f"V2_SCORE={final_score:.1f} | "
        f"BASE={base_score:.1f}/40 | "
        f"QUAL={quality_score:.1f}/70 | "
        f"ENV={env_mult:.3f} | "
        f"WEIGHTED={bd_weighted:.1f}{bd_note} | "
        f"MERGED={bd_raw:.1f} | "
        f"EV_ADJ={ev_adjustment:+.2f} | "
        f"REGIME={regime} | "
        f"{cal_note}"
    )

    return {
        "final_score": round(final_score, 2),
        "base_score": round(base_score, 2),
        "quality_score": round(quality_score, 2),
        "env_mult": round(env_mult, 3),
        "quality_weighted": round(quality_weighted, 2),
        "ev_adjustment": round(ev_adjustment, 2),
        "calibration_note": cal_note,
        "regime": regime,
        "breakdown": breakdown,
    }
