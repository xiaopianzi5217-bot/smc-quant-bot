# -*- coding: utf-8 -*-
"""
P0-2: 防御性数据提取工具

职责：
  消除所有链式 .get() 空指针风险
  提供统一的防御性提取函数，无论中间节点是 None / dict / 其他类型均安全返回默认值

用法：
  from utils.safe_extract import safe_get, safe_get_str, safe_get_float, safe_get_int, safe_get_bool

  result = safe_get(data, "decision", "signal", "setup_type", default="")
  score  = safe_get_float(data, "decision", "score", default=0.0)
"""

from typing import Any, Optional, TypeVar, Union

T = TypeVar("T")


def safe_get(data: Any, *keys: str, default: Any = None) -> Any:
    """
    对 data 依次提取 keys，遇到 None 或非 dict 时立即返回 default。

    替代链式: data.get("a", {}).get("b", {}).get("c", default)
    写法:     safe_get(data, "a", "b", "c", default=default)
    """
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key, default)
        if current is None:
            return default
    return current


def safe_get_str(data: Any, *keys: str, default: str = "") -> str:
    """防御性提取字符串字段。"""
    v = safe_get(data, *keys, default=default)
    if v is None:
        return default
    return str(v)


def safe_get_float(data: Any, *keys: str, default: float = 0.0) -> float:
    """防御性提取浮点数字段。"""
    v = safe_get(data, *keys, default=default)
    if v is None:
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def safe_get_int(data: Any, *keys: str, default: int = 0) -> int:
    """防御性提取整数字段。"""
    v = safe_get(data, *keys, default=default)
    if v is None:
        return default
    try:
        return int(v)
    except (ValueError, TypeError):
        return default


def safe_get_bool(data: Any, *keys: str, default: bool = False) -> bool:
    """防御性提取布尔值字段。"""
    v = safe_get(data, *keys, default=default)
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.lower() in ("true", "1", "yes", "y")
    try:
        return bool(int(v))
    except (ValueError, TypeError):
        return default


def safe_get_list(data: Any, *keys: str, default: Optional[list] = None) -> list:
    """防御性提取列表字段。"""
    v = safe_get(data, *keys, default=default)
    if v is None:
        return default or []
    if isinstance(v, list):
        return v
    return default or []
