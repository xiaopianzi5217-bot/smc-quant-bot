# -*- coding: utf-8 -*-
"""
特征分类映射器（Feature Mapper）
==============================
统一将 raw feature 归类到 Feature Group，
确保所有模块（V6 DataEngine、回测分析、监控报表）使用同一套分类逻辑，
避免数据标签污染。

用法：
    from state.feature_mapper import classify_feature_group, FEATURE_GROUP_MAP

    1. 查找单个特征名所属分组：
       group = classify_feature_group("squeeze_release")   # -> "MOMENTUM"

    2. 批量对 raw_features_json 中的所有键分类：
       feature_groups = classify_all_features(raw_feature_dict)
       # -> {"MOMENTUM": ["squeeze_release", ...], "STRUCTURE": [...], ...}

    3. 直接引用映射表：
       FEATURE_GROUP_MAP["ob"]  # -> "STRUCTURE"
"""

# ============================================================
# 核心映射表：raw_feature_key → Feature Group
# ============================================================
# 所有用到特征分类的地方，只许引用此表，不许硬编码。
FEATURE_GROUP_MAP: dict[str, str] = {
    # ── Momentum ──
    "squeeze_release": "MOMENTUM",
    "sqzmom": "MOMENTUM",
    "sqz_released": "MOMENTUM",
    "sqz_duration": "MOMENTUM",
    "sqz_strength": "MOMENTUM",
    "sqz_vol_ratio": "MOMENTUM",
    "sqz_volume_confirmed": "MOMENTUM",
    "momentum": "MOMENTUM",
    "dmi_bull": "MOMENTUM",
    "dmi_bear": "MOMENTUM",
    "adx": "MOMENTUM",

    # ── Structure ──
    "ob": "STRUCTURE",
    "ob_valid": "STRUCTURE",
    "fvg": "STRUCTURE",
    "bullish_fvg": "STRUCTURE",
    "bearish_fvg": "STRUCTURE",
    "choch": "STRUCTURE",
    "structure_break": "STRUCTURE",
    "eq_high": "STRUCTURE",
    "eq_low": "STRUCTURE",
    "last_lower_high": "STRUCTURE",
    "last_higher_low": "STRUCTURE",
    "last_swing_high": "STRUCTURE",
    "last_swing_low": "STRUCTURE",
    "pivot_strength_high": "STRUCTURE",
    "pivot_strength_low": "STRUCTURE",

    # ── Liquidity ──
    "liquidity_sweep": "LIQUIDITY",
    "liquidity_sweep_confirmed": "LIQUIDITY",
    "bsl_sweep": "LIQUIDITY",
    "ssl_sweep": "LIQUIDITY",
    "is_bsl_swept": "LIQUIDITY",
    "is_ssl_swept": "LIQUIDITY",
    "bsl": "LIQUIDITY",
    "ssl": "LIQUIDITY",

    # ── Volatility ──
    "atr": "VOLATILITY",
    "atr_14": "VOLATILITY",
    "atr_pct": "VOLATILITY",
    "atr_expansion": "VOLATILITY",
    "wvf": "VOLATILITY",
    "volatility": "VOLATILITY",

    # ── Trend ──
    "ema_distance": "TREND",
    "ema_alignment": "TREND",
    "ema_50": "TREND",
    "ema_200": "TREND",
    "htf_direction": "TREND",
    "allowed_direction": "TREND",

    # ── Volume ──
    "volume_ratio": "VOLUME",
    "volume": "VOLUME",
    "vol_state": "VOLUME",
    "volume_spike": "VOLUME",

    # ── VWAP ──
    "vwap_dist": "VWAP",
    "vwap_align": "VWAP",

    # ── Divergence ──
    "divergence": "DIVERGENCE",
    "has_bot_div": "DIVERGENCE",
    "has_top_div": "DIVERGENCE",
    "bot_div_age": "DIVERGENCE",
    "top_div_age": "DIVERGENCE",
    "bot_div_strength": "DIVERGENCE",
    "top_div_strength": "DIVERGENCE",

    # ── Regime ──
    "regime": "REGIME",
    "macro_regime": "REGIME",

    # ── Misc / Other ──
    "rr": "RISK",
    "estimated_rr": "RISK",
    "kelly_size": "RISK",
    "confidence": "RISK",
    "p_win_raw": "RISK",
    "p_win_calibrated": "RISK",
}


def classify_feature_group(feature_name: str) -> str:
    """
    将单个特征名称映射到所属 Feature Group。

    参数
    ----
    feature_name : str
        特征名（raw feature key）。

    返回
    ----
    str
        对应的 Feature Group 名称；若未找到则返回 "OTHER"。
    """
    return FEATURE_GROUP_MAP.get(feature_name, "OTHER")


def classify_all_features(
    raw_feature_dict: dict,
) -> dict[str, list[str]]:
    """
    批量将 raw_feature_dict 中的所有 key 按 Feature Group 分类。

    参数
    ----
    raw_feature_dict : dict
        原始特征字典（例如从 raw_features_json 解析出的 dict）。

    返回
    ----
    dict[str, list[str]]
        结构为 {group_name: [feature_key, ...], ...}
    """
    result: dict[str, list[str]] = {}
    for key in raw_feature_dict:
        group = classify_feature_group(key)
        result.setdefault(group, []).append(key)
    return result


# 仅供向后兼容：v6_data_engine 原有的 feature weight 分组名称
# （用于 DynamicFeatureOptimizer）
GROUP_MAP_V6_BRIDGE: dict[str, str] = {
    "MOMENTUM": "SQZMOM",
    "STRUCTURE": "STRUCTURE",
    "LIQUIDITY": "LIQUIDITY",
    "VOLATILITY": "VOLATILITY",
    "TREND": "TREND",
    "VOLUME": "VOLUME",
    "VWAP": "VWAP",
    "DIVERGENCE": "DIVERGENCE",
    "REGIME": "REGIME",
    "RISK": "RISK",
    "OTHER": "OTHER",
}