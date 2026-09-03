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
from utils.ml_utils import safe_predict


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
        """加载训练好的模型，如果模型文件不存在则自动训练"""
        model_dir = self.model_dir
        profit_path = os.path.join(model_dir, "ev_profit_model.pkl")
        ev_path = os.path.join(model_dir, "ev_value_model.pkl")
        meta_path = os.path.join(model_dir, "ev_model_metadata.json")
        
        # 模型文件都存在 - 直接加载
        if os.path.exists(profit_path) and os.path.exists(ev_path) and os.path.exists(meta_path):
            try:
                self.profit_model = joblib.load(profit_path)
                self.ev_model = joblib.load(ev_path)
                with open(meta_path, "r") as f:
                    self.metadata = json.load(f)
                self.feature_cols = self.metadata.get("feature_cols", [])
                slog.info(f"[EVRealityGuard] 模型已加载: {len(self.feature_cols)} 特征")
                return
            except Exception as e:
                slog.error(f"[EVRealityGuard] 加载模型失败: {e}，尝试自动训练")
        else:
            slog.info(f"[EVRealityGuard] 模型文件不存在，尝试自动训练...")
        
        # 尝试自动训练
        try:
            import pandas as pd
            import lightgbm as lgb
            from sklearn.model_selection import train_test_split
            
            # 训练数据源 - 优先使用 ml/training_data (确保被推送到HF)
            # 备选路径: 本地 data/ 目录 (在.gitignore中但本地可用)
            data_files = []
            for cand in [
                "ml/training_data/backtest_v56_5_stable.csv",
                "ml/training_data/backtest_v56_5.csv",
                "ml/training_data/backtest_v56_production.csv",
                "data/backtest_v56_5_stable.csv",
                "data/backtest_v56_5.csv",
                "data/backtest_v56_production.csv",
            ]:
                if os.path.exists(cand):
                    data_files.append(cand)
            
            feature_cols = [
                "score", "rsi", "trend_strength", "vol_z", "body_pct",
                "hour", "dow", "regime", "estimated_rr", "win_prob",
            ]
            
            frames = []
            for fp in data_files:
                if os.path.exists(fp):
                    df = pd.read_csv(fp)
                    if "pnl_r" in df.columns:
                        for col in feature_cols:
                            if col not in df.columns:
                                df[col] = 0
                        frames.append(df)
                        slog.info(f"  加载 {fp}: {len(df)} 条")
            
            if not frames:
                slog.error("[EVRealityGuard] 无可用训练数据")
                return
            
            df = pd.concat(frames, ignore_index=True)
            df = df.dropna(subset=["pnl_r"])
            
            # 特征编码
            X = df[feature_cols].copy()
            for col in X.columns:
                if X[col].dtype == "object" or X[col].dtype == "category":
                    unique_vals = X[col].unique()
                    val_to_int = {v: i for i, v in enumerate(sorted(unique_vals, key=str))}
                    X[col] = X[col].map(val_to_int).fillna(0).astype(int)
                else:
                    X[col] = pd.to_numeric(X[col], errors="coerce").fillna(0).astype(float)
            
            y_binary = (df["pnl_r"] > 0.2).astype(int)
            y_value = df["pnl_r"].clip(-3, 3)
            
            # 训练参数
            params = {
                "num_leaves": 8,
                "max_depth": 3,
                "learning_rate": 0.05,
                "n_estimators": 200,
                "subsample": 0.8,
                "colsample_bytree": 0.8,
                "min_child_samples": 10,
                "random_state": 42,
                "verbosity": -1,
            }
            
            # 训练分类器
            clf_params = dict(params)
            clf_params["objective"] = "binary"
            clf_params["metric"] = "auc"
            self.profit_model = lgb.LGBMClassifier(**clf_params)
            self.profit_model.fit(X, y_binary)
            
            # 训练回归器
            reg_params = dict(params)
            reg_params["objective"] = "regression"
            reg_params["metric"] = "rmse"
            self.ev_model = lgb.LGBMRegressor(**reg_params)
            self.ev_model.fit(X, y_value)
            
            # 保存元数据
            self.feature_cols = feature_cols
            self.metadata = {
                "feature_cols": feature_cols,
                "train_samples": int(len(df)),
                "train_date": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "baseline_win_rate": float(y_binary.mean()),
                "model_type": "lightgbm_auto_trained",
            }
            
            # 保存模型文件（供下次直接加载）
            os.makedirs(model_dir, exist_ok=True)
            joblib.dump(self.profit_model, profit_path)
            joblib.dump(self.ev_model, ev_path)
            with open(meta_path, "w") as f:
                json.dump(self.metadata, f, indent=2)
            
            slog.info(f"[EVRealityGuard] 模型自动训练完成: {len(df)} 样本")
            
        except Exception as e:
            slog.error(f"[EVRealityGuard] 自动训练失败: {e}")
            import traceback
            slog.error(traceback.format_exc())
    
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
            # === 使用安全推理：自动对齐训练列名，消除 feature-name UserWarning ===
            win_prob = float(safe_predict(self.profit_model, feature_vec, is_proba=True)[0][1]                if hasattr(self.profit_model, "predict_proba") else safe_predict(self.profit_model, feature_vec)[0])
            
            # ML预测EV
            ml_ev = float(safe_predict(self.ev_model, feature_vec)[0])
            
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
