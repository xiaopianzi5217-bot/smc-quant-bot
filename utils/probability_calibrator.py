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
                print(f"[ProbabilityEngine] 加载失败: {exc}")

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w") as f:
                json.dump(dict(self.table), f, indent=2)
        except Exception as exc:
            print(f"[ProbabilityEngine] 保存失败: {exc}")

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
            # Fallback logistic：58 分对应 ~50%
            return round(1 / (1 + math.exp(-(score - 58) / 13)), 4)

        # Beta 平滑（先验贝叶斯）
        return round((wins + 5) / (total + 10), 4)

    def get_prob(self, score: float) -> float:
        """兼容旧接口。"""
        return self.predict(score)

    def calculate_ev(self, score: float, reward: float, risk: float = 1.0) -> dict:
        """计算给定评分和盈亏比的预期价值（EV）。

        Args:
            score: 模型评分（0~100）
            reward: 当前信号的实际预期盈利 R 倍数（动态 RR）
            risk: 当前信号的实际预期亏损 R 倍数（固定为 1.0）

        Returns:
            {"probability": P(win), "ev": expected_value}
        """
        p = self.predict(score)
        ev = p * reward - (1 - p) * risk
        return {
            "probability": round(p, 4),
            "ev": round(ev, 4),
        }
