# -*- coding: utf-8 -*-
"""概率引擎（扁平化版）

- 每 5 分一个桶，记录 wins/losses/neutral/total_r
- update 不触发 I/O，外部定时保存

用法：
    engine = ProbabilityEngine()
    engine.update(score=72.5, profit_r=1.2)
    prob = engine.predict(score=68.0)
"""
import json
import math
import os
from collections import defaultdict
from utils.structured_logger import slog


class ProbabilityEngine:
    """"""

    def __init__(self, path: str = "data/probability_table.json"):
        self.path = path
        self.table: dict = defaultdict(lambda: {"wins": 0, "losses": 0, "neutral": 0, "total_r": 0.0})
        self._load()

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def load(self):
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r") as f:
                    raw = json.load(f)
                    for k, v in raw.items():
                        self.table[k] = v
            except Exception as exc:
                slog.error(f"[ProbabilityEngine] 加载失败: {exc}")

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w") as f:
                json.dump(dict(self.table), f, indent=2)
        except Exception as exc:
            slog.error(f"[ProbabilityEngine] 保存失败: {exc}")

    # ------------------------------------------------------------------
    # 核心逻辑
    # ------------------------------------------------------------------
    @staticmethod
    def _bucket(score: float) -> str:
        return str(int(score // 5) * 5)

    def update(self, score: float, profit_r: float):
        """扁平化状态更新，不触发 I/O。"""
        bucket = self._bucket(score)
        data = self.table[bucket]

        if profit_r > 0.2:
            data["wins"] += 1
        elif profit_r < -0.2:
            data["losses"] += 1
        else:
            data["neutral"] += 1

        data["total_r"] = round(data.get("total_r", 0.0) + profit_r, 4)

    def predict(self, score: float) -> float:
        """给定评分，返回校准胜率 P(win)。"""
        bucket = self._bucket(score)
        data = self.table.get(bucket, {})

        wins = data.get("wins", 0)
        losses = data.get("losses", 0)
        total = wins + losses

        if total < 30:
            # Fallback: logistic 先验 + 实际样本 Beta 修正的线性混合
            # 样本数为 0 时 = 100% logistic（保持原有冷启动行为）
            # 样本数 = 30 时 = 100% Beta（与 total>=30 的 Beta 平滑无缝衔接）
            logistic_p = 1 / (1 + math.exp(-(score - 58) / 13))
            beta_p = (wins + 5) / (total + 10)
            mix_ratio = total / 30.0  # 0→0%, 30→100%
            return round(mix_ratio * beta_p + (1 - mix_ratio) * logistic_p, 4)

        # Beta 平滑（先验贝叶斯）
        return round((wins + 5) / (total + 10), 4)

    def get_prob(self, score: float) -> float:
        """兼容旧接口。"""
        return self.predict(score)

    def calculate_ev(self, score: float, reward: float, risk: float = 1.0,
                     regime: str = "unknown") -> dict:
        """计算给定评分和盈亏比的预期价值（EV）。

        Args:
            score: 模型评分（0~100）
            reward: 当前信号的实际预期盈利 R 倍数（动态 RR）
            risk: 当前信号的实际预期亏损 R 倍数（固定为 1.0）

        Returns:
            {"probability": P(win), "ev": expected_value}
        """
        p = self.predict(score)

        # ---- Regime multiplier：趋势放大，泥泞缩小 ----
        regime_mult = {"trend": 1.25, "mud": 0.65, "transition": 1.0, "chop": 0.85}
        mult = regime_mult.get(str(regime).strip().lower(), 1.0)
        ev = (p * reward - (1 - p) * risk) * mult

        # ---- 样本量置信衰减 ----
        bucket = self._bucket(score)
        data = self.table.get(bucket, {})
        n = data.get("wins", 0) + data.get("losses", 0)
        confidence = n / (n + 100)  # 10样本->9%, 500->83%, 5000->98%

        # ════════════════════════════════════════════════════════
        # 【方案四】贝叶斯收缩（Bayesian Shrinkage）
        # 目的: 在小样本时，将极端 EV 收缩回无偏先验(0)，避免噪声污染决策
        #
        # 公式: ev_final = shrinkage * ev_raw + (1 - shrinkage) * prior_ev
        #   shrinkage = n / (n + prior_strength)
        #   prior_strength = 150 (BTC/ETH 高频采样建议值)
        #
        # 逻辑:
        #   n=0   -> shrinkage=0    -> ev_final = prior_ev (=0)
        #   n=50  -> shrinkage=0.25 -> ev_final = 25% 统计 + 75% 先验
        #   n=150 -> shrinkage=0.50 -> ev_final = 50% 统计 + 50% 先验
        #   n=600 -> shrinkage=0.80 -> ev_final = 80% 统计 + 20% 先验
        #   n→∞   -> shrinkage→1    -> ev_final → 完全信任统计值
        # ════════════════════════════════════════════════════════
        prior_strength = 150.0  # 需要 150 笔同桶结算才让统计占据 50% 权重
        prior_ev = 0.0          # 无偏先验：无信息时的合理默认 EV
        shrinkage = n / (n + prior_strength) if n > 0 else 0.0
        ev_shrunk = shrinkage * ev + (1 - shrinkage) * prior_ev

        # 记录收缩前后值（便于日志调试）
        raw_ev = ev
        ev = round(ev_shrunk, 4)

        return {
            "probability": round(p, 4),
            "ev": round(ev, 4),
            # sample_conf 即为 confidence (n/(n+100))
            "confidence": round(confidence, 4),
            # 新增调试字段
            "raw_ev": round(raw_ev, 4),      # 收缩前 EV
            "shrinkage": round(shrinkage, 4), # 收缩系数
            "prior_strength": prior_strength,
        }
