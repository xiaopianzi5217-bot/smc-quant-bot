# utils/probability_calibrator.py
import json
import math
import os
from collections import defaultdict


class ProbabilityCalibrator:
    """概率校准器：将模型评分映射为真实胜率（P(win)）。

    使用分桶 + Beta 平滑：
      - 样本 >= 30 时：Beta 经验贝叶斯平滑 (wins+8)/(total+15)
      - 样本 < 30 时：Logistic fallback 1/(1+exp(-(score-58)/13))

    用法：
        calibrator = ProbabilityCalibrator()
        # 每次交易结束后更新
        calibrator.update(score=72.5, is_win=True)
        # 查询校准概率
        p = calibrator.get_prob(score=68.0)
    """

    def __init__(self, save_path: str = "data/calibrator_bins.json"):
        self.save_path = save_path
        self.bins = defaultdict(lambda: [0, 0])  # bin_key: [wins, total]
        self._load()

    _NOISE_THRESHOLD = 0.2  # 仅 |pnl_r| > 0.2R 才算有效交易

    def _load(self):
        """从磁盘加载分桶数据，防止容器重启后丢失。"""
        if os.path.exists(self.save_path):
            try:
                with open(self.save_path, "r") as f:
                    raw = json.load(f)
                    for k, v in raw.items():
                        self.bins[int(k)] = v
            except Exception as exc:
                print(f"[ProbabilityCalibrator] 加载失败: {exc}")

    def _save(self):
        """持久化到磁盘。"""
        try:
            os.makedirs(os.path.dirname(self.save_path), exist_ok=True)
            with open(self.save_path, "w") as f:
                json.dump(dict(self.bins), f, indent=2)
        except Exception as exc:
            print(f"[ProbabilityCalibrator] 保存失败: {exc}")

    def update(self, score: float, pnl_r: float):
        """记录一笔交易的评分与结果。

        噪音过滤：仅 |pnl_r| > 0.2R 才纳入校准，
        微利/微亏（滑点、平推）不视为有效信号。

        Args:
            score: 评分（0~100）
            pnl_r: 盈亏 R 倍数（带符号）
        """
        if abs(pnl_r) < self._NOISE_THRESHOLD:
            return
        bin_key = int(score // 5) * 5
        self.bins[bin_key][1] += 1
        if pnl_r > 0:
            self.bins[bin_key][0] += 1
        self._save()

    def get_prob(self, score: float) -> float:
        """查询给定评分的校准概率 P(win)。

        Args:
            score: 评分（0~100）

        Returns:
            校准后的获胜概率 [0, 1]
        """
        bin_key = int(score // 5) * 5
        if bin_key in self.bins:
            wins, total = self.bins[bin_key]
            if total >= 30:
                # Beta 平滑：先验 α=8, β=7（假设平均胜率 ~53%）
                return (wins + 8) / (total + 15)
        # Fallback logistic：评分 58 分对应 ~50% 概率
        return 1 / (1 + math.exp(-(score - 58) / 13))
