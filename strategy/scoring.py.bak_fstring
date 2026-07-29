# -*- coding: utf-8 -*-
"""
scoring.py — 评分层（V2 Scorecard 适配器）

设计原则：
    ✅ 只做一件事：转发到评分引擎（V1 smc_impulse_score / V2 v2_scorecard）
    ✅ 支持新旧并行运行，输出兼容格式
    ❌ 不做 penalty / compression / normalization / clamp
    ❌ 不做 gate / filter / reject

用法：
    from strategy.scoring import scoring_layer
    result = scoring_layer(ctx)
    score = result["final_score"]
"""

from __future__ import annotations
from typing import Any, Dict, Optional, Tuple

from strategy.smc_impulse_engine import smc_impulse_score
from strategy.v2_scorecard import v2_scorecard

# 全局引擎选择（可运行时切换）
_SCORING_ENGINE: str = "v2"  # "v1" = smc_impulse_score, "v2" = v2_scorecard


def set_scoring_engine(engine: str) -> None:
    """运行时切换评分引擎"""
    global _SCORING_ENGINE
    assert engine in ("v1", "v2"), f"Unknown engine: {engine}"
    _SCORING_ENGINE = engine


def scoring_layer(ctx: Dict[str, Any], engine: Optional[str] = None, **kwargs) -> Dict[str, Any]:
    """
    评分层：转发到指定评分引擎
    
    参数:
        ctx: 包含所有评分所需字段的上下文字典
        engine: "v1"(smc_impulse_score) / "v2"(v2_scorecard)，默认全局设置
        **kwargs: 透传给引擎的额外参数（如 ev_learner）
    
    返回:
        评分引擎完整结果（兼容格式）
    """
    eng = engine or _SCORING_ENGINE
    if eng == "v1":
        return smc_impulse_score(ctx)
    else:
        return v2_scorecard(ctx, **kwargs)


# ============================================================
# 废弃兼容接口（仅保留一个 adaptive_signal_score 供外部使用）
# ============================================================

def adaptive_signal_score(*args: Any, **kwargs: Any) -> Tuple[float, float, Dict[str, Any]]:
    """转发到当前评分引擎，返回 (score, threshold, meta) 元组"""
    ctx = _parse_ctx(*args, **kwargs)
    result = scoring_layer(ctx)
    score = result["final_score"]
    threshold = 20.0  # V2 没有单独 threshold，兼容占位
    meta = {
        "score": score,
        "smc": result.get("smc", result.get("quality_score", 0)),
        "sqzmom": result.get("sqzmom", 0),
        "regime": result.get("regime", "mixed"),
        "weights": result.get("weights", {}),
        "breakdown": result.get("breakdown", ""),
        "grade": _grade_from_score(score),
        "allow": True,
        "model": "V2_SCORECARD" if _SCORING_ENGINE == "v2" else "SMC_IMPULSE_ENGINE",
        "reasons": [result.get("breakdown", "")],
        "smc_passed": result.get("smc_passed", True),
        "sqz_passed": result.get("sqz_passed", True),
        "base_score": result.get("base_score", 0),
        "quality_score": result.get("quality_score", 0),
        "env_mult": result.get("env_mult", 1.0),
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
