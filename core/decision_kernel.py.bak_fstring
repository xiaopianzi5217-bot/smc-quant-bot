# -*- coding: utf-8 -*-
"""
Institutional single decision kernel for SMC_Bot.

This module is the one authoritative pre-trade decision entry point used by
backtest and can be reused by live/paper execution.  Feature generation remains
outside; this kernel owns signal choice, EV/risk sizing, and alpha-cluster
pre-trade gating so the runner no longer contains competing decision brains.
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Optional
import json

from core.alpha_master_engine import V37MasterEngine
from strategy.alpha_cluster_guard import AlphaClusterGuard, ClusterDecision

from utils.safe import safe_float, safe_bool, safe_str




def _cluster_decision_to_json(decision: ClusterDecision) -> str:
    try:
        return json.dumps(decision.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        return json.dumps(asdict(decision), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class InstitutionalDecisionKernel:
    """Single authoritative decision kernel.

    The historical project accumulated several decision layers in backtest,
    strategy and decision packages.  This class deliberately wraps those proven
    components behind one public ``decide`` method:

    1. core.alpha_master_engine.V37MasterEngine creates exactly one Long/Short candidate and base size.
    2. AlphaClusterGuard performs cluster-level pre-trade approval/compression.
    3. The returned dictionary is normalized for the runner/execution layer.
    """

    def __init__(
        self,
        *,
        project_root: str | Path = ".",
        base_risk: float = 0.12,
        min_score_raw: float = 18.0,
        min_score_norm: float = 50.0,
        max_position_mult: float = 0.40,
        min_expected_value: float = 0.0,
        master_engine: Optional[V37MasterEngine] = None,
        cluster_guard: Optional[AlphaClusterGuard] = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.master = master_engine or V37MasterEngine(
            base_risk=base_risk,
            min_score_raw=min_score_raw,
            min_score_norm=min_score_norm,
            max_position_mult=max_position_mult,
            min_expected_value=min_expected_value,
        )
        self.cluster_guard = cluster_guard or AlphaClusterGuard.from_files(self.project_root)

    @classmethod
    def from_kwargs(cls, kwargs: Dict[str, Any], *, project_root: str | Path = ".") -> "InstitutionalDecisionKernel":
        """Build from runner kwargs while keeping old CLI/API parameters stable."""
        return cls(
            project_root=project_root,
            base_risk=float(kwargs.get("v37_base_risk", 0.12)),
            min_score_raw=float(kwargs.get("v37_min_score_raw", 18.0)),
            min_score_norm=float(kwargs.get("v37_min_score_norm", 50.0)),
            max_position_mult=float(kwargs.get("v37_max_position_mult", 0.40)),
            min_expected_value=float(kwargs.get("v37_min_expected_value", 0.0)),
        )

    @property
    def account(self):
        return self.master.account

    def update_account(self, pnl_r: float) -> None:
        self.master.update_account(pnl_r)

    def state_dict(self) -> Dict[str, Any]:
        return self.master.state_dict()

    def decide(self, row: Any, exec_ctx: Dict[str, Any], macro_ctx: Dict[str, Any]) -> Dict[str, Any]:
        decision = self.master.decide(row, exec_ctx, macro_ctx)
        if not isinstance(decision, dict):
            return {"allow": False, "reason": "DECISION_KERNEL_INVALID_OUTPUT", "signal": {}}

        if not decision.get("allow", False):
            return decision

        signal = dict(decision.get("signal", {}) or {})
        regime = decision.get("regime")
        book = decision.get("book")

        cluster_decision = self.cluster_guard.evaluate(signal, regime, book)
        signal["alpha_cluster"] = cluster_decision.cluster
        signal["alpha_cluster_coarse"] = cluster_decision.coarse_cluster
        signal["alpha_cluster_action"] = cluster_decision.action
        signal["alpha_cluster_reason"] = cluster_decision.reason
        signal["alpha_cluster_position_mult"] = round(float(cluster_decision.position_multiplier), 6)
        signal["alpha_cluster_stats_json"] = json.dumps(cluster_decision.stats, ensure_ascii=False, sort_keys=True)
        signal["alpha_cluster_guard_json"] = _cluster_decision_to_json(cluster_decision)
        signal["ev_reasons"] = str(signal.get("ev_reasons", "")) + f";{cluster_decision.reason}"

        normalized = dict(decision)
        normalized["signal"] = signal
        normalized["cluster_decision"] = cluster_decision.to_dict()

        pre_cluster_size = safe_float(normalized.get("size"), 0.0)
        cluster_mult = float(cluster_decision.position_multiplier)
        post_cluster_size = pre_cluster_size * cluster_mult

        # V54: 澶氬眰闄嶄粨淇濇姢銆侰luster 鍏佽鎴愪氦鏃讹紝寮轰俊鍙蜂笉浼氬洜涓鸿繛缁箻娉曡鍘嬪埌
        # 鎺ヨ繎 0锛涚湡姝ｉ渶瑕侀殧绂荤殑 PROBE_BOOK 浠嶄繚鐣欏皬浠撹瀵熴€?        signal_ev = safe_float(signal.get("expected_value"), 0.0)
        signal_score = safe_float(signal.get("score"), 0.0)
        if cluster_decision.allow and signal_ev >= 0.05 and signal_score >= 80.0 and cluster_mult >= 0.30:
            post_cluster_size = max(post_cluster_size, pre_cluster_size * 0.55)
        elif cluster_decision.allow and signal_ev >= 0.0 and signal_score >= 70.0 and cluster_mult >= 0.30:
            post_cluster_size = max(post_cluster_size, pre_cluster_size * 0.40)

        normalized["size"] = round(post_cluster_size, 6)

        if not cluster_decision.allow:
            normalized["allow"] = False
            normalized["reason"] = cluster_decision.reason
            return normalized

        if safe_float(normalized.get("size"), 0.0) <= 0.0:
            normalized["allow"] = False
            normalized["reason"] = "DECISION_KERNEL_SIZE_ZERO_AFTER_CLUSTER"
            return normalized

        normalized["reason"] = f"{normalized.get('reason', 'ALLOW')};{cluster_decision.reason}"
        return normalized

