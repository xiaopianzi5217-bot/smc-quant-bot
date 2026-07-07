# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Any, Dict, Optional, Tuple
from collections.abc import Mapping

def _as_dict(v: Any) -> Dict[str, Any]:
    if v is None: return {}
    if isinstance(v, dict): return v
    if isinstance(v, Mapping): return dict(v)
    if hasattr(v, "to_dict"):
        try: return dict(v.to_dict())
        except Exception: return {}
    return {}

def _num(v: Any, default: float = 0.0) -> float:
    try:
        if v is None: return default
        return float(v)
    except Exception: return default

def _pick(d: Dict[str, Any], *keys: str, default=None):
    for k in keys:
        if k in d and d[k] is not None: return d[k]
    return default

def _title_direction(v: Any) -> str:
    s = str(v or "").strip().lower()
    if s in {"long", "buy", "bull", "bullish"}: return "Long"
    if s in {"short", "sell", "bear", "bearish"}: return "Short"
    return ""


class V9DecisionKernel:
    """V9 Decision Kernel — 轻量审批层（严格版）。

    设计原则：
    - 只做两件事：选方向（多/空比较），做审批（是否允许成交）
    - 不做评分（评分由 smc_impulse_score 完成）
    - 不做 feature 工程
    - 不做 filters/rejects 以外的任何逻辑

    严格版本变更（2026-07-15）：
    - 默认阈值从 20.0 → 35.0（滤掉大量假信号）
    - 默认 min_edge 从 2.0 → 5.0（要求多空方向更明确）
    - EV 门槛从 -0.20 → 0.0（不允许负期望的信号通过）
    - 配置从 v9_kernel 子段读取（v1.5+），兼容旧版 v9_threshold/v9_min_edge
    """

    def __init__(self, cfg: Optional[Dict[str, Any]] = None, *args, **kwargs):
        self.cfg = _as_dict(cfg) or _as_dict(kwargs.get("params")) or _as_dict(kwargs.get("config"))
        self.version = "v9.STABLE_20260701_STRICT"
        # 从配置中读取 v9_kernel 子段，兼容旧版直接配置
        _kernel_cfg = self.cfg.get("v9_kernel", self.cfg)
        self._threshold = float(_kernel_cfg.get("threshold", _kernel_cfg.get("v9_threshold", 35.0)))
        self._min_edge = float(_kernel_cfg.get("min_edge", _kernel_cfg.get("v9_min_edge", 5.0)))
        self._ev_threshold = float(_kernel_cfg.get("ev_threshold", 0.0))

    def _normalize_args(self, *args, **kwargs) -> Tuple[str, Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        params = dict(kwargs)
        symbol = str(params.pop("symbol", "UNKNOWN"))
        decision = _as_dict(params.pop("decision", None))
        curr = _as_dict(params.pop("curr", None))
        macro_ctx = _as_dict(params.pop("macro_ctx", None))
        exec_ctx = _as_dict(params.pop("exec_ctx", None))

        if args:
            if args and not isinstance(args[0], str):
                curr = _as_dict(args[0]) if len(args) > 0 else curr
                macro_ctx = _as_dict(args[1]) if len(args) > 1 else macro_ctx
                exec_ctx = _as_dict(args[2]) if len(args) > 2 else exec_ctx
            else:
                symbol = str(args[0]) if len(args) > 0 else symbol
                curr = _as_dict(args[1]) if len(args) > 1 else curr
                macro_ctx = _as_dict(args[2]) if len(args) > 2 else macro_ctx
                exec_ctx = _as_dict(args[3]) if len(args) > 3 else exec_ctx
                decision = _as_dict(args[4]) if len(args) > 4 else decision

        cfg = _as_dict(params.pop("cfg", None)) or self.cfg
        params["cfg"] = cfg
        return symbol, curr, macro_ctx, exec_ctx, decision, params

    def decide(self, *args, **kwargs) -> Dict[str, Any]:
        symbol, curr, macro_ctx, exec_ctx, decision, params = self._normalize_args(*args, **kwargs)
        cfg = _as_dict(params.get("cfg"))

        long_score = _num(_pick(curr, "long_score", "score_long", default=_pick(decision, "long_score", default=_pick(params, "long_score", default=0))))
        short_score = _num(_pick(curr, "short_score", "score_short", default=_pick(decision, "short_score", default=_pick(params, "short_score", default=0))))

        direction = _title_direction(_pick(curr, "direction", "trend_direction", default=_pick(params, "direction", default=_pick(decision, "direction", default=""))))
        if not direction:
            direction = "Long" if long_score >= short_score else "Short"
        price = _num(_pick(curr, "close", "price", "last", default=_pick(params, "price", default=0)), 0.0)
        base_min_rr = 1.0

        # ===== 三重门槛：评分 + edge + EV =====
        primary_score = long_score if direction == "Long" else short_score
        edge = abs(long_score - short_score)

        # EV 门槛（从 self._ev_threshold 读取）
        long_ev = _num(params.get("long_ev", _pick(params, "long_ev", default=0)), 0.0)
        short_ev = _num(params.get("short_ev", _pick(params, "short_ev", default=0)), 0.0)
        selected_ev = long_ev if direction == "Long" else short_ev
        ev_reason = ""
        ev_ok = True

        if selected_ev < self._ev_threshold:
            ev_ok = False
            ev_reason = f"ev_{selected_ev:.2f}_below_{self._ev_threshold:.2f}"
        
        score_ok = primary_score >= self._threshold
        # HTF 强制方向时不要求 edge 高分（可能弱方向反而分数低）
        _htf_forced = exec_ctx.get("htf_forced", params.get("htf_forced", False))
        if isinstance(_htf_forced, str):
            _htf_forced = _htf_forced.lower() in {"1", "true", "yes"}
        if not bool(_htf_forced):
            score_ok = score_ok and edge >= self._min_edge
        
        approved = score_ok and ev_ok

        if not approved:
            reason_parts = [f"score={primary_score:.1f}_edge={edge:.1f}_ev={selected_ev:.2f}"]
            if not score_ok: reason_parts.append("below_min")
            if not ev_ok: reason_parts.append(ev_reason)
            return self._hold(symbol, price, direction, long_score, short_score, self._threshold, self._threshold,
                              "_".join(reason_parts))

        passed_rr = _num(_pick(params, "rr", default=_pick(decision, "rr", default=_pick(_as_dict(decision.get("risk_plan")), "rr", default=base_min_rr))), base_min_rr)
        
        reasons = params.get("long_reasons") if direction == "Long" else params.get("short_reasons")
        if not isinstance(reasons, list):
            reasons = []

        risk_plan = _as_dict(decision.get("risk_plan"))
        for key in ["entry", "sl", "tp1", "tp2", "tp3", "rr"]:
            if key in params and params[key] is not None:
                risk_plan[key] = params[key]
        if price and "entry" not in risk_plan:
            risk_plan["entry"] = price
        risk_plan["direction"] = direction
        risk_plan["rr"] = passed_rr

        return {
            "symbol": symbol,
            "version": self.version,
            "source": "V9DecisionKernel",
            "decision_source": "v9.smc_impulse_score",
            "approved": True,
            "decision_approved": True,
            "is_approved": True,
            "entry_signal": "LONG" if direction == "Long" else "SHORT",
            "action": "BUY" if direction == "Long" else "SELL",
            "side": "LONG" if direction == "Long" else "SHORT",
            "direction": direction,
            "price": price,
            "long_score": long_score,
            "short_score": short_score,
            "threshold": self._threshold,
            "rr": passed_rr,
            "reason": f"score_{primary_score:.1f}_edge_{edge:.1f}_ev_{selected_ev:.2f}_approved",
            "primary": {
                "direction": direction,
                "priority": "A",
                "score": primary_score,
                "score_gap": edge,
                "reasons": reasons,
                "triggers": reasons,
                "in_ote": False,
            },
            "risk_plan": risk_plan,
            "regime": exec_ctx,
        }

    def _hold(self, symbol, price, direction, long_score, short_score, threshold_long, threshold_short, reason):
        return {
            "symbol": symbol, "version": self.version, "approved": False, "decision_approved": False, "is_approved": False,
            "entry_signal": None, "action": "HOLD", "side": "NONE", "direction": direction, "price": price,
            "long_score": long_score, "short_score": short_score, "threshold_long": threshold_long, "threshold_short": threshold_short, "reason": reason
        }

    def __call__(self, *args, **kwargs): return self.decide(*args, **kwargs)
    def make_decision(self, *args, **kwargs): return self.decide(*args, **kwargs)
    def run(self, *args, **kwargs): return self.decide(*args, **kwargs)