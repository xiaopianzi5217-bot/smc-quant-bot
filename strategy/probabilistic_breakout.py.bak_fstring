# -*- coding: utf-8 -*-
"""
Probabilistic Breakout Engine V1

绐佺牬姒傜巼璇勫垎锛?~100锛夛紝鏇夸唬鏃х殑 AND gate / binary filter銆?
Design principles:    鉁?杩炵画姒傜巼绌洪棿锛?鈫?00锛夛紝闈?0/1 浜屽厓
    鉁?鍥涘洜瀛愬姞鏉冿細ATR + Volume + Squeeze + Momentum
    鉁?Regime-aware 鍔ㄦ€佹潈閲?    鉁?姣忎釜瀛愭ā鍧楄緭鍑?0~100 鍒?
鐢ㄦ硶锛?    from strategy.probabilistic_breakout import breakout_probability
    bp = breakout_probability(ctx)
    breakout_score = bp["breakout_prob"]  # 0~100
    if breakout_score >= 60:
        # 绐佺牬淇″彿
"""

from __future__ import annotations
from typing import Any, Dict
import math

from utils.safe import safe_float, safe_bool, safe_str







# ============================================================
# 瀛愭ā鍧?1锛欰TR 娉㈠姩閲婃斁璇勫垎锛?~100锛?# ============================================================
def _atr_score(ctx: Dict[str, Any]) -> float:
    """
    ATR 娉㈠姩閲婃斁璇勫垎锛?~100锛?    
    Input:    - atr_pct: ATR / Close 鐧惧垎姣?    - 鎴?atr / close 璁＄畻
    
    璇勫垎锛?    - > 3.0%: 90锛堟瀬绔尝鍔級
    - > 2.0%: 70锛堥珮娉㈠姩锛?    - > 1.0%: 50锛堟甯告尝鍔級
    - <= 1.0%: 30锛堜綆娉㈠姩锛?    """
    atr_pct = safe_float(ctx.get("atr_pct", 0.0))
    if atr_pct > 0.03:
        return 90.0
    elif atr_pct > 0.02:
        return 70.0
    elif atr_pct > 0.01:
        return 50.0
    else:
        return 30.0


# ============================================================
# 瀛愭ā鍧?2锛歏olume 閲忚兘璇勫垎锛?~100锛?# ============================================================
def _volume_score(ctx: Dict[str, Any]) -> float:
    """
    Volume 閲忚兘璇勫垎锛?~100锛?    
    Input:    - volume_ratio: 褰撳墠閲?/ 20鏃ュ潎閲?    
    璇勫垎锛?    - > 1.5: 90锛堝法閲忕垎鍙戯級
    - > 1.2: 70锛堟斁閲忥級
    - > 1.0: 50锛堟甯搁噺锛?    - <= 1.0: 30锛堢缉閲忥級
    """
    vol = safe_float(ctx.get("volume_ratio", 1.0))
    if vol > 1.5:
        return 90.0
    elif vol > 1.2:
        return 70.0
    elif vol > 1.0:
        return 50.0
    else:
        return 30.0


# ============================================================
# 瀛愭ā鍧?3锛歋queeze 鍘嬬缉閲婃斁璇勫垎锛?~100锛?# ============================================================
def _squeeze_score(ctx: Dict[str, Any]) -> float:
    """
    Squeeze 鍘嬬缉閲婃斁璇勫垎锛?~100锛?    
    Input:    - squeeze: "tight" / "mid" / "none"锛堝瓧绗︿覆锛?    - 鎴?squeeze_level: 3 / 2 / 1 / 0锛堟暣鏁帮級
    
    璇勫垎锛?    - tight / level >= 3: 85锛堟繁搴﹀帇缂╋級
    - mid / level == 2: 60锛堜腑搴﹀帇缂╋級
    - none / level <= 1: 40锛堟棤鍘嬬缉锛?    """
    squeeze = safe_str(ctx.get("squeeze", "none")).lower()
    squeeze_level = int(safe_float(ctx.get("squeeze_level", 0), 0))
    
    if squeeze == "tight" or squeeze_level >= 3:
        return 85.0
    elif squeeze == "mid" or squeeze_level == 2:
        return 60.0
    else:
        return 40.0


# ============================================================
# 瀛愭ā鍧?4锛歁omentum 鏂瑰悜鍔ㄩ噺璇勫垎锛?~100锛?# ============================================================
def _momentum_score(ctx: Dict[str, Any]) -> float:
    """
    Momentum 鏂瑰悜鍔ㄩ噺璇勫垎锛?~100锛?    
    Input:    - sqzmom_score: 0~44锛堟潵鑷?_sqzmom_context_score锛?    
    璇勫垎锛?    - 鐩存帴鏄犲皠 sqzmom_score 鍒?0~100
    - sqzmom_score 鑼冨洿 0~44锛屼箻浠?100/44 鈮?2.27
    """
    sqz = safe_float(ctx.get("sqzmom_score", 0.0))
    return min(max(sqz * (100.0 / 44.0), 0.0), 100.0)


# ============================================================
# 涓诲嚱鏁帮細breakout_probability
# ============================================================
def breakout_probability(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    Probabilistic Breakout Engine V1
    
    鍥涘洜瀛愬姞鏉冭瘎鍒嗭紝杈撳嚭绐佺牬姒傜巼锛?~100锛夈€?    
    鏉冮噸鏍规嵁甯傚満鐘舵€佸姩鎬佽皟鏁达細
    - trend:      ATR=0.3, Vol=0.2, Squeeze=0.2, Mom=0.3
    - transition: ATR=0.25, Vol=0.25, Squeeze=0.25, Mom=0.25
    - mud:        ATR=0.2, Vol=0.3, Squeeze=0.2, Mom=0.3
    
    鍙傛暟:
        ctx: 涓婁笅鏂囧瓧鍏革紝闇€鍖呭惈锛?            - atr_pct (float): ATR / Close
            - volume_ratio (float): 閲忔瘮
            - squeeze (str) 鎴?squeeze_level (int): 鍘嬬缉鐘舵€?            - sqzmom_score (float): SQZMOM 鍘熷鍒?            - regime (str): 甯傚満鐘舵€?    
    杩斿洖:
        {
            "breakout_prob": float,     # 0~100 绐佺牬姒傜巼
            "components": {
                "atr": float,           # ATR 鍒?                "volume": float,        # 閲忚兘鍒?                "squeeze": float,       # 鍘嬬缉鍒?                "momentum": float       # 鍔ㄩ噺鍒?            },
            "weights": {
                "atr": float,
                "volume": float,
                "squeeze": float,
                "momentum": float
            }
        }
    """
    # 1. 璁＄畻鍚勫瓙妯″潡鍒嗘暟
    atr = _atr_score(ctx)
    vol = _volume_score(ctx)
    squeeze = _squeeze_score(ctx)
    momentum = _momentum_score(ctx)

    # 2. Regime-aware 动态权重
    regime = safe_str(ctx.get("regime", "trend")).lower()
    if regime == "trend":
        w_atr, w_vol, w_sqz, w_mom = 0.3, 0.2, 0.2, 0.3
    elif regime == "transition":
        w_atr, w_vol, w_sqz, w_mom = 0.25, 0.25, 0.25, 0.25
    else:  # mud
        w_atr, w_vol, w_sqz, w_mom = 0.2, 0.3, 0.2, 0.3

    # 3. 鍔犳潈姹傚拰
    prob = (
        atr * w_atr +
        vol * w_vol +
        squeeze * w_sqz +
        momentum * w_mom
    )

    return {
        "breakout_prob": round(prob, 2),
        "components": {
            "atr": round(atr, 2),
            "volume": round(vol, 2),
            "squeeze": round(squeeze, 2),
            "momentum": round(momentum, 2),
        },
        "weights": {
            "atr": w_atr,
            "volume": w_vol,
            "squeeze": w_sqz,
            "momentum": w_mom,
        },
    }

