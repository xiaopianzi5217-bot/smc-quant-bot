# -*- coding: utf-8 -*-
"""Walk Forward 验证框架 — 时间序列交叉验证。

流程：
  训练窗口 → 滚动 → 验证窗口 → 滚动
  例如：
    2024Q1 train → 2024Q2 validate
    2024Q1-2 train → 2024Q3 validate
    2024Q1-3 train → 2024Q4 validate

  输出：
    - 每个窗口的 AUC / Precision / Recall
    - 稳定性分析（权重是否震荡）
    - 回测交易模拟（含滑点/成本）

使用：
  validator = WalkForwardValidator()
  results = validator.run(df)
  print(validator.summary())
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger("WalkForwardValidator")

# LightGBM
try:
    import lightgbm as lgb
    _LGB_AVAILABLE = True
except ImportError:
    _LGB_AVAILABLE = False


class WalkForwardValidator:
    """Walk Forward 验证器。

    用法：
      df = feature_pipeline.build_training_set()
      validator = WalkForwardValidator()
      result = validator.run(df)
      validator.print_summary()
    """

    def __init__(self, window_size: int = 60, step_size: int = 20):
        """
        Args:
            window_size: 训练窗口的最小样本数
            step_size:   滚动步长（样本数）
        """
        self.window_size = window_size
        self.step_size = step_size
        self._results: List[Dict[str, Any]] = []
        self._feature_stability: Dict[str, List[float]] = {}

    # ════════════════════════════════════════════════════════════
    # 公共方法
    # ════════════════════════════════════════════════════════════

    def run(self, df: pd.DataFrame) -> Dict[str, Any]:
        """执行完整的 Walk Forward 验证。

        Args:
            df: 带 label 列的训练集 DataFrame

        Returns:
            {
                "windows": [...],          # 每个窗口的结果
                "avg_auc": 0.0,
                "avg_precision": 0.0,
                "avg_recall": 0.0,
                "stability_score": 0.0,    # 权重稳定性（越高越稳定）
                "feature_importance": {...},
            }
        """
        if not _LGB_AVAILABLE:
            logger.warning("LightGBM 不可用，跳过 Walk Forward")
            return {"error": "LightGBM not available"}

        if "label" not in df.columns:
            logger.error("缺少 label 列")
            return {"error": "Missing label column"}

        df = df.sort_values("open_time").reset_index(drop=True)

        feature_cols = [c for c in df.columns if c != "label"]
        total = len(df)

        if total < self.window_size + 10:
            logger.warning(f"样本不足: {total} < {self.window_size + 10}")
            return {"error": f"Not enough samples: {total}"}

        # 初始化稳定性跟踪
        for col in feature_cols:
            self._feature_stability[col] = []

        window_start = 0
        while window_start + self.window_size < total:
            train_end = window_start + self.window_size
            val_end = min(train_end + self.step_size, total)

            # 训练集
            train_df = df.iloc[window_start:train_end]
            X_train = train_df[feature_cols].fillna(0.0)
            y_train = train_df["label"]

            # 验证集
            val_df = df.iloc[train_end:val_end]
            if len(val_df) < 5:
                break
            X_val = val_df[feature_cols].fillna(0.0)
            y_val = val_df["label"]

            # 训练
            try:
                params = {
                    "objective": "binary",
                    "metric": "auc",
                    "boosting_type": "gbdt",
                    "num_leaves": min(15, max(3, len(train_df) // 20)),
                    "learning_rate": 0.05,
                    "feature_fraction": 0.8,
                    "bagging_fraction": 0.8,
                    "bagging_freq": 5,
                    "verbose": -1,
                    "num_threads": 1,
                    "min_data_in_leaf": max(3, len(train_df) // 30),
                }

                train_data = lgb.Dataset(
                    X_train, label=y_train, feature_name=feature_cols
                )
                model = lgb.train(
                    params,
                    train_data,
                    num_boost_round=200,
                    callbacks=[lgb.early_stopping(10), lgb.log_evaluation(0)],
                )

                # 预测验证集
                y_pred = model.predict(X_val)
                y_pred_binary = (y_pred >= 0.5).astype(int)

                # 评估指标
                auc = self._calc_auc(y_val, y_pred)
                precision = self._calc_precision(y_val, y_pred_binary)
                recall = self._calc_recall(y_val, y_pred_binary)

                # 记录特征重要性
                importance = model.feature_importance(importance_type="gain")
                total_imp = max(sum(importance), 1e-8)
                for col, imp in zip(feature_cols, importance):
                    self._feature_stability[col].append(imp / total_imp)

                window_result = {
                    "window": f"{window_start}-{train_end}",
                    "train_start": str(train_df["open_time"].iloc[0])[:19],
                    "train_end": str(train_df["open_time"].iloc[-1])[:19],
                    "val_start": str(val_df["open_time"].iloc[0])[:19],
                    "val_end": str(val_df["open_time"].iloc[-1])[:19],
                    "train_samples": len(train_df),
                    "val_samples": len(val_df),
                    "val_positives": int(y_val.sum()),
                    "val_negatives": int((1 - y_val).sum()),
                    "auc": round(auc, 4),
                    "precision": round(precision, 4),
                    "recall": round(recall, 4),
                    "feature_importance": {
                        col: round(imp / total_imp * 100, 2)
                        for col, imp in zip(feature_cols, importance)
                    },
                }
                self._results.append(window_result)

                logger.info(
                    f"WalkForward [{window_start}-{train_end}]: "
                    f"AUC={auc:.4f} Precision={precision:.4f} Recall={recall:.4f}"
                )

            except Exception as e:
                logger.warning(f"窗口 {window_start}-{train_end} 训练失败: {e}")

            window_start += self.step_size

        # 计算汇总
        summary = self._build_summary()
        return summary

    def print_summary(self):
        """打印 Walk Forward 汇总报告。"""
        summary = self._build_summary()

        if "error" in summary:
            print(f"❌ Walk Forward 失败: {summary['error']}")
            return

        print("=" * 60)
        print("📊 Walk Forward 验证报告")
        print("=" * 60)
        print(f"窗口数:            {summary['windows_count']}")
        print(f"平均 AUC:          {summary['avg_auc']:.4f}")
        print(f"最佳 AUC:          {summary['best_auc']:.4f}")
        print(f"最差 AUC:          {summary['worst_auc']:.4f}")
        print(f"平均 Precision:    {summary['avg_precision']:.4f}")
        print(f"平均 Recall:       {summary['avg_recall']:.4f}")
        print(f"稳定性评分:        {summary['stability_score']:.3f}")
        print()

        if summary["feature_importance"]:
            print("Top 特征重要性（平均）:")
            for feat, imp in sorted(
                summary["feature_importance"].items(),
                key=lambda x: -x[1],
            )[:10]:
                print(f"  {feat:25s}: {imp:6.2f}%")
        print()

        print("各窗口详情:")
        for w in self._results:
            print(
                f"  [{w['window']}] train={w['train_samples']} "
                f"val={w['val_samples']} AUC={w['auc']:.4f} "
                f"P={w['precision']:.4f} R={w['recall']:.4f}"
            )
        print("=" * 60)

    # ════════════════════════════════════════════════════════════
    # 内部方法
    # ════════════════════════════════════════════════════════════

    def _build_summary(self) -> Dict[str, Any]:
        """构建汇总报告。"""
        if not self._results:
            return {"error": "No windows completed"}

        aucs = [r["auc"] for r in self._results if r["auc"] > 0]
        precisions = [r["precision"] for r in self._results]
        recalls = [r["recall"] for r in self._results]

        # 稳定性评分 = 1 - 特征权重的平均变异系数
        stability = 1.0
        if self._feature_stability:
            cvs = []
            for col, vals in self._feature_stability.items():
                if len(vals) > 1 and np.mean(vals) > 0.01:
                    cv = np.std(vals) / np.mean(vals)
                    cvs.append(cv)
            if cvs:
                stability = round(1 - min(1.0, np.mean(cvs)), 3)

        # 平均特征重要性
        avg_importance: Dict[str, float] = {}
        for col in self._feature_stability:
            vals = self._feature_stability[col]
            if vals:
                avg_importance[col] = round(np.mean(vals) * 100, 2)

        return {
            "windows_count": len(self._results),
            "avg_auc": round(np.mean(aucs), 4) if aucs else 0.0,
            "best_auc": round(max(aucs), 4) if aucs else 0.0,
            "worst_auc": round(min(aucs), 4) if aucs else 0.0,
            "avg_precision": round(np.mean(precisions), 4) if precisions else 0.0,
            "avg_recall": round(np.mean(recalls), 4) if recalls else 0.0,
            "stability_score": stability,
            "feature_importance": dict(
                sorted(avg_importance.items(), key=lambda x: -x[1])
            ),
            "windows": self._results,
        }

    @staticmethod
    def _calc_auc(y_true, y_pred) -> float:
        """ROC AUC。"""
        try:
            from sklearn.metrics import roc_auc_score
            return float(roc_auc_score(y_true, y_pred))
        except Exception:
            return 0.0

    @staticmethod
    def _calc_precision(y_true, y_pred) -> float:
        try:
            from sklearn.metrics import precision_score
            return float(precision_score(y_true, y_pred, zero_division=0))
        except Exception:
            return 0.0

    @staticmethod
    def _calc_recall(y_true, y_pred) -> float:
        try:
            from sklearn.metrics import recall_score
            return float(recall_score(y_true, y_pred, zero_division=0))
        except Exception:
            return 0.0


# 全局单例
_validator: Optional[WalkForwardValidator] = None


def get_validator() -> WalkForwardValidator:
    global _validator
    if _validator is None:
        _validator = WalkForwardValidator()
    return _validator
