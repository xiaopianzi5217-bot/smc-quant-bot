# -*- coding: utf-8 -*-
"""
Alpha Cluster Guard V1

第二阶段优化：把回测审计中已经暴露的坏簇从“事后报告”前移到
“执行前风控层”。它不是新的技术指标，而是利用 trade log 中的
regime / book / EV bucket / RR bucket 做条件期望约束。

设计原则：
1. Base Trigger 仍然只由 SMC + SQZMOM 决定；本模块不替代第一道准入。
2. Scorecard 仍然保留；本模块只在 execution 之前做簇级仓位压缩/拒绝。
3. SCALP/DUMPSTER 不再一票否决，先进入 PROBE，避免误杀特定环境下有效的短线 alpha。
4. 小样本 cluster 不轻易一票否决，只降仓；大样本负 EV cluster 也先 probe 而不是硬断路。
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple
import json
import math

try:  # pandas is available in the backtest environment, but keep import optional for live runtime.
    import pandas as pd  # type: ignore
except Exception:  # pragma: no cover
    pd = None  # type: ignore


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

def _bucket(value: float, cuts: Iterable[Tuple[float, float, str]], default: str) -> str:
    v = _safe_float(value, 0.0)
    for low, high, label in cuts:
        if low <= v < high:
            return label
    return default


EV_BUCKETS = [
    (-1e9, 0.0, "NEG"),
    (0.0, 0.05, "LOW"),
    (0.05, 0.10, "MID"),
    (0.10, 0.15, "HIGH"),
    (0.15, 1e9, "EXTREME"),
]

RR_BUCKETS = [
    (-1e9, 0.8, "WEAK"),
    (0.8, 1.2, "OK"),
    (1.2, 1.6, "GOOD"),
    (1.6, 2.5, "STRONG"),
    (2.5, 1e9, "EXTREME"),
]


@dataclass
class ClusterDecision:
    allow: bool
    action: str
    reason: str
    cluster: str
    coarse_cluster: str
    position_multiplier: float
    stats: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["position_multiplier"] = round(float(d["position_multiplier"]), 6)
        return d


DEFAULT_RULES: Dict[str, Any] = {
    "version": "AlphaClusterGuard_V54_ALPHA_EXPANSION_20260621",
    # block_books 保持兼容旧配置名，但执行语义是 probe，不是硬封杀。
    "block_books": ["DUMPSTER", "SCALP"],
    "block_book_probe_multiplier": 0.35,
    "hard_bad_probe_multiplier": 0.30,
    "stat_bad_probe_multiplier": 0.30,
    "no_sweep_probe_multiplier": 0.35,
    # 高置信坏簇：样本多、PF/胜率明显差的组合。小样本坏簇不放这里，避免过拟合。
    "hard_bad_clusters": [
        "TREND_V37_DUMPSTER_NEG_OK",
        "TRANSITION_V37_DUMPSTER_NEG_OK",
        "TRANSITION_V37_DUMPSTER_NEG_WEAK",
        "TRANSITION_V37_DUMPSTER_NEG_GOOD",
        "CHOP_V37_DUMPSTER_NEG_OK",
        "CHOP_V37_DUMPSTER_NEG_WEAK",
    ],
    # 低置信或结构上容易噪音化的簇，只降仓或进入 PROBE，不立即删除。
    "soft_bad_clusters": [
        "TREND_V37_CORE_EXTREME_GOOD",
        "TREND_V37_CORE_EXTREME_STRONG",
        "TRANSITION_V37_TACTICAL_EXTREME_GOOD",
        "CHOP_V37_SCALP_HIGH_GOOD",
    ],
    # 当前日志中较稳定/可观察的 alpha 区域。这里只做保留/轻微加权，不做无限加仓。
    "alpha_clusters": [
        "TRANSITION_V37_CORE_EXTREME_GOOD",
        "TRANSITION_V37_PROBE_LOW_GOOD",
        "CHOP_V37_TACTICAL_EXTREME_GOOD",
    ],
    "min_trades_for_hard_stat": 20,
    "hard_pf_floor": 0.75,
    "hard_win_rate_floor": 0.08,
    "soft_pf_floor": 1.00,
    "unknown_cluster_multiplier": 0.85,
    "soft_bad_multiplier": 0.45,
    "soft_bad_requires_liquidity_sweep": True,
    "weak_cluster_multiplier": 0.60,
    "alpha_multiplier": 1.05,
    "alpha_min_trades_for_boost": 30,
    "fragile_alpha_multiplier": 0.65,
    "fragile_alpha_requires_liquidity_sweep": True,
}


class AlphaClusterGuard:
    def __init__(self, rules: Optional[Dict[str, Any]] = None, stats: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
        self.rules = dict(DEFAULT_RULES)
        if rules:
            # Merge shallowly; lists are replaced intentionally.
            self.rules.update(rules)
        self.stats = stats or {}

    @staticmethod
    def make_cluster(regime: Any, book: Any, expected_value: Any, estimated_rr: Any) -> Tuple[str, str]:
        reg = str(regime or "UNKNOWN").upper()
        setup = f"V37_{str(book or 'UNKNOWN').upper()}"
        evb = _bucket(_safe_float(expected_value, 0.0), EV_BUCKETS, "EXTREME")
        rrb = _bucket(_safe_float(estimated_rr, 0.0), RR_BUCKETS, "EXTREME")
        coarse = f"{reg}_{setup}"
        fine = f"{coarse}_{evb}_{rrb}"
        return fine, coarse

    @classmethod
    def from_files(cls, project_root: str | Path = ".") -> "AlphaClusterGuard":
        root = Path(project_root)
        rules: Dict[str, Any] = {}
        stats: Dict[str, Dict[str, Any]] = {}

        cfg = root / "config" / "alpha_cluster_rules.json"
        if cfg.exists():
            try:
                rules = json.loads(cfg.read_text(encoding="utf-8"))
            except Exception:
                rules = {}

        report = root / "outputs" / "ev_cluster_report.csv"
        if pd is not None and report.exists():
            try:
                df = pd.read_csv(report)
                for _, r in df.iterrows():
                    key = str(r.get("cluster", ""))
                    if not key:
                        continue
                    stats[key] = {
                        "trades": int(_safe_float(r.get("trades"), 0)),
                        "win_rate": round(_safe_float(r.get("win_rate"), 0.0), 6),
                        "pf": round(_safe_float(r.get("pf"), 0.0), 6),
                        "avg_ev": round(_safe_float(r.get("avg_ev"), 0.0), 6),
                        "avg_rr": round(_safe_float(r.get("avg_rr"), 0.0), 6),
                        "stability": round(_safe_float(r.get("stability"), 0.0), 6),
                    }
            except Exception:
                stats = {}
        return cls(rules=rules, stats=stats)

    def evaluate(self, signal: Dict[str, Any], regime: Any, book: Any) -> ClusterDecision:
        ev = _safe_float(signal.get("expected_value"), 0.0)
        rr = _safe_float(signal.get("estimated_rr"), 0.0)
        cluster, coarse = self.make_cluster(regime, book, ev, rr)
        book_u = str(book or "UNKNOWN").upper()
        stats = self.stats.get(cluster, {})

        block_books = set(map(str.upper, self.rules.get("block_books", [])))
        if book_u in block_books:
            # V54 Alpha Expansion: SCALP/DUMPSTER 不再一票否决，改成 probe。
            # 这样可以继续收集真实样本，避免把“特定环境下有效”的短线 alpha 误杀。
            mult = _safe_float(self.rules.get("block_book_probe_multiplier"), 0.25)
            return ClusterDecision(
                allow=True,
                action="PROBE_BOOK",
                reason=f"ALPHA_CLUSTER_PROBE_BOOK_{book_u}",
                cluster=cluster,
                coarse_cluster=coarse,
                position_multiplier=mult,
                stats=stats,
            )

        hard = set(map(str, self.rules.get("hard_bad_clusters", [])))
        if cluster in hard or coarse in hard:
            mult = _safe_float(self.rules.get("hard_bad_probe_multiplier"), 0.25)
            return ClusterDecision(True, "PROBE_BAD_CLUSTER", "ALPHA_CLUSTER_HARD_BAD_PROBE", cluster, coarse, mult, stats)

        trades = int(_safe_float(stats.get("trades"), 0.0)) if stats else 0
        pf = _safe_float(stats.get("pf"), 0.0) if stats else 0.0
        win_rate = _safe_float(stats.get("win_rate"), 0.0) if stats else 0.0
        min_trades = int(_safe_float(self.rules.get("min_trades_for_hard_stat"), 20))

        if trades >= min_trades:
            if pf < _safe_float(self.rules.get("hard_pf_floor"), 0.75):
                mult = _safe_float(self.rules.get("stat_bad_probe_multiplier"), 0.25)
                return ClusterDecision(True, "PROBE_STAT_PF", "ALPHA_CLUSTER_STAT_PF_PROBE", cluster, coarse, mult, stats)
            if win_rate < _safe_float(self.rules.get("hard_win_rate_floor"), 0.08):
                mult = _safe_float(self.rules.get("stat_bad_probe_multiplier"), 0.25)
                return ClusterDecision(True, "PROBE_STAT_WINRATE", "ALPHA_CLUSTER_STAT_WINRATE_PROBE", cluster, coarse, mult, stats)

        soft = set(map(str, self.rules.get("soft_bad_clusters", [])))
        alpha = set(map(str, self.rules.get("alpha_clusters", [])))

        if cluster in alpha or coarse in alpha:
            min_boost = int(_safe_float(self.rules.get("alpha_min_trades_for_boost"), 30))
            if stats and trades >= min_boost and pf >= 1.20:
                mult = _safe_float(self.rules.get("alpha_multiplier"), 1.05)
                return ClusterDecision(True, "ALLOW_ALPHA_CLUSTER", "ALPHA_CLUSTER_ALLOWLIST", cluster, coarse, mult, stats)
            # 旧样本里表现好的 cluster 如果样本不足，不能直接当成稳定 alpha。
            # 对这类 fragile alpha，要求真正的流动性扫单/止损猎杀确认；否则只进 audit，
            # 避免把“看起来很高端的 transition core”变成连续噪音交易。
            meta = signal.get("entry_meta", {}) if isinstance(signal, dict) else {}
            needs_sweep = bool(self.rules.get("fragile_alpha_requires_liquidity_sweep", True))
            has_sweep = _safe_bool(meta.get("liquidity_sweep_confirmed", False))
            if needs_sweep and not has_sweep:
                mult = _safe_float(self.rules.get("no_sweep_probe_multiplier"), 0.25)
                return ClusterDecision(True, "PROBE_FRAGILE_ALPHA_NO_SWEEP", "ALPHA_CLUSTER_FRAGILE_NO_SWEEP_PROBE", cluster, coarse, mult, stats)
            mult = _safe_float(self.rules.get("fragile_alpha_multiplier"), 0.55)
            return ClusterDecision(True, "FRAGILE_ALPHA_CLUSTER", "ALPHA_CLUSTER_FRAGILE_SIZE_DOWN", cluster, coarse, mult, stats)

        if cluster in soft or coarse in soft:
            meta = signal.get("entry_meta", {}) if isinstance(signal, dict) else {}
            needs_sweep = bool(self.rules.get("soft_bad_requires_liquidity_sweep", True))
            has_sweep = _safe_bool(meta.get("liquidity_sweep_confirmed", False))
            if needs_sweep and not has_sweep:
                mult = _safe_float(self.rules.get("no_sweep_probe_multiplier"), 0.25)
                return ClusterDecision(True, "PROBE_SOFT_BAD_NO_SWEEP", "ALPHA_CLUSTER_SOFT_BAD_NO_SWEEP_PROBE", cluster, coarse, mult, stats)
            mult = _safe_float(self.rules.get("soft_bad_multiplier"), 0.35)
            return ClusterDecision(True, "SOFT_BAD_CLUSTER", "ALPHA_CLUSTER_SOFT_BAD_SIZE_DOWN", cluster, coarse, mult, stats)

        if stats:
            if pf < _safe_float(self.rules.get("soft_pf_floor"), 1.0):
                mult = _safe_float(self.rules.get("weak_cluster_multiplier"), 0.50)
                return ClusterDecision(True, "WEAK_CLUSTER", "ALPHA_CLUSTER_WEAK_SIZE_DOWN", cluster, coarse, mult, stats)
            return ClusterDecision(True, "ALLOW_OBSERVED_CLUSTER", "ALPHA_CLUSTER_OBSERVED_OK", cluster, coarse, 1.0, stats)

        # 未见过的 cluster 不硬拦截，只降仓，保留探索能力。
        mult = _safe_float(self.rules.get("unknown_cluster_multiplier"), 0.70)
        return ClusterDecision(True, "UNKNOWN_CLUSTER", "ALPHA_CLUSTER_UNKNOWN_SIZE_DOWN", cluster, coarse, mult, stats)


def decision_to_json(decision: ClusterDecision) -> str:
    return json.dumps(decision.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

# ================= PURE MODE FILTER =================
from config import PURE_MODE, ALLOWED_BOOKS

def _pure_mode_book_filter(book: str) -> bool:
    if not PURE_MODE:
        return True
    return book in ALLOWED_BOOKS
