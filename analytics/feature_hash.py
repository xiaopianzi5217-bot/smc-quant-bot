# -*- coding: utf-8 -*-
"""
Feature Vector → Hash 工具

V1.1 (20260917 修复)：
- 加固输入：支持 Dict 或 List[Dict]（取最后一个快照）
- 防止 None 键 / NaN / 无穷大 / 非字符串键 导致崩溃
"""
import hashlib
import math
import json
from typing import Dict, Any, Union, List


def _sanitize_value(v: Any) -> str:
    """把任意类型值转成可参与哈希排序的字符串（防 NaN/Inf/None/异构对象）。"""
    if v is None:
        return ""
    if isinstance(v, float):
        if math.isnan(v):
            return "__NaN__"
        if math.isinf(v):
            return "__INF__" if v > 0 else "__NINF__"
        return repr(v)
    if isinstance(v, (int, bool, str)):
        return str(v)
    # list/dict 等复合类型 —— 扁平为字符串（防止后续序列化出问题）
    try:
        return json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(v)


def generate_feature_hash(feature: Union[Dict[str, Any], List[Dict[str, Any]], None]) -> str:
    """Feature Vector → Hash

    支持三种输入：
    - Dict[str, Any]               → 单个特征快照
    - List[Dict[str, Any]]         → 多个快照（如持仓features历史），取最后一个再哈希
    - None / 空列表 / 空字典       → 返回 "EMPTY"

    示例: {"regime": "BULL", "atr": 0.5} → "2AF84E1C"
    """
    if feature is None:
        return "EMPTY"

    # ---- 防御: List 类型 → 递归取最后一个条目 ----
    if isinstance(feature, list):
        if not feature:
            return "EMPTY"
        return generate_feature_hash(feature[-1])

    # ---- 非 dict 的其他类型 → 直接做字符串哈希 ----
    if not isinstance(feature, dict):
        return hashlib.md5(str(feature).encode("utf-8")).hexdigest()[:8].upper()

    # ---- dict 类型: 过滤 None 键 + 统一转字符串 ----
    try:
        sorted_items = sorted(
            (str(k), _sanitize_value(v))
            for k, v in feature.items()
            if k is not None
        )
    except Exception:
        # 极端兜底——如果 items() 本身出错（如畸形对象），退回全串哈希
        return hashlib.md5(str(feature).encode("utf-8")).hexdigest()[:8].upper()

    feature_str = "|".join(f"{k}={v}" for k, v in sorted_items)
    hash_obj = hashlib.md5(feature_str.encode("utf-8"))
    return hash_obj.hexdigest()[:8].upper()
