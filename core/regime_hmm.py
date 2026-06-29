"""
RegimeHMM — 隐马尔可夫模型市场状态识别 (V53.2)

使用 HMM 根据 [momentum, volatility] 序列自动识别市场状态:
  - CHOP: 低波动 + 低动量 (盘整)
  - TRANSITION: 中波动 + 中动量 (过渡)
  - TREND: 高波动 + 高动量 (趋势)

核心特性:
  - posterior inference (避免 predict bias)
  - 数据驱动状态排序 (自动映射)
  - 硬门控 + 软置信度门控
  - fallback 机制 (模型未训练时使用规则)
"""
import numpy as np
import joblib
import os
import warnings
from typing import Dict, Optional

from hmmlearn import hmm

warnings.filterwarnings("ignore")


class RegimeHMM:
    """
    隐马尔可夫模型市场状态识别器

    用法:
        hmm = RegimeHMM(model_path="data/hmm_model.pkl")

        # 训练
        hmm.train(X)  # X: (n_samples, n_features)

        # 检测
        result = hmm.detect(X_window)  # X_window: (window_size, n_features)
        # -> {"regime": "TRANSITION", "confidence": 0.85, "allow_trade": True, "is_hmm": True}
    """

    def __init__(self, model_path: str = "data/hmm_model.pkl"):
        """
        初始化 HMM 模型。

        Args:
            model_path: 预训练模型路径
        """
        self.model_path = model_path
        self.n_components = 3  # CHOP, TRANSITION, TREND
        self.model = None
        self._load_model()

    def _load_model(self) -> None:
        """从文件加载预训练模型, 或创建新模型。"""
        if os.path.exists(self.model_path):
            try:
                self.model = joblib.load(self.model_path)
                print(f"[RegimeHMM] [OK] 加载预训练模型: {self.model_path}")
            except Exception as e:
                print(f"[RegimeHMM] [ERR] 模型加载失败: {e}, 创建新模型")
                self.model = hmm.GaussianHMM(
                    n_components=self.n_components,
                    covariance_type="diag",
                    n_iter=200,
                )
        else:
            print(f"[RegimeHMM] [!] 模型文件不存在: {self.model_path}, 创建新模型")
            self.model = hmm.GaussianHMM(
                n_components=self.n_components,
                covariance_type="diag",
                n_iter=200,
            )

    # -------------------------
    # TRAIN
    # -------------------------
    def train(self, X: np.ndarray) -> None:
        """
        训练 HMM 模型。

        Args:
            X: 训练数据, shape (n_samples, n_features)
               建议特征: [momentum, volatility]
        """
        if len(X) < self.n_components * 10:
            print(f"[RegimeHMM] [WARN] 训练数据不足 (n={len(X)}), 建议至少 {self.n_components * 10} 条")

        self.model.fit(X)
        joblib.dump(self.model, self.model_path)
        print(f"[RegimeHMM] [OK] 模型训练完成, 已保存至: {self.model_path}")

    # -------------------------
    # DETECT
    # -------------------------
    def detect(self, X_window: np.ndarray) -> Dict:
        """
        检测当前市场状态。

        Args:
            X_window: 窗口数据, shape (window_size, n_features)
                       建议 window_size >= 30

        Returns:
            dict: {
                "regime": str,         # "CHOP" | "TRANSITION" | "TREND"
                "confidence": float,   # 置信度 0~1
                "allow_trade": bool,   # 是否允许交易
                "is_hmm": bool,        # 是否是 HMM 判定 (False 表示 fallback)
            }
        """
        if not hasattr(self.model, "transmat_"):
            return self._fallback(X_window[-1])

        # posterior inference (避免 predict bias)
        logprob, post = self.model.score_samples(X_window)

        current_state = int(np.argmax(post[-1]))
        confidence = float(np.max(post[-1]))

        # -------------------------
        # STATE INTERPRETATION (DATA-DRIVEN)
        # 根据每个状态的平均 [mom, vol] 排序
        # -------------------------
        state_profile = []

        for i in range(self.n_components):
            mask = (np.argmax(post, axis=1) == i)
            if np.sum(mask) == 0:
                state_profile.append((i, 0.0, 0.0))
                continue

            avg = np.mean(X_window[mask], axis=0)

            # 假设特征顺序: [momentum, volatility]
            vol_score = float(avg[1])
            mom_score = float(avg[0])

            state_profile.append((i, vol_score, mom_score))

        # 排序:
        #   CHOP = low vol + low mom
        #   TREND = high vol + high mom
        state_profile.sort(key=lambda x: (x[1] + x[2]))

        regime_map = {
            state_profile[0][0]: "CHOP",
            state_profile[1][0]: "TRANSITION",
            state_profile[2][0]: "TREND",
        }

        regime_name = regime_map.get(current_state, "NEUTRAL")

        # -------------------------
        # HARD + SOFT GATING
        # -------------------------
        if regime_name == "CHOP":
            allow_trade = False
        else:
            allow_trade = confidence > 0.6

        return {
            "regime": regime_name,
            "confidence": confidence,
            "allow_trade": allow_trade,
            "is_hmm": True,
        }

    # -------------------------
    # FALLBACK
    # -------------------------
    def _fallback(self, f: np.ndarray) -> Dict:
        """
        HMM 模型未训练时使用规则作为 fallback。

        Args:
            f: shape (n_features,) = [momentum, volatility]
        """
        # 从 ndarray 提取特征: [momentum, volatility]
        mom = float(f[0]) if len(f) > 0 else 0.0
        vol = float(f[1]) if len(f) > 1 else 0.5

        # 用 momentum 作为趋势强度的近似
        score = mom - vol * 0.5

        if score > 0.6:
            return {
                "regime": "TRANSITION",
                "confidence": 0.8,
                "allow_trade": True,
                "is_hmm": False,
            }

        return {
            "regime": "CHOP",
            "confidence": 0.9,
            "allow_trade": False,
            "is_hmm": False,
        }