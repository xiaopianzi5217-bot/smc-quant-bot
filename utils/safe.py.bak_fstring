# -*- coding: utf-8 -*-
"""
安全类型转换工具函数

消除项目中 26 个文件重复定义 _safe_float / _safe_bool / _safe_str 的问题。
所有需要这些函数的地方统一从此导入。

用法:
    from utils.safe import safe_float, safe_bool, safe_str, safe_num
"""

from __future__ import annotations

import math
from typing import Any


def safe_float(value: Any, default: float = 0.0) -> float:
    """安全转换为 float，NaN/Inf/None 返回 default"""
    try:
        if value is None:
            return default
        out = float(value)
        if math.isnan(out) or math.isinf(out):
            return default
        return out
    except (TypeError, ValueError, OverflowError):
        return default


def safe_bool(value: Any) -> bool:
    """安全转换为 bool，支持字符串 '1','true','yes','long','short' 等"""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "long", "short", "bull", "bear"}
    try:
        return bool(value)
    except Exception:
        return False


def safe_str(value: Any, default: str = "") -> str:
    """安全转换为 str"""
    try:
        if value is None:
            return default
        return str(value)
    except Exception:
        return default


def safe_num(value: Any, default: float = 0.0) -> float:
    """safe_float 的别名，兼容旧代码中的 _num"""
    return safe_float(value, default)


def safe_clip(value: float, low: float, high: float) -> float:
    """裁剪到 [low, high] 区间"""
    return max(low, min(high, value))
