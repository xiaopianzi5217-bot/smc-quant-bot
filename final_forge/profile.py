# -*- coding: utf-8 -*-
"""
V55 engineering profile.

Compatibility note:
- The public apply_v52_final_profile name is preserved for older scripts.
- V55 removes the V54 MFE-driven TP1 replay proxy by default.
- V55 does not micro-cap wins or hard-floor losses by default.
- Candidate-pool replay is only a fast diagnostic layer. The raw event runner
  remains the authoritative path for execution-level testing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, Optional
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class V52ProfileConfig:
    """Backward-compatible config name. Defaults now implement V55 behavior."""
    allowed_regimes: tuple[str, ...] = ("TREND", "TRANSITION", "CHOP")
    min_score: float = 60.0
    min_expected_value: float = 0.01
    min_win_prob: float = 0.45
    min_estimated_rr: float = 1.10
    # None means no artificial micro cap/floor.
    profit_cap_r: float | None = None
    loss_floor_r: float | None = None
    # Kept only for legacy experiments; disabled in V55.
    tp1_mfe_threshold_r: float = 1.0
    tp1_replay_profit_r: float = 0.0
    use_mfe_tp1_replay: bool = False
    # Candidate-pool fields are imperfect: raw_pnl_r is bar-exit before sizing,
    # trade_r is after cost. V55 uses raw_pnl_r minus a bounded cost stress to
    # avoid the old micro-position pnl_r artefact while still penalizing costs.
    outcome_column: str = "raw_pnl_r"
    cost_column: str = "cost_r"
    cost_stress_cap_r: float = 0.10
    setup_name: str = "V55_ENGINEERING_REALISTIC"
    profile_rule: str = "REGIME_ALL__SCORE60_EV001_WP045_RR110__NO_MFE_REPLAY__NO_MICRO_CAP__COST_STRESS010"


def _numeric(series: pd.Series, default: float = 0.0) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(default)


def _pick_outcome(df: pd.DataFrame, cfg: V52ProfileConfig) -> pd.Series:
    if cfg.outcome_column in df.columns:
        base = _numeric(df[cfg.outcome_column], 0.0)
    elif "trade_r" in df.columns:
        base = _numeric(df["trade_r"], 0.0)
    else:
        base = _numeric(df.get("pnl_r", pd.Series(0.0, index=df.index)), 0.0)

    # In candidate replay, cost_r was calculated from old tiny stops.  We do not
    # ignore it, but cap the stress so one bad old stop estimate cannot dominate
    # a diagnostic profile after V55 widened the live/event stop model.
    if cfg.cost_column in df.columns and cfg.cost_stress_cap_r is not None and cfg.cost_stress_cap_r > 0:
        cost = _numeric(df[cfg.cost_column], 0.0).clip(lower=0.0, upper=float(cfg.cost_stress_cap_r))
        return base - cost
    return base


def apply_v55_engineering_profile(pool: pd.DataFrame, config: Optional[V52ProfileConfig] = None) -> pd.DataFrame:
    """Apply the V55 executable diagnostic profile to an audited candidate pool."""
    cfg = config or V52ProfileConfig()
    if pool is None or pool.empty:
        return pd.DataFrame()

    df = pool.copy()
    for col, default in [
        ("score", 0.0),
        ("expected_value", 0.0),
        ("win_prob", 0.0),
        ("estimated_rr", 0.0),
        ("pnl_r", 0.0),
        ("mfe_r", 0.0),
    ]:
        if col not in df.columns:
            df[col] = default
        df[col] = _numeric(df[col], default)

    if "regime" not in df.columns:
        df["regime"] = "UNKNOWN"
    if "setup_type" not in df.columns:
        df["setup_type"] = "UNKNOWN"

    mask = (
        df["regime"].astype(str).isin(cfg.allowed_regimes)
        & (df["score"] >= float(cfg.min_score))
        & (df["expected_value"] >= float(cfg.min_expected_value))
        & (df["win_prob"] >= float(cfg.min_win_prob))
        & (df["estimated_rr"] >= float(cfg.min_estimated_rr))
    )
    out = df.loc[mask].copy()
    if out.empty:
        return out

    out["pnl_r_raw_candidate"] = _numeric(out.get("pnl_r", pd.Series(0.0, index=out.index)), 0.0)
    out["pnl_r_outcome_source"] = cfg.outcome_column
    out["pnl_r_pre_stress"] = _pick_outcome(out, V52ProfileConfig(**{**cfg.__dict__, "cost_stress_cap_r": 0.0}))
    out["pnl_r"] = _pick_outcome(out, cfg)
    out["pnl_r_uncapped"] = out["pnl_r"]

    if cfg.profit_cap_r is not None:
        out["pnl_r"] = np.minimum(out["pnl_r"], float(cfg.profit_cap_r))
    if cfg.loss_floor_r is not None:
        out["pnl_r"] = np.maximum(out["pnl_r"], float(cfg.loss_floor_r))

    out["exit_policy"] = "REALIZED_BAR_EXIT_NO_MFE_REPLAY_COST_STRESSED"
    out["v52_final_profile"] = False
    out["v55_engineering_realistic"] = True
    out["v55_allowed_regimes"] = ",".join(cfg.allowed_regimes)
    out["v55_min_score"] = float(cfg.min_score)
    out["v55_min_expected_value"] = float(cfg.min_expected_value)
    out["v55_min_win_prob"] = float(cfg.min_win_prob)
    out["v55_min_estimated_rr"] = float(cfg.min_estimated_rr)
    out["v55_profit_cap_r"] = cfg.profit_cap_r
    out["v55_loss_floor_r"] = cfg.loss_floor_r
    out["v55_use_mfe_tp1_replay"] = bool(cfg.use_mfe_tp1_replay)
    out["v55_cost_stress_cap_r"] = float(cfg.cost_stress_cap_r)
    out["target_profile"] = True
    out["profile_rule"] = cfg.profile_rule
    out["setup_type_original"] = out.get("setup_type", "UNKNOWN")
    out["setup_type"] = cfg.setup_name
    return out.reset_index(drop=True)


def apply_v52_final_profile(pool: pd.DataFrame, config: Optional[V52ProfileConfig] = None) -> pd.DataFrame:
    """Backward-compatible alias; now applies the V55 profile defaults."""
    return apply_v55_engineering_profile(pool, config)


def summarize_profile(trades: pd.DataFrame) -> Dict[str, Any]:
    if trades is None or getattr(trades, "empty", False) or "pnl_r" not in getattr(trades, "columns", []):
        return {"trades": 0, "win_rate": 0.0, "pf": 0.0, "pnl": 0.0, "avg_r": 0.0}
    pnl = _numeric(trades["pnl_r"], 0.0)
    wins = float(pnl[pnl > 0].sum())
    losses = abs(float(pnl[pnl < 0].sum()))
    pf = wins / losses if losses > 0 else (999.0 if wins > 0 else 0.0)
    equity = pnl.cumsum()
    dd = equity - equity.cummax()
    micro_loss_freq = float(((pnl < 0) & (pnl > -0.20)).mean()) if len(pnl) else 0.0
    losing_density = float((pnl < 0).mean()) if len(pnl) else 0.0
    return {
        "trades": int(len(pnl)),
        "win_rate": round(float((pnl > 0).mean()), 4),
        "pf": round(float(pf), 4),
        "pnl": round(float(pnl.sum()), 4),
        "avg_r": round(float(pnl.mean()), 5),
        "max_win_r": round(float(pnl.max()), 4),
        "max_loss_r": round(float(pnl.min()), 4),
        "max_dd_r": round(float(dd.min()), 4),
        "losing_trade_density": round(losing_density, 4),
        "micro_loss_frequency": round(micro_loss_freq, 4),
    }


def temporal_stability_report(trades: pd.DataFrame, slices: int = 4) -> Dict[str, Any]:
    """Simple anti-overfit check: metrics by chronological slices."""
    if trades is None or getattr(trades, "empty", False):
        return {"slices": []}
    df = trades.copy()
    if "opened_at" in df.columns:
        df["opened_at"] = pd.to_datetime(df["opened_at"], errors="coerce")
        df = df.sort_values("opened_at")
    chunks = np.array_split(df.index.to_numpy(), max(1, int(slices)))
    return {"slices": [summarize_profile(df.iloc[c]) for c in chunks if len(c) > 0]}


def compression_test(trades: pd.DataFrame, extra_slippage_r: float = 0.01, tp_decay: float = 0.05, random_delay_loss_r: float = 0.02) -> Dict[str, Any]:
    """Deterministic PF compression check used by V55 deep detection."""
    if trades is None or trades.empty or "pnl_r" not in trades.columns:
        return summarize_profile(pd.DataFrame())
    df = trades.copy()
    pnl = _numeric(df["pnl_r"], 0.0)
    compressed = pnl.copy()
    compressed = compressed - float(extra_slippage_r)
    compressed = np.where(compressed > 0, compressed * (1.0 - float(tp_decay)), compressed)
    # Deterministic delay stress: every 7th trade gets a small adverse fill shock.
    delay_mask = (np.arange(len(compressed)) % 7) == 0
    compressed = pd.Series(compressed, index=df.index)
    compressed.loc[delay_mask] = compressed.loc[delay_mask] - float(random_delay_loss_r)
    df["pnl_r"] = compressed
    return summarize_profile(df)
