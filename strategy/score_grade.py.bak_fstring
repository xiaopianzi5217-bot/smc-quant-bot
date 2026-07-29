# -*- coding: utf-8 -*-
"""
Score Grade — 优化3: Score 重新分级

当前问题：
  70分也可能交易，没有清晰的分级标准。
  高分和低分信号用同样的标准处理，导致垃圾信号也能进入。

解决方案：
  将 score 分为 A_PLUS / A / B / REJECT 四个等级，
  每个等级有对应的 min_score 和 min_ev 要求。

用法:
  from strategy.score_grade import GradeConfig, score_grader
  grade = score_grader.grade(score=85, ev=0.12)
  # grade["grade"] = "A_PLUS"
  # grade["allow"] = True
  # grade["size_mult"] = 1.0
"""

from __future__ import annotations

from typing import Dict, Any, Optional


# ============================================================
# 默认分级配置
# ============================================================
DEFAULT_GRADE_CONFIG: Dict[str, Dict[str, float]] = {
    "A_PLUS": {
        "min_score": 85,
        "min_ev": 0.10,
        "size_mult": 1.0,
    },
    "A": {
        "min_score": 78,
        "min_ev": 0.05,
        "size_mult": 0.85,
    },
    "B": {
        "min_score": 64,   # 【修复】从65降到64，ETH FeatureLearning 82.7→64.1 的临界问题
        "min_ev": 0.01,
        "size_mult": 0.65,
    },
    "REJECT": {
        "min_score": 0,
        "min_ev": -999.0,
        "size_mult": 0.0,
    },
}


class ScoreGrader:
    """Score 分级器

    根据 score 和 EV 将信号分为不同等级，
    允许调用方根据等级做不同处理（仓位、权限等）。
    """

    def __init__(self, grade_config: Optional[Dict[str, Dict[str, float]]] = None):
        """
        参数:
            grade_config: 可选自定义分级配置
        """
        self.config = grade_config or DEFAULT_GRADE_CONFIG

    def grade(
        self,
        score: float,
        ev: float,
        regime: str = "UNKNOWN",
    ) -> Dict[str, Any]:
        """对信号进行分级

        参数:
            score: 信号分数
            ev: 预期值
            regime: 市场状态（可选，用于日志）

        返回:
            {
                "grade": "A_PLUS" | "A" | "B" | "REJECT",
                "allow": bool,
                "size_mult": float,
                "min_score_for_grade": float,
                "min_ev_for_grade": float,
            }
        """
        # 按优先级从上到下检查
        for grade_name in ["A_PLUS", "A", "B"]:
            cfg = self.config[grade_name]
            if score >= cfg["min_score"] and ev >= cfg["min_ev"]:
                return {
                    "grade": grade_name,
                    "allow": True,
                    "size_mult": cfg["size_mult"],
                    "min_score_for_grade": cfg["min_score"],
                    "min_ev_for_grade": cfg["min_ev"],
                }

        # 不满足任何等级 → REJECT
        reject_cfg = self.config["REJECT"]
        return {
            "grade": "REJECT",
            "allow": False,
            "size_mult": reject_cfg["size_mult"],
            "min_score_for_grade": reject_cfg["min_score"],
            "min_ev_for_grade": reject_cfg["min_ev"],
        }

    def get_grade_names(self) -> list:
        """返回所有等级名称列表"""
        return list(self.config.keys())

    def update_config(self, grade_name: str, key: str, value: float):
        """动态更新配置"""
        if grade_name in self.config:
            self.config[grade_name][key] = value


# 全局单例
_score_grader = ScoreGrader()


def get_score_grader() -> ScoreGrader:
    return _score_grader

