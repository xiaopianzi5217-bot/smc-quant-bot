# utils/probability_calibrator.py
import math
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

    def __init__(self):
        self.bins = defaultdict(lambda: [0, 0])  # bin_key: [wins, total]

    def update(self, score: float, is_win: bool):
        """记录一笔交易的评分与结果。

        Args:
            score: 评分（0~100）
            is_win: 是否盈利（pnl_r > 0）
        """
        bin_key = int(score // 5) * 5  # 每5分一个桶：0, 5, 10, ... 95, 100
        self.bins[bin_key][1] += 1
        if is_win:
            self.bins[bin_key][0] += 1

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
