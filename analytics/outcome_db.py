# -*- coding: utf-8 -*-
"""
V38 Outcome Database: Feature Hash → 聚合统计（非逐笔）

V38.5 新增：
  • variance / std / skewness 跟踪（逐笔在线更新，Welford 算法）
  • lower_bound / upper_bound（95% 置信区间）
  • sharp_ratio 指标
  • confidence 使用 t 分布修正小样本
"""

import json
import math
from pathlib import Path
from typing import Dict, Any, Optional


class OutcomeDatabase:
    """V38 Outcome Database: Feature Hash → 聚合统计（非逐笔）

    V38.5 新增方差/置信区间/skew/sharpe 追踪。
    """

    def __init__(self, db_path: str = "storage/outcome_stats.json"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.data: Dict[str, Dict[str, Any]] = self._load()

    def _load(self) -> Dict[str, Dict[str, Any]]:
        if self.db_path.exists():
            try:
                with open(self.db_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save(self):
        with open(self.db_path, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    def lookup(self, feature_hash: str) -> Optional[Dict[str, Any]]:
        return self.data.get(feature_hash)

    def update(self, feature_hash: str, realized_r: float):
        if feature_hash not in self.data:
            self.data[feature_hash] = {
                "trade": 0, "win": 0, "loss": 0, "mean_r": 0.0,
                "sum_r": 0.0, "wins_r": 0.0, "losses_r": 0.0,
                "pf": 0.0, "confidence": 0.0,
                # V38.5 新增字段
                "sum_sq": 0.0,        # Σ(r - μ)² 在线维护
                "sum_cu": 0.0,        # Σ(r - μ)³ 用于偏度
                "old_mean": 0.0,      # Welford 暂存
                "new_mean": 0.0,
                "old_sum_sq": 0.0,
                "new_sum_sq": 0.0,
                "variance": 0.0,
                "std": 0.0,
                "skewness": 0.0,
                "lower_bound": 0.0,
                "upper_bound": 0.0,
                "sharpe_ratio": 0.0,
                "max_r": -999.0,
                "min_r": 999.0,
            }

        entry = self.data[feature_hash]

        # 兼容旧数据（V38.5 前存储的不含新字段）
        for _k, _v in [
            ("sum_sq", 0.0), ("sum_cu", 0.0),
            ("old_mean", 0.0), ("new_mean", 0.0),
            ("old_sum_sq", 0.0), ("new_sum_sq", 0.0),
            ("variance", 0.0), ("std", 0.0), ("skewness", 0.0),
            ("lower_bound", 0.0), ("upper_bound", 0.0),
            ("sharpe_ratio", 0.0),
            ("max_r", -999.0), ("min_r", 999.0),
        ]:
            if _k not in entry:
                entry[_k] = _v

        n = entry["trade"]  # 旧样本数（用于 Welford 算法）
        entry["trade"] += 1
        entry["sum_r"] += realized_r

        if realized_r > 0:
            entry["win"] += 1
            entry["wins_r"] += realized_r
        else:
            entry["loss"] += 1
            entry["losses_r"] += abs(realized_r)

        entry["mean_r"] = entry["sum_r"] / entry["trade"]

        if entry["losses_r"] > 0:
            entry["pf"] = entry["wins_r"] / entry["losses_r"]
        else:
            entry["pf"] = entry["wins_r"] if entry["wins_r"] > 0 else 0.0

        # 更新极值
        entry["max_r"] = max(entry.get("max_r", -999.0), realized_r)
        entry["min_r"] = min(entry.get("min_r", 999.0), realized_r)

        # ---- V38.5: Welford 在线方差/偏度更新 ----
        if n == 0:
            entry["old_mean"] = 0.0
            entry["new_mean"] = realized_r
            entry["old_sum_sq"] = 0.0
            entry["new_sum_sq"] = 0.0
            entry["sum_cu"] = 0.0
        else:
            entry["new_mean"] = entry["old_mean"] + (realized_r - entry["old_mean"]) / (n + 1)
            entry["new_sum_sq"] = entry["old_sum_sq"] + (realized_r - entry["old_mean"]) * (realized_r - entry["new_mean"])

            # 偏度增量（Welford 三阶矩）
            delta = realized_r - entry["old_mean"]
            delta_n = delta / (n + 1)
            term1 = delta * delta_n * n
            entry["sum_cu"] = entry.get("sum_cu", 0.0) + term1 * (n - 1) - 3.0 * delta_n * entry["old_sum_sq"]

            # 保存 Welford 状态
            entry["old_mean"] = entry["new_mean"]
            entry["old_sum_sq"] = entry["new_sum_sq"]

        # 方差 / std（总体方差，ddof=0）
        entry["variance"] = entry["new_sum_sq"] / entry["trade"] if entry["trade"] > 1 else 0.0
        entry["std"] = math.sqrt(entry["variance"]) if entry["variance"] > 0 else 0.0

        # 偏度（正态分布 ≈ 0，正偏 = 右尾长，负偏 = 左尾长）
        # 【P2】小于 100 笔时偏度不稳定，输出 0.0 但外部通过 skewness_valid=False 知晓
        if entry["variance"] > 0 and entry["trade"] > 2:
            s3 = entry["sum_cu"] / (entry["trade"] * entry["variance"] * entry["std"])
            entry["skewness"] = round(s3, 4) if entry["trade"] >= 100 else 0.0
        else:
            entry["skewness"] = 0.0

        # ---- 置信区间（95%，用 t 分布近似） ----
        mean_r = entry["mean_r"]
        std = entry["std"]
        n_ = entry["trade"]
        if std > 0 and n_ > 1:
            # 小样本 t 近似：t_{0.025}(n-1) ≈ 1.96 + 2.0/(n-1)
            t_val = 1.96 + (2.0 / max(1, n_ - 1))
            margin = t_val * std / math.sqrt(n_)
            entry["lower_bound"] = round(mean_r - margin, 4)
            entry["upper_bound"] = round(mean_r + margin, 4)
        else:
            entry["lower_bound"] = round(mean_r, 4)
            entry["upper_bound"] = round(mean_r, 4)

        # ---- Sharpe Ratio（年化近似，以 0 为无风险） ----
        if std > 0:
            entry["sharpe_ratio"] = round(mean_r / std, 4)
        else:
            entry["sharpe_ratio"] = 0.0

        # ---- V38.5: 改进的 Confidence（t 分布 + 样本量 + Sharpe 修正） ----
        win_rate = entry["win"] / n_ if n_ > 0 else 0.0
        # 基础分：样本量置信
        sample_conf = n_ / (n_ + 15.0)
        # 表现分：win_rate 与 Sharpe 混合
        perf_score = win_rate + 0.3 * max(0, min(1.0, entry.get("sharpe_ratio", 0) * 2.0))
        perf_score = min(1.0, perf_score)
        # 合并
        confidence = 0.55 * sample_conf + 0.45 * perf_score
        confidence = max(0.10, min(0.99, confidence))

        # 小样本额外惩罚（< 30 笔再压低一档）
        if n_ < 30:
            confidence *= 0.75 + 0.25 * (n_ / 30.0)
        entry["confidence"] = round(confidence, 4)

        self._save()

    def get_ev(self, feature_hash: str, min_trades: int = 15) -> Optional[Dict[str, Any]]:
        stats = self.lookup(feature_hash)
        if stats and stats["trade"] >= min_trades:
            return {
                "ev": round(stats["mean_r"], 4),
                "confidence": round(stats["confidence"], 4),
                "sample": stats["trade"],
                "pf": round(stats["pf"], 4),
                "win_rate": round(stats["win"] / stats["trade"], 4) if stats["trade"] > 0 else 0.0,
                # V38.5 新增
                "std": round(stats["std"], 4),
                "lower_bound": round(stats.get("lower_bound", 0.0), 4),
                "upper_bound": round(stats.get("upper_bound", 0.0), 4),
                "sharpe_ratio": round(stats.get("sharpe_ratio", 0.0), 4),
                "skewness": round(stats.get("skewness", 0.0), 4) if stats["trade"] >= 100 else None,
                "skewness_valid": stats["trade"] >= 100,
            }
        return None

    def get_top_features(self, top_n: int = 20, min_trades: int = 15) -> list:
        """返回 EV 排序最高的 Feature Hash 列表"""
        scored = []
        for h, s in self.data.items():
            if s["trade"] >= min_trades:
                scored.append({
                    "feature_hash": h,
                    "ev": round(s["mean_r"], 4),
                    "confidence": round(s["confidence"], 4),
                    "sample": s["trade"],
                    "pf": round(s["pf"], 4),
                    "sharpe": round(s.get("sharpe_ratio", 0), 4),
                    "std": round(s.get("std", 0), 4),
                })
        scored.sort(key=lambda x: x["ev"], reverse=True)
        return scored[:top_n]

    def get_worst_features(self, top_n: int = 20, min_trades: int = 15) -> list:
        """返回 EV 排序最差的 Feature Hash 列表"""
        scored = []
        for h, s in self.data.items():
            if s["trade"] >= min_trades:
                scored.append({
                    "feature_hash": h,
                    "ev": round(s["mean_r"], 4),
                    "confidence": round(s["confidence"], 4),
                    "sample": s["trade"],
                    "pf": round(s["pf"], 4),
                    "sharpe": round(s.get("sharpe_ratio", 0), 4),
                })
        scored.sort(key=lambda x: x["ev"])
        return scored[:top_n]
