# -*- coding: utf-8 -*-
"""V6 risk engine. The previous file at this path was JSON-like content, so importing ``risk.v6_risk_engine`` crashed before the live runner could start. This module restores the Python class expected by ``decision.v6_decision_kernel`` while keeping the same default risk numbers. """
from __future__ import annotations

from typing import Any, Dict, Optional

try:
    from risk.position_sizing import fixed_fraction_position_size
except Exception:  # pragma: no cover
    from .position_sizing import fixed_fraction_position_size


DEFAULT_RISK_CONFIG: Dict[str, Any] = {
    "account_risk_pct": 0.01,
    "max_position_pct": 0.25,
    "max_daily_loss_pct": 0.03,
    "max_total_exposure_pct": 0.60,
    "atr_sl_mult": 1.5,
    "safety_atr_mult": 0.25,
    "tp1_rr": 1.0,
    "tp2_rr": 2.0,
    "tp3_rr": 3.0,
}

# 数据收集阶段配置：默认软降仓，不硬 ban
# 样本够了后把 sqz_require_released_hard 改为 True 即可升级硬过滤
QUALITY_CONFIG: Dict[str, Any] = {
    "sqz_require_released_hard": False,  # True=硬拦截未释放SQZ；False=只降仓
    "sqz_min_vol_ratio": 1.0,
    "sqz_unreleased_size_mult": 0.40,    # 未释放时仓位乘数
    "range_low_adx_threshold": 25.0,
    "range_size_mult": 0.50,             # RANGE + 低ADX 仓位乘数
    "min_size_mult": 0.25,               # 最低保留仓位，保证还能收数据
}


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        v = float(value)
        return v if v == v else default
    except Exception:
        return default


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class V6RiskEngine:
    """Build TP/SL and optional position plans for V6DecisionKernel."""

    def __init__(self, cfg: Optional[Dict[str, Any]] = None):
        self.cfg = dict(DEFAULT_RISK_CONFIG)
        if isinstance(cfg, dict):
            self.cfg.update(cfg)
        # 允许从 risk 配置覆盖 QUALITY_CONFIG
        q = (cfg or {}).get("quality") if isinstance(cfg, dict) else None
        self.quality_cfg = dict(QUALITY_CONFIG)
        if isinstance(q, dict):
            self.quality_cfg.update(q)

    def _entry_atr(self, curr: Any, exec_ctx: Optional[Dict[str, Any]] = None) -> tuple[float, float]:
        entry = _num(_get(curr, "close", _get(curr, "price", 0.0)), 0.0)
        ctx = exec_ctx or {}
        atr = _num(ctx.get("atr") or ctx.get("ATRr_14") or _get(curr, "ATRr_14", _get(curr, "atr", 0.0)), 0.0)
        if atr <= 0 and entry > 0:
            atr = entry * 0.008
        return entry, atr

    def _build_levels(self, direction: str, curr: Any, exec_ctx: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
        entry, atr = self._entry_atr(curr, exec_ctx)
        atr_sl_mult = max(_num(self.cfg.get("atr_sl_mult"), 1.5), 0.01)
        stop_dist = max(atr * atr_sl_mult, entry * 0.001 if entry > 0 else 0.0)
        tp1_rr = _num(self.cfg.get("tp1_rr"), 1.0)
        tp2_rr = _num(self.cfg.get("tp2_rr"), 2.0)
        tp3_rr = _num(self.cfg.get("tp3_rr"), 3.0)

        if str(direction).lower() == "short":
            sl = entry + stop_dist
            tp1 = entry - stop_dist * tp1_rr
            tp2 = entry - stop_dist * tp2_rr
            tp3 = entry - stop_dist * tp3_rr
        else:
            sl = entry - stop_dist
            tp1 = entry + stop_dist * tp1_rr
            tp2 = entry + stop_dist * tp2_rr
            tp3 = entry + stop_dist * tp3_rr
        return {
            "entry": round(entry, 8), "sl": round(sl, 8), "tp1": round(tp1, 8),
            "tp2": round(tp2, 8), "tp3": round(tp3, 8), "rr": round(tp3_rr, 4),
            "atr": round(atr, 8), "stop_distance": round(stop_dist, 8),
        }

    def build_observer_plan(self, direction: str, curr: Any, exec_ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {"direction": direction, **self._build_levels(direction, curr, exec_ctx), "observer_only": True}

    def _assess_entry_quality(self, direction: str, curr: Any, exec_ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """软质量评估：返回标签 + 建议仓位乘数。数据收集阶段默认不硬 ban。"""
        ctx = exec_ctx or {}
        tags = []
        mult = 1.0
        qc = self.quality_cfg

        # --- SQZ ---
        sqz_released = bool(
            ctx.get("sqz_released")
            or ctx.get("squeeze_released")
            or (isinstance(curr, dict) and (curr.get("sqz_released") or curr.get("squeeze_released")))
        )
        sqz_vol_ratio = _num(
            ctx.get("sqz_vol_ratio")
            or ctx.get("sqz_volume_ratio")
            or (curr.get("sqz_vol_ratio") if isinstance(curr, dict) else None),
            0.0,
        )
        sqz_vol_confirmed = bool(
            ctx.get("sqz_volume_confirmed")
            or (curr.get("sqz_volume_confirmed") if isinstance(curr, dict) else False)
        )

        if not sqz_released:
            tags.append("SQZ_NOT_RELEASED")
            mult *= float(qc.get("sqz_unreleased_size_mult", 0.40))
        if sqz_vol_ratio < float(qc.get("sqz_min_vol_ratio", 1.0)):
            tags.append("SQZ_LOW_VOL")
            mult *= 0.85
        if not sqz_vol_confirmed:
            tags.append("SQZ_NO_VOL_CONFIRM")

        # --- REGIME / ADX ---
        regime_info = ctx.get("regime_info") or {}
        regime = str(
            ctx.get("regime")
            or regime_info.get("regime")
            or (curr.get("regime") if isinstance(curr, dict) else "")
            or ""
        ).upper()
        adx = _num(
            ctx.get("adx")
            or ctx.get("ADX_14")
            or ctx.get("adx_14")
            or (curr.get("adx_14") if isinstance(curr, dict) else None)
            or regime_info.get("adx"),
            0.0,
        )

        if regime in ("RANGE", "CHOP", "MUD") and adx < float(qc.get("range_low_adx_threshold", 25.0)):
            tags.append(f"RANGE_LOW_ADX_{adx:.1f}")
            mult *= float(qc.get("range_size_mult", 0.50))

        min_mult = float(qc.get("min_size_mult", 0.25))
        mult = max(min_mult, min(1.0, mult))
        is_probe = mult < 0.85 or any(t.startswith("SQZ_NOT_RELEASED") or t.startswith("RANGE_LOW_ADX") for t in tags)

        return {
            "tags": tags,
            "size_mult": round(mult, 4),
            "is_probe": is_probe,
            "sqz_released": sqz_released,
            "sqz_vol_ratio": sqz_vol_ratio,
            "regime": regime,
            "adx": adx,
        }

    def build_plan(
        self,
        direction: str,
        curr: Any,
        exec_ctx: Optional[Dict[str, Any]] = None,
        equity: Optional[float] = None,
        level: str = "A",
    ) -> Dict[str, Any]:
        levels = self._build_levels(direction, curr, exec_ctx)
        quality = self._assess_entry_quality(direction, curr, exec_ctx)

        # 可选硬拦截（数据阶段默认 False）
        if self.quality_cfg.get("sqz_require_released_hard") and not quality["sqz_released"]:
            return {
                "direction": direction,
                **levels,
                "level": level,
                "position": {"allowed": False, "qty": 0.0, "reason_cn": "SQZ未释放，硬拦截"},
                "quality": quality,
                "risk_model": "V6RiskEngine.fixed_fraction_atr",
            }

        equity_f = _num(equity, 0.0)
        position = None
        if equity_f > 0:
            grade_mult = {"S": 1.0, "A": 1.0, "B": 0.5, "C": 0.0, "D": 0.0}.get(str(level or "A").upper()[:1], 1.0)
            risk_pct = _num(self.cfg.get("account_risk_pct"), 0.01) * grade_mult * float(quality["size_mult"])
            position = fixed_fraction_position_size(
                equity=equity_f,
                entry=levels["entry"],
                stop_loss=levels["sl"],
                risk_per_trade=risk_pct,
                max_position_pct=self.cfg.get("max_position_pct"),
            )
            if position is not None:
                position["quality_tags"] = quality["tags"]
                position["quality_size_mult"] = quality["size_mult"]
                position["is_probe"] = quality["is_probe"]
                if quality["is_probe"]:
                    base_reason = position.get("reason_cn") or ""
                    position["reason_cn"] = (base_reason + f" | PROBE降仓 x{quality['size_mult']}").strip(" |")

        return {
            "direction": direction,
            **levels,
            "level": level,
            "position": position,
            "quality": quality,
            "risk_model": "V6RiskEngine.fixed_fraction_atr",
        }
