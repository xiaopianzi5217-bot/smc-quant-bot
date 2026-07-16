# -*- coding: utf-8 -*-
"""Probability Engine — LightGBM 训练 + 推理。

功能：
  1. 接收 feature_pipeline 输出的训练集
  2. 训练 LightGBM 分类模型 → P(win)
  3. 输出 Feature Importance → 权重可解释
  4. 实时推理：Features → P(win) → EV

用法：
  engine = get_probability_engine()
  engine.train(df)           # 训练
  prob = engine.predict(df)  # 推理
  fi = engine.feature_importance()
"""
from __future__ import annotations

import json
import logging
import os
import pickle
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger("ProbabilityEngine")

MODEL_DIR = Path("models")
MODEL_PATH = MODEL_DIR / "lgb_prob.pkl"
IMPORTANCE_PATH = MODEL_DIR / "feature_importance.json"
EV_CAL_PATH = MODEL_DIR / "ev_calibration.json"

# LightGBM 条件导入（如果没有安装则自动安装）
try:
    import lightgbm as lgb
    _LGB_AVAILABLE = True
except ImportError:
    _LGB_AVAILABLE = False
    logger.warning("LightGBM 未安装，ProbabilityEngine 使用 Fallback 模式")


class ProbabilityEngine:
    """LightGBM 概率引擎。

    训练模式：接收 DataFrame（含 label 列）→ 训练 LGBM
    推理模式：接收特征 DataFrame → 返回 P(win)
    Fallback 模式：未训练/样本不足时，返回 0.5
    """

    def __init__(self):
        self._model: Any = None
        self._feature_names: List[str] = []
        self._feature_importance: Dict[str, float] = {}
        self._ev_buckets: Dict[str, float] = {}  # probability_bucket -> avg_r
        self._trained: bool = False
        self._train_count: int = 0
        self._last_train_time: float = 0.0

        # 尝试加载已保存的模型
        self._load_model()

    # ════════════════════════════════════════════════════════════
    # 公共方法
    # ════════════════════════════════════════════════════════════

    def train(self, df: pd.DataFrame, force: bool = False) -> bool:
        """训练 LightGBM 模型。

        Args:
            df: 训练集 DataFrame（必须含 label 列）
            force: 是否强制重训

        Returns:
            True 训练成功 / False 样本不足
        """
        if not _LGB_AVAILABLE:
            logger.warning("LightGBM 不可用，跳过训练")
            return False

        if "label" not in df.columns:
            logger.error("训练集缺少 label 列")
            return False

        # 样本量检查
        n_samples = len(df)
        n_pos = int(df["label"].sum())
        n_neg = n_samples - n_pos

        if n_samples < 50:
            logger.info(f"训练样本不足: {n_samples} < 50")
            return False

        if n_pos < 5 or n_neg < 5:
            logger.info(f"正负样本不平衡: pos={n_pos} neg={n_neg}")
            return False

        # 准备特征列
        feature_cols = [c for c in df.columns if c != "label"]
        X = df[feature_cols].copy()
        y = df["label"].copy()

        # 处理缺失值
        X = X.fillna(0.0)

        # 处理无穷值
        X = X.replace([np.inf, -np.inf], 0.0)

        try:
            # LightGBM 参数（针对小样本调优）
            params = {
                "objective": "binary",
                "metric": "auc",
                "boosting_type": "gbdt",
                "num_leaves": min(15, max(3, n_samples // 20)),
                "learning_rate": 0.05,
                "feature_fraction": 0.8,
                "bagging_fraction": 0.8,
                "bagging_freq": 5,
                "verbose": -1,
                "num_threads": 1,
                "min_data_in_leaf": max(3, n_samples // 30),
                "lambda_l1": 0.1,
                "lambda_l2": 0.1,
                "min_gain_to_split": 0.01,
            }

            # 训练
            train_data = lgb.Dataset(X, label=y, feature_name=feature_cols)
            self._model = lgb.train(
                params,
                train_data,
                num_boost_round=200,
                callbacks=[lgb.early_stopping(20), lgb.log_evaluation(0)],
            )

            self._feature_names = feature_cols
            self._trained = True
            self._train_count = n_samples
            self._last_train_time = time.time()

            # 计算 Feature Importance
            self._calc_importance()

            # 计算 EV 校准
            self._calibrate_ev(df)

            # 保存模型
            self._save_model()

            logger.info(f"LightGBM 训练完成: {n_samples} 样本, "
                        f"auc={self._model.best_score['valid_0']['auc']:.4f}"
                        if self._model.best_score else "")

            return True

        except Exception as e:
            logger.error(f"LightGBM 训练失败: {e}")
            return False

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        """推理：返回 P(win) 概率数组。

        Args:
            df: 特征 DataFrame（列名与训练集一致）

        Returns:
            P(win) 数组，范围 [0, 1]
        """
        if not self._trained or self._model is None:
            return np.full(len(df), 0.5)

        try:
            # 确保列顺序与训练一致
            X = df[self._feature_names].copy() if self._feature_names else df.copy()
            X = X.fillna(0.0).replace([np.inf, -np.inf], 0.0)

            prob = self._model.predict(X)
            return prob

        except Exception as e:
            logger.error(f"推理失败: {e}")
            return np.full(len(df), 0.5)

    def predict_single(self, row: Dict[str, float]) -> float:
        """推理单样本。

        Args:
            row: 特征字典

        Returns:
            P(win)，范围 [0, 1]
        """
        df = pd.DataFrame([row])
        return float(self.predict(df)[0])

    def expected_value(self, prob: float, avg_win_r: float = 1.5,
                       avg_loss_r: float = 1.0) -> float:
        """根据概率计算 Expected Value。

        EV = P(win) * avg_win_r - (1 - P(win)) * avg_loss_r

        Args:
            prob: P(win)
            avg_win_r: 平均盈利 R 倍数（从校准数据获取）
            avg_loss_r: 平均亏损 R 倍数（从校准数据获取）

        Returns:
            EV
        """
        # 从校准数据获取更准确的 avg_r
        _win_r, _loss_r = self._get_calibrated_avg_r()

        return prob * _win_r - (1 - prob) * _loss_r

    def feature_importance(self) -> Dict[str, float]:
        """返回特征重要性（归一化到 0-100）。"""
        return self._feature_importance

    def get_decision(self, prob: float, min_prob: float = 0.52) -> Tuple[bool, float]:
        """根据 P(win) 做出交易决策。

        Args:
            prob: P(win)
            min_prob: 最低概率门槛

        Returns:
            (should_trade, confidence)
            confidence = (prob - 0.5) * 2, 范围 [0, 1]
        """
        should_trade = prob >= min_prob
        confidence = min(1.0, max(0.0, (prob - 0.5) * 2))
        return should_trade, confidence

    def is_ready(self) -> bool:
        """模型是否已训练并可用。"""
        return self._trained and self._model is not None

    def get_train_info(self) -> Dict[str, Any]:
        """返回训练统计。"""
        return {
            "trained": self._trained,
            "train_count": self._train_count,
            "last_train_time": self._last_train_time,
            "feature_count": len(self._feature_names),
        }

    # ════════════════════════════════════════════════════════════
    # 内部方法
    # ════════════════════════════════════════════════════════════

    def _calc_importance(self):
        """计算 Feature Importance。"""
        if self._model is None:
            return

        try:
            importance = self._model.feature_importance(
                importance_type="gain"
            )
            total = max(sum(importance), 1e-8)
            normalized = {
                name: round(val / total * 100, 2)
                for name, val in zip(self._feature_names, importance)
            }
            self._feature_importance = dict(
                sorted(normalized.items(), key=lambda x: -x[1])
            )
        except Exception as e:
            logger.warning(f"Feature Importance 计算失败: {e}")

    def _calibrate_ev(self, df: pd.DataFrame):
        """校准概率到 EV 的映射。

        按概率分桶，计算每个桶的平均 R 倍数。
        """
        if self._model is None:
            return

        try:
            # 预测概率
            X = df[self._feature_names].fillna(0.0)
            probs = self._model.predict(X)

            # 分 5 个桶
            bins = [0.0, 0.3, 0.4, 0.5, 0.6, 1.0]
            labels = ["0-30", "30-40", "40-50", "50-60", "60-100"]

            bucket_df = pd.DataFrame({
                "prob": probs,
                "pnl_r": df["pnl_r"].values,
            })
            bucket_df["bucket"] = pd.cut(
                bucket_df["prob"], bins=bins, labels=labels
            )

            self._ev_buckets = {}
            for label in labels:
                subset = bucket_df[bucket_df["bucket"] == label]
                if len(subset) > 0:
                    self._ev_buckets[label] = round(
                        float(subset["pnl_r"].mean()), 4
                    )
                else:
                    self._ev_buckets[label] = 0.0

            logger.info(f"EV 校准完成: {self._ev_buckets}")

        except Exception as e:
            logger.warning(f"EV 校准失败: {e}")

    def _get_calibrated_avg_r(self) -> Tuple[float, float]:
        """从校准数据获取平均盈/亏 R。"""
        if not self._ev_buckets:
            return 1.5, 1.0

        wins = [v for v in self._ev_buckets.values() if v > 0]
        losses = [v for v in self._ev_buckets.values() if v < 0]

        avg_win_r = np.mean(wins) if wins else 1.5
        avg_loss_r = abs(np.mean(losses)) if losses else 1.0

        return float(avg_win_r), float(avg_loss_r)

    def _save_model(self):
        """保存模型到磁盘。"""
        try:
            MODEL_DIR.mkdir(parents=True, exist_ok=True)

            if self._model is not None:
                with open(MODEL_PATH, "wb") as f:
                    pickle.dump(self._model, f)

            with open(IMPORTANCE_PATH, "w", encoding="utf-8") as f:
                json.dump(self._feature_importance, f, ensure_ascii=False, indent=2)

            with open(EV_CAL_PATH, "w", encoding="utf-8") as f:
                json.dump(self._ev_buckets, f, ensure_ascii=False, indent=2)

            logger.info(f"模型已保存: {MODEL_PATH}")
        except Exception as e:
            logger.error(f"模型保存失败: {e}")

    def _load_model(self):
        """从磁盘加载模型。"""
        try:
            if MODEL_PATH.exists() and _LGB_AVAILABLE:
                with open(MODEL_PATH, "rb") as f:
                    self._model = pickle.load(f)
                self._trained = True

                # 加载特征名（从保存的模型获取）
                if self._model is not None:
                    try:
                        self._feature_names = self._model.feature_name()
                    except Exception:
                        self._feature_names = []

                self._train_count = 999  # 未知，标记为已训练

                # 加载重要性
                if IMPORTANCE_PATH.exists():
                    self._feature_importance = json.loads(
                        IMPORTANCE_PATH.read_text(encoding="utf-8")
                    )

                # 加载 EV 校准
                if EV_CAL_PATH.exists():
                    self._ev_buckets = json.loads(
                        EV_CAL_PATH.read_text(encoding="utf-8")
                    )

                logger.info(f"模型已加载: {MODEL_PATH}")
            elif MODEL_PATH.exists() and not _LGB_AVAILABLE:
                logger.warning("LightGBM 未安装，无法加载已保存模型")
        except Exception as e:
            logger.warning(f"模型加载失败: {e}")


# 全局单例
_engine: Optional[ProbabilityEngine] = None


def get_probability_engine() -> ProbabilityEngine:
    global _engine
    if _engine is None:
        _engine = ProbabilityEngine()
    return _engine
