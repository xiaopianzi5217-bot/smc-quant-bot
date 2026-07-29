# -*- coding: utf-8 -*-
"""Simple grid optimizer for Strategy filter thresholds based on filter_audit.csv."""
import csv
import json
from itertools import product
from pathlib import Path


def _f(x, default=0.0):
    try:
        if x is None or x == "":
            return default
        v = float(x)
        return default if v != v else v
    except Exception:
        return default


def _load_rows(path):
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    with p.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def optimize_from_audit(
    audit_path="reports/filter_audit.csv",
    output_path="reports/strategy_param_optimization.json",
    min_rr_values=(1.5, 1.8, 2.0, 2.2, 2.5),
    volume_ratio_values=(1.0, 1.1, 1.15, 1.25, 1.4, 1.5),
    max_structure_atr_values=(1.2, 1.5, 2.0, 2.5, 3.0),
):
    rows = _load_rows(audit_path)
    scored = []
    for min_rr, volume_ratio, max_struct in product(min_rr_values, volume_ratio_values, max_structure_atr_values):
        kept = []
        for r in rows:
            rr = _f(r.get("rr"), 0.0)
            vol = _f(r.get("volume_ratio"), 0.0)
            near = _f(r.get("near_structure_atr"), 999.0)
            if rr >= min_rr and vol >= volume_ratio and near <= max_struct:
                kept.append(r)
        n = len(kept)
        if n == 0:
            continue
        avg_r = sum(_f(x.get("future_r"), 0.0) for x in kept) / n
        avg_ret = sum(_f(x.get("future_return"), 0.0) for x in kept) / n
        win_rate = sum(1 for x in kept if _f(x.get("future_r"), 0.0) > 0) / n
        score = avg_r * 0.65 + win_rate * 0.25 + avg_ret * 10.0 * 0.10
        scored.append({
            "min_rr": min_rr,
            "min_volume_ratio": volume_ratio,
            "max_structure_atr": max_struct,
            "trades": n,
            "avg_future_r": avg_r,
            "avg_future_return": avg_ret,
            "win_rate": win_rate,
            "score": score,
        })
    scored.sort(key=lambda x: (x["score"], x["trades"]), reverse=True)
    result = {"ok": True, "tested": len(scored), "best": scored[0] if scored else None, "top10": scored[:10]}
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
