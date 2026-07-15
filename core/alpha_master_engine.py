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

from utils.safe import safe_float, safe_bool

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

from analysis.fvg_stop_hunt import nearest_mitigation_price


VERSION = "V38_DECOUPLED_SCORECARD_20260616"


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
        pnl_r = safe_float(pnl_r, 0.0)
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
        self.last_trade_direction: Optional[str] = None

    # ------------------------------------------------------------------
    # V34: single regime source (kept for backward compat)
    # ------------------------------------------------------------------
    def classify_regime(self, row: Any, exec_ctx: Dict[str, Any]) -> str:
        adx = safe_float(row.get("adx", exec_ctx.get("adx", 0.0)), 0.0)
        atr = safe_float(row.get("ATRr_14", exec_ctx.get("atr", 0.0)), 0.0)
        close = safe_float(row.get("close", 0.0), 0.0)
        volume_ratio = safe_float(row.get("volume_ratio", 1.0), 1.0)
        atr_pct = atr / close if close > 0 else safe_float(exec_ctx.get("atr_pct", 0.0), 0.0)

        # ===== V21: MarketCrisisDetector 多维危机检测叠加 =====
        crisis_level = int(exec_ctx.get("crisis_level", 0))
        if crisis_level >= 3:
            return "CRISIS_RISK_OFF"
        if crisis_level >= 2:
            if atr_pct >= 0.020 or volume_ratio <= 0.40:
                return "CRISIS_RISK_OFF"
            return "TRANSITION"

        # Crisis has priority: liquidity drain or abnormal volatility.
        if atr_pct >= 0.030 or volume_ratio <= 0.35 or volume_ratio >= 4.0:
            return "CRISIS_RISK_OFF"
        if adx >= 23.0:
            return "TREND"
        if adx <= 15.0:
            return "CHOP"
        return "TRANSITION"

    def volatility_state(self, row: Any, exec_ctx: Dict[str, Any]) -> str:
        atr_pct = safe_float(exec_ctx.get("atr_pct", 0.0), 0.0)
        volume_ratio = safe_float(row.get("volume_ratio", 1.0), 1.0)
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
        price = safe_float(row.get("close", 0.0), 0.0)
        atr = max(safe_float(row.get("ATRr_14", exec_ctx.get("atr", 0.0)), 0.0), 1e-12)

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
            zone_near_atr = abs(price - safe_float(mitigation_price, price)) / atr

        vwap = safe_float(row.get("vwap_48", row.get("VWAP", row.get("vwap", price))), price)
        vwap_dist_atr = abs(price - vwap) / atr if atr > 0 else 9.99

        # Directional SMC quality from the feature pipeline.  This is used by the
        # decoupled base trigger, while FVG/OB/sweep stay as soft structure evidence.
        if direction == "Long":
            smc_quality_100 = safe_float(row.get("smc_quality_score_bull", row.get("smc_quality_score", 0.0)), 0.0)
        else:
            smc_quality_100 = safe_float(row.get("smc_quality_score_bear", row.get("smc_quality_score", 0.0)), 0.0)

        # Directional liquidity context.
        long_sweep = any(safe_bool(row.get(k, False)) for k in [
            "sellside_sweep", "sellside_liquidity_taken", "bullish_stop_hunt", "sweep_low", "liquidity_sweep_long"
        ])
        short_sweep = any(safe_bool(row.get(k, False)) for k in [
            "buyside_sweep", "buyside_liquidity_taken", "bearish_stop_hunt", "sweep_high", "liquidity_sweep_short"
        ])
        liquidity_sweep = long_sweep if direction == "Long" else short_sweep
        liquidity_wrong_side = short_sweep if direction == "Long" else long_sweep

        # Lightweight SQZMOM score matching existing 0~44 convention.
        sqz_score = 0.0
        if direction == "Long":
            if safe_float(row.get("momentum", 0.0), 0.0) > 0:
                sqz_score += 7.0
            if safe_float(row.get("momentum_slope", 0.0), 0.0) > 0:
                sqz_score += 6.0
            if safe_bool(row.get("sqzmom_reversal_confirm_long", False)):
                sqz_score += 8.0
            if safe_bool(row.get("dmi_bull", False)) or safe_float(row.get("plus_di", 0.0), 0.0) >= safe_float(row.get("minus_di", 0.0), 0.0):
                sqz_score += 7.0
        else:
            if safe_float(row.get("momentum", 0.0), 0.0) < 0:
                sqz_score += 7.0
            if safe_float(row.get("momentum_slope", 0.0), 0.0) < 0:
                sqz_score += 6.0
            if safe_bool(row.get("sqzmom_reversal_confirm_short", False)):
                sqz_score += 8.0
            if safe_bool(row.get("dmi_bear", False)) or safe_float(row.get("minus_di", 0.0), 0.0) > safe_float(row.get("plus_di", 0.0), 0.0):
                sqz_score += 7.0
        if safe_bool(row.get("squeeze_released", False)):
            sqz_score += 6.0
        if str(row.get("sqzmom_divergence_dir", "None")) == direction and safe_float(row.get("sqzmom_divergence_age", 999), 999) <= 18:
            sqz_score += 10.0
        sqz_score = max(0.0, min(44.0, sqz_score))

        trend_dir = str(exec_ctx.get("trend_direction", "None"))
        momentum_align = (
            (direction == "Long" and safe_float(row.get("momentum", 0.0), 0.0) > 0)
            or (direction == "Short" and safe_float(row.get("momentum", 0.0), 0.0) < 0)
        )

        # Directional setup flags are soft EV inputs, not hard gates.
        if direction == "Long":
            setup_direction_match = any(safe_bool(row.get(k, False)) for k in ["reversal_long", "breakout_long", "combo_long"])
        else:
            setup_direction_match = any(safe_bool(row.get(k, False)) for k in ["reversal_short", "breakout_short", "combo_short"])
        has_any_setup = any(safe_bool(row.get(k, False)) for k in [
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
                (direction == "Long" and (safe_bool(row.get("dmi_bull", False)) or safe_float(row.get("plus_di", 0), 0) >= safe_float(row.get("minus_di", 0), 0)))
                or (direction == "Short" and (safe_bool(row.get("dmi_bear", False)) or safe_float(row.get("minus_di", 0), 0) > safe_float(row.get("plus_di", 0), 0)))
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
        raw_score = safe_float(result.get("final_score", 0.0), 0.0)

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
            "smc": safe_float(result.get("smc", 0.0), 0.0),
            "sqzmom": safe_float(result.get("sqzmom", 0.0), 0.0),
            "breakout": safe_float(result.get("breakout", 0.0), 0.0),
            "raw_base": safe_float(result.get("raw_base", 0.0), 0.0),
            "signal_tier": result.get("signal_tier", "C"),
            "position_multiplier": safe_float(result.get("position_multiplier", 0.6), 0.6),
            "bonus": safe_float(result.get("bonus", 0.0), 0.0),
            "smc_passed": bool(result.get("smc_passed", False)),
            "sqz_passed": bool(result.get("sqz_passed", False)),
            "breakout_passed": bool(result.get("breakout_passed", False)),
            "fallback_active": bool(result.get("fallback_active", False)),
            "dominance": result.get("dominance", "none"),
            "breakdown": result.get("breakdown", ""),
            "base_trigger": base_trigger,
            "base_trigger_passed": bool(base_trigger.get("passed", False)),
            "base_trigger_strength": safe_float(base_trigger.get("strength", 0.0), 0.0),
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
        base_ev = safe_float(signal.get("expected_value"), 0.0)
        adjusted_ev = base_ev + safe_float(scorecard.get("ev_adjustment"), 0.0)
        signal["expected_value_before_scorecard"] = round(float(base_ev), 4)
        signal["expected_value"] = round(float(adjusted_ev), 4)
        signal["ev_grade"] = grade_from_expected_value(signal["expected_value"])
        signal["scorecard"] = scorecard
        signal["scorecard_total"] = safe_float(scorecard.get("total_score"), 0.0)
        signal["scorecard_summary"] = scorecard.get("summary", "")
        signal["scorecard_json"] = dumps_compact(scorecard)
        signal["size_multiplier"] = round(
            safe_float(signal.get("size_multiplier", 1.0), 1.0)
            * safe_float(scorecard.get("position_multiplier", 1.0), 1.0),
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
        long_score_delta = safe_float(long_sig.get("score_delta", long_sig.get("entry_meta", {}).get("score_delta", 0)), 0)
        short_score_delta = safe_float(short_sig.get("score_delta", short_sig.get("entry_meta", {}).get("score_delta", 0)), 0)
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
            long_key = (safe_float(long_sig.get("base_trigger_strength"), 0.0), safe_float(long_sig.get("expected_value"), -9.0))
            short_key = (safe_float(short_sig.get("base_trigger_strength"), 0.0), safe_float(short_sig.get("expected_value"), -9.0))
            chosen = long_sig if long_key >= short_key else short_sig
            chosen["no_base_trigger_candidates"] = True
            return chosen

        # 【修复20260704】当两方向 EV 都接近 0 或都 < 0.08 时，
        # 不再仅凭 EV 比较（谁大谁赢），改为评分/置信度优先。
        # 防止 CHOP 市场中 EV=0.0 vs EV=-0.64 这种不合理的偏空选择。
        long_ev = safe_float(long_sig.get("expected_value"), 0.0)
        short_ev = safe_float(short_sig.get("expected_value"), 0.0)
        both_ev_low = (long_ev < 0.08 and short_ev < 0.08)
        both_ev_neg = (long_ev <= 0.0 and short_ev <= 0.0)
        
        if both_ev_neg:
            # 两方向 EV 都 <= 0：完全按 score_raw + confidence 选
            return max(
                candidates,
                key=lambda s: (
                    safe_float(s.get("score_raw"), 0.0),
                    safe_float(s.get("confidence"), 0.0),
                    safe_float(s.get("base_trigger_strength"), 0.0),
                ),
        )
        elif both_ev_low:
            # EV 都很低（<0.08）：EV 权重降到 0.3，score_raw 权重 0.7
            return max(
                candidates,
                key=lambda s: (
                    safe_float(s.get("expected_value"), -9.0) * 0.3 +
                    safe_float(s.get("score_raw"), 0.0) * 0.7 / 100.0,
                    safe_float(s.get("base_trigger_strength"), 0.0),
                ),
            )
        else:
            # 至少一边 EV 健康：正常 EV 优先 + alignment bonus
            return max(
                candidates,
                key=lambda s: (
                    safe_float(s.get("expected_value"), -9.0) + (
                        long_alignment_bonus * 0.15 if str(s.get("direction", "")).title() == "Long"
                        else short_alignment_bonus * 0.15
                    ),
                    safe_float(s.get("base_trigger_strength"), 0.0),
                    safe_float(s.get("score_raw"), 0.0),
                ),
            )

    # ------------------------------------------------------------------
    # V35/V36: tail filter, risk engine, portfolio allocator.
    # ------------------------------------------------------------------
    def circuit_breaker(self) -> Tuple[bool, str]:
        # V37.5: circuit state is handled as risk-size compression, not as a backtest-ending breaker.
        # This prevents a 6-loss streak from stopping the remaining 365-day simulation.
        if self.account.loss_streak >= 12:
            return False, "CIRCUIT_HARD_STOP_12LOSS"
        if self.account.drawdown_pct_proxy >= 0.35:
            return False, "CIRCUIT_HARD_STOP_DD35"
        return True, "CIRCUIT_SOFT_OK"

    def tail_filter(self, signal: Dict[str, Any], regime: str, vol_state: str) -> Tuple[bool, str]:
        # V37.6: 废弃硬拦截，改为降级但放行，让单子跑完去验证真实胜率。
        # 拦截权交给成本防火墙和基础 SQZMOM 信号。
        ev = safe_float(signal.get("expected_value"), -9.0)
        win_prob = safe_float(signal.get("win_prob"), 0.0)
        est_rr = safe_float(signal.get("estimated_rr"), 0.0)
        if not signal.get("direction"):
            return False, "INVALID_NO_DIRECTION"
        if ev < self.min_expected_value:
            signal["ev_grade"] = "D_NEG_EV"
            signal["ev_reasons"] = f"EV_TOO_LOW_{round(ev, 4)}_GRADE_DOWNGRADED"
        return True, f"ALLOW_EV_{signal.get('ev_grade', grade_from_expected_value(ev))}"

    def risk_budget(self, signal: Dict[str, Any], regime: str, vol_state: str) -> float:
        ev = safe_float(signal.get("expected_value"), 0.0)
        confidence = safe_float(signal.get("confidence"), 0.0)
        edge_term = safe_float((ev - self.min_expected_value) / max(1e-9, 0.35 - self.min_expected_value), 0.0)
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

        # V21: MarketCrisisDetector 额外压缩（叠加在 regime 之上）
        crisis_level = int(exec_ctx.get("crisis_level", 0))
        if crisis_level >= 3:
            risk *= 0.0  # 熔断
        elif crisis_level >= 2:
            risk *= 0.15  # 严重危机
        elif crisis_level >= 1:
            risk *= 0.50  # 预警

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

        risk *= safe_float(signal.get("size_multiplier"), 1.0)
        return max(0.0, min(float(risk), self.max_position_mult))

    def allocate(self, signal: Dict[str, Any], risk: float, regime: str, vol_state: str) -> Tuple[str, float]:
        ev = safe_float(signal.get("expected_value"), -9.0)
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

    def _build_verdict(
        self,
        allow: bool,
        reason: str,
        regime: str,
        vol_state: str,
        signal: Dict[str, Any],
        book: str = "",
        size: float = 0.0,
        long_sig: Optional[Dict[str, Any]] = None,
        short_sig: Optional[Dict[str, Any]] = None,
        is_follow: bool = False,
    ) -> Dict[str, Any]:
        """构建全中文可读信号单"""
        sig = signal or {}
        entry = sig.get("entry_meta", {})
        close = safe_float(entry.get("close", 0.0))
        atr = max(safe_float(entry.get("ATRr_14", entry.get("atr", 0.0)), 0.0), 1e-12)

        # 双方向 EV
        ev_snapshot = {}
        for label, s in [("Long", long_sig), ("Short", short_sig)]:
            if s:
                ev_snapshot[label] = {
                    "score": round(safe_float(s.get("score"), 0.0), 2),
                    "score_raw": round(safe_float(s.get("score_raw"), 0.0), 2),
                    "expected_value": round(safe_float(s.get("expected_value"), 0.0), 4),
                    "ev_grade": str(s.get("ev_grade", "?")),
                    "base_trigger_passed": bool(s.get("base_trigger_passed", False)),
                }

        l_info = ev_snapshot.get("Long", {})
        s_info = ev_snapshot.get("Short", {})
        long_score = l_info.get("score", 0.0)
        short_score = s_info.get("score", 0.0)
        long_ev = l_info.get("expected_value", 0.0)
        short_ev = s_info.get("expected_value", 0.0)
        score_diff = abs(long_score - short_score)

        if long_score > short_score and long_ev > short_ev:
            adv_detail = "\u504f\u591a" if long_ev > 0 else "\u504f\u591a\u4f46EV\u504f\u5f31"
        elif short_score > long_score and short_ev > long_ev:
            adv_detail = "\u504f\u7a7a" if short_ev > 0 else "\u504f\u7a7a\u4f46EV\u504f\u5f31"
        else:
            adv_detail = "\u89c2\u671b\u7b49\u5f85"

        # 价格失衡区
        fvg_dir = str(entry.get("fvg_direction", "None"))
        fvg_mid = entry.get("fvg_mid", None)
        ob_dir = str(entry.get("ob_direction", "None"))
        ob_top = entry.get("ob_top", None)
        ob_bottom = entry.get("ob_bottom", None)

        imbalance_parts = []
        if fvg_dir != "None" and fvg_mid is not None:
            _fvg_mid = float(fvg_mid) if isinstance(fvg_mid, (int, float)) else 0.0
            label_fvg = "\u591a\u5934FVG" if fvg_dir == "Long" else "\u7a7a\u5934FVG"
            imbalance_parts.append(f"{label_fvg}: {_fvg_mid:.2f}")

        ob_str = "\u6682\u65e0"
        if ob_dir != "None" and ob_top is not None and ob_bottom is not None:
            _ob_top = float(ob_top) if isinstance(ob_top, (int, float)) else 0.0
            _ob_bot = float(ob_bottom) if isinstance(ob_bottom, (int, float)) else 0.0
            label_ob = "\u4e70\u65b9OB" if ob_dir == "Long" else "\u5356\u65b9OB"
            ob_str = f"{label_ob}: {_ob_bot:.2f}~{_ob_top:.2f}"
            imbalance_parts.append(ob_str)

        imbalance_str = "\u6682\u65e0" if not imbalance_parts else ", ".join(imbalance_parts)

        # 行情环境
        regime_cn = {"TREND": "\u8d8b\u52bf", "CHOP": "\u9707\u8361", "TRANSITION": "\u8fc7\u6e21", "CRISIS_RISK_OFF": "\u5371\u673a\u6a21\u5f0f", "?": "\u672a\u77e5"}.get(regime, regime)
        vol_cn = {"HIGH_VOL": "\u9ad8\u6ce2\u52a8", "LOW_VOL": "\u4f4e\u6ce2\u52a8", "MID_VOL": "\u6b63\u5e38"}.get(vol_state, vol_state)
        sqz_mult = safe_float(entry.get("sqz_mult", 1.0))
        sqz_state = "\u538b\u7f29\u4e2d" if sqz_mult < 1.0 else ("\u6269\u5f20\u4e2d" if sqz_mult > 1.5 else "\u6b63\u5e38")
        volume_ratio = safe_float(entry.get("volume_ratio", 1.0))
        if volume_ratio < 0.5:
            vol_str = f"{volume_ratio:.2f}x (\u6781\u5ea6\u7f29\u91cf)"
        elif volume_ratio < 0.8:
            vol_str = f"{volume_ratio:.2f}x (\u7f29\u91cf)"
        elif volume_ratio > 2.0:
            vol_str = f"{volume_ratio:.2f}x (\u653e\u91cf)"
        else:
            vol_str = f"{volume_ratio:.2f}x (\u6b63\u5e38)"

        mom = safe_float(entry.get("momentum", 0.0))
        rsi = safe_float(entry.get("rsi", 50.0))
        adx = safe_float(entry.get("adx", 0.0))
        macd = safe_float(entry.get("macd", safe_float(entry.get("MACD", 0.0))))
        atr_pct = atr / close * 100 if close > 0 else 0.0
        sqz_white = safe_bool(entry.get("sqzmom_white", False))

        kline_color = "\u767d\u8272 (\u8870\u7aed)" if sqz_white else ("\u7eff\u8272 (\u591a\u5934)" if mom > 0 else "\u7ea2\u8272 (\u7a7a\u5934)")
        rsi_status = "\u8d85\u4e70" if rsi > 70 else ("\u8d85\u5356" if rsi < 30 else ("\u504f\u5f3a" if rsi > 55 else ("\u504f\u5f31" if rsi < 45 else "\u4e2d\u6027")))
        adx_status = "\u5f3a\u8d8b\u52bf" if adx >= 25 else ("\u5f31\u8d8b\u52bf/\u9707\u8361" if adx >= 15 else "\u6781\u5f31/\u65e0\u8d8b\u52bf")
        macd_status = "\u504f\u591a" if macd > 0 else "\u504f\u7a7a"

        bsl = safe_float(entry.get("last_swing_high", 0.0))
        ssl = safe_float(entry.get("last_swing_low", 0.0))
        bsl_swept = any(safe_bool(entry.get(k, False)) for k in ["buyside_sweep", "buyside_liquidity_taken", "bearish_stop_hunt"])
        ssl_swept = any(safe_bool(entry.get(k, False)) for k in ["sellside_sweep", "sellside_liquidity_taken", "bullish_stop_hunt"])

        liquidity_lines = []
        if bsl > 0:
            bsl_dist = (bsl - close) / close * 100
            liquidity_lines.append(f"BSL: {bsl:.2f}(\u8ddd\u79bb{abs(bsl_dist):.2f}%) | \u5df2\u626b: {'\u662f' if bsl_swept else '\u5426'}")
        if ssl > 0:
            ssl_dist = (close - ssl) / close * 100
            liquidity_lines.append(f"SSL: {ssl:.2f}(\u8ddd\u79bb{abs(ssl_dist):.2f}%) | \u5df2\u626b: {'\u662f' if ssl_swept else '\u5426'}")

        notional_dir = str(sig.get("direction", "?")).title()
        dir_emoji = "\U0001f4c8" if notional_dir == "Long" else "\U0001f4c9"
        dir_cn = "\u591a\u5934" if notional_dir == "Long" else "\u7a7a\u5934"
        entry_price = close
        direction_mult = 1.0 if notional_dir == "Long" else -1.0
        sl = entry_price - direction_mult * 1.5 * atr
        tp1 = entry_price + direction_mult * 1.5 * atr
        tp2 = entry_price + direction_mult * 3.0 * atr
        tp3 = entry_price + direction_mult * 4.5 * atr
        est_rr = safe_float(sig.get("estimated_rr", 1.5))

        verdict = {
            "allow": allow,
            "reason": reason,
            "regime": regime,
            "vol_state": vol_state,
        }

        if allow:
            verdict["book"] = book
            verdict["size"] = round(float(size), 6)
            verdict["action"] = "OPEN"
            verdict["direction"] = notional_dir

            lines = []
            lines.append(f"\u2501\u2501\u2501 [{signal_type}] {dir_emoji} {dir_cn} \u2501\u2501\u2501")
            lines.append(f"\U0001f4d0 [\u4ef7\u683c\u5931\u8861\u533a] {imbalance_str}")
            lines.append(f"\u65b9\u5411: {dir_emoji} {dir_cn} | {imbalance_str}")
            lines.append(f"\u2501\u2501\u2501 \u591a\u7a7a\u535a\u5f08 \u2501\u2501\u2501")
            lines.append(f"\u591a\u5934: {long_score:.1f}\u5206 EV:{long_ev:+.4f}  \u7a7a\u5934: {short_score:.1f}\u5206 EV:{short_ev:+.4f}  \u5206\u5dee: {score_diff:.1f}\u5206")
            lines.append(f"\u5efa\u8bae: {adv_detail}")
            lines.append(f"\u2501\u2501\u2501 \u884c\u60c5\u73af\u5883 \u2501\u2501\u2501")
            lines.append(f"\u8d8b\u52bf: {regime_cn} | \u6ce2\u52a8: {vol_cn} | \u538b\u7f29: {sqz_state}")
            lines.append(f"\u6210\u4ea4\u91cf: {vol_str}")
            lines.append(f"\u2501\u2501\u2501 \u6307\u6807\u900f\u89c6 \u2501\u2501\u2501")
            lines.append(f"K\u7ebf: {kline_color} | \u53d8\u8272: {'\u662f' if sqz_white else '\u5426'}")
            lines.append(f"RSI: {rsi:.1f}({rsi_status}) ADX: {adx:.1f}({adx_status})")
            lines.append(f"MACD: {macd:.4f}({macd_status}) ATR: {atr:.2f} | {atr_pct:.2f}%")
            lines.append(f"\u2501\u2501\u2501 \u6d41\u52a8\u6027/\u5173\u952e\u4f4d \u2501\u2501\u2501")
            for liq in liquidity_lines:
                lines.append(liq)
            lines.append(f"\u4e70\u65b9OB: {ob_str if ob_dir == 'Long' else '\u6682\u65e0'}")
            lines.append(f"\u5356\u65b9OB: {ob_str if ob_dir == 'Short' else '\u6682\u65e0'}")
            lines.append(f"\u591a\u5934FVG: {float(fvg_mid):.2f}" if (fvg_dir == 'Long' and fvg_mid is not None) else "\u591a\u5934FVG: \u6682\u65e0")
            lines.append(f"\u7a7a\u5934FVG: {float(fvg_mid):.2f}" if (fvg_dir == 'Short' and fvg_mid is not None) else "\u7a7a\u5934FVG: \u6682\u65e0")
            lines.append(f"\u53c2\u8003\u5f00\u5355: {dir_emoji} {dir_cn} \u5165\u573a{entry_price:.2f} SL{sl:.2f} TP1{tp1:.2f} TP2{tp2:.2f} TP3{tp3:.2f} RR{est_rr:.2f}")
            verdict["summary"] = "\n".join(lines)
        else:
            verdict["action"] = "REJECT"
            verdict["summary"] = f"\u62d2\u7edd\u5f00\u5355: {reason}"

        reasons_flat = []
        bt = sig.get("base_trigger", {})
        if not bt.get("passed", False):
            reasons_flat.append(f"base_trigger={bt.get('reason', 'NOT_PASSED')}")
        ev_rs = str(sig.get("ev_reasons", "")).strip()
        if ev_rs and ev_rs != "EV_CONTEXT_OK":
            for r in ev_rs.split(";"):
                r = r.strip()
                if r:
                    reasons_flat.append(f"ev:{r}")
        cluster_r = str(sig.get("alpha_cluster_reason", "")).strip()
        if cluster_r:
            reasons_flat.append(f"cluster:{cluster_r}")

        verdict["reasons"] = reasons_flat
        verdict["ev_snapshot"] = ev_snapshot
        verdict["signal"] = sig

        return verdict

    def decide(self, row: Any, exec_ctx: Dict[str, Any], macro_ctx: Dict[str, Any]) -> Dict[str, Any]:
        ok, circuit_reason = self.circuit_breaker()
        if not ok:
            return self._build_verdict(False, circuit_reason, "?", "?", {})

        regime = self.classify_regime(row, exec_ctx)
        vol_state = self.volatility_state(row, exec_ctx)
        exec_ctx = dict(exec_ctx)
        exec_ctx["regime"] = regime
        exec_ctx["vol_state"] = vol_state

        long_sig = self.generate_signal(row, "Long", exec_ctx, macro_ctx)
        short_sig = self.generate_signal(row, "Short", exec_ctx, macro_ctx)
        signal = self.choose_signal(row, exec_ctx, macro_ctx)

        if not signal.get("base_trigger_passed", False):
            return self._build_verdict(
                False,
                str(signal.get("base_trigger", {}).get("reason", "BASE_TRIGGER_NOT_PASSED")),
                regime, vol_state, signal,
                long_sig=long_sig, short_sig=short_sig,
            )
        ok, reason = self.tail_filter(signal, regime, vol_state)
        if not ok:
            return self._build_verdict(
                False, reason, regime, vol_state, signal,
                long_sig=long_sig, short_sig=short_sig,
            )

        risk = self.risk_budget(signal, regime, vol_state)
        book, size = self.allocate(signal, risk, regime, vol_state)
        if size <= 0.0:
            return self._build_verdict(
                False, "PORTFOLIO_SIZE_ZERO", regime, vol_state, signal,
                long_sig=long_sig, short_sig=short_sig,
            )

        return self._build_verdict(
            True,
            f"ALLOW_{regime}_{book}_{signal.get('ev_grade', grade_from_expected_value(signal.get('expected_value', 0.0)))}",
            regime, vol_state, signal, book, size,
            long_sig=long_sig, short_sig=short_sig,
        )

    def update_account(self, pnl_r: float) -> None:
        self.account.update(pnl_r)

    def state_dict(self) -> Dict[str, Any]:
        return asdict(self.account)
