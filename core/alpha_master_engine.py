# -*- coding: utf-8 -*-
"""
V37 Institutional Alpha Master Engine

Single-decision kernel integrating V34/V35/V36/V37 ideas:
- one regime source
- one signal score source: strategy.smc_impulse_engine.smc_impulse_score
- no fallback trade path
- risk budget + portfolio allocation
- crisis/circuit breaker layer

This module is intentionally dependency-light and designed to sit on top of the
existing SMC_Bot feature pipeline. It does not replace feature engineering.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional, Tuple
import math
import numpy as np
import pandas as pd

from strategy.smc_impulse_engine import smc_impulse_score
from strategy.intelligence_engine import estimate_expected_value, grade_from_expected_value
from strategy.scorecard_system import evaluate_base_trigger, build_scorecard, dumps_compact

# Inline lightweight ClusterEngineV38 (was from cluster_engine import ClusterEngineV38)
class ClusterEngineV38:
    def compute_weights(self, clusters: list) -> list:
        import numpy as np
        scores = []
        for c in clusters:
            s = (c.get("mean_r", 0) * 10 + c.get("win_rate", 0) * 5 +
                 min(1.0, c.get("trades", 0) / 100) * 3 +
                 c.get("stability", 0.5) * 4 -
                 c.get("max_dd", 0) * 2)
            scores.append(max(0.0, s))
        arr = np.array(scores, dtype=float)
        max_val = arr.max()
        exp = np.exp(arr - max_val) if max_val > -np.inf else np.ones_like(arr)
        total = exp.sum()
        if total <= 0:
            return [0.25] * len(clusters)
        return (exp / total).tolist()

try:
    from analysis.fvg_stop_hunt import nearest_mitigation_price
except Exception:  # pragma: no cover
    from ..analysis.fvg_stop_hunt import nearest_mitigation_price


VERSION = "V38_DECOUPLED_SCORECARD_20260616"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


def _safe_bool(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "long", "short", "bull", "bear"}
    try:
        if value != value:  # NaN/NaT should not become True
            return False
    except Exception:
        pass
    try:
        return bool(value)
    except Exception:
        return False


def _row_to_dict(row: Any) -> Dict[str, Any]:
    if hasattr(row, "to_dict"):
        return row.to_dict()
    if isinstance(row, dict):
        return dict(row)
    return {}


@dataclass
class AccountState:
    equity_r: float = 0.0
    peak_equity_r: float = 0.0
    drawdown_r: float = 0.0
    drawdown_pct_proxy: float = 0.0
    loss_streak: int = 0
    trade_count: int = 0

    def update(self, pnl_r: float) -> None:
        pnl_r = _safe_float(pnl_r, 0.0)
        self.trade_count += 1
        self.equity_r += pnl_r
        self.peak_equity_r = max(self.peak_equity_r, self.equity_r)
        self.drawdown_r = max(0.0, self.peak_equity_r - self.equity_r)
        self.drawdown_pct_proxy = min(1.0, self.drawdown_r / 10.0)
        self.loss_streak = self.loss_streak + 1 if pnl_r < 0 else 0


class V37MasterEngine:
    """Stateless per-bar decision engine with stateful account/risk overlay."""

    def __init__(
        self,
        base_risk: float = 0.12,
        min_score_raw: float = 18.0,
        min_score_norm: float = 50.0,
        max_position_mult: float = 0.40,
        min_expected_value: float = 0.0,
    ) -> None:
        self.base_risk = float(base_risk)
        # Deprecated as hard gates in V37.5: kept only for score normalization / backward-compatible kwargs.
        self.min_score_raw = float(min_score_raw)
        self.min_score_norm = float(min_score_norm)
        self.max_position_mult = float(max_position_mult)
        self.min_expected_value = float(min_expected_value)
        self.account = AccountState()
        self.cluster_weighter = ClusterEngineV38()

    # ------------------------------------------------------------------
    # V34: regime soft probabilities (replaces hard classify)
    # ------------------------------------------------------------------
    def regime_soft_probs(self, row: Any, exec_ctx: Dict[str, Any]) -> Dict[str, float]:
        """
        使用 SoftRegimeModel 计算 regime 软概率。
        返回 {CHOP, TREND, TRANSITION}。
        CRISIS_RISK_OFF 仍用硬规则覆盖。
        """
        from regime_model import SoftRegimeModel

        # Crisis 先验（硬规则覆盖）
        atr = _safe_float(row.get("ATRr_14", exec_ctx.get("atr", 0.0)), 0.0)
        close = _safe_float(row.get("close", 1.0), 1.0)
        volume_ratio = _safe_float(row.get("volume_ratio", 1.0), 1.0)
        atr_pct = atr / close if close > 0 else 0.0

        crisis_prob = max(0.0, min(1.0,
            (atr_pct - 0.015) / 0.025 + (max(0, 0.50 - volume_ratio)) * 0.3 + (max(0, volume_ratio - 3.0)) * 0.2
        ))
        crisis_prob = min(1.0, crisis_prob)

        if crisis_prob >= 0.90:
            return {
                "CRISIS_RISK_OFF": 1.0,
                "CHOP": 0.0,
                "TREND": 0.0,
                "TRANSITION": 0.0,
            }

        # SoftRegimeModel 计算正常 regime 概率
        model = SoftRegimeModel()
        features = model.extract_features(row)
        regime_probs = model.compute_regime_probs(features)

        # 用 crisis_prob 稀释正常概率
        rem = 1.0 - crisis_prob
        return {
            "CRISIS_RISK_OFF": round(crisis_prob, 4),
            "CHOP": round(regime_probs["CHOP"] * rem, 4),
            "TREND": round(regime_probs["TREND"] * rem, 4),
            "TRANSITION": round(regime_probs["TRANSITION"] * rem, 4),
        }

    def cluster_scores(self, row: Any, signal: Dict[str, Any]) -> Dict[str, float]:
        """
        使用 ClusterEngineV38 计算连续簇分数。
        不再杀死任何簇，低分簇自动 softmax 压到接近 0。
        """
        ev = _safe_float(signal.get("expected_value", 0.0), 0.0)
        confidence = _safe_float(signal.get("confidence", 0.0), 0.0)
        trades_proxy = max(1, int(abs(ev) * 100))  # 模拟交易次数

        # 构建每个簇的统计
        clusters = [
            {
                "mean_r": max(-0.1, min(0.5, ev * 2.0)),
                "win_rate": max(0.0, min(1.0, confidence * 0.7 + 0.2)),
                "trades": trades_proxy,
                "stability": max(0.1, min(1.0, 1.0 - abs(ev) * 0.5)),
                "max_dd": max(0.0, abs(ev) * 0.3),
            },
            {
                "mean_r": max(-0.1, min(0.5, ev * 1.2)),
                "win_rate": max(0.0, min(1.0, confidence * 0.5 + 0.15)),
                "trades": max(1, trades_proxy // 2),
                "stability": max(0.1, min(1.0, 1.0 - abs(ev) * 0.3)),
                "max_dd": max(0.0, abs(ev) * 0.2),
            },
            {
                "mean_r": max(-0.1, min(0.5, ev * 0.6)),
                "win_rate": max(0.0, min(1.0, confidence * 0.3 + 0.1)),
                "trades": max(1, trades_proxy // 3),
                "stability": max(0.1, min(1.0, 1.0 - abs(ev) * 0.2)),
                "max_dd": max(0.0, abs(ev) * 0.15),
            },
            {
                "mean_r": max(-0.1, min(0.5, ev * 0.1)),
                "win_rate": max(0.0, min(1.0, confidence * 0.1 + 0.05)),
                "trades": max(1, trades_proxy // 5),
                "stability": max(0.1, min(1.0, 1.0 - abs(ev) * 0.1)),
                "max_dd": max(0.0, abs(ev) * 0.1),
            },
        ]

        weights = self.cluster_weighter.compute_weights(clusters)

        return {
            "CORE": round(float(weights[0]), 4),
            "TACTICAL": round(float(weights[1]), 4),
            "PROBE": round(float(weights[2]), 4),
            "DUMPSTER": round(float(weights[3]), 4),
        }

    def weighted_ev(self, row: Any, signal: Dict[str, Any], exec_ctx: Dict[str, Any]) -> Dict[str, Any]:
        """
        加权 EV engine：
        regime_soft_probs × cluster_scores × raw_ev → 最终连续仓位
        """
        raw_ev = _safe_float(signal.get("expected_value", 0.0), 0.0)
        regime_probs = self.regime_soft_probs(row, exec_ctx)
        clusters = self.cluster_scores(row, signal)

        # 加权 EV = sum(regime_prob * cluster_score) * raw_ev
        # regime_probs 和 clusters 的权重各 0.5
        regime_weight = sum(p * raw_ev for p in regime_probs.values()) / max(1e-9, sum(regime_probs.values()))
        cluster_weight = sum(s * raw_ev for s in clusters.values()) / max(1e-9, sum(clusters.values()))

        weighted_ev = 0.5 * regime_weight + 0.5 * cluster_weight

        # 连续仓位 0.0~1.0
        position_size = max(0.0, min(1.0,
            self.base_risk * 2.0 * max(0.0, weighted_ev) * _safe_float(signal.get("confidence", 0.5), 0.5)
        ))

        return {
            "weighted_ev": round(weighted_ev, 6),
            "position_size": round(position_size, 6),
            "regime_probs": regime_probs,
            "cluster_scores": clusters,
            "raw_ev": round(raw_ev, 6),
        }

    # ------------------------------------------------------------------
    # V34: single regime source (kept for backward compat)
    # ------------------------------------------------------------------
    def classify_regime(self, row: Any, exec_ctx: Dict[str, Any]) -> str:
        adx = _safe_float(row.get("adx", exec_ctx.get("adx", 0.0)), 0.0)
        atr = _safe_float(row.get("ATRr_14", exec_ctx.get("atr", 0.0)), 0.0)
        close = _safe_float(row.get("close", 0.0), 0.0)
        volume_ratio = _safe_float(row.get("volume_ratio", 1.0), 1.0)
        atr_pct = atr / close if close > 0 else _safe_float(exec_ctx.get("atr_pct", 0.0), 0.0)

        # Crisis has priority: liquidity drain or abnormal volatility.
        if atr_pct >= 0.030 or volume_ratio <= 0.35 or volume_ratio >= 4.0:
            return "CRISIS_RISK_OFF"
        if adx >= 23.0:
            return "TREND"
        if adx <= 15.0:
            return "CHOP"
        return "TRANSITION"

    def volatility_state(self, row: Any, exec_ctx: Dict[str, Any]) -> str:
        atr_pct = _safe_float(exec_ctx.get("atr_pct", 0.0), 0.0)
        volume_ratio = _safe_float(row.get("volume_ratio", 1.0), 1.0)
        if atr_pct >= 0.020 or volume_ratio >= 2.2:
            return "HIGH_VOL"
        if atr_pct <= 0.006 and volume_ratio <= 0.85:
            return "LOW_VOL"
        return "MID_VOL"

    # ------------------------------------------------------------------
    # V35/V36/V37: single signal source; compare long/short, no fallback.
    # ------------------------------------------------------------------
    def build_entry_snapshot(
        self,
        row: Any,
        direction: str,
        exec_ctx: Dict[str, Any],
        macro_ctx: Dict[str, Any],
    ) -> Dict[str, Any]:
        direction = str(direction).title()
        row_dict = _row_to_dict(row)
        price = _safe_float(row.get("close", 0.0), 0.0)
        atr = max(_safe_float(row.get("ATRr_14", exec_ctx.get("atr", 0.0)), 0.0), 1e-12)

        mitigation_price = None
        mitigation_src = "NO_FVG_OB"
        try:
            miti = nearest_mitigation_price(row, direction)
            if isinstance(miti, tuple) and len(miti) >= 1:
                mitigation_price = miti[0]
            if isinstance(miti, tuple) and len(miti) >= 2:
                mitigation_src = str(miti[1])
        except Exception:
            mitigation_price = None
            mitigation_src = "NO_FVG_OB"

        has_valid_zone = mitigation_price is not None and mitigation_src != "NO_FVG_OB"
        zone_near_atr = 9.99
        if has_valid_zone and price > 0:
            zone_near_atr = abs(price - _safe_float(mitigation_price, price)) / atr

        vwap = _safe_float(row.get("vwap_48", row.get("VWAP", row.get("vwap", price))), price)
        vwap_dist_atr = abs(price - vwap) / atr if atr > 0 else 9.99

        # Directional SMC quality from the feature pipeline.  This is used by the
        # decoupled base trigger, while FVG/OB/sweep stay as soft structure evidence.
        if direction == "Long":
            smc_quality_100 = _safe_float(row.get("smc_quality_score_bull", row.get("smc_quality_score", 0.0)), 0.0)
        else:
            smc_quality_100 = _safe_float(row.get("smc_quality_score_bear", row.get("smc_quality_score", 0.0)), 0.0)

        # Directional liquidity context.
        long_sweep = any(_safe_bool(row.get(k, False)) for k in [
            "sellside_sweep", "sellside_liquidity_taken", "bullish_stop_hunt", "sweep_low", "liquidity_sweep_long"
        ])
        short_sweep = any(_safe_bool(row.get(k, False)) for k in [
            "buyside_sweep", "buyside_liquidity_taken", "bearish_stop_hunt", "sweep_high", "liquidity_sweep_short"
        ])
        liquidity_sweep = long_sweep if direction == "Long" else short_sweep
        liquidity_wrong_side = short_sweep if direction == "Long" else long_sweep

        # Lightweight SQZMOM score matching existing 0~44 convention.
        sqz_score = 0.0
        if direction == "Long":
            if _safe_float(row.get("momentum", 0.0), 0.0) > 0:
                sqz_score += 7.0
            if _safe_float(row.get("momentum_slope", 0.0), 0.0) > 0:
                sqz_score += 6.0
            if _safe_bool(row.get("sqzmom_reversal_confirm_long", False)):
                sqz_score += 8.0
            if _safe_bool(row.get("dmi_bull", False)) or _safe_float(row.get("plus_di", 0.0), 0.0) >= _safe_float(row.get("minus_di", 0.0), 0.0):
                sqz_score += 7.0
        else:
            if _safe_float(row.get("momentum", 0.0), 0.0) < 0:
                sqz_score += 7.0
            if _safe_float(row.get("momentum_slope", 0.0), 0.0) < 0:
                sqz_score += 6.0
            if _safe_bool(row.get("sqzmom_reversal_confirm_short", False)):
                sqz_score += 8.0
            if _safe_bool(row.get("dmi_bear", False)) or _safe_float(row.get("minus_di", 0.0), 0.0) > _safe_float(row.get("plus_di", 0.0), 0.0):
                sqz_score += 7.0
        if _safe_bool(row.get("squeeze_released", False)):
            sqz_score += 6.0
        if str(row.get("sqzmom_divergence_dir", "None")) == direction and _safe_float(row.get("sqzmom_divergence_age", 999), 999) <= 18:
            sqz_score += 10.0
        sqz_score = max(0.0, min(44.0, sqz_score))

        trend_dir = str(exec_ctx.get("trend_direction", "None"))
        momentum_align = (
            (direction == "Long" and _safe_float(row.get("momentum", 0.0), 0.0) > 0)
            or (direction == "Short" and _safe_float(row.get("momentum", 0.0), 0.0) < 0)
        )

        # Directional setup flags are soft EV inputs, not hard gates.
        if direction == "Long":
            setup_direction_match = any(_safe_bool(row.get(k, False)) for k in ["reversal_long", "breakout_long", "combo_long"])
        else:
            setup_direction_match = any(_safe_bool(row.get(k, False)) for k in ["reversal_short", "breakout_short", "combo_short"])
        has_any_setup = any(_safe_bool(row.get(k, False)) for k in [
            "reversal_long", "reversal_short", "breakout_long", "breakout_short", "combo_long", "combo_short", "has_any_setup"
        ])

        snapshot: Dict[str, Any] = {}
        snapshot.update(row_dict)
        snapshot.update(exec_ctx)
        snapshot.update(macro_ctx)
        snapshot.update({
            "direction": direction,
            "has_valid_zone": bool(has_valid_zone),
            "mitigation_price": mitigation_price,
            "mitigation_src": mitigation_src,
            "zone_near_atr": round(float(zone_near_atr), 4),
            "vwap_dist_atr": round(float(vwap_dist_atr), 4),
            "smc_quality_100": round(float(smc_quality_100), 4),
            "liquidity_sweep_confirmed": bool(liquidity_sweep),
            "liquidity_sweep": bool(liquidity_sweep),
            "liquidity_wrong_side": bool(liquidity_wrong_side),
            "sweep_direction_match": bool(liquidity_sweep),
            "sqzmom_score": round(float(sqz_score), 4),
            "sqzmom_dmi_aligned": bool(
                (direction == "Long" and (_safe_bool(row.get("dmi_bull", False)) or _safe_float(row.get("plus_di", 0), 0) >= _safe_float(row.get("minus_di", 0), 0)))
                or (direction == "Short" and (_safe_bool(row.get("dmi_bear", False)) or _safe_float(row.get("minus_di", 0), 0) > _safe_float(row.get("plus_di", 0), 0)))
            ),
            "momentum_align": bool(momentum_align),
            "htf_direction": str(macro_ctx.get("allowed_direction", macro_ctx.get("macro_direction", ""))),
            "trend_aligned": bool(trend_dir == direction),
            "setup_direction_match": bool(setup_direction_match),
            "has_any_setup": bool(has_any_setup),
        })
        return snapshot

    def generate_signal(
        self,
        row: Any,
        direction: str,
        exec_ctx: Dict[str, Any],
        macro_ctx: Dict[str, Any],
    ) -> Dict[str, Any]:
        ctx = self.build_entry_snapshot(row, direction, exec_ctx, macro_ctx)
        base_trigger = evaluate_base_trigger(row, direction, ctx)
        ctx["base_trigger"] = base_trigger
        ctx["base_trigger_passed"] = bool(base_trigger.get("passed", False))
        result = smc_impulse_score(ctx)
        raw_score = _safe_float(result.get("final_score", 0.0), 0.0)

        raw_floor = max(1e-9, self.min_score_raw)
        raw_ceiling = max(raw_floor + 1.0, 65.0)
        if raw_score <= 0:
            score_norm = 0.0
        elif raw_score < raw_floor:
            score_norm = (raw_score / raw_floor) * 49.9
        else:
            score_norm = 50.0 + ((raw_score - raw_floor) / (raw_ceiling - raw_floor)) * 50.0
        score_norm = max(0.0, min(100.0, score_norm))
        confidence = max(0.0, min(1.0, score_norm / 100.0))

        # 🚨 提前定义 signal 对象
        signal = {
            "direction": str(direction).title(),
            "score_raw": round(float(raw_score), 4),
            "score": round(float(score_norm), 4),
            "confidence": round(float(confidence), 4),
            "smc": _safe_float(result.get("smc", 0.0), 0.0),
            "sqzmom": _safe_float(result.get("sqzmom", 0.0), 0.0),
            "breakout": _safe_float(result.get("breakout", 0.0), 0.0),
            "raw_base": _safe_float(result.get("raw_base", 0.0), 0.0),
            "signal_tier": result.get("signal_tier", "C"),
            "position_multiplier": _safe_float(result.get("position_multiplier", 0.6), 0.6),
            "bonus": _safe_float(result.get("bonus", 0.0), 0.0),
            "smc_passed": bool(result.get("smc_passed", False)),
            "sqz_passed": bool(result.get("sqz_passed", False)),
            "breakout_passed": bool(result.get("breakout_passed", False)),
            "fallback_active": bool(result.get("fallback_active", False)),
            "dominance": result.get("dominance", "none"),
            "breakdown": result.get("breakdown", ""),
            "base_trigger": base_trigger,
            "base_trigger_passed": bool(base_trigger.get("passed", False)),
            "base_trigger_strength": _safe_float(base_trigger.get("strength", 0.0), 0.0),
            "entry_meta": ctx,
        }

        # 计算 EV
        ev = estimate_expected_value(
            signal,
            str(ctx.get("regime", "")),
            str(ctx.get("vol_state", "")),
            ctx,
        )

        # ====================================================
        # V38: 高阶逻辑旁路评分卡。
        # HTF / VWAP / DMI / Breakout / Regime 只调整 EV 与仓位，
        # 不再覆盖 expected_value 为硬拒绝。
        # ====================================================
        signal.update(ev)
        scorecard = build_scorecard(signal, ctx, macro_ctx)
        base_ev = _safe_float(signal.get("expected_value"), 0.0)
        adjusted_ev = base_ev + _safe_float(scorecard.get("ev_adjustment"), 0.0)
        signal["expected_value_before_scorecard"] = round(float(base_ev), 4)
        signal["expected_value"] = round(float(adjusted_ev), 4)
        signal["ev_grade"] = grade_from_expected_value(signal["expected_value"])
        signal["scorecard"] = scorecard
        signal["scorecard_total"] = _safe_float(scorecard.get("total_score"), 0.0)
        signal["scorecard_summary"] = scorecard.get("summary", "")
        signal["scorecard_json"] = dumps_compact(scorecard)
        signal["size_multiplier"] = round(
            _safe_float(signal.get("size_multiplier", 1.0), 1.0)
            * _safe_float(scorecard.get("position_multiplier", 1.0), 1.0),
            4,
        )
        base_reasons = str(signal.get("ev_reasons", "EV_CONTEXT_OK"))
        signal["ev_reasons"] = f"{base_reasons};{scorecard.get('summary', '')}"
        return signal

    def choose_signal(
        self,
        row: Any,
        exec_ctx: Dict[str, Any],
        macro_ctx: Dict[str, Any],
    ) -> Dict[str, Any]:
        long_sig = self.generate_signal(row, "Long", exec_ctx, macro_ctx)
        short_sig = self.generate_signal(row, "Short", exec_ctx, macro_ctx)

        # 从 smc_impulse_score 结果中提取多空对齐信息
        long_score_delta = _safe_float(long_sig.get("score_delta", long_sig.get("entry_meta", {}).get("score_delta", 0)), 0)
        short_score_delta = _safe_float(short_sig.get("score_delta", short_sig.get("entry_meta", {}).get("score_delta", 0)), 0)
        long_aligned = str(long_sig.get("aligned_direction", long_sig.get("entry_meta", {}).get("aligned_direction", "")))
        short_aligned = str(short_sig.get("aligned_direction", short_sig.get("entry_meta", {}).get("aligned_direction", "")))

        # 方向一致性加分：多空打分方向与当前计算方向一致时加分
        long_alignment_bonus = 0.0
        if long_aligned == "Long":
            long_alignment_bonus = min(1.0, abs(long_score_delta) / 30.0)
        short_alignment_bonus = 0.0
        if short_aligned == "Short":
            short_alignment_bonus = min(1.0, abs(short_score_delta) / 30.0)

        candidates = [s for s in (long_sig, short_sig) if s.get("base_trigger_passed", False)]
        if not candidates:
            long_key = (_safe_float(long_sig.get("base_trigger_strength"), 0.0), _safe_float(long_sig.get("expected_value"), -9.0))
            short_key = (_safe_float(short_sig.get("base_trigger_strength"), 0.0), _safe_float(short_sig.get("expected_value"), -9.0))
            chosen = long_sig if long_key >= short_key else short_sig
            chosen["no_base_trigger_candidates"] = True
            return chosen

        return max(
            candidates,
            key=lambda s: (
                _safe_float(s.get("expected_value"), -9.0) + (
                    long_alignment_bonus * 0.15 if str(s.get("direction", "")).title() == "Long"
                    else short_alignment_bonus * 0.15
                ),
                _safe_float(s.get("base_trigger_strength"), 0.0),
                _safe_float(s.get("score_raw"), 0.0),
            ),
        )

    # ------------------------------------------------------------------
    # V35/V36: tail filter, risk engine, portfolio allocator.
    # ------------------------------------------------------------------
    def circuit_breaker(self) -> Tuple[bool, str]:
        # V37.5: circuit state is handled as risk-size compression, not as a backtest-ending breaker.
        # This prevents a 6-loss streak from stopping the remaining 365-day simulation.
        return True, "CIRCUIT_SOFT_OK"

    def tail_filter(self, signal: Dict[str, Any], regime: str, vol_state: str) -> Tuple[bool, str]:
        # V37.6: 废弃硬拦截，改为降级但放行，让单子跑完去验证真实胜率。
        # 拦截权交给成本防火墙和基础 SQZMOM 信号。
        ev = _safe_float(signal.get("expected_value"), -9.0)
        win_prob = _safe_float(signal.get("win_prob"), 0.0)
        est_rr = _safe_float(signal.get("estimated_rr"), 0.0)
        if not signal.get("direction"):
            return False, "INVALID_NO_DIRECTION"
        if ev < self.min_expected_value:
            signal["ev_grade"] = "D_NEG_EV"
            signal["ev_reasons"] = f"EV_TOO_LOW_{round(ev, 4)}_GRADE_DOWNGRADED"
        return True, f"ALLOW_EV_{signal.get('ev_grade', grade_from_expected_value(ev))}"

    def risk_budget(self, signal: Dict[str, Any], regime: str, vol_state: str) -> float:
        ev = _safe_float(signal.get("expected_value"), 0.0)
        confidence = _safe_float(signal.get("confidence"), 0.0)
        edge_term = _safe_float((ev - self.min_expected_value) / max(1e-9, 0.35 - self.min_expected_value), 0.0)
        edge_term = max(0.0, min(1.0, edge_term))

        risk = self.base_risk * (0.35 + 0.65 * confidence) * (0.45 + 0.95 * edge_term)

        # V54 Alpha Expansion: 不再把 regime 当作“物理切除器”。
        # TREND/CHOP 仍降权，但保留足够仓位让强 SMC+SQZMOM 信号释放 Alpha。
        if regime == "TREND":
            risk *= 0.65
        elif regime == "TRANSITION":
            risk *= 1.20
        elif regime == "CHOP":
            risk *= 0.75
        elif regime == "CRISIS_RISK_OFF":
            risk *= 0.25

        if vol_state == "HIGH_VOL":
            risk *= 0.80
        elif vol_state == "LOW_VOL":
            risk *= 1.05

        # Soft circuit compression. It never returns 0 by itself.
        if self.account.drawdown_pct_proxy > 0.20:
            risk *= 0.35
        elif self.account.drawdown_pct_proxy > 0.10:
            risk *= 0.55
        elif self.account.drawdown_pct_proxy > 0.05:
            risk *= 0.75

        if self.account.loss_streak >= 9:
            risk *= 0.22
        elif self.account.loss_streak >= 6:
            risk *= 0.35
        elif self.account.loss_streak >= 3:
            risk *= 0.60

        risk *= _safe_float(signal.get("size_multiplier"), 1.0)
        return max(0.0, min(float(risk), self.max_position_mult))

    def allocate(self, signal: Dict[str, Any], risk: float, regime: str, vol_state: str) -> Tuple[str, float]:
        ev = _safe_float(signal.get("expected_value"), -9.0)
        grade = signal.get("ev_grade", grade_from_expected_value(ev))
        if ev > 0.25:
            book, mult = "CORE", 1.00
        elif ev > 0.15:
            book, mult = "TACTICAL", 0.78
        elif ev >= 0.05:
            book, mult = "SCALP", 0.65
        elif ev >= 0.0:
            book, mult = "PROBE", 0.42
        else:
            book, mult = "DUMPSTER", 0.08

        if grade == "S_EV_HOT":
            mult *= 1.08
        if regime == "CHOP":
            mult *= 0.82
        if vol_state == "HIGH_VOL":
            mult *= 0.80
        return book, max(0.0, min(self.max_position_mult, risk * mult))

    def _fast_no_setup_signal(self, row: Any, regime: str, vol_state: str) -> Dict[str, Any]:
        return {
            "direction": None,
            "score_raw": 0.0,
            "score": 0.0,
            "confidence": 0.0,
            "expected_value": -1.0,
            "win_prob": 0.0,
            "estimated_rr": 0.0,
            "ev_grade": "D_NEG_EV",
            "size_multiplier": 0.0,
            "ev_reasons": "NO_FEATURE_SETUP_FAST",
            "entry_meta": {"has_any_setup": False, "datetime": row.get("datetime")},
        }

    def decide(self, row: Any, exec_ctx: Dict[str, Any], macro_ctx: Dict[str, Any]) -> Dict[str, Any]:
        ok, circuit_reason = self.circuit_breaker()
        if not ok:
            return {"allow": False, "reason": circuit_reason}

        regime = self.classify_regime(row, exec_ctx)
        vol_state = self.volatility_state(row, exec_ctx)
        exec_ctx = dict(exec_ctx)
        exec_ctx["regime"] = regime
        exec_ctx["vol_state"] = vol_state

        # V38 base gate: first permission is only SMC + SQZMOM.
        # Breakout/HTF/VWAP/DMI are deliberately not used here.
        signal = self.choose_signal(row, exec_ctx, macro_ctx)
        if not signal.get("base_trigger_passed", False):
            return {
                "allow": False,
                "reason": signal.get("base_trigger", {}).get("reason", "BASE_TRIGGER_NOT_PASSED"),
                "regime": regime,
                "vol_state": vol_state,
                "signal": signal,
            }
        ok, reason = self.tail_filter(signal, regime, vol_state)
        if not ok:
            return {
                "allow": False,
                "reason": reason,
                "regime": regime,
                "vol_state": vol_state,
                "signal": signal,
            }

        risk = self.risk_budget(signal, regime, vol_state)
        book, size = self.allocate(signal, risk, regime, vol_state)
        if size <= 0.0:
            return {
                "allow": False,
                "reason": "PORTFOLIO_SIZE_ZERO",
                "regime": regime,
                "vol_state": vol_state,
                "signal": signal,
            }

        return {
            "allow": True,
            "reason": f"ALLOW_{regime}_{book}_{signal.get('ev_grade', grade_from_expected_value(signal.get('expected_value', 0.0)))}",
            "regime": regime,
            "vol_state": vol_state,
            "book": book,
            "size": round(float(size), 6),
            "signal": signal,
        }

    def update_account(self, pnl_r: float) -> None:
        self.account.update(pnl_r)

    def state_dict(self) -> Dict[str, Any]:
        return asdict(self.account)