# -*- coding: utf-8 -*-
"""
Probe Manager — Observer 转探针交易决策引擎

核心逻辑：
  1) Observer-only 信号如果满足条件，允许以极小仓位执行（Probe）
  2) Probe 交易独立记录（mode = "PROBE"），不进入正式仓位统计
  3) 积累真实胜率 / PF 数据，为策略升级提供依据

设计原则：
  - 不影响现有 Score/EV/Outcome 数据结构
  - 与 config.py 中的 PROBE_* 配置联动
  - 与 analytics/reject_analytics 联动记录拒绝原因

用法：
  from execution.probe_manager import probe_manager
  allow = probe_manager.allow_probe(score, confidence, observer_events)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# ════════════════════════════════════════════════════════════════
# 默认 Probe 参数（仅当 config 不可用时作为后备）
# ════════════════════════════════════════════════════════════════
DEFAULT_PROBE_MIN_SCORE = 55.0
DEFAULT_PROBE_MIN_CONFIDENCE = 0.55
DEFAULT_STRONG_EVENTS = [
    "CHOCH",
    "LIQUIDITY_SWEEP",
    "FVG",
    "SQUEEZE_RELEASE",
]
DEFAULT_MIN_STRONG_EVENTS = 2
DEFAULT_SIZE_MULTIPLIER = 0.25


def _load_probe_config() -> dict:
    """从 config.py 读取 Probe 配置，失败时返回默认值"""
    try:
        from config import (
            PROBE_MIN_SCORE,
            PROBE_MIN_CONFIDENCE,
            PROBE_REQUIRED_EVENTS,
            PROBE_MIN_STRONG_EVENTS,
            PROBE_SIZE_MULTIPLIER,
        )
        return {
            "min_score": float(PROBE_MIN_SCORE),
            "min_confidence": float(PROBE_MIN_CONFIDENCE),
            "strong_events": list(PROBE_REQUIRED_EVENTS),
            "min_strong_events": int(PROBE_MIN_STRONG_EVENTS),
            "size_multiplier": float(PROBE_SIZE_MULTIPLIER),
        }
    except (ImportError, AttributeError, ValueError, TypeError):
        return {
            "min_score": DEFAULT_PROBE_MIN_SCORE,
            "min_confidence": DEFAULT_PROBE_MIN_CONFIDENCE,
            "strong_events": DEFAULT_STRONG_EVENTS,
            "min_strong_events": DEFAULT_MIN_STRONG_EVENTS,
            "size_multiplier": DEFAULT_SIZE_MULTIPLIER,
        }


class ProbeManager:
    """
    Probe 交易决策管理器。

    决定一个 Observer-only 信号是否可以转为 Probe 交易，
    以及应该使用多大的仓位乘数。

    配置来源（优先级）：
      1. 构造参数显式传入
      2. config.py 中的 PROBE_* 常量
      3. 文件硬编码默认值（DEFAULT_*）
    """

    def __init__(
        self,
        min_score: Optional[float] = None,
        min_confidence: Optional[float] = None,
        strong_events: Optional[List[str]] = None,
        min_strong_events: Optional[int] = None,
        size_multiplier: Optional[float] = None,
    ):
        # 1. 用 config 值兜底
        _cfg = _load_probe_config()
        self.min_score = min_score if min_score is not None else _cfg["min_score"]
        self.min_confidence = (
            min_confidence if min_confidence is not None else _cfg["min_confidence"]
        )
        self.strong_events = strong_events if strong_events is not None else _cfg["strong_events"]
        self.min_strong_events = (
            min_strong_events if min_strong_events is not None else _cfg["min_strong_events"]
        )
        self.size_multiplier = (
            size_multiplier if size_multiplier is not None else _cfg["size_multiplier"]
        )

    def allow_probe(
        self,
        score: float,
        confidence: float,
        observer_events: Optional[List[Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        判断是否允许将 Observer-only 信号转为 Probe 交易。

        条件:
          1. score >= min_score（默认 55）
          2. confidence >= min_confidence（默认 0.55）
          3. observer_events 中至少包含 min_strong_events 个白名单事件

        参数:
            score: 信号评分
            confidence: 信号置信度
            observer_events: Observer 识别到的事件列表
            extra: 额外字段（暂未使用）

        返回:
            True 表示允许 Probe 交易, False 表示拒绝
        """
        # 1. Score 门槛
        if score < self.min_score:
            return False

        # 2. Confidence 门槛
        if confidence < self.min_confidence:
            return False

        # 3. 如果没有 Observer 事件，不允许
        if not observer_events:
            return False

        # 4. 统计白名单事件数量
        strong_count = 0
        for event in observer_events:
            # event 可能是字符串，也可能是 dict
            event_str = str(event).upper() if not isinstance(event, str) else event.upper()
            if event_str in self.strong_events:
                strong_count += 1

        return strong_count >= self.min_strong_events

    def size_multiplier(self) -> float:
        """
        返回 Probe 交易使用的仓位乘数。

        Returns:
            float: 0.0 ~ 1.0，默认 0.25
        """
        return self.size_multiplier

    def reject_reason(
        self,
        score: float,
        confidence: float,
        observer_events: Optional[List[Any]] = None,
    ) -> str:
        """
        返回 Probe 被拒绝的具体原因（用于 RejectAnalytics 记录）。

        Returns:
            "NO_OBSERVER_EVENTS" | "LOW_SCORE" | "LOW_CONFIDENCE" | "NOT_ENOUGH_STRONG_EVENTS"
        """
        if not observer_events:
            return "NO_OBSERVER_EVENTS"
        if score < self.min_score:
            return "LOW_SCORE"
        if confidence < self.min_confidence:
            return "LOW_CONFIDENCE"

        strong_count = 0
        for event in observer_events:
            event_str = str(event).upper() if not isinstance(event, str) else event.upper()
            if event_str in self.strong_events:
                strong_count += 1

        if strong_count < self.min_strong_events:
            return "NOT_ENOUGH_STRONG_EVENTS"

        return "ALLOWED"


# 全局单例 — 自动从 config.py 读取 PROBE_*
# 修改 config.py 中的 PROBE_* 常量即可调整探针行为
probe_manager = ProbeManager()