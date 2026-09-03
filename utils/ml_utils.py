# -*- coding: utf-8 -*-
"""ML 安全推理辅助工具

消除 LightGBM/Sklearn 在训练(pd.DataFrame带列名)后推理(传入np.array/list)
时产生的 UserWarning：
    "X does not have valid feature names, but LGBMClassifier was fitted with feature names"
"""
import numpy as np
import pandas as pd
from typing import Any, Optional


def _get_feature_names(model: Any) -> Optional[list]:
    """获取模型训练期的特征列名（兼容多种框架属性命名）"""
    for attr in ("feature_name_", "feature_names_in_"):
        fn = getattr(model, attr, None)
        if fn is not None:
            return list(fn)
    return None


def safe_predict(model: Any, X: Any, is_proba: bool = False) -> np.ndarray:
    """安全调用模型推理，自动对齐训练期特征列名。

    场景：训练阶段传入带列名的 pd.DataFrame → 模型记住了 feature_name_；
          推理阶段若传入原始 np.ndarray / list / dict.values → 触发警告。
    本函数检测输入类型，自动补列名包装为 DataFrame，消除警告。

    Args:
        model: 已训练的 sklearn / lightgbm 模型（须有 feature_name_ 或 feature_names_in_）
        X: 输入特征。支持 pd.DataFrame / np.ndarray (1D 或 2D) / list / dict
        is_proba: True 时调用 predict_proba（分类器），否则 predict

    Returns:
        与直接调用 predict* 相同的 ndarray 输出
    """
    feature_names = _get_feature_names(model)

    # 已经是 DataFrame 且列名齐全 → 直接预测
    if isinstance(X, pd.DataFrame):
        if feature_names and not set(feature_names).issubset(set(X.columns)):
            X = X.reindex(columns=feature_names)
        return _do_predict(model, X, is_proba)

    # np.ndarray / list / dict 等 → 包装为带列名 DataFrame
    if feature_names is not None:
        # dict 形式（如 {'score': 50, 'rsi': 33, ...}）
        if isinstance(X, dict):
            arr_features = []
            for col in feature_names:
                arr_features.append(float(X.get(col, 0) or 0))
            X_df = pd.DataFrame([arr_features], columns=feature_names)
            return _do_predict(model, X_df, is_proba)

        # ndarray 或 list
        X_arr = np.asarray(X, dtype=float)
        if X_arr.ndim == 1:
            X_arr = X_arr.reshape(1, -1)
        X_df = pd.DataFrame(X_arr, columns=feature_names)
        return _do_predict(model, X_df, is_proba)

    # 模型没有特征名信息 → 直接用原输入
    return _do_predict(model, X, is_proba)


def _do_predict(model: Any, X: Any, is_proba: bool) -> np.ndarray:
    """内部执行 predict / predict_proba + 异常兜底"""
    try:
        if is_proba and hasattr(model, "predict_proba"):
            return model.predict_proba(X)
        return model.predict(X)
    except Exception:
        # 列名对齐有误时回退到原始 ndarray 推理（带警告但保证可用）
        X_raw = X.values if isinstance(X, pd.DataFrame) else X
        if is_proba and hasattr(model, "predict_proba"):
            return model.predict_proba(X_raw)
        return model.predict(X_raw)