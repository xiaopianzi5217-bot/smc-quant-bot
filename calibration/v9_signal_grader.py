# -*- coding: utf-8 -*-
"""V9 signal grading.

The grader can work from historical statistics when available; otherwise it
falls back to the real-time V6 priority score.  Opening permission is no longer
hard-coded to S/A only when the runtime config explicitly allows B-grade sizing.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def _grade_letter(value: Any, default: str = "C") -> str:
    text = str(value or default).strip().upper()
    return text[0] if text else default


class V9SignalGrader:
    def __init__(self, stats_lookup: Optional[Dict[str, Dict[str, Any]]] = None):
        self.stats_lookup = stats_lookup or {}

    def grade(self, signal_key: str, priority: float = 0.0, score: float = 0.0) -> Tuple[str, str]:
        stats = self.stats_lookup.get(signal_key)
        if stats:
            n = int(stats.get("trades", 0))
            wr = float(stats.get("win_rate", 0))
            avg_r = float(stats.get("avg_r", 0))
            if n >= 30 and wr >= 0.58 and avg_r >= 0.60:
                return "S级", "历史胜率和平均收益都很强，可重点关注"
            if n >= 20 and wr >= 0.52 and avg_r >= 0.30:
                return "A级", "历史表现合格，可考虑开单"
            if n >= 10 and avg_r >= 0:
                return "B级", "历史表现一般，降低仓位或只观察"
            return "C级", "历史表现不足或偏弱，不建议开单"

        p = float(priority or 0)
        s = float(score or 0)
        x = max(p, s)
        if x >= 8:
            return "S级", "实时评分很强，可重点关注"
        if x >= 6:
            return "A级", "实时评分合格，可考虑开单"
        if x >= 4:
            return "B级", "有结构提醒，按配置小仓或观察"
        return "C级", "信号较弱，仅记录"

    def can_open(self, grade: Any, cfg: Optional[Dict[str, Any]] = None) -> bool:
        """Return whether a grade is allowed to become a Strategy open alert.

        If position_sizing is configured, that config is the source of truth:
        grades in observe_grades or with a zero multiplier cannot open.  Without
        config, keep the conservative legacy behavior: only S/A can open.
        """
        g = _grade_letter(grade)
        sizing = (cfg or {}).get("position_sizing") or {}
        if sizing:
            observe = {_grade_letter(x) for x in sizing.get("observe_grades", ["C", "D"])}
            mults = sizing.get("grade_risk_multiplier") or {}
            try:
                multiplier = float(mults.get(g, 0.0))
            except Exception:
                multiplier = 0.0
            return g not in observe and multiplier > 0
        return g in {"S", "A"}
