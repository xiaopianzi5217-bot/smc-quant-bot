# -*- coding: utf-8 -*-
""" V37.5 EV Intelligence Engine (校准版) 核心变更： 1. compute_expected_value — 校准版 EV（防过拟合） 2. get_win_prob — 分桶统计概率（防止幻想模型） 3. grade_from_expected_value — EV 评级 """
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import math


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        out = float(value)
        if math.isnan(out) or math.isinf(out):
            return default
        return out
    except Exception:
        return default


def _safe_bool(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "long", "short", "bull", "bear"}
    try:
        if value != value:
            return False
    except Exception:
        pass
    try:
        return bool(value)
    except Exception:
        return False


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def assign_grade(score: float, setup: str, regime: str) -> str:
    """ 重写 grade：删除 S_EV_HOT，只保留 A_EV / B_EV """
    if setup == "V37_TACTICAL" and score >= 88:
        return "A_EV"
    if setup == "V37_SCALP" and score >= 95:
        return "B_EV"
    return ""


def grade_from_expected_value(expected_value: float) -> str:
    """Production grade labels based on EV, not on legacy score."""
    ev = _safe_float(expected_value, -9.0)
    if ev > 0.25:
        return "A_EV"
    if ev > 0.15:
        return "A_EV"
    if ev > 0.05:
        return "B_EV"
    if ev >= 0.00:
        return "C_EV"
    return "D_NEG_EV"


# ============================================================
# ✅ 一、校准版 EV 计算（防止过拟合）
# ============================================================
def compute_expected_value( win_prob: float, estimated_rr: float, regime: Optional[str] = None, score: Optional[float] = None, ) -> float:
    """
    EV 校准版本。

    V54 Alpha Expansion 调整：旧版本同时压缩胜率和 RR，导致真实强信号
    被过度打低。本版本仍做保守校准，但减少 shrink 幅度，让优质
    SMC + SQZMOM 信号的收益期望可以释放出来。
    """
    regime_u = str(regime or "").upper()

    # 1. win_prob shrink：V54 从 78/22 再放宽到 84/16。
    # 仍向保守基准回归，但让强 SMC+SQZMOM 信号不要被二次腰斩。
    base_rate = 0.40 if regime_u in {"TRANSITION", "CHOP"} else 0.37
    win_prob_adj = 0.84 * _clip(win_prob, 0.05, 0.76) + 0.16 * base_rate

    # 2. RR shrink：保留上限，避免幻想收益；上限放到 2.05 以匹配实际 TP2/TP3 路径。
    rr_adj = _clip(estimated_rr, 0.80, 2.05)

    # 3. regime penalty：把环境惩罚从硬压制改为轻微折扣。
    regime_penalty = 0.0
    if regime_u == "CHOP":
        regime_penalty = -0.015
    elif regime_u == "TREND":
        regime_penalty = -0.005
    elif regime_u == "CRISIS_RISK_OFF":
        regime_penalty = -0.090

    # 4. score dampening：低分仍降级，高分给轻微释放空间。
    score_penalty = 0.0
    if score is not None:
        score_v = _safe_float(score, 0.0)
        if score_v < 50:
            score_penalty = -0.025
        elif score_v < 60:
            score_penalty = -0.010
        elif score_v >= 90:
            score_penalty = 0.015

    # 5. EV core
    ev = (win_prob_adj * rr_adj) - (1.0 - win_prob_adj)

    # 6. final EV
    ev = ev + regime_penalty + score_penalty

    return ev


# ============================================================
# ✅ ① 修 win_prob（去掉过拟合）
# ============================================================
def _win_prob_bucket(regime: str, setup_type: str, score: float = 50.0) -> float:
    """ 修正版本：去掉过拟合 参数： regime: TREND / TRANSITION / CHOP setup_type: V37_CORE / V37_TACTICAL / V37_SCALP score: 原始评分（0~100） 返回： 校准后胜率（0.25~0.58） """
    base_map = {
        ("TREND", "V37_CORE"): 0.41,
        ("TREND", "V37_TACTICAL"): 0.39,
        ("TRANSITION", "V37_CORE"): 0.37,
        ("TRANSITION", "V37_TACTICAL"): 0.36,
        ("CHOP", "V37_CORE"): 0.33,
        ("CHOP", "V37_SCALP"): 0.32,
    }

    p = base_map.get((regime, setup_type), 0.35)

    # V54: 分桶胜率仍保守，但减少过度回归，配合反过滤器扩张。
    p = 0.92 * p + 0.055

    # score 修正弱化，避免评分虚高，同时允许强信号释放一点概率空间。
    if score > 90:
        p += 0.015
    elif score < 70:
        p -= 0.015

    return max(0.25, min(0.62, p))


class RRTracker:
    """ 真实 RR 追踪器（不要预测，用历史回放） """

    def __init__(self) -> None:
        self.win: List[float] = []
        self.loss: List[float] = []

    def update(self, r: float) -> None:
        if r > 0:
            self.win.append(r)
        else:
            self.loss.append(abs(r))

    def rr(self) -> float:
        if not self.win or not self.loss:
            return 1.0
        avg_win = sum(self.win) / len(self.win)
        avg_loss = sum(self.loss) / len(self.loss)
        if avg_loss == 0:
            return 1.0
        return min(2.0, avg_win / avg_loss)


# ⑥ 全局 RRTracker 实例（交易结束后需调用 rr_tracker.update(pnl_r)）
rr_tracker: RRTracker = RRTracker()

# V37.6: disabled hard cluster blocking. Previous blocks used a mismatched
# setup_type field and removed the better TREND trades while leaving weak
# TRANSITION/SCALP trades. Use EV + cost + portfolio size instead.
BLOCKED_CLUSTERS = set()


def _score_bucket(score_norm: float) -> str:
    """将 score 映射到分桶"""
    if score_norm >= 80:
        return "HIGH"
    if score_norm >= 55:
        return "MID"
    return "LOW"


# ============================================================
# 原有 estimate_expected_value 保留但内部改用校准版
# ============================================================
def estimate_expected_value(signal: Dict[str, Any], regime: str, vol_state: str, ctx: dict = None) -> Dict[str, Any]:
    """ Estimate win probability, R:R and EV from existing V37 signal metadata. 内部使用校准版 compute_expected_value + 分桶 get_win_prob。 """
    meta = signal.get("entry_meta", {}) if isinstance(signal, dict) else {}
    if ctx is None:
        ctx = {}

    sqz_mult = _safe_float(ctx.get("sqz_mult"), 1.0)
    regime = str(regime or "").upper()
    vol_state = str(vol_state or "").upper()

    score_raw = _safe_float(signal.get("score_raw"), 0.0)
    score_norm = _safe_float(signal.get("score"), 0.0)
    smc = _safe_float(signal.get("smc"), 0.0)
    sqz = _safe_float(signal.get("sqzmom"), 0.0)
    brk = _safe_float(signal.get("breakout"), 0.0)
    raw_base = _safe_float(signal.get("raw_base"), 0.0)

    raw_term = _clip(score_raw / 42.0, 0.0, 1.0)
    score_term = _clip(score_norm / 100.0, 0.0, 1.0)
    smc_term = _clip(smc / 40.0, 0.0, 1.0)
    sqz_term = _clip(sqz / 30.0, 0.0, 1.0)
    brk_term = _clip(brk / 30.0, 0.0, 1.0)
    base_term = _clip(raw_base / 20.0, 0.0, 1.0)

    trend_aligned = _safe_bool(meta.get("trend_aligned", False))
    trend_misaligned = regime == "TREND" and not trend_aligned
    liquidity_wrong_side = _safe_bool(meta.get("liquidity_wrong_side", False))
    liquidity_confirmed = _safe_bool(meta.get("liquidity_sweep_confirmed", False))
    setup_match = _safe_bool(meta.get("setup_direction_match", False))
    has_any_setup = _safe_bool(meta.get("has_any_setup", False))
    fallback_active = _safe_bool(signal.get("fallback_active", False))
    smc_passed = _safe_bool(signal.get("smc_passed", False))
    breakout_passed = _safe_bool(signal.get("breakout_passed", False))

    reasons: List[str] = []

    # Win probability: quality adds probability; hostile context subtracts it.
    win_prob = (
        0.30
        + 0.085 * raw_term
        + 0.045 * score_term
        + 0.065 * smc_term
        + 0.045 * sqz_term
        + 0.045 * brk_term
        + 0.020 * base_term
    )

    # V54 修复：移除与评分层重复的惩罚。
    # 评分层 smc_impulse_score 已经通过 soft_mult 处理了 SMC 弱/fallback/不对齐，
    # EV 层再做一次同样的惩罚会导致评分 78.8 的信号 EV 仍为负的 bug。
    # EV 层只保留环境层面的独立判断（CRISIS/CHOP/HIGH_VOL）。
    # 【修复20260701】NO_DIRECTIONAL_SETUP 不扣 win_prob，只调 size_multiplier
    if setup_match:
        win_prob += 0.040
    elif not has_any_setup:
        # 不扣 win_prob，在 size_multiplier 中处理
        reasons.append("NO_DIRECTIONAL_SETUP_SOFT")
    else:
        reasons.append("SETUP_OPPOSITE_SIDE_SOFT")

    if liquidity_confirmed:
        win_prob += 0.020
    if trend_aligned:
        win_prob += 0.025
    if breakout_passed:
        win_prob += 0.020

    if regime == "CRISIS_RISK_OFF":
        win_prob -= 0.300
        reasons.append("CRISIS_RISK_OFF_SOFT")
    if vol_state == "HIGH_VOL":
        win_prob -= 0.030
        reasons.append("HIGH_VOL_SOFT")

    # Estimated R:R: strong breakout/structure expands upside; bad context compresses it.
    estimated_rr = (
        0.68
        + 0.24 * raw_term
        + 0.34 * smc_term
        + 0.22 * sqz_term
        + 0.42 * brk_term
    )
    if regime == "TREND":
        estimated_rr += 0.14
    elif regime == "TRANSITION":
        estimated_rr += 0.04
    elif regime == "CHOP":
        estimated_rr -= 0.16
    elif regime == "CRISIS_RISK_OFF":
        estimated_rr -= 0.58

    if vol_state == "HIGH_VOL":
        estimated_rr -= 0.10

    wp_est = _clip(win_prob, 0.05, 0.68)
    estimated_rr = _clip(estimated_rr, 0.35, 2.65)

    # V37.6: keep RR local to the current signal.
    # The global RRTracker created temporal feedback and made estimated_rr jump
    # to 2.0 after a few outlier winners, corrupting EV ranking.
    estimated_rr = _clip(estimated_rr, 0.80, 1.80)

    # ✅ 使用校准版 EV 计算
    setup_type = str(meta.get("setup_type", "V37_CORE") or "V37_CORE")

    # ⑦ 禁掉最差组合
    if (regime, setup_type) in BLOCKED_CLUSTERS:
        return {
            "win_prob": round(float(_clip(win_prob, 0.05, 0.68)), 4),
            "estimated_rr": round(float(estimated_rr), 4),
            "expected_value": 0.0,
            "ev_grade": "D_NEG_EV",
            "size_multiplier": 0.0,
            "ev_reasons": "BLOCKED_BAD_CLUSTER",
            "trend_misaligned_soft": bool(trend_misaligned),
            "liquidity_wrong_side_soft": bool(liquidity_wrong_side),
            "risk_off_soft": bool(regime == "CRISIS_RISK_OFF"),
        }

    bucket_wp = _win_prob_bucket(regime, setup_type, score_norm)

    # ====================================================
    # 🚨 TREND 鱼尾行情风控 (Tail-End Penalty)
    # ====================================================
    is_tail_regime = False
    if regime == "TREND":
        # 提取底层传上来的动量过热指标
        sqz_mult = _safe_float(ctx.get("sqz_mult"), 1.0)
        dominance = str(ctx.get("dominance", "")).upper()

        # 触发条件：动量乘数极高(指标超买/超卖区) 或 完全由动量主导的突破
        if sqz_mult >= 1.5 or "MOMENTUM" in dominance:
            is_tail_regime = True
            # V54: 鱼尾行情仍惩罚，但不再强行把胜率打到失真。
            bucket_wp *= 0.75

    # 融合：原始 win_prob 和分桶概率各占一半
    blended_wp = 0.8 * wp_est + 0.2 * bucket_wp

    # 惩罚2：如果是鱼尾，额外压低融合胜率。
    if is_tail_regime:
        blended_wp *= 0.90

    expected_value = compute_expected_value(blended_wp, estimated_rr, regime, score_norm)
    ev_grade = grade_from_expected_value(expected_value)

    # 惩罚3：强制降级 (剥夺 A_EV 资格)
    if is_tail_regime and ev_grade == "A_EV":
        ev_grade = "B_EV"

    # Size缩放。评分层已通过 soft_mult 处理了信号质量，此处只处理环境风险。
    size_multiplier = 1.0
    
    if regime == "CRISIS_RISK_OFF":
        size_multiplier *= 0.40
    if vol_state == "HIGH_VOL":
        size_multiplier *= 0.85
        
    if expected_value <= 0.0:
        size_multiplier *= 0.80
        
    # 【修复20260701】方向性 setup 只调 size，不改 win_prob（从上面移过来）
    if not has_any_setup and not setup_match:
        size_multiplier *= 0.75  # 无方向性 setup 时仓位打 75 折
    elif not setup_match and has_any_setup:
        size_multiplier *= 0.60  # setup 在对面时仓位打 6 折

    return {
        "win_prob": round(float(blended_wp), 4),
        "estimated_rr": round(float(estimated_rr), 4),
        "expected_value": round(float(expected_value), 4),
        "ev_grade": ev_grade,
        "size_multiplier": round(float(_clip(size_multiplier, 0.05, 1.50)), 4),
        "ev_reasons": ";".join(reasons) if reasons else "EV_CONTEXT_OK",
        "trend_misaligned_soft": bool(trend_misaligned),
        "liquidity_wrong_side_soft": bool(liquidity_wrong_side),
        "risk_off_soft": bool(regime == "CRISIS_RISK_OFF"),
    }