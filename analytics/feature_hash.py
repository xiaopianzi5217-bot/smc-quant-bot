# -*- coding: utf-8 -*-
"""
Feature Vector → Hash 工具
"""

import hashlib
from typing import Dict, Any


def generate_feature_hash(feature: Dict[str, Any]) -> str:
    """Feature Vector → Hash (e.g. "2AF84E1C")"""
    sorted_items = sorted((str(k), str(v)) for k, v in feature.items())
    feature_str = "|".join(f"{k}={v}" for k, v in sorted_items)
    hash_obj = hashlib.md5(feature_str.encode('utf-8'))
    return hash_obj.hexdigest()[:8].upper()
