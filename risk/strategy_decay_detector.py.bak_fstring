# -*- coding: utf-8 -*-
"""
Strategy Decay Detector — 策略绩效衰减检测器
=============================================

检测策略在多个维度上的性能衰减趋势，输出衰减等级和详细原因。

维度:
  1. Win Rate Decay     — 近期胜率 vs 远期基准
  2. EV Decay           — 近期信号 EV 均值 vs 远期基准
  3. Score Decay        — 近期信号 Score 均值 vs 远期基准
  4. MAE Decay          — 最大不利偏移 (Max Adverse Excursion) 趋势
  5. Giveback Decay     — 浮盈回吐比例 (Giveback Ratio) 趋势

用法:
  detector = StrategyDecayDetector()
  detector.record_trade(outcome)   # 每笔交易后调用
  decay = detector.analyze()       # 获取当前衰减状态
"""
from __future__ import annotations

import time
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


class StrategyDecayDetector:
    """策略绩效衰减检测器

    使用滑动窗口对比近期 vs 远期的绩效指标，检测系统性衰减。
    """

    def __init__(
        self,
        recent_window: int = 30,
        baseline_window: int = 100,
        min_trades_for_decay: int = 15,
    ):
        """
        Args:
            recent_window: 近期窗口大小（用于检测当前表现）
            baseline_window: 基准窗口大小（用于对比的历史表现）
            min_trades_for_decay: 最少需要多少笔交易才能做出衰减判断
        """
        self.recent_window = recent_window
        self.baseline_window = baseline_window
        self.min_trades_for_decay = min_trades_for_decay

        # 交易记录（定长队列，保留最近 baseline_window 笔）
        self._trades: deque = deque(maxlen=baseline_window)

        # 信号记录（用于 score/EV 趋势分析）
        self._signals: deque = deque(maxlen=baseline_window)

        # 衰减检测结果缓存
        self._last_analysis: Optional[Dict[str, Any]] = None
        self._last_analysis_time: float = 0.0
        self._analysis_interval: float = 60.0  # 最少间隔 60 秒

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------
    def record_trade(self, outcome: Dict[str, Any]) -> None:
        """记录一笔已平仓的交易

        Args:
            outcome: {
                "pnl_r": float,       # 盈亏 R 倍数
                "max_forward_r": float,  # 最大顺向 R
                "max_adverse_r": float,  # 最大逆向 R
                "exit_reason": str,    # "TP1" | "TP2" | "TP3" | "SL"
                "direction": str,      # "Long" | "Short"
                "regime": str,         # 开单时市况
                "score": float,        # 开单时评分
                "ev": float,           # 开单时 EV
                "setup_type": str,     # 开单模式
                "timestamp": float,    # 可选，默认 time.time()
            }
        """
        outcome.setdefault("timestamp", time.time())
        self._trades.append(outcome)

    def record_signal(self, signal: Dict[str, Any]) -> None:
        """记录一次信号（不论是否开单）

        Args:
            signal: {
                "score": float,
                "ev": float,
                "direction": str,
                "regime": str,
                "timestamp": float,  # 可选
            }
        """
        signal.setdefault("timestamp", time.time())
        self._signals.append(signal)

    def analyze(self, force: bool = False) -> Dict[str, Any]:
        """分析当前衰减状态

        Args:
            force: 是否强制重新计算（忽略缓存）

        Returns:
            {
                "decay_level": 0 | 1 | 2 | 3,   # 衰减等级
                "decay_signal": "NORMAL" | "WARNING" | "DEGRADED" | "CRITICAL",
                "dimensions": {
                    "win_rate_decay": {...},
                    "ev_decay": {...},
                    "score_decay": {...},
                    "mae_decay": {...},
                    "giveback_decay": {...},
                },
                "recommendation": str,
                "total_trades": int,
                "timestamp": float,
            }
        """
        now = time.time()
        if not force and self._last_analysis is not None:
            if now - self._last_analysis_time < self._analysis_interval:
                return self._last_analysis

        total_trades = len(self._trades)
        result: Dict[str, Any] = {
            "decay_level": 0,
            "decay_signal": "NORMAL",
            "dimensions": {},
            "recommendation": "",
            "total_trades": total_trades,
            "timestamp": now,
        }

        if total_trades < self.min_trades_for_decay:
            result["recommendation"] = f"样本不足: {total_trades}/{self.min_trades_for_decay}"
            self._last_analysis = result
            self._last_analysis_time = now
            return result

        # 计算各维度分数
        dims = {}

        dims["win_rate_decay"] = self._analyze_win_rate_decay()
        dims["ev_decay"] = self._analyze_ev_decay()
        dims["score_decay"] = self._analyze_score_decay()
        dims["mae_decay"] = self._analyze_mae_decay()
        dims["giveback_decay"] = self._analyze_giveback_decay()

        result["dimensions"] = dims

        # 加权总分
        weights = {
            "win_rate_decay": 0.30,
            "ev_decay": 0.25,
            "score_decay": 0.15,
            "mae_decay": 0.15,
            "giveback_decay": 0.15,
        }

        total_decay_score = 0.0
        for key, w in weights.items():
            d = dims.get(key, {})
            total_decay_score += d.get("decay_score", 0.0) * w

        result["total_decay_score"] = round(total_decay_score, 3)

        # 衰减等级映射
        if total_decay_score >= 2.5:
            result["decay_level"] = 3
            result["decay_signal"] = "CRITICAL"
            result["recommendation"] = "策略严重衰减: 建议暂停交易，审查参数"
        elif total_decay_score >= 1.5:
            result["decay_level"] = 2
            result["decay_signal"] = "DEGRADED"
            result["recommendation"] = "策略明显衰减: 降低仓位，加强筛选"
        elif total_decay_score >= 0.6:
            result["decay_level"] = 1
            result["decay_signal"] = "WARNING"
            result["recommendation"] = "策略轻微衰减: 关注信号质量，可缩减仓位"
        else:
            result["decay_level"] = 0
            result["decay_signal"] = "NORMAL"
            result["recommendation"] = "策略表现正常，无需调整"

        self._last_analysis = result
        self._last_analysis_time = now
        return result

    def get_current_level(self) -> int:
        """获取当前衰减等级"""
        if self._last_analysis is None:
            return 0
        return self._last_analysis.get("decay_level", 0)

    def is_decayed(self) -> bool:
        """是否处于明显衰减状态 (>= DEGRADED)"""
        return self.get_current_level() >= 2

    def is_critical(self) -> bool:
        """是否处于严重衰减状态 (CRITICAL)"""
        return self.get_current_level() >= 3

    def reset(self) -> None:
        """重置所有记录"""
        self._trades.clear()
        self._signals.clear()
        self._last_analysis = None
        self._last_analysis_time = 0.0

    # ------------------------------------------------------------------
    # 内部：各维度衰减分析
    # ------------------------------------------------------------------
    def _get_recent_vs_baseline(
        self, values: List[float]
    ) -> Tuple[float, float, float, float]:
        """计算近期 vs 基准的统计量

        Returns:
            (recent_mean, baseline_mean, recent_std, change_pct)
        """
        if len(values) < self.min_trades_for_decay:
            return 0.0, 0.0, 0.0, 0.0

        arr = np.array(values)
        recent = arr[-self.recent_window:]
        baseline = arr[:self.baseline_window]

        if len(recent) == 0 or len(baseline) == 0:
            return 0.0, 0.0, 0.0, 0.0

        recent_mean = float(np.mean(recent))
        baseline_mean = float(np.mean(baseline))
        recent_std = float(np.std(recent)) if len(recent) > 1 else 0.0
        baseline_std = float(np.std(baseline)) if len(baseline) > 1 else 0.0

        change_pct = 0.0
        if abs(baseline_mean) > 1e-8:
            change_pct = (recent_mean - baseline_mean) / abs(baseline_mean)

        return recent_mean, baseline_mean, recent_std, change_pct

    def _decay_score_from_change(
        self, change_pct: float, is_negative_bad: bool = True
    ) -> float:
        """从变化百分比计算衰减分数 [0, 3]

        Args:
            change_pct: 变化百分比（如 -0.2 = 下降 20%）
            is_negative_bad: 下降是否代表恶化

        Returns:
            0 = 无衰减, 1 = 轻微, 2 = 明显, 3 = 严重
        """
        # 如果下降是好的（例如 MAE 下降），取反
        effective_change = change_pct if is_negative_bad else -change_pct

        if effective_change <= -0.40:
            return 3.0
        elif effective_change <= -0.25:
            return 2.0 + (effective_change + 0.25) / (-0.15) * 1.0
        elif effective_change <= -0.10:
            return 1.0 + (effective_change + 0.10) / (-0.15) * 1.0
        elif effective_change <= -0.03:
            return effective_change / (-0.10) * 1.0
        return 0.0

    def _analyze_win_rate_decay(self) -> Dict[str, Any]:
        """胜率衰减分析"""
        trades = list(self._trades)
        if len(trades) < self.min_trades_for_decay:
            return {"decay_score": 0.0, "detail": "样本不足"}

        recent_trades = trades[-self.recent_window:]
        baseline_trades = trades[:self.baseline_window]

        recent_wins = sum(1 for t in recent_trades if t.get("pnl_r", 0) > 0)
        baseline_wins = sum(1 for t in baseline_trades if t.get("pnl_r", 0) > 0)

        recent_wr = recent_wins / len(recent_trades) if recent_trades else 0
        baseline_wr = baseline_wins / len(baseline_trades) if baseline_trades else 0

        change = recent_wr - baseline_wr
        change_pct = change / baseline_wr if baseline_wr > 0 else 0

        decay_score = self._decay_score_from_change(change_pct, is_negative_bad=True)

        return {
            "decay_score": round(decay_score, 3),
            "recent_win_rate": round(recent_wr, 4),
            "baseline_win_rate": round(baseline_wr, 4),
            "change": round(change, 4),
            "change_pct": round(change_pct, 4),
        }

    def _analyze_ev_decay(self) -> Dict[str, Any]:
        """EV 衰减分析"""
        signals = list(self._signals)
        evs = [s.get("ev", 0) for s in signals if s.get("ev") is not None]

        if len(evs) < self.min_trades_for_decay:
            # 如果信号不够，从 trade 记录的 ev 补充
            trades = list(self._trades)
            evs = [t.get("ev", 0) for t in trades if t.get("ev") is not None]
            if len(evs) < self.min_trades_for_decay:
                return {"decay_score": 0.0, "detail": "EV 数据不足"}

        recent_mean, baseline_mean, _, change_pct = self._get_recent_vs_baseline(evs)
        decay_score = self._decay_score_from_change(change_pct, is_negative_bad=True)

        return {
            "decay_score": round(decay_score, 3),
            "recent_mean_ev": round(recent_mean, 4),
            "baseline_mean_ev": round(baseline_mean, 4),
            "change_pct": round(change_pct, 4),
        }

    def _analyze_score_decay(self) -> Dict[str, Any]:
        """Score 衰减分析"""
        signals = list(self._signals)
        scores = [s.get("score", 0) for s in signals if s.get("score") is not None]

        if len(scores) < self.min_trades_for_decay:
            trades = list(self._trades)
            scores = [t.get("score", 0) for t in trades if t.get("score") is not None]
            if len(scores) < self.min_trades_for_decay:
                return {"decay_score": 0.0, "detail": "Score 数据不足"}

        recent_mean, baseline_mean, _, change_pct = self._get_recent_vs_baseline(scores)
        decay_score = self._decay_score_from_change(change_pct, is_negative_bad=True)

        return {
            "decay_score": round(decay_score, 3),
            "recent_mean_score": round(recent_mean, 2),
            "baseline_mean_score": round(baseline_mean, 2),
            "change_pct": round(change_pct, 4),
        }

    def _analyze_mae_decay(self) -> Dict[str, Any]:
        """最大不利偏移 (MAE) 衰减分析

        MAE 增大 = 交易更难拿住，说明入场时机恶化。
        """
        trades = list(self._trades)
        maes = [t.get("max_adverse_r", 0) for t in trades if t.get("max_adverse_r") is not None]

        if len(maes) < self.min_trades_for_decay:
            return {"decay_score": 0.0, "detail": "MAE 数据不足"}

        recent_mean, baseline_mean, _, change_pct = self._get_recent_vs_baseline(maes)
        # MAE 增大 = 恶化（is_negative_bad=False, 因为增大是坏的）
        decay_score = self._decay_score_from_change(change_pct, is_negative_bad=False)

        return {
            "decay_score": round(decay_score, 3),
            "recent_mean_mae": round(recent_mean, 4),
            "baseline_mean_mae": round(baseline_mean, 4),
            "change_pct": round(change_pct, 4),
        }

    def _analyze_giveback_decay(self) -> Dict[str, Any]:
        """浮盈回吐比例 (Giveback) 衰减分析

        giveback = (max_forward_r - final_pnl_r) / max_forward_r
        回吐比例增大说明策略的风控/止盈能力下降。
        """
        trades = list(self._trades)
        givebacks = []
        for t in trades:
            mf = t.get("max_forward_r", 0)
            pnl = t.get("pnl_r", 0)
            if mf and mf > 0:
                gb = (mf - max(pnl, 0)) / mf  # 只考虑盈利交易的回吐
                givebacks.append(gb)

        if len(givebacks) < max(5, self.min_trades_for_decay // 2):
            return {"decay_score": 0.0, "detail": "Giveback 数据不足"}

        recent_mean, baseline_mean, _, change_pct = self._get_recent_vs_baseline(givebacks)
        # Giveback 增大 = 恶化
        decay_score = self._decay_score_from_change(change_pct, is_negative_bad=False)

        return {
            "decay_score": round(decay_score, 3),
            "recent_mean_giveback": round(recent_mean, 4),
            "baseline_mean_giveback": round(baseline_mean, 4),
            "change_pct": round(change_pct, 4),
        }


# 全局单例
_decay_detector: Optional[StrategyDecayDetector] = None


def get_decay_detector() -> StrategyDecayDetector:
    global _decay_detector
    if _decay_detector is None:
        _decay_detector = StrategyDecayDetector()
    return _decay_detector
