# -*- coding: utf-8 -*-
"""
SMC V2锛氫笁缁寸粨鏋勬鐜囪瘎鍒嗗櫒锛堥潪 gate锛?
Scoring formula:    SMC_TOTAL = Zone_Quality(40%) + Mitigation_Strength(30%) + Structure_Alignment(30%)
    
    杈撳嚭 0~100 杩炵画鍒嗘暟锛屼笉鍐嶆湁 0/49 鏂礀寮?binary 鍒ゅ畾銆?
鐢ㄦ硶锛?    from strategy.smc_module_v2 import calculate_smc_score
    result = calculate_smc_score(ctx)
    smc_score = result["smc_score"]  # 0~100 杩炵画
"""

from __future__ import annotations
from typing import Any, Dict

from utils.safe import safe_float, safe_bool, safe_str










# ============================================================
# 缁村害 1锛歀iquidity Zone Quality锛堟祦鍔ㄦ€х粨鏋勮川閲忥級鈥?鏉冮噸 40%
# ============================================================
def _zone_quality(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    璇勪及娴佸姩鎬х粨鏋勮川閲忥細
    - 鏄惁鏈夋湁鏁?SMC 鍖哄煙锛圤B/FVG锛?    - 鏄惁鍙戠敓浜嗘祦鍔ㄦ€ф壂闄わ紙Sweep锛?    - OB 寮哄害
    - 鍖哄煙鎺ヨ繎搴︼紙zone_near_atr锛?    """
    score = 0.0
    reasons = []

    # 1a. 鏈夋晥鍖哄煙鍩虹鍒嗭紙0~25锛?    has_valid_zone = safe_bool(ctx.get("has_valid_zone", False))
    if has_valid_zone:
        score += 25.0
        reasons.append("VALID_ZONE_+25")

    # 1b. 娴佸姩鎬ф壂闄ゅ姞鍒嗭紙0~15锛?    liquidity_sweep = safe_bool(ctx.get("liquidity_sweep", ctx.get("liquidity_sweep_confirmed", False)))
    if liquidity_sweep:
        score += 15.0
        reasons.append("LIQUIDITY_SWEEP_+15")

    # 1c. OB 寮哄害鍔犲垎锛?~10锛?    ob_strength = safe_float(ctx.get("ob_strength", 0.0))
    if ob_strength > 0.6:
        score += 10.0
        reasons.append(f"OB_STRENGTH_{ob_strength:.1f}_+10")
    elif ob_strength > 0.3:
        score += 5.0
        reasons.append(f"OB_STRENGTH_{ob_strength:.1f}_+5")

    # 1d. 鍖哄煙鎺ヨ繎搴﹀姞鍒嗭紙zone_near_atr <= 0.7 琛ㄧず浠锋牸闈犺繎鍖哄煙锛?    zone_near = safe_float(ctx.get("zone_near_atr", 9.99))
    if zone_near <= 0.35:
        score += 8.0
        reasons.append(f"ZONE_NEAR_{zone_near:.2f}ATR_+8")
    elif zone_near <= 0.70:
        score += 5.0
        reasons.append(f"ZONE_NEAR_{zone_near:.2f}ATR_+5")
    elif zone_near <= 1.05:
        score += 2.0
        reasons.append(f"ZONE_NEAR_{zone_near:.2f}ATR_+2")

    final_score = min(score, 40.0)
    return {"score": round(final_score, 2), "raw": round(score, 2), "reasons": reasons}


# ============================================================
# 缁村害 2锛歁itigation Strength锛堝洖琛ュ己搴︼級鈥?鏉冮噸 30%
# ============================================================
def _mitigation_strength(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    璇勪及鍥炶ˉ/娴嬭瘯寮哄害锛?    - Wick Fill Ratio锛堝奖绾垮洖琛ユ瘮渚嬶級
    - 鏄惁鏈?Mitigation Source锛團VG/OB锛?    - 鏄惁鍙戠敓浜?Retest锛堥噸鏂版祴璇曪級
    """
    score = 0.0
    reasons = []

    # 2a. Wick Fill Ratio锛?~15锛?    fill_ratio = safe_float(ctx.get("wick_fill_ratio", ctx.get("fill_ratio", 0.0)))
    if fill_ratio > 0.7:
        score += 15.0
        reasons.append(f"WICK_FILL_{fill_ratio:.2f}_+15")
    elif fill_ratio > 0.5:
        score += 10.0
        reasons.append(f"WICK_FILL_{fill_ratio:.2f}_+10")
    elif fill_ratio > 0.3:
        score += 5.0
        reasons.append(f"WICK_FILL_{fill_ratio:.2f}_+5")

    # 2b. Mitigation Source 瀛樺湪锛?~10锛?    mitigation_src = safe_str(ctx.get("mitigation_src", "NO_FVG_OB"))
    has_mitigation = mitigation_src != "NO_FVG_OB"
    if has_mitigation:
        score += 10.0
        reasons.append(f"MITIGATION_SRC_{mitigation_src}_+10")

    # 2c. Retest 纭锛?~5锛?    retest_confirmed = safe_bool(ctx.get("retest_confirmed", False))
    if retest_confirmed:
        score += 5.0
        reasons.append("RETEST_CONFIRMED_+5")

    # 2d. 瀹炰綋纭鍔犲垎锛坆ody_pct >= 0.36 琛ㄧず寮虹‘璁わ級
    body_pct = safe_float(ctx.get("body_pct", 0.0))
    if body_pct >= 0.42:
        score += 5.0
        reasons.append(f"BODY_{body_pct:.2f}_+5")
    elif body_pct >= 0.36:
        score += 3.0
        reasons.append(f"BODY_{body_pct:.2f}_+3")

    final_score = min(score, 30.0)
    return {"score": round(final_score, 2), "raw": round(score, 2), "reasons": reasons}


# ============================================================
# 缁村害 3锛歋tructure Alignment锛堢粨鏋勪竴鑷存€э級鈥?鏉冮噸 30%
# ============================================================
def _structure_alignment(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    璇勪及缁撴瀯涓€鑷存€э細
    - HTF 鏂瑰悜涓庝氦鏄撴柟鍚戜竴鑷?    - Sweep 鏂瑰悜鍖归厤
    - 鍔ㄩ噺鏂瑰悜瀵归綈
    - DMI 瀵归綈
    """
    score = 0.0
    reasons = []
    direction = safe_str(ctx.get("direction", "")).lower()

    # 3a. HTF 鏂瑰悜涓€鑷达紙0~15锛?    htf_direction = safe_str(ctx.get("htf_direction", "")).lower()
    if htf_direction and direction:
        if htf_direction == direction:
            score += 15.0
            reasons.append(f"HTF_{htf_direction.upper()}_ALIGN_+15")
        else:
            score -= 5.0
            reasons.append(f"HTF_{htf_direction.upper()}_CONFLICT_-5")

    # 3b. Sweep 鏂瑰悜鍖归厤锛?~10锛?    sweep_direction_match = safe_bool(ctx.get("sweep_direction_match", False))
    if sweep_direction_match:
        score += 10.0
        reasons.append("SWEEP_DIR_MATCH_+10")

    # 3c. 鍔ㄩ噺鏂瑰悜瀵归綈锛?~5锛?    momentum_align = safe_bool(ctx.get("momentum_align", False))
    if momentum_align:
        score += 5.0
        reasons.append("MOMENTUM_ALIGN_+5")

    # 3d. DMI 瀵归綈鍔犲垎锛?~5锛?    dmi_aligned = safe_bool(ctx.get("sqzmom_dmi_aligned", ctx.get("dmi_aligned", False)))
    if dmi_aligned:
        score += 5.0
        reasons.append("DMI_ALIGNED_+5")

    # 3e. 瓒嬪娍鏂瑰悜涓€鑷村姞鍒嗭紙regime + trend_direction锛?    regime = safe_str(ctx.get("regime", "mud"))
    trend_dir = safe_str(ctx.get("trend_direction", "None")).lower()
    if regime == "trend" and trend_dir == direction:
        score += 5.0
        reasons.append(f"TREND_{trend_dir.upper()}_ALIGN_+5")

    final_score = min(score, 30.0)
    return {"score": round(final_score, 2), "raw": round(score, 2), "reasons": reasons}


# ============================================================
# 涓诲叆鍙ｏ細calculate_smc_score
# ============================================================
def calculate_smc_score(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    SMC V2锛氱粨鏋勫寲璇勫垎妯″潡锛堥潪 gate锛?    
    鍙傛暟:
        ctx: 鍖呭惈鎵€鏈?SMC 鐩稿叧瀛楁鐨勪笂涓嬫枃瀛楀吀
    
    杩斿洖:
        {
            "smc_score": float,       # 0~100 杩炵画鍒嗘暟
            "zone": {...},            # 缁村害 1 鏄庣粏
            "mitigation": {...},      # 缁村害 2 鏄庣粏
            "alignment": {...},       # 缁村害 3 鏄庣粏
            "breakdown": str          # 鍙鐨勮瘎鍒嗘槑缁?        }
    
    鐢ㄦ硶:
        result = calculate_smc_score(ctx)
        smc_score = result["smc_score"]  # 0~100
        print(result["breakdown"])
    """
    zone = _zone_quality(ctx)
    mitigation = _mitigation_strength(ctx)
    alignment = _structure_alignment(ctx)

    score = (
        zone["score"] * 0.4 +
        mitigation["score"] * 0.3 +
        alignment["score"] * 0.3
    )

    # 鏋勫缓鍙鐨?breakdown
    breakdown = (
        f"SMC={score:.1f} | "
        f"Zone({zone['score']:.1f}脳0.4={zone['score']*0.4:.1f}) | "
        f"Miti({mitigation['score']:.1f}脳0.3={mitigation['score']*0.3:.1f}) | "
        f"Align({alignment['score']:.1f}脳0.3={alignment['score']*0.3:.1f})"
    )

    return {
        "smc_score": round(score, 2),
        "zone": zone,
        "mitigation": mitigation,
        "alignment": alignment,
        "breakdown": breakdown,
    }

