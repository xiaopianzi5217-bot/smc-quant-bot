
# utils/feedback_loop.py
"""
全链路反馈闭环引擎

Feature → Feature Weight (按Regime分表+时间衰减)
        → Probability Calibration (统计表映射)
        → Confidence
        → Adaptive Reject (按Cluster自适应)
        → Decision (EV决策)
        → Execution
        → Outcome
        → Feature Statistics
          └→ Weight Update (Regime感知)
          └→ Probability Update
          └→ Reject Update
          └→ Regime Performance Update
"""
import json
import os
import time
import math
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Any
from utils.structured_logger import slog


class CalibrationTable:
    """Probability calibration table: maps Score → P(win) using historical stats"""
    def __init__(self, save_path: str = "data/calibration_table.json"):
        self.save_path = Path(save_path)
        self.save_path.parent.mkdir(parents=True, exist_ok=True)
        self.bins: Dict[int, Dict] = defaultdict(lambda: {
            "wins": 0, "total": 0, "avg_win_r": 0.0, "avg_loss_r": 0.0
        })
        self._load()

    def _load(self):
        if self.save_path.exists():
            try:
                raw = json.loads(self.save_path.read_text(encoding="utf-8"))
                for k, v in raw.items():
                    self.bins[int(k)] = v
            except Exception:
                pass

    def save(self):
        try:
            self.save_path.write_text(json.dumps(dict(self.bins), indent=2), encoding="utf-8")
        except Exception as e:
            slog.error(f"[CalibrationTable] save failed: {e}")

    def update(self, score: float, pnl_r: float):
        bin_key = int(score // 5) * 5
        b = self.bins[bin_key]
        b["total"] += 1
        if pnl_r > 0.2:  # 噪音过滤
            b["wins"] += 1
            prev_total = b.get("avg_win_r", 0) * (b["wins"] - 1)
            b["avg_win_r"] = (prev_total + pnl_r) / b["wins"]
        else:
            loss_count = b["total"] - b["wins"]
            prev_total = b.get("avg_loss_r", 0) * max(loss_count - 1, 1)
            b["avg_loss_r"] = (prev_total + abs(pnl_r)) / max(loss_count, 1)
        self.save()

    def predict(self, score: float) -> dict:
        bin_key = int(score // 5) * 5
        b = self.bins.get(bin_key)
        if b and b["total"] >= 30:
            wins, total = b["wins"], b["total"]
            prob = (wins + 8) / (total + 15)
            return {
                "prob": round(prob, 4),
                "avg_win_r": b.get("avg_win_r", 0.5),
                "avg_loss_r": b.get("avg_loss_r", 0.5),
                "samples": total,
                "is_reliable": True,
            }
        prob = 1 / (1 + math.exp(-(score - 58) / 13))
        return {
            "prob": round(prob, 4),
            "avg_win_r": 0.5,
            "avg_loss_r": 0.5,
            "samples": 0,
            "is_reliable": False,
        }


class RegimeFeatureStats:
    """Regime-aware feature statistics with time decay"""
    def __init__(self, save_path: str = "data/regime_feature_stats.json",
                 window: int = 300, time_decay_half_life_days: int = 30):
        self.save_path = Path(save_path)
        self.save_path.parent.mkdir(parents=True, exist_ok=True)
        self.window = window
        self.half_life_seconds = time_decay_half_life_days * 86400
        self.data: Dict[str, Dict[str, Dict]] = defaultdict(
            lambda: defaultdict(lambda: {
                "wins": 0, "losses": 0, "avg_r": 0.0,
                "recent_wins": 0, "recent_total": 0, "recent_avg_r": 0.0,
                "weight": 1.0, "last_update": 0, "total_trades": 0,
            })
        )
        self._load()

    def _load(self):
        if self.save_path.exists():
            try:
                raw = json.loads(self.save_path.read_text(encoding="utf-8"))
                for regime, feats in raw.items():
                    for feat, s in feats.items():
                        self.data[regime][feat] = s
            except Exception:
                pass

    def save(self):
        try:
            self.save_path.write_text(json.dumps(dict(self.data), indent=2), encoding="utf-8")
        except Exception as e:
            slog.error(f"[RegimeFeatureStats] save failed: {e}")

    def _time_decay_weight(self, last_ts: float) -> float:
        elapsed = time.time() - last_ts
        if elapsed <= 0:
            return 1.0
        return math.exp(-elapsed / self.half_life_seconds)

    def update(self, regime: str, features: List[str], pnl_r: float):
        now = time.time()
        for feat in features:
            s = self.data[regime][feat]
            decay = self._time_decay_weight(s["last_update"])
            s["total_trades"] += 1
            if pnl_r > 0.2:  # 噪音过滤
                s["wins"] += 1
                s["recent_wins"] += 1
            else:
                s["losses"] += 1
            s["recent_total"] += 1
            if s["recent_total"] > self.window:
                s["recent_total"] = self.window
                s["recent_wins"] = max(0, int(s["recent_wins"] * 0.95))

            old_r = s.get("avg_r", 0.0)
            if s["total_trades"] == 1:
                s["avg_r"] = pnl_r
            else:
                n = min(s["total_trades"], self.window)
                s["avg_r"] = old_r * decay * (n - 1) / n + pnl_r / n

            if s["recent_total"] <= 1:
                s["recent_avg_r"] = pnl_r
            else:
                n = min(s["recent_total"], self.window)
                s["recent_avg_r"] = s.get("recent_avg_r", 0) * (n - 1) / n + pnl_r / n

            recent_win_rate = s["recent_wins"] / max(s["recent_total"], 1)
            recent_r = s.get("recent_avg_r", 0.0)
            perf_factor = 0.8 + 0.4 * (recent_win_rate - 0.5) * 2
            profit_factor = 1.0 + 0.3 * max(-0.9, min(0.9, recent_r))
            confidence = min(1.0, s["recent_total"] / 30)

            new_weight = 0.7 * s.get("weight", 1.0) + 0.3 * (perf_factor * profit_factor * confidence)
            s["weight"] = round(max(0.5, min(2.0, new_weight)), 4)
            s["last_update"] = int(now)
        self.save()

    def get_weight(self, regime: str, feature: str) -> float:
        return self.data.get(regime, {}).get(feature, {}).get("weight", 1.0)

    def get_weighted_score(self, regime: str, raw_scores: Dict[str, float]) -> float:
        total = 0.0
        for feat, value in raw_scores.items():
            total += value * self.get_weight(regime, feat)
        return total

    def get_feature_summary(self, regime: str) -> dict:
        return dict(self.data.get(regime, {}))


class AdaptiveRejector:
    """Adaptive reject threshold per cluster"""
    def __init__(self, save_path: str = "data/adaptive_reject.json",
                 base_threshold: float = 0.25, window: int = 40,
                 min_samples_for_reject: int = 20):
        self.save_path = Path(save_path)
        self.save_path.parent.mkdir(parents=True, exist_ok=True)
        self.base_threshold = base_threshold
        self.window = window
        self.min_samples_for_reject = min_samples_for_reject
        self.clusters: Dict[str, Dict] = defaultdict(lambda: {
            "wins": 0, "total": 0, "avg_r": 0.0, "reject_threshold": base_threshold,
            "recent_wins": 0, "recent_total": 0,
        })
        self._load()

    def _load(self):
        if self.save_path.exists():
            try:
                raw = json.loads(self.save_path.read_text(encoding="utf-8"))
                for k, v in raw.items():
                    self.clusters[k] = v
            except Exception:
                pass

    def save(self):
        try:
            self.save_path.write_text(json.dumps(dict(self.clusters), indent=2), encoding="utf-8")
        except Exception as e:
            slog.error(f"[AdaptiveRejector] save failed: {e}")

    def _make_cluster_key(self, regime: str, features: List[str], confidence: float) -> str:
        feat_tag = "_".join(sorted(features)) if features else "NONE"
        if confidence > 0.65:
            conf_level = "HIGH"
        elif confidence > 0.50:
            conf_level = "MID"
        else:
            conf_level = "LOW"
        return f"{regime}|{feat_tag}|{conf_level}"

    def update(self, regime: str, features: List[str], confidence: float, pnl_r: float):
        key = self._make_cluster_key(regime, features, confidence)
        c = self.clusters[key]
        c["total"] += 1
        c["recent_total"] += 1
        if pnl_r > 0.2:  # 噪音过滤
            c["wins"] += 1
            c["recent_wins"] += 1
        if c["recent_total"] > self.window:
            c["recent_total"] = self.window
            c["recent_wins"] = max(0, int(c["recent_wins"] * 0.92))

        if c["total"] == 1:
            c["avg_r"] = pnl_r
        else:
            c["avg_r"] = c["avg_r"] * (c["total"] - 1) / c["total"] + pnl_r / c["total"]

        recent_win_rate = c["recent_wins"] / max(c["recent_total"], 1)
        if c["recent_total"] >= 10 and recent_win_rate < 0.40:
            c["reject_threshold"] = min(0.60, c.get("reject_threshold", 0.25) + 0.03)
        elif c["recent_total"] >= 10 and recent_win_rate > 0.60:
            c["reject_threshold"] = max(0.10, c.get("reject_threshold", 0.25) - 0.02)
        else:
            delta = self.base_threshold - c.get("reject_threshold", 0.25)
            c["reject_threshold"] = c.get("reject_threshold", 0.25) + delta * 0.1
        c["reject_threshold"] = max(0.10, min(0.70, c["reject_threshold"]))
        self.save()

    def get_threshold(self, regime: str, features: List[str], confidence: float) -> float:
        key = self._make_cluster_key(regime, features, confidence)
        return self.clusters.get(key, {}).get("reject_threshold", self.base_threshold)

    def should_reject(self, regime: str, features: List[str], confidence: float, ev: float) -> bool:
        # ① 绝对硬底防线（Hard Floor）：置信度极低的异常信号（如垃圾扫描产出）
        #    无论冷启动还是成熟阶段都无条件拒绝，防止垃圾穿透。
        ABSOLUTE_MIN_CONFIDENCE = 0.05
        if confidence < ABSOLUTE_MIN_CONFIDENCE:
            return True

        # ② 负期望值直接拒绝（不可逆交易没有探索价值）
        if ev < 0:
            return True

        key = self._make_cluster_key(regime, features, confidence)
        cluster = self.clusters.get(key, {})
        total_trades = cluster.get("total", 0)

        # ③ 冷启动探索期放行：新组合样本不足时放行让其积累数据，
        #    打破死锁（否则 BEAR/LOB 等新组织永远不会获得 20 笔样本）。
        #    已在 ① 中拦截了 <0.05 的极低置信，所以这里放行的信号
        #    仅在 [0.05, base_threshold) 区间发生，不会穿透硬底。
        if total_trades < self.min_samples_for_reject:
            return False

        # ④ 样本充足时，按该集群自适应拒绝阈值过滤
        threshold = cluster.get("reject_threshold", self.base_threshold)
        return confidence < threshold

    def get_cluster_total(self, regime: str, features: List[str], confidence: float) -> int:
        """获取当前 cluster 累计样本量，供上游决定冷启动仓位缩放。"""
        key = self._make_cluster_key(regime, features, confidence)
        return int(self.clusters.get(key, {}).get("total", 0))

    def get_cold_start_scale(self, regime: str, features: List[str], confidence: float) -> float:
        """冷启动仓位缩放因子：样本小于 min 时按线性 ramp 0.50→1.0。
        达到 min_samples_for_reject 即解除限制。"""
        total = self.get_cluster_total(regime, features, confidence)
        if total >= self.min_samples_for_reject:
            return 1.0
        # 线性 ramp：0 样本 = 0.50, min_samples 时 = 1.0
        progress = total / self.min_samples_for_reject
        return round(0.50 + 0.50 * progress, 4)


class FeedbackLoop:
    """Full-loop feedback engine: integrates CalibrationTable + RegimeFeatureStats + AdaptiveRejector"""
    def __init__(self, data_dir: str = "data", probability_engine=None):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.calibration = CalibrationTable(
            save_path=str(self.data_dir / "calibration_table.json")
        )
        self.feature_stats = RegimeFeatureStats(
            save_path=str(self.data_dir / "regime_feature_stats.json")
        )
        self.rejector = AdaptiveRejector(
            save_path=str(self.data_dir / "adaptive_reject.json")
        )
        # 统一的概率引擎来源
        self.probability_engine = probability_engine

    def on_trade_closed(self, regime: str, features: List[str],
                        score: float, confidence: float,
                        pnl_r: float, direction: str = ""):
        self.calibration.update(score=score, pnl_r=pnl_r)
        self.feature_stats.update(regime=regime, features=features, pnl_r=pnl_r)
        self.rejector.update(regime=regime, features=features,
                             confidence=confidence, pnl_r=pnl_r)
        # 统一概率引擎数据回流
        if self.probability_engine is not None:
            try:
                self.probability_engine.update(score=score, profit_r=pnl_r)
            except Exception as exc:
                slog.error(f"[FeedbackLoop] probability_engine.update exception: {exc}")

    def evaluate_signal(self, regime: str, features: List[str],
                        score: float, raw_feature_scores: Dict[str, float],
                        base_ev: float = 0.0) -> dict:
        weighted_score = self.feature_stats.get_weighted_score(regime, raw_feature_scores)
        if weighted_score <= 0 and raw_feature_scores:
            weighted_score = score
        elif weighted_score <= 0:
            weighted_score = score
        else:
            weighted_score = score * 0.7 + weighted_score * 0.3
        # 防止分数越界：上限 100
        weighted_score = min(weighted_score, 100.0)

        # === 统一概率出口 ===
        # 1) 计算加权分数
        # 2) 从 ProbabilityEngine 获取校准概率（含 regime 微调）
        # 3) 从 CalibrationTable 获取历史盈亏比
        # 4) 计算动态 EV

        if self.probability_engine is not None:
            # 使用统一的 ProbabilityEngine（含 regime 微调）
            confidence = self.probability_engine.predict(
                score=weighted_score, regime=regime, features=features
            )
        else:
            # 兜底：用 CalibrationTable 的 logistic 回退
            calib = self.calibration.predict(weighted_score)
            confidence = calib["prob"]

        # 盈亏比从 CalibrationTable 获取（需要有样本才可靠）
        calib = self.calibration.predict(weighted_score)
        avg_win_r = calib.get("avg_win_r", 0.5) or 0.5
        avg_loss_r = calib.get("avg_loss_r", 0.5) or 0.5
        ev = confidence * avg_win_r - (1 - confidence) * avg_loss_r

        # 样本不足时混合 base_ev
        if not calib["is_reliable"] and base_ev != 0:
            ev = base_ev * 0.6 + ev * 0.4

        should_reject = self.rejector.should_reject(regime, features, confidence, ev)

        # === 冷启动信息：让上游可对探索期仓位做安全缩放 ===
        cluster_total = self.rejector.get_cluster_total(regime, features, confidence)
        cold_start_scale = self.rejector.get_cold_start_scale(regime, features, confidence)

        return {
            "weighted_score": round(weighted_score, 2),
            "calibration": calib,
            "confidence": round(confidence, 4),
            "ev": round(ev, 4),
            "reject_threshold": round(
                self.rejector.get_threshold(regime, features, confidence), 4
            ),
            "should_reject": should_reject,
            "is_reliable": calib["is_reliable"],
            "cluster_total": cluster_total,
            "cold_start_scale": cold_start_scale,
        }

    def get_signal_features(self, reason: str, result: dict, exec_ctx: dict) -> tuple:
        features = []
        raw_feature_scores = {}
        score_val = result.get("score", 0)

        if "OB" in str(reason) or result.get("bullish_ob") or result.get("bearish_ob"):
            features.append("OB")
            raw_feature_scores["OB"] = score_val * 0.15
        if "FVG" in str(reason) or result.get("bullish_fvg") or result.get("bearish_fvg"):
            features.append("FVG")
            raw_feature_scores["FVG"] = score_val * 0.10
        if "CHOCH" in str(reason) or "MSS" in str(reason):
            features.append("CHOCH")
            raw_feature_scores["CHOCH"] = score_val * 0.20
        if "SQZMOM" in str(reason):
            features.append("SQZMOM")
            raw_feature_scores["SQZMOM"] = score_val * 0.15
        if exec_ctx.get("squeeze_release") or "DIVERGENCE" in str(reason):
            features.append("DIVERGENCE")
            raw_feature_scores["DIVERGENCE"] = score_val * 0.12

        return features, raw_feature_scores
