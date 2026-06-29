# -*- coding: utf-8 -*-
"""Audit and summarize Strategy filter decisions."""
import csv
import json
from pathlib import Path

FIELDS = [
    "timestamp", "symbol", "bar_index", "price", "direction", "grade", "approved",
    "state", "blocked_by", "reason", "rr", "volume_ratio", "atr_pct", "near_structure_atr",
    "opposite_structure_atr", "future_return", "future_r", "raw_json",
]


def _safe_float(x, default=0.0):
    try:
        if x is None:
            return default
        v = float(x)
        return default if v != v else v
    except Exception:
        return default


def _get(curr, key, default=None):
    try:
        if hasattr(curr, "get"):
            return curr.get(key, default)
        return curr[key]
    except Exception:
        return default


def _direction(decision):
    primary = (decision or {}).get("primary") or {}
    risk_plan = (decision or {}).get("risk_plan") or {}
    return primary.get("direction") or risk_plan.get("direction") or ""


def _grade(decision):
    primary = (decision or {}).get("primary") or {}
    return str(primary.get("grade") or primary.get("level") or "").upper()


def _rr(decision):
    risk_plan = (decision or {}).get("risk_plan") or {}
    return _safe_float(risk_plan.get("rr"), 0.0)


def _first_blocked_filter(strategy_filters):
    for item in (strategy_filters or {}).get("results", []):
        if not item.get("passed", True):
            return item.get("filter", "unknown")
    return ""


def _metric(strategy_filters, filter_name, metric_name, default=0.0):
    for item in (strategy_filters or {}).get("results", []):
        if item.get("filter") == filter_name:
            return _safe_float((item.get("metrics") or {}).get(metric_name), default)
    return default


class FilterAuditLogger:
    def __init__(self, path="reports/filter_audit.csv"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            with self.path.open("w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=FIELDS).writeheader()

    def record(self, symbol, curr, decision, future_price=None, future_r=None):
        decision = decision or {}
        sf = decision.get("strategy_filters") or {}
        price = _safe_float(_get(curr, "close"), 0.0)
        future_return = 0.0
        if future_price is not None and price > 0:
            direction = str(_direction(decision)).title()
            fp = _safe_float(future_price, 0.0)
            if direction == "Long":
                future_return = (fp - price) / price
            elif direction == "Short":
                future_return = (price - fp) / price
        row = {
            "timestamp": str(_get(curr, "datetime", _get(curr, "timestamp", ""))),
            "symbol": symbol,
            "bar_index": getattr(curr, "name", ""),
            "price": price,
            "direction": _direction(decision),
            "grade": _grade(decision),
            "approved": bool(decision.get("approved")),
            "state": decision.get("state_name") or decision.get("state") or "",
            "blocked_by": _first_blocked_filter(sf),
            "reason": decision.get("reason") or decision.get("reason_cn") or "",
            "rr": _rr(decision),
            "volume_ratio": _metric(sf, "volume_confirmation", "volume_ratio"),
            "atr_pct": _metric(sf, "atr_volatility", "atr_pct"),
            "near_structure_atr": _metric(sf, "structure_distance", "near_atr"),
            "opposite_structure_atr": _metric(sf, "structure_distance", "opposite_atr"),
            "future_return": future_return,
            "future_r": _safe_float(future_r, 0.0),
            "raw_json": json.dumps(decision, ensure_ascii=False, default=str),
        }
        with self.path.open("a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=FIELDS).writerow(row)
        return row

    def log(self, *args, **kwargs):
        """
        向后兼容别名：解决外部调用 .log() 导致的 AttributeError 报错。
        将所有传给 log() 的参数无缝路由至实际工作的 record() 方法中。
        """
        return self.record(*args, **kwargs)


def summarize_filter_audit(path="reports/filter_audit.csv", output_path="reports/filter_audit_summary.json"):
    p = Path(path)
    if not p.exists():
        return {"ok": False, "reason": f"missing file: {p}"}
    with p.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    total = len(rows)
    by_filter = {}
    for r in rows:
        blocked_by = r.get("blocked_by") or "approved_or_no_filter"
        d = by_filter.setdefault(blocked_by, {"count": 0, "future_return_sum": 0.0, "future_r_sum": 0.0})
        d["count"] += 1
        d["future_return_sum"] += _safe_float(r.get("future_return"), 0.0)
        d["future_r_sum"] += _safe_float(r.get("future_r"), 0.0)
    for d in by_filter.values():
        n = max(1, d["count"])
        d["block_rate"] = d["count"] / max(1, total)
        d["avg_future_return"] = d["future_return_sum"] / n
        d["avg_future_r"] = d["future_r_sum"] / n
    summary = {"ok": True, "total": total, "by_filter": by_filter}
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


# ==========================================
# 补充：暴露给外部直接调用的 wrapper 函数
# ==========================================
_default_logger = FilterAuditLogger()

def record_filter_audit(symbol, curr, decision, future_price=None, future_r=None):
    """
    提供给 v9_decision_kernel.py 调用的接口
    """
    return _default_logger.record(symbol, curr, decision, future_price, future_r)
