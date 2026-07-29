# -*- coding: utf-8 -*-
"""
safe_extract.py — 统一防御性字典/对象字段提取
替代深层链式 .get().get().get()，避免 AttributeError / TypeError。
用法:
from utils.safe_extract import safe_get, safe_get_str, safe_get_float, safe_get_int, safe_get_bool
direction = safe_get_str(result, "decision", "signal", "direction", default="")
score = safe_get_float(result, "score", default=0.0)
nested = safe_get(result, "exec_ctx", "swing_high", default=0)
"""
from __future__ import annotations
import math
from typing import Any, Optional, Sequence, Union

KeyPath = Union[str, int]


def _as_mapping(obj: Any) -> Optional[dict]:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        try:
            d = obj.to_dict()
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    if hasattr(obj, "__getitem__") and hasattr(obj, "keys"):
        try:
            return dict(obj)
        except Exception:
            pass
    return None


def safe_get(obj: Any, *keys: KeyPath, default: Any = None) -> Any:
    cur = obj
    for key in keys:
        if cur is None:
            return default
        mapping = _as_mapping(cur)
        if mapping is not None:
            if key in mapping:
                cur = mapping[key]
            elif isinstance(key, int) and str(key) in mapping:
                cur = mapping[str(key)]
            elif isinstance(key, str) and key.isdigit() and int(key) in mapping:
                cur = mapping[int(key)]
            else:
                return default
            continue
        if isinstance(cur, (list, tuple)):
            if not isinstance(key, int):
                try:
                    key = int(key)
                except (TypeError, ValueError):
                    return default
            if key < 0 or key >= len(cur):
                return default
            cur = cur[key]
            continue
        if isinstance(key, str) and hasattr(cur, key):
            try:
                cur = getattr(cur, key)
                continue
            except Exception:
                return default
        return default
    return default if cur is None and default is not None else cur


def safe_get_str(obj: Any, *keys: KeyPath, default: str = "") -> str:
    val = safe_get(obj, *keys, default=default)
    if val is None:
        return default
    try:
        return str(val)
    except Exception:
        return default


def safe_get_float(obj: Any, *keys: KeyPath, default: float = 0.0) -> float:
    val = safe_get(obj, *keys, default=default)
    if val is None:
        return default
    try:
        out = float(val)
        if math.isnan(out) or math.isinf(out):
            return default
        return out
    except (TypeError, ValueError, OverflowError):
        return default


def safe_get_int(obj: Any, *keys: KeyPath, default: int = 0) -> int:
    val = safe_get(obj, *keys, default=default)
    if val is None:
        return default
    try:
        return int(float(val))
    except (TypeError, ValueError, OverflowError):
        return default


def safe_get_bool(obj: Any, *keys: KeyPath, default: bool = False) -> bool:
    val = safe_get(obj, *keys, default=default)
    if val is None:
        return default
    if isinstance(val, str):
        return val.strip().lower() in {"1", "true", "yes", "y", "on", "long", "short"}
    try:
        return bool(val)
    except Exception:
        return default


def safe_pick(obj: Any, paths: Sequence[Sequence[KeyPath]], default: Any = None) -> Any:
    for path in paths:
        if not path:
            continue
        val = safe_get(obj, *path, default=None)
        if val is not None:
            return val
    return default
