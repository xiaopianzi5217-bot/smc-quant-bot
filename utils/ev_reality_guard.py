# -*- coding: utf-8 -*-
"""
EV现实校验器 (EV Reality Guard v2)

使用ML模型对交易信号的EV进行二次校准：
1. 用 RandomForest 分类器预测盈利概率
2. 用 GradientBoosting 回归器预测预期EV值
3. 将ML预测与原有信号EV融合，给出最终EV

核心逻辑：
- 如果ML预测盈利概率 > 阈值 (0.55) 且 预测EV > 0.3 → 视为高质量信号
- 如果ML预测盈利概率 < 0.45 → 需要额外谨慎

数据来源：
- 训练数据: backtest_v56_5.csv / stable / production (1046条)
- 训练结果: 准确率80.6%, AUC 0.92
"""

import json
import math
import os
import numpy as np
import joblib
from utils.structured_logger import slog


class EVRealityGuard:
    """EV现实校验器 - 基于ML模型过滤信号"""
    
    def __init__(self, model_dir: str = "models"):
        """
        Args:
            model_dir: 模型目录
        """
        self.model_dir = model_dir
        self.profit_model = None
        self.ev_model = None
        self.feature_cols = None
        self.metadata = None
        self._load_models()
        
        # 配置参数（来自训练结果）
        self.profit_threshold = 0.55      # 盈利概率阈值（在交叉验证中最佳）
        self.ev_quality_threshold = 0.30  # EV质量阈值
        self.signal_gate = True           # 是否启用信号门控
    
    def _load_models(self):
        """加载训练好的模型"""
        model_dir = self.model_dir
        profit_path = os.path.join(model_dir, "ev_profit_model.pkl")
        ev_path = os.path.join(model_dir, "ev_value_model.pkl")
        meta_path = os.path.join(model_dir, "ev_model_metadata.json")
        
        try:
            if os.path.exists(profit_path):
                self.profit_model = joblib.load(profit_path)
                slog.info(f"[EVRealityGuard] 盈利分类器已加载")
            else:
                slog.warning(f"[EVRealityGuard] 盈利模型不存在: {profit_path}")
            
            if os.path.exists(ev_path):
                self.ev_model = joblib.load(ev_path)
                slog.info(f"[EVRealityGuard] EV回归器已加载")
            else:
                slog.warning(f"[EVRealityGuard] EV模型不存在: {ev_path}")
            
            if os.path.exists(meta_path):
                with open(meta_path, "r") as f:
                    self.metadata = json.load(f)
                self.feature_cols = self.metadata.get("feature_cols", [])
                slog.info(f"[EVRealityGuard] 元数据已加载, 特征列数: {len(self.feature_cols)}")
        except Exception as e:
            slog.error(f"[EVRealityGuard] 加载模型失败: {e}")
    
    def _build_feature_vector(self, ctx: dict) -> np.ndarray:
        """从上下文构建特征向量
        
        Args:
            ctx: 信号上下文，包含 rsi, trend_strength, vol_z, etc.
        
        Returns:
            np.ndarray: 特征向量
        """
        if not self.feature_cols:
            return None
        
        features = []
        for col in self.feature_cols:
            # 从上下文中提取值
            val = ctx.get(col, 0)
            if val is None:
                val = 0
            features.append(float(val))
        
        return np.array(features).reshape(1, -1)
    
    def evaluate(self, signal: dict, ctx: dict) -> dict:
        """评估信号质量
        
        Args:
            signal: {"expected_value": float, "score": float, "direction": str}
            ctx: 上下文特征 {"rsi": float, "trend_strength": float, ...}
        
        Returns:
            {
                "ml_win_prob": float,       # ML预测的盈利概率
                "ml_predicted_ev": float,    # ML预测的EV
                "adjusted_ev": float,        # 调整后的EV（融合ML结果）
                "signal_quality": str,       # high / medium / low
                "should_enter": bool,        # 是否建议入场
                "reason": str,               # 原因说明
            }
        """
        # 如果模型未加载，返回原值
        if self.profit_model is None or self.ev_model is None:
            return {
                "ml_win_prob": None,
                "ml_predicted_ev": None,
                "adjusted_ev": signal.get("expected_value", 0),
                "signal_quality": "unknown",
                "should_enter": True,
                "reason": "models_not_loaded",
            }
        
        # 构建特征向量
        feature_vec = self._build_feature_vector(ctx)
        if feature_vec is None:
            return {
                "ml_win_prob": None,
                "ml_predicted_ev": None,
                "adjusted_ev": signal.get("expected_value", 0),
                "signal_quality": "unknown",
                "should_enter": True,
                "reason": "no_features",
            }
        
        try:
            # ML预测盈利概率
            if hasattr(self.profit_model, "predict_proba"):
                win_prob = float(self.profit_model.predict_proba(feature_vec)[0][1])
            else:
                win_prob = float(self.profit_model.predict(feature_vec)[0])
            
            # ML预测EV
            ml_ev = float(self.ev_model.predict(feature_vec)[0])
            
            # 原始信号的EV和概率
            orig_ev = float(signal.get("expected_value", 0))
            orig_prob = float(signal.get("probability", 0.5))
            
            # 融合: 80% ML权重 + 20% 原始权重
            fused_prob = 0.8 * win_prob + 0.2 * orig_prob
            adjusted_ev = 0.8 * ml_ev + 0.2 * orig_ev
            
            # 判断信号质量
            if win_prob >= self.profit_threshold and ml_ev >= self.ev_quality_threshold:
                quality = "high"
                should_enter = True
                reason = f"ML确认: P(win)={win_prob:.3f}≥{self.profit_threshold:.2f}, EV={ml_ev:.3f}≥{self.ev_quality_threshold:.2f}"
            elif win_prob >= 0.45 and ml_ev >= 0:
                quality = "medium"
                should_enter = True
                reason = f"ML中性: P(win)={win_prob:.3f}, EV={ml_ev:.3f}"
            else:
                quality = "low"
                should_enter = False
                reason = f"ML拒绝: P(win)={win_prob:.3f}<0.45, EV={ml_ev:.3f}<0"
            
            return {
                "ml_win_prob": round(win_prob, 4),
                "ml_predicted_ev": round(ml_ev, 4),
                "fused_win_prob": round(fused_prob, 4),
                "adjusted_ev": round(adjusted_ev, 4),
                "signal_quality": quality,
                "should_enter": should_enter,
                "reason": reason,
            }
            
        except Exception as e:
            slog.error(f"[EVRealityGuard] 评估失败: {e}")
            return {
                "ml_win_prob": None,
                "ml_predicted_ev": None,
                "adjusted_ev": signal.get("expected_value", 0),
                "signal_quality": "error",
                "should_enter": True,
                "reason": f"evaluation_error: {e}",
            }
    
    def should_reject(self, signal: dict, ctx: dict) -> dict:
        """判断是否拒绝信号（与现有风控接口兼容）
        
        Args:
            signal: {"expected_value": float, "score": float, "direction": str}
            ctx: 上下文特征
        
        Returns:
            {"reject": bool, "reason": str, "details": dict}
        """
        result = self.evaluate(signal, ctx)
        
        return {
            "reject": not result.get("should_enter", True),
            "reason": result.get("reason", ""),
            "details": result,
        }
    
    def get_stats(self) -> dict:
        """获取模型统计信息"""
        if self.metadata:
            return {
                "trained_samples": self.metadata.get("trained_samples", 0),
                "baseline_win_rate": self.metadata.get("baseline_win_rate", 0),
                "avg_pnl_r": self.metadata.get("avg_pnl_r", 0),
                "avg_ev": self.metadata.get("avg_ev", 0),
                "profit_threshold": self.profit_threshold,
                "ev_quality_threshold": self.ev_quality_threshold,
            }
        return {}
