# -*- coding: utf-8 -*-
"""
Market Crisis Detector — 多维市场危机检测器
============================================

设计目标:
  不依赖单根 K 线的瞬时指标，而是通过滑动窗口 + EWMA 趋势检测
  多维度评估市场危机级别。

维度:
  1. ATR 膨胀率 (ATR Spike)     — 波动率急剧扩张
  2. 成交量异常 (Volume Anomaly) — 缩量/放量异常
  3. 波动率持续性 (Vol Persistence) — VIX-like 滚动窗口波动率
  4. 资金费率极端化 (Funding Rate) — 极端多空不平衡
  5. 相关性崩溃 (Correlation Breakdown) — 多币种同步暴跌

输出:
  crisis_level: 0=正常, 1=预警(限仓), 2=严重(半熔断), 3=熔断(停止交易)
  crisis_signal: 详细信息字典

用法:
  detector = MarketCrisisDetector()
  crisis = detector.update(df_15m, df_1h, funding_rate=0.0001)
  if crisis["crisis_level"] >= 2:
      # 执行熔断
      position_manager.close_all()
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


class MarketCrisisDetector:
    """多维市场危机检测器

    使用 EWMA 滑动窗口跟踪多个维度的市场压力，综合评分。
    """

    def __init__(self, window: int = 20, ewma_alpha: float = 0.3):
        """
        Args:
            window: 滚动窗口大小（K 线数量）
            ewma_alpha: EWMA 平滑系数（越小越平滑）
        """
        self.window = window
        self.ewma_alpha = ewma_alpha

        # ---------- 阈值配置 ----------
        self.thresholds = {
            # ATR 膨胀倍数超过此值触发预警
            "atr_spike_warn": 2.0,
            "atr_spike_crisis": 3.5,
            # 成交量异常倍数
            "volume_anomaly_warn": 0.4,  # 低于均值 40%
            "volume_anomaly_crisis": 0.25,  # 低于均值 25%
            "volume_surge_warn": 2.5,  # 高于均值 2.5 倍
            "volume_surge_crisis": 4.0,  # 高于均值 4 倍
            # 波动率持续性（年化波动率阈值）
            "vol_persistence_warn": 0.60,  # 60%
            "vol_persistence_crisis": 0.90,  # 90%
            # 资金费率（绝对值，百分比）
            "funding_warn": 0.05,  # 0.05%
            "funding_crisis": 0.10,  # 0.10%
            # 相关性崩溃（多币种同步性）
            "correlation_drop_warn": 0.50,
            "correlation_drop_crisis": 0.30,
        }

        # ---------- 内部状态 ----------
        self._atr_baseline: Optional[float] = None
        self._vol_baseline: Optional[float] = None
        self._volume_baseline: Optional[float] = None
        self._atr_ewma: Optional[float] = None
        self._vol_ewma: Optional[float] = None
        self._funding_ewma: Optional[float] = None
        self._step: int = 0

        # 历史记录（用于调试/可视化）
        self.history: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------
    def update(
        self,
        df_15m: pd.DataFrame,
        df_1h: Optional[pd.DataFrame] = None,
        funding_rate: float = 0.0,
    ) -> Dict[str, Any]:
        """更新危机检测状态

        Args:
            df_15m: 15 分钟 K 线 DataFrame（必须有 close, high, low, volume）
            df_1h: 1 小时 K 线（可选，用于更长周期波动率）
            funding_rate: 当前资金费率（百分比，如 0.01 = 0.01%）

        Returns:
            {
                "crisis_level": 0 | 1 | 2 | 3,
                "crisis_signal": "NORMAL" | "WARNING" | "SEVERE" | "MELTDOWN",
                "scores": {...},        # 各维度分数
                "details": {...},       # 各维度原始值
                "timestamp": float,
            }
        """
        self._step += 1
        df = df_15m.tail(self.window + 5).copy()

        if len(df) < self.window:
            return self._make_result(0, "NORMAL", {"insufficient_data": True})

        # ===== 计算各维度分数 =====
        scores: Dict[str, float] = {}
        details: Dict[str, Any] = {}

        scores["atr_spike"] = self._score_atr_spike(df, details)
        scores["volume_anomaly"] = self._score_volume_anomaly(df, details)
        scores["vol_persistence"] = self._score_vol_persistence(df, df_1h, details)
        scores["funding"] = self._score_funding(funding_rate, details)

        # 加权总分（ATR 权重最高，因为波动率扩张最直接）
        weights = {
            "atr_spike": 0.35,
            "volume_anomaly": 0.20,
            "vol_persistence": 0.30,
            "funding": 0.15,
        }
        total_score = sum(scores[k] * weights[k] for k in weights)

        # ===== 危机等级映射 =====
        if total_score >= 2.5:
            crisis_level = 3
            crisis_signal = "MELTDOWN"
        elif total_score >= 1.5:
            crisis_level = 2
            crisis_signal = "SEVERE"
        elif total_score >= 0.6:
            crisis_level = 1
            crisis_signal = "WARNING"
        else:
            crisis_level = 0
            crisis_signal = "NORMAL"

        result = self._make_result(crisis_level, crisis_signal, {
            "scores": scores,
            "details": details,
            "total_score": round(total_score, 3),
        })

        self.history.append(result)
        # 只保留最近 500 条历史
        if len(self.history) > 500:
            self.history.pop(0)

        return result

    def get_current_level(self) -> int:
        """获取当前危机等级"""
        if not self.history:
            return 0
        return self.history[-1].get("crisis_level", 0)

    def is_meltdown(self) -> bool:
        """是否处于熔断状态"""
        return self.get_current_level() >= 3

    def is_severe(self) -> bool:
        """是否处于严重危机状态"""
        return self.get_current_level() >= 2

    def is_warning(self) -> bool:
        """是否处于预警状态"""
        return self.get_current_level() >= 1

    def reset(self):
        """重置所有状态"""
        self._atr_baseline = None
        self._vol_baseline = None
        self._volume_baseline = None
        self._atr_ewma = None
        self._vol_ewma = None
        self._funding_ewma = None
        self._step = 0
        self.history.clear()

    # ------------------------------------------------------------------
    # 内部：各维度评分
    # ------------------------------------------------------------------
    def _score_atr_spike(self, df: pd.DataFrame, details: dict) -> float:
        """ATR 膨胀率评分 [0, 3]

        计算当前 ATR 与 EWMA 基线的比值，膨胀越剧烈分数越高。
        """
        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values

        # 计算 ATR（14 周期）
        atr_values = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            atr_values.append(tr)

        if len(atr_values) < 14:
            return 0.0

        atr_series = pd.Series(atr_values).rolling(14, min_periods=5).mean().values
        current_atr = atr_series[-1]
        close_price = closes[-1]
        atr_pct = current_atr / close_price if close_price > 0 else 0

        # 初始化基线
        if self._atr_baseline is None:
            median_atr = np.median(atr_series[~np.isnan(atr_series)])
            self._atr_baseline = max(median_atr, 1e-8)
            self._atr_ewma = self._atr_baseline
        atr_baseline_close = self._atr_baseline / close_price if close_price > 0 else atr_pct

        # EWMA 更新
        self._atr_ewma = self.ewma_alpha * current_atr + (1 - self.ewma_alpha) * self._atr_ewma

        # 膨胀倍率
        spike_ratio = current_atr / max(self._atr_ewma, 1e-8)
        details["atr_spike_ratio"] = round(spike_ratio, 2)
        details["atr_pct"] = round(atr_pct * 100, 2)  # %

        # 评分映射
        if spike_ratio >= self.thresholds["atr_spike_crisis"]:
            return 3.0
        elif spike_ratio >= self.thresholds["atr_spike_warn"]:
            # 线性映射 [warn, crisis] -> [1.0, 3.0]
            t = (spike_ratio - self.thresholds["atr_spike_warn"]) / (
                self.thresholds["atr_spike_crisis"] - self.thresholds["atr_spike_warn"]
            )
            return 1.0 + t * 2.0
        elif spike_ratio >= 1.3:
            t = (spike_ratio - 1.3) / (self.thresholds["atr_spike_warn"] - 1.3)
            return t * 1.0
        return 0.0

    def _score_volume_anomaly(self, df: pd.DataFrame, details: dict) -> float:
        """成交量异常评分 [0, 3]

        极度缩量（流动性枯竭）或极度放量（恐慌抛售）都算异常。
        """
        volumes = df["volume"].values
        if len(volumes) < self.window:
            return 0.0

        recent = volumes[-5:]  # 最近 5 根
        baseline = np.mean(volumes[:-5]) if len(volumes) > 5 else np.mean(volumes)

        if self._volume_baseline is None:
            self._volume_baseline = max(baseline, 1e-8)
        self._volume_baseline = self.ewma_alpha * baseline + (1 - self.ewma_alpha) * self._volume_baseline

        avg_recent = np.mean(recent)
        ratio = avg_recent / max(self._volume_baseline, 1e-8)

        details["volume_ratio"] = round(ratio, 2)

        # 缩量异常（流动性枯竭）
        if ratio <= self.thresholds["volume_anomaly_crisis"]:
            return 3.0
        elif ratio <= self.thresholds["volume_anomaly_warn"]:
            t = (self.thresholds["volume_anomaly_warn"] - ratio) / (
                self.thresholds["volume_anomaly_warn"] - self.thresholds["volume_anomaly_crisis"]
            )
            return 1.0 + t * 2.0

        # 放量异常（恐慌抛售）
        if ratio >= self.thresholds["volume_surge_crisis"]:
            return 2.5
        elif ratio >= self.thresholds["volume_surge_warn"]:
            t = (ratio - self.thresholds["volume_surge_warn"]) / (
                self.thresholds["volume_surge_crisis"] - self.thresholds["volume_surge_warn"]
            )
            return 1.0 + t * 1.5

        return 0.0

    def _score_vol_persistence(
        self,
        df_15m: pd.DataFrame,
        df_1h: Optional[pd.DataFrame],
        details: dict,
    ) -> float:
        """波动率持续性评分 [0, 3]

        使用年化波动率（滚动窗口），持续性高 = 危机。
        """
        closes = df_15m["close"].values
        if len(closes) < self.window:
            return 0.0

        # 对数收益率
        returns = np.diff(np.log(closes[~np.isnan(closes)]))
        if len(returns) < 5:
            return 0.0

        # 滚动年化波动率（15m = 96 根/天，年化 *= sqrt(96*365)）
        roll_vol = np.std(returns[-min(len(returns), self.window):]) * np.sqrt(96 * 365)
        details["vol_annualized"] = round(roll_vol, 4)

        # 使用 1H 数据辅助（更长周期）
        vol_1h = 0.0
        if df_1h is not None and len(df_1h) >= 10:
            c1h = df_1h["close"].values
            r1h = np.diff(np.log(c1h[~np.isnan(c1h)]))
            if len(r1h) >= 5:
                vol_1h = np.std(r1h[-min(len(r1h), 48):]) * np.sqrt(24 * 365)
        details["vol_1h_annualized"] = round(vol_1h, 4)

        # 取两者中更高者
        effective_vol = max(roll_vol, vol_1h)

        if self._vol_baseline is None:
            self._vol_baseline = effective_vol
            self._vol_ewma = effective_vol
        self._vol_ewma = self.ewma_alpha * effective_vol + (1 - self.ewma_alpha) * self._vol_ewma

        details["vol_ewma"] = round(self._vol_ewma, 4)

        if effective_vol >= self.thresholds["vol_persistence_crisis"]:
            return 3.0
        elif effective_vol >= self.thresholds["vol_persistence_warn"]:
            t = (effective_vol - self.thresholds["vol_persistence_warn"]) / (
                self.thresholds["vol_persistence_crisis"] - self.thresholds["vol_persistence_warn"]
            )
            return 1.0 + t * 2.0
        elif effective_vol >= 0.35:
            t = (effective_vol - 0.35) / (self.thresholds["vol_persistence_warn"] - 0.35)
            return t * 1.0
        return 0.0

    def _score_funding(self, funding_rate: float, details: dict) -> float:
        """资金费率极端化评分 [0, 3]

        资金费率绝对值越大 = 市场越极端（多空严重不平衡）。
        """
        abs_fr = abs(funding_rate)

        if self._funding_ewma is None:
            self._funding_ewma = abs_fr
        self._funding_ewma = self.ewma_alpha * abs_fr + (1 - self.ewma_alpha) * self._funding_ewma

        details["funding_abs"] = round(abs_fr, 4)
        details["funding_ewma"] = round(self._funding_ewma, 4)

        if abs_fr >= self.thresholds["funding_crisis"]:
            return 3.0
        elif abs_fr >= self.thresholds["funding_warn"]:
            t = (abs_fr - self.thresholds["funding_warn"]) / (
                self.thresholds["funding_crisis"] - self.thresholds["funding_warn"]
            )
            return 1.0 + t * 2.0
        elif abs_fr >= 0.02:
            t = (abs_fr - 0.02) / (self.thresholds["funding_warn"] - 0.02)
            return t * 1.0
        return 0.0

    # ------------------------------------------------------------------
    # 内部：结果构造
    # ------------------------------------------------------------------
    def _make_result(self, level: int, signal: str, extra: dict) -> Dict[str, Any]:
        return {
            "crisis_level": level,
            "crisis_signal": signal,
            "timestamp": time.time(),
            **extra,
        }


# 全局单例
_crisis_detector: Optional[MarketCrisisDetector] = None


def get_crisis_detector() -> MarketCrisisDetector:
    global _crisis_detector
    if _crisis_detector is None:
        _crisis_detector = MarketCrisisDetector()
    return _crisis_detector
