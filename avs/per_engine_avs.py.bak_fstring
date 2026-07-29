# -*- coding: utf-8 -*-
"""
Per-Engine AVS（真正修复关键点）。

计算每个引擎的累计 PnL 和归一化分数。
"""
from __future__ import annotations

from typing import Any, Dict

def compute_avs(engine_results: Dict[str, float]) -> Dict[str, Any]:
    """
    接收 EngineRouter.run() 返回的 {引擎名: 累计 PnL}。
    返回每个引擎的 AVS 报告。
    """
    avs: Dict[str, Any] = {}

    for k, v in engine_results.items():
        avs[k] = {
            "total_pnl": round(float(v), 6),
            "normalized": round(float(v) / (abs(v) + 1e-9), 6),
        }

    return avs
