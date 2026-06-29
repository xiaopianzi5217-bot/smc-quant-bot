# -*- coding: utf-8 -*-
"""
scoring.py — 评分层（唯一职责：转发到 SMC-Impulse Engine）

设计原则：
    ✅ 只做一件事：smc_impulse_score(ctx) 的转发
    ❌ 不做 penalty / compression / normalization / clamp
    ❌ 不做 gate / filter / reject

用法：
    from strategy.scoring import scoring_layer
    result = scoring_layer(ctx)
    score = result["final_score"]
"""

from __future__ import annotations
from typing import Any, Dict, Tuple

from strategy.smc_impulse_engine import smc_impulse_score


def scoring_layer(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    评分层：转发到 SMC-Impulse Engine（唯一信号源）
    
    参数:
        ctx: 包含所有评分所需字段的上下文字典
    
    返回:
        smc_impulse_score(ctx) 的完整结果
    """
    return smc_impulse_score(ctx)


# ============================================================
# 废弃兼容接口（仅保留一个 adaptive_signal_score 供 app.py 使用）
# ============================================================

def adaptive_signal_score(*args: Any, **kwargs: Any) -> Tuple[float, float, Dict[str, Any]]:
    """转发到 smc_impulse_score，返回 (score, threshold, meta) 元组"""
    ctx = _parse_ctx(*args, **kwargs)
    result = smc_impulse_score(ctx)
    score = result["final_score"]
    threshold = 20.0
    meta = {
        "score": score,
        "smc": result["smc"],
        "sqzmom": result["sqzmom"],
        "regime": result["regime"],
        "weights": result["weights"],
        "breakdown": result["breakdown"],
        "grade": _grade_from_score(score),
        "allow": True,
        "model": "SMC_IMPULSE_ENGINE",
        "reasons": [result["breakdown"]],
        "smc_passed": result.get("smc_passed", True),
        "sqz_passed": result.get("sqz_passed", True),
    }
    return (score, threshold, meta)


def _parse_ctx(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """解析参数为 ctx 字典"""
    ctx: Dict[str, Any] = {}
    for arg in args:
        if isinstance(arg, dict):
            ctx.update(arg)
    ctx.update(kwargs)
    for arg in args:
        if isinstance(arg, str) and arg.lower() in ("long", "short", "buy", "sell", "bull", "bear"):
            ctx["direction"] = arg
    return ctx


def _grade_from_score(score: float) -> str:
    if score >= 85:
        return "S"
    if score >= 70:
        return "A"
    if score >= 55:
        return "B"
    if score >= 40:
        return "C"
    return "D"
