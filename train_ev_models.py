# -*- coding: utf-8 -*-
"""
训练 EVRealityGuard 模型

用回测数据 (backtest_v56_5_stable.csv + backtest_v56_5.csv + backtest_v56_production.csv)
训练:
  1. LightGBM 分类器 → P(win) 盈利概率
  2. LightGBM 回归器 → EV 预测

输出:
  models/ev_profit_model.pkl       (分类器)
  models/ev_value_model.pkl        (回归器)
  models/ev_model_metadata.json     (元数据)
"""

import json
import os
import sys
import numpy as np
import pandas as pd
from pathlib import Path
from sklearn.model_selection import train_test_split, KFold, StratifiedKFold
from sklearn.metrics import accuracy_score, roc_auc_score, mean_squared_error, mean_absolute_error
import lightgbm as lgb
import joblib
import warnings

warnings.filterwarnings("ignore")

# ── 配置 ──────────────────────────────────────────────
DATA_FILES = [
    "data/backtest_v56_5_stable.csv",
    "data/backtest_v56_5.csv",
    "data/backtest_v56_production.csv",
]
MODEL_DIR = Path("models")
MODEL_DIR.mkdir(parents=True, exist_ok=True)

FEATURE_COLS = [
    "score", "rsi", "trend_strength", "vol_z", "body_pct",
    "hour", "dow", "regime", "estimated_rr", "win_prob",
]
TARGET_COL = "pnl_r"
WIN_THRESHOLD = 0.2  # pnl_r > 0.2 视为盈利

RANDOM_STATE = 42
TEST_SIZE = 0.2
CV_FOLDS = 5


def load_data() -> pd.DataFrame:
    """加载并合并所有回测数据"""
    frames = []
    for fp in DATA_FILES:
        if Path(fp).exists():
            df = pd.read_csv(fp)
            if TARGET_COL in df.columns:
                # 确保所有特征列存在
                for col in FEATURE_COLS:
                    if col not in df.columns:
                        df[col] = 0
                frames.append(df)
                print(f"  ✓ 加载 {fp}: {len(df)} 条")
            else:
                print(f"  ✗ 跳过 {fp}: 缺少 {TARGET_COL} 列")
        else:
            print(f"  ✗ 跳过 {fp}: 文件不存在")

    if not frames:
        raise RuntimeError("没有可用数据文件!")

    return pd.concat(frames, ignore_index=True)


def preprocess(df: pd.DataFrame) -> tuple:
    """预处理数据，保留有效行"""
    # 只保留有明确盈亏结果的样本
    df = df.dropna(subset=["pnl_r"])

    # 输入特征
    X = df[FEATURE_COLS].copy()

    # 处理类别特征：将 object/str 列转换为数值编码
    for col in X.columns:
        if X[col].dtype == "object" or X[col].dtype == "category":
            print(f"  将 '{col}' 转为类别编码 (当前 {X[col].nunique()} 类)")
            unique_vals = X[col].unique()
            val_to_int = {v: i for i, v in enumerate(sorted(unique_vals, key=str))}
            X[col] = X[col].map(val_to_int).fillna(0).astype(int)
        else:
            X[col] = pd.to_numeric(X[col], errors="coerce").fillna(0).astype(float)

    # 目标: 二元盈利 (pnl_r > 0.2) + 连续 EV (pnl_r)
    y_binary = (df[TARGET_COL] > WIN_THRESHOLD).astype(int)
    y_value = df[TARGET_COL].clip(-3, 3)  # 修剪异常值

    print(f"\n样本统计:")
    print(f"  总样本: {len(df)}")
    print(f"  盈利 (>{WIN_THRESHOLD}): {y_binary.sum()} ({y_binary.mean()*100:.1f}%)")
    print(f"  亏损: {len(y_binary) - y_binary.sum()} ({(1-y_binary.mean())*100:.1f}%)")

    return X, y_binary, y_value


def train_classifier(X, y_binary) -> tuple:
    """训练LightGBM分类器预测盈利概率"""
    print("\n" + "=" * 60)
    print("训练 LightGBM 分类器 (P(win))...")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y_binary, test_size=TEST_SIZE, random_state=RANDOM_STATE,
        stratify=y_binary
    )

    # LightGBM 参数（针对小样本优化）
    params = {
        "objective": "binary",
        "metric": "auc",
        "boosting_type": "gbdt",
        "num_leaves": 8,              # 小样本避免过拟合
        "max_depth": 3,
        "learning_rate": 0.05,
        "n_estimators": 200,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "min_child_samples": 10,
        "reg_alpha": 0.1,
        "reg_lambda": 0.3,
        "random_state": RANDOM_STATE,
        "verbosity": -1,
        "is_unbalance": False,
    }

    model = lgb.LGBMClassifier(**params)
    model.fit(
        X_train, y_train,
        eval_set=[(X_test, y_test)],
        eval_metric="auc",
        callbacks=[lgb.early_stopping(30, verbose=False)],
    )

    # 评估
    y_pred = model.predict_proba(X_test)[:, 1]
    y_pred_bin = (y_pred >= 0.5).astype(int)
    acc = accuracy_score(y_test, y_pred_bin)
    auc = roc_auc_score(y_test, y_pred)

    print(f"  分类器评估:")
    print(f"    Accuracy: {acc:.4f}")
    print(f"    AUC:      {auc:.4f}")
    print(f"    最佳迭代: {model.best_iteration_}")

    return model, {"accuracy": float(acc), "auc": float(auc)}


def train_regressor(X, y_value) -> tuple:
    """训练LightGBM回归器预测EV"""
    print("\n" + "=" * 60)
    print("训练 LightGBM 回归器 (EV预测)...")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y_value, test_size=TEST_SIZE, random_state=RANDOM_STATE
    )

    params = {
        "objective": "regression",
        "metric": "rmse",
        "boosting_type": "gbdt",
        "num_leaves": 8,
        "max_depth": 3,
        "learning_rate": 0.05,
        "n_estimators": 200,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "min_child_samples": 10,
        "reg_alpha": 0.1,
        "reg_lambda": 0.3,
        "random_state": RANDOM_STATE,
        "verbosity": -1,
    }

    model = lgb.LGBMRegressor(**params)
    model.fit(
        X_train, y_train,
        eval_set=[(X_test, y_test)],
        eval_metric="rmse",
        callbacks=[lgb.early_stopping(30, verbose=False)],
    )

    # 评估
    y_pred = model.predict(X_test)
    mse = mean_squared_error(y_test, y_pred)
    mae = mean_absolute_error(y_test, y_pred)

    print(f"  回归器评估:")
    print(f"    MSE:  {mse:.4f}")
    print(f"    MAE:  {mae:.4f}")
    print(f"    最佳迭代: {model.best_iteration_}")

    return model, {"mse": float(mse), "mae": float(mae)}


def cross_validate_classifier(X, y_binary, cv_folds=CV_FOLDS) -> tuple:
    """交叉验证分类器获取稳定性评估"""
    print("\n" + "=" * 60)
    print(f"交叉验证分类器 ({cv_folds}折)...")

    kf = StratifiedKFold(n_splits=cv_folds, shuffle=True, random_state=RANDOM_STATE)
    acc_scores, auc_scores = [], []

    params = {
        "objective": "binary",
        "metric": "auc",
        "boosting_type": "gbdt",
        "num_leaves": 8,
        "max_depth": 3,
        "learning_rate": 0.05,
        "n_estimators": 200,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "min_child_samples": 10,
        "reg_alpha": 0.1,
        "reg_lambda": 0.3,
        "random_state": RANDOM_STATE,
        "verbosity": -1,
    }

    for fold, (tr_idx, te_idx) in enumerate(kf.split(X, y_binary)):
        X_tr, X_te = X.iloc[tr_idx], X.iloc[te_idx]
        y_tr, y_te = y_binary.iloc[tr_idx], y_binary.iloc[te_idx]

        model = lgb.LGBMClassifier(**params)
        model.fit(X_tr, y_tr, eval_set=[(X_te, y_te)], eval_metric="auc",
                  callbacks=[lgb.early_stopping(20, verbose=False)])

        y_pred = model.predict_proba(X_te)[:, 1]
        y_pred_bin = (y_pred >= 0.5).astype(int)
        acc_scores.append(accuracy_score(y_te, y_pred_bin))
        auc_scores.append(roc_auc_score(y_te, y_pred))

        print(f"    Fold {fold+1}: acc={acc_scores[-1]:.4f}, auc={auc_scores[-1]:.4f}")

    print(f"  交叉验证汇总:")
    print(f"    Accuracy均值: {np.mean(acc_scores):.4f} ± {np.std(acc_scores):.4f}")
    print(f"    AUC均值:      {np.mean(auc_scores):.4f} ± {np.std(auc_scores):.4f}")

    return float(np.mean(acc_scores)), float(np.mean(auc_scores))


def main():
    print("=" * 60)
    print("EVRealityGuard 模型训练")
    print("=" * 60)

    # 1. 加载数据
    print("\n加载数据...")
    df = load_data()

    # 2. 预处理
    X, y_binary, y_value = preprocess(df)

    # 3. 特征重要性
    print("\n" + "=" * 60)
    print("初步特征分析...")
    for i, col in enumerate(FEATURE_COLS):
        vals = X[col]
        if vals.dtype == "object":
            print(f"  {col}: 类别型 {vals.nunique()} 类")
        else:
            print(f"  {col}: {vals.min():.2f} ~ {vals.max():.2f}")

    # 4. 训练最终模型
    classifier, clf_metrics = train_classifier(X, y_binary)
    regressor, reg_metrics = train_regressor(X, y_value)

    # 5. 交叉验证（分类器）
    cv_acc, cv_auc = cross_validate_classifier(X, y_binary)

    # 6. 保存模型
    print("\n" + "=" * 60)
    print("保存模型...")

    clf_path = MODEL_DIR / "ev_profit_model.pkl"
    reg_path = MODEL_DIR / "ev_value_model.pkl"
    meta_path = MODEL_DIR / "ev_model_metadata.json"

    joblib.dump(classifier, clf_path)
    joblib.dump(regressor, reg_path)

    metadata = {
        "feature_cols": FEATURE_COLS,
        "win_threshold": WIN_THRESHOLD,
        "train_samples": int(len(df)),
        "train_date": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "accuracy": clf_metrics["accuracy"],
        "auc": clf_metrics["auc"],
        "cv_accuracy": cv_acc,
        "cv_auc": cv_auc,
        "mse": reg_metrics["mse"],
        "mae": reg_metrics["mae"],
        "baseline_win_rate": float(y_binary.mean()),
        "avg_pnl_r": float(df["pnl_r"].mean()),
        "model_type": "lightgbm_v2_ensemble",
        "data_sources": DATA_FILES,
    }

    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    # 7. 特征重要性
    print("\n" + "=" * 60)
    print("特征重要性:")
    imp = pd.Series(classifier.feature_importances_, index=FEATURE_COLS).sort_values(ascending=False)
    for feat, val in imp.items():
        print(f"  {feat:20s}: {val:.0f}")

    print("\n" + "=" * 60)
    print("✅ 模型训练完成!")
    print(f"  分类器: {clf_path}")
    print(f"  回归器: {reg_path}")
    print(f"  元数据: {meta_path}")
    print(f"  样本数: {len(df)}")
    print(f"  Accuracy: {clf_metrics['accuracy']:.4f}, AUC: {clf_metrics['auc']:.4f}")
    print(f"  CV Accuracy: {cv_acc:.4f} ± {cv_auc:.4f}")


if __name__ == "__main__":
    main()
