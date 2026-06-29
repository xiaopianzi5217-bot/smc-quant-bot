# -*- coding: utf-8 -*-
"""
Alpha Validity Score (AVS) engine.

Purpose
-------
This module does not create new trades and does not change entry logic. It reads
an existing trade log and answers three post-backtest questions:

1. Is the alpha likely overfit?
2. Which clusters are fake / fragile alpha?
3. Which regimes carry real edge?

The implementation is intentionally conservative. It favours robust diagnostics
over optimistic scoring, because it is designed to protect the system from
curve-fitting after cluster pruning.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
import json
import math

import pandas as pd


EPS = 1e-12


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


def _pf_from_pnl(pnl: pd.Series) -> float:
    pnl = pd.to_numeric(pnl, errors="coerce").fillna(0.0)
    wins = float(pnl[pnl > 0].sum())
    losses = abs(float(pnl[pnl < 0].sum()))
    if losses <= EPS:
        return 999.0 if wins > 0 else 0.0
    return float(wins / losses)


def _max_drawdown(pnl: pd.Series) -> float:
    pnl = pd.to_numeric(pnl, errors="coerce").fillna(0.0)
    if pnl.empty:
        return 0.0
    equity = pnl.cumsum()
    peak = equity.cummax()
    dd = equity - peak
    return float(dd.min()) if len(dd) else 0.0


def _stats(sub: pd.DataFrame, pnl_col: str = "pnl_r") -> Dict[str, Any]:
    if sub is None or len(sub) == 0 or pnl_col not in sub.columns:
        return {"trades": 0, "win_rate": 0.0, "pf": 0.0, "total_r": 0.0, "avg_r": 0.0, "max_dd_r": 0.0}
    pnl = pd.to_numeric(sub[pnl_col], errors="coerce").fillna(0.0)
    return {
        "trades": int(len(sub)),
        "win_rate": round(float((pnl > 0).mean()), 4),
        "pf": round(_pf_from_pnl(pnl), 4),
        "total_r": round(float(pnl.sum()), 6),
        "avg_r": round(float(pnl.mean()), 6),
        "max_dd_r": round(_max_drawdown(pnl), 6),
    }


def _normalise_dates(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    date_col = None
    for col in ["opened_at", "signal_at", "closed_at", "datetime", "time"]:
        if col in out.columns:
            date_col = col
            break
    if date_col is not None:
        out["__avs_time"] = pd.to_datetime(out[date_col], errors="coerce")
    else:
        out["__avs_time"] = pd.NaT
    return out


def _split_by_time_or_index(df: pd.DataFrame, n_splits: int = 3) -> List[pd.DataFrame]:
    if df is None or df.empty:
        return []
    d = _normalise_dates(df)
    if d["__avs_time"].notna().sum() >= max(10, len(d) // 2):
        d = d.sort_values("__avs_time").reset_index(drop=True)
    else:
        d = d.reset_index(drop=True)
    n = len(d)
    if n == 0:
        return []
    splits: List[pd.DataFrame] = []
    for i in range(n_splits):
        lo = int(round(i * n / n_splits))
        hi = int(round((i + 1) * n / n_splits))
        if hi > lo:
            splits.append(d.iloc[lo:hi].copy())
    return splits


@dataclass
class AVSConfig:
    min_cluster_trades: int = 8
    min_regime_trades: int = 8
    edge_pf_floor: float = 1.20
    true_edge_pf_floor: float = 1.35
    weak_pf_floor: float = 1.00
    fake_pf_ceiling: float = 0.85
    concentration_warning: float = 0.55
    dominance_warning: float = 0.70
    min_avs_trade_count: int = 50


class AlphaValidationEngine:
    """Trade-log based anti-overfitting validator.

    Parameters
    ----------
    trades_df:
        Closed-trade dataframe. Expected but optional columns include pnl_r,
        regime, alpha_cluster, alpha_cluster_coarse, book, cost_r,
        raw_pnl_r, position_size, opened_at.
    config:
        Optional AVSConfig. Defaults are intentionally conservative.
    """

    def __init__(self, trades_df: pd.DataFrame, config: Optional[AVSConfig] = None) -> None:
        self.config = config or AVSConfig()
        self.df = self._prepare(trades_df)

    def _prepare(self, trades_df: pd.DataFrame) -> pd.DataFrame:
        if trades_df is None:
            return pd.DataFrame()
        df = trades_df.copy()
        if df.empty:
            return df
        if "pnl_r" not in df.columns:
            # Try legacy field names before giving up.
            for alt in ["trade_r", "result_r", "pnl", "r"]:
                if alt in df.columns:
                    df["pnl_r"] = df[alt]
                    break
        if "pnl_r" not in df.columns:
            df["pnl_r"] = 0.0
        df["pnl_r"] = pd.to_numeric(df["pnl_r"], errors="coerce").fillna(0.0)
        for col in ["regime", "book", "grade", "setup_type", "alpha_cluster", "alpha_cluster_coarse"]:
            if col not in df.columns:
                df[col] = "UNKNOWN"
            df[col] = df[col].fillna("UNKNOWN").astype(str).replace({"": "UNKNOWN", "nan": "UNKNOWN"})
        if "alpha_cluster" not in df.columns or (df["alpha_cluster"] == "UNKNOWN").all():
            df["alpha_cluster"] = df["regime"].astype(str) + "_" + df["book"].astype(str) + "_" + df["grade"].astype(str)
        for col in ["cost_r", "raw_pnl_r", "trade_r", "position_size", "expected_value", "estimated_rr"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        return df

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def run_full_assessment(self) -> Dict[str, Any]:
        if self.df.empty:
            return {
                "version": "AVS_v1_20260616",
                "overall": _stats(self.df),
                "overfit_score": 1.0,
                "avs_score": 0.0,
                "verdict": "NO_TRADES",
                "fake_clusters": [],
                "fragile_clusters": [],
                "true_edge_regimes": [],
                "warnings": ["No closed trades available for validation."],
            }

        temporal = self.temporal_validity()
        regimes = self.regime_robustness()
        perturb = self.perturbation_stress()
        independence = self.trade_independence()
        fake_clusters, fragile_clusters, cluster_table = self.detect_fake_clusters()
        true_regimes, regime_table = self.detect_true_edge_regimes()

        temporal_score = _safe_float(temporal.get("score"), 0.0)
        regime_score = _safe_float(regimes.get("score"), 0.0)
        perturb_score = _safe_float(perturb.get("score"), 0.0)
        independence_score = _safe_float(independence.get("score"), 0.0)
        avs_score = max(0.0, min(1.0, 0.25 * temporal_score + 0.25 * regime_score + 0.25 * perturb_score + 0.25 * independence_score))
        overfit_score = round(1.0 - avs_score, 4)

        warnings: List[str] = []
        if len(self.df) < self.config.min_avs_trade_count:
            warnings.append(f"Trade count {len(self.df)} is below {self.config.min_avs_trade_count}; AVS is diagnostic, not conclusive.")
        if independence.get("top_cluster_profit_share", 0.0) >= self.config.dominance_warning:
            warnings.append("Profit is dominated by a small number of clusters; cluster overfit risk is high.")
        if temporal.get("failing_splits", 0) > 0:
            warnings.append("At least one temporal split has PF below 1.0.")
        if perturb.get("high_cost_pf", 0.0) < 1.0 and _stats(self.df)["pf"] > 1.0:
            warnings.append("Edge is cost-sensitive; execution assumptions must be stress-tested before scaling.")

        return {
            "version": "AVS_v1_20260616",
            "overall": _stats(self.df),
            "avs_score": round(avs_score, 4),
            "overfit_score": overfit_score,
            "verdict": self._verdict(avs_score),
            "component_scores": {
                "temporal_validity": round(temporal_score, 4),
                "regime_robustness": round(regime_score, 4),
                "perturbation_stability": round(perturb_score, 4),
                "trade_independence": round(independence_score, 4),
            },
            "temporal_validity": temporal,
            "regime_robustness": regimes,
            "perturbation_stress": perturb,
            "trade_independence": independence,
            "fake_clusters": fake_clusters,
            "fragile_clusters": fragile_clusters,
            "true_edge_regimes": true_regimes,
            "cluster_table": cluster_table,
            "regime_table": regime_table,
            "warnings": warnings,
        }

    def save_report(self, output_dir: str | Path = "outputs", prefix: str = "avs") -> Dict[str, str]:
        report = self.run_full_assessment()
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        json_path = out / f"{prefix}_report.json"
        md_path = out / f"{prefix}_report.md"
        cluster_path = out / f"{prefix}_cluster_table.csv"
        regime_path = out / f"{prefix}_regime_table.csv"

        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        md_path.write_text(self.to_markdown(report), encoding="utf-8")
        pd.DataFrame(report.get("cluster_table", [])).to_csv(cluster_path, index=False)
        pd.DataFrame(report.get("regime_table", [])).to_csv(regime_path, index=False)
        return {
            "json": str(json_path),
            "markdown": str(md_path),
            "cluster_table": str(cluster_path),
            "regime_table": str(regime_path),
        }

    @staticmethod
    def to_markdown(report: Dict[str, Any]) -> str:
        lines = [
            "# Alpha Validity Score Report",
            "",
            f"- AVS Score: **{report.get('avs_score', 0.0)}**",
            f"- Overfit Score: **{report.get('overfit_score', 0.0)}**",
            f"- Verdict: **{report.get('verdict', 'UNKNOWN')}**",
            "",
            "## Overall",
            "```json",
            json.dumps(report.get("overall", {}), ensure_ascii=False, indent=2),
            "```",
            "",
            "## Component Scores",
            "```json",
            json.dumps(report.get("component_scores", {}), ensure_ascii=False, indent=2),
            "```",
            "",
            "## True Edge Regimes",
        ]
        true_regimes = report.get("true_edge_regimes", []) or []
        if true_regimes:
            for item in true_regimes:
                lines.append(f"- {item.get('regime')}: PF={item.get('pf')}, trades={item.get('trades')}, total_r={item.get('total_r')}")
        else:
            lines.append("- None detected under current thresholds.")
        lines.extend(["", "## Fake / Bad Clusters"])
        fake = report.get("fake_clusters", []) or []
        if fake:
            for item in fake[:30]:
                lines.append(f"- {item.get('cluster')}: {item.get('reason')} | PF={item.get('pf')} | trades={item.get('trades')}")
        else:
            lines.append("- None detected under current thresholds.")
        lines.extend(["", "## Fragile Clusters"])
        fragile = report.get("fragile_clusters", []) or []
        if fragile:
            for item in fragile[:30]:
                lines.append(f"- {item.get('cluster')}: {item.get('reason')} | PF={item.get('pf')} | trades={item.get('trades')}")
        else:
            lines.append("- None detected under current thresholds.")
        warnings = report.get("warnings", []) or []
        if warnings:
            lines.extend(["", "## Warnings"])
            for w in warnings:
                lines.append(f"- {w}")
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    # Component tests
    # ------------------------------------------------------------------
    def temporal_validity(self) -> Dict[str, Any]:
        splits = _split_by_time_or_index(self.df, 3)
        rows = []
        pfs = []
        failing = 0
        for idx, s in enumerate(splits, start=1):
            st = _stats(s)
            st["split"] = idx
            rows.append(st)
            pf = _safe_float(st.get("pf"), 0.0)
            if pf < 1.0:
                failing += 1
            if pf < 100:
                pfs.append(pf)
        if not rows:
            return {"score": 0.0, "failing_splits": 0, "splits": []}
        positive_ratio = sum(1 for r in rows if _safe_float(r.get("total_r"), 0.0) > 0) / max(1, len(rows))
        pf_floor_score = sum(1 for r in rows if _safe_float(r.get("pf"), 0.0) >= self.config.weak_pf_floor) / max(1, len(rows))
        dispersion_penalty = 0.0
        if len(pfs) >= 2:
            mean_pf = sum(pfs) / len(pfs)
            if mean_pf > EPS:
                var = sum((x - mean_pf) ** 2 for x in pfs) / len(pfs)
                cv = math.sqrt(var) / max(mean_pf, EPS)
                dispersion_penalty = min(0.35, cv * 0.20)
        score = max(0.0, min(1.0, 0.55 * positive_ratio + 0.45 * pf_floor_score - dispersion_penalty))
        return {"score": round(score, 4), "failing_splits": int(failing), "splits": rows}

    def regime_robustness(self) -> Dict[str, Any]:
        if "regime" not in self.df.columns:
            return {"score": 0.0, "edge_regime_count": 0, "profitable_regime_count": 0}
        table = []
        edge_count = 0
        profitable_count = 0
        total_profit = max(float(self.df["pnl_r"].clip(lower=0).sum()), EPS)
        max_profit_share = 0.0
        for regime, g in self.df.groupby("regime"):
            st = _stats(g)
            st["regime"] = str(regime)
            profit_share = float(g["pnl_r"].clip(lower=0).sum()) / total_profit
            st["profit_share"] = round(profit_share, 4)
            max_profit_share = max(max_profit_share, profit_share)
            if st["total_r"] > 0:
                profitable_count += 1
            if st["trades"] >= self.config.min_regime_trades and st["pf"] >= self.config.edge_pf_floor and st["total_r"] > 0:
                edge_count += 1
                st["edge_label"] = "EDGE"
            elif st["trades"] >= self.config.min_regime_trades and st["pf"] < 1.0:
                st["edge_label"] = "NEGATIVE"
            else:
                st["edge_label"] = "INSUFFICIENT_OR_NEUTRAL"
            table.append(st)
        regime_diversity_score = min(1.0, edge_count / 2.0)  # one regime can be real but fragile; two is stronger.
        dominance_penalty = max(0.0, max_profit_share - 0.70) * 1.2
        score = max(0.0, min(1.0, 0.65 * regime_diversity_score + 0.35 * min(1.0, profitable_count / 2.0) - dominance_penalty))
        return {
            "score": round(score, 4),
            "edge_regime_count": int(edge_count),
            "profitable_regime_count": int(profitable_count),
            "max_profit_share": round(max_profit_share, 4),
            "table": sorted(table, key=lambda x: x.get("total_r", 0.0), reverse=True),
        }

    def perturbation_stress(self) -> Dict[str, Any]:
        base = _stats(self.df)
        base_pf = _safe_float(base.get("pf"), 0.0)
        base_total = _safe_float(base.get("total_r"), 0.0)
        scenarios: Dict[str, Dict[str, Any]] = {"base": base}

        if "raw_pnl_r" in self.df.columns and "cost_r" in self.df.columns:
            pos = pd.to_numeric(self.df.get("position_size", 1.0), errors="coerce").fillna(1.0)
            raw = pd.to_numeric(self.df["raw_pnl_r"], errors="coerce").fillna(0.0)
            cost = pd.to_numeric(self.df["cost_r"], errors="coerce").fillna(0.0)
            for mult in [0.5, 1.5, 2.0]:
                adjusted = (raw - cost * mult) * pos
                tmp = self.df.copy()
                tmp["pnl_r"] = adjusted
                scenarios[f"cost_x_{mult}"] = _stats(tmp)
        elif "cost_r" in self.df.columns:
            # Fallback: perturb only the known cost component from final pnl.
            cost = pd.to_numeric(self.df["cost_r"], errors="coerce").fillna(0.0)
            for mult in [0.5, 1.5, 2.0]:
                tmp = self.df.copy()
                tmp["pnl_r"] = self.df["pnl_r"] - cost * (mult - 1.0)
                scenarios[f"cost_x_{mult}"] = _stats(tmp)

        # Temporal entry-delay proxy: remove the top tail. If the edge depends on
        # a few perfectly timed fills, this conservative stress test will expose it.
        tmp = self.df.copy()
        if len(tmp) > 0:
            cap = tmp["pnl_r"].quantile(0.90)
            tmp["pnl_r"] = tmp["pnl_r"].clip(upper=cap)
            scenarios["top_tail_clipped_p90"] = _stats(tmp)

        stressed_pfs = [_safe_float(v.get("pf"), 0.0) for k, v in scenarios.items() if k != "base"]
        stressed_totals = [_safe_float(v.get("total_r"), 0.0) for k, v in scenarios.items() if k != "base"]
        if not stressed_pfs:
            score = 0.45
        else:
            min_pf = min(stressed_pfs)
            min_total = min(stressed_totals) if stressed_totals else base_total
            pf_score = min(1.0, max(0.0, min_pf / max(self.config.edge_pf_floor, EPS)))
            total_score = 1.0 if min_total > 0 else 0.0
            score = 0.65 * pf_score + 0.35 * total_score
        return {
            "score": round(float(max(0.0, min(1.0, score))), 4),
            "base_pf": round(base_pf, 4),
            "high_cost_pf": round(_safe_float(scenarios.get("cost_x_1.5", {}).get("pf"), base_pf), 4),
            "scenarios": scenarios,
        }

    def trade_independence(self) -> Dict[str, Any]:
        total_profit = float(self.df["pnl_r"].clip(lower=0).sum())
        if total_profit <= EPS:
            return {"score": 0.0, "top_cluster_profit_share": 0.0, "top_trade_profit_share": 0.0}
        cluster_col = "alpha_cluster" if "alpha_cluster" in self.df.columns else "regime"
        cluster_profit = self.df.groupby(cluster_col)["pnl_r"].apply(lambda x: float(x.clip(lower=0).sum())).sort_values(ascending=False)
        top_cluster_share = float(cluster_profit.iloc[0] / total_profit) if len(cluster_profit) else 0.0
        top_trade_share = float(self.df["pnl_r"].clip(lower=0).max() / total_profit) if total_profit > EPS else 0.0
        top5_share = float(self.df["pnl_r"].clip(lower=0).nlargest(min(5, len(self.df))).sum() / total_profit) if total_profit > EPS else 0.0
        cluster_penalty = max(0.0, (top_cluster_share - self.config.dominance_warning) / max(1.0 - self.config.dominance_warning, EPS))
        trade_penalty = max(0.0, (top_trade_share - self.config.concentration_warning) / max(1.0 - self.config.concentration_warning, EPS))
        top5_penalty = max(0.0, (top5_share - 0.80) / 0.20)
        score = max(0.0, min(1.0, 1.0 - 0.45 * cluster_penalty - 0.35 * trade_penalty - 0.20 * top5_penalty))
        return {
            "score": round(score, 4),
            "top_cluster_profit_share": round(top_cluster_share, 4),
            "top_trade_profit_share": round(top_trade_share, 4),
            "top5_trade_profit_share": round(top5_share, 4),
            "top_cluster": str(cluster_profit.index[0]) if len(cluster_profit) else "UNKNOWN",
        }

    # ------------------------------------------------------------------
    # Cluster / regime classifiers
    # ------------------------------------------------------------------
    def detect_fake_clusters(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        cluster_col = "alpha_cluster" if "alpha_cluster" in self.df.columns else "regime"
        rows: List[Dict[str, Any]] = []
        fake: List[Dict[str, Any]] = []
        fragile: List[Dict[str, Any]] = []
        total_profit = max(float(self.df["pnl_r"].clip(lower=0).sum()), EPS)
        for cluster, g in self.df.groupby(cluster_col):
            st = _stats(g)
            profit = float(g["pnl_r"].clip(lower=0).sum())
            st.update({
                "cluster": str(cluster),
                "regime_count": int(g["regime"].nunique()) if "regime" in g.columns else 0,
                "profit_share": round(profit / total_profit, 4),
                "top_trade_profit_share_in_cluster": round(float(g["pnl_r"].clip(lower=0).max() / max(profit, EPS)), 4) if profit > EPS else 0.0,
            })
            label = "NEUTRAL"
            reason = ""
            if st["trades"] >= self.config.min_cluster_trades and st["pf"] <= self.config.fake_pf_ceiling:
                label = "FAKE_ALPHA"
                reason = "NEGATIVE_EXPECTANCY_CLUSTER"
            elif st["trades"] < self.config.min_cluster_trades and st["pf"] >= 1.5 and st["total_r"] > 0:
                label = "FRAGILE_ALPHA"
                reason = "HIGH_PF_WITH_TOO_FEW_TRADES"
            elif st["pf"] >= 1.5 and st["top_trade_profit_share_in_cluster"] >= self.config.concentration_warning:
                label = "FRAGILE_ALPHA"
                reason = "PROFIT_DOMINATED_BY_ONE_TRADE"
            elif st["pf"] >= 1.5 and st["regime_count"] <= 1 and st["trades"] < 2 * self.config.min_cluster_trades:
                label = "FRAGILE_ALPHA"
                reason = "SINGLE_REGIME_SMALL_SAMPLE"
            elif st["trades"] >= self.config.min_cluster_trades and st["pf"] >= self.config.edge_pf_floor and st["total_r"] > 0:
                label = "CANDIDATE_TRUE_ALPHA"
                reason = "POSITIVE_EXPECTANCY_CLUSTER"
            st["label"] = label
            st["reason"] = reason
            rows.append(st)
            if label == "FAKE_ALPHA":
                fake.append(st)
            elif label == "FRAGILE_ALPHA":
                fragile.append(st)
        rows = sorted(rows, key=lambda x: (x.get("label") != "CANDIDATE_TRUE_ALPHA", -_safe_float(x.get("total_r"), 0.0)))
        fake = sorted(fake, key=lambda x: (_safe_float(x.get("pf"), 0.0), -int(x.get("trades", 0))))
        fragile = sorted(fragile, key=lambda x: -_safe_float(x.get("profit_share"), 0.0))
        return fake, fragile, rows

    def detect_true_edge_regimes(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        rows = []
        true_edges = []
        if "regime" not in self.df.columns:
            return true_edges, rows
        splits = _split_by_time_or_index(self.df, 2)
        for regime, g in self.df.groupby("regime"):
            st = _stats(g)
            st["regime"] = str(regime)
            split_ok = 0
            split_total = 0
            for s in splits:
                sg = s[s["regime"].astype(str) == str(regime)] if "regime" in s.columns else pd.DataFrame()
                if len(sg) >= max(3, self.config.min_regime_trades // 2):
                    split_total += 1
                    if _stats(sg)["pf"] >= 1.0 and _stats(sg)["total_r"] > 0:
                        split_ok += 1
            st["temporal_pass_ratio"] = round(split_ok / max(1, split_total), 4) if split_total else 0.0
            if st["trades"] >= self.config.min_regime_trades and st["pf"] >= self.config.true_edge_pf_floor and st["total_r"] > 0 and st["temporal_pass_ratio"] >= 0.5:
                st["edge_label"] = "TRUE_EDGE_CANDIDATE"
                true_edges.append(st)
            elif st["trades"] >= self.config.min_regime_trades and st["pf"] < 1.0:
                st["edge_label"] = "NEGATIVE_REGIME"
            else:
                st["edge_label"] = "NEUTRAL_OR_INSUFFICIENT"
            rows.append(st)
        return sorted(true_edges, key=lambda x: -_safe_float(x.get("total_r"), 0.0)), sorted(rows, key=lambda x: -_safe_float(x.get("total_r"), 0.0))

    @staticmethod
    def _verdict(avs: float) -> str:
        if avs >= 0.80:
            return "TRUE_ALPHA_CANDIDATE_SCALE_ALLOWED"
        if avs >= 0.60:
            return "WEAK_ALPHA_TEST_MORE_BEFORE_SCALING"
        if avs >= 0.40:
            return "FRAGILE_ALPHA_HIGH_OVERFIT_RISK"
        return "OVERFIT_OR_NO_REAL_ALPHA"


def run_alpha_validation(trades_df: pd.DataFrame, output_dir: str | Path = "outputs", prefix: str = "avs") -> Dict[str, Any]:
    engine = AlphaValidationEngine(trades_df)
    report = engine.run_full_assessment()
    try:
        paths = engine.save_report(output_dir=output_dir, prefix=prefix)
        report["saved_paths"] = paths
    except Exception as exc:  # keep backtests alive even if report writing fails
        report["save_error"] = f"{type(exc).__name__}: {exc}"
    return report
