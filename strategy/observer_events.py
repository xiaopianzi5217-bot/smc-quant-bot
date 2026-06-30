# strategy/observer_events.py
from typing import Any, Dict, List

def _safe_bool(v: Any) -> bool:
    if v is None: return False
    if isinstance(v, bool): return v
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "y", "on"}
    try: return bool(v)
    except: return False

def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None: return default
        val = float(v)
        return val if not (val != val) else default
    except: return default

def detect_observer_events(curr: Any, exec_ctx: Dict[str, Any]) -> List[Dict]:
    events = []
    close = _safe_float(getattr(curr, 'close', None) or exec_ctx.get("close", 0))

    # SQZMOM 变白
    if _safe_bool(exec_ctx.get("sqzmom_white_reversal_long")):
        events.append({"type": "SQZMOM_WHITE", "dir": "Long", "desc": "SQZMOM K线变白（多头动量衰竭）", "key": "sqz_white_long"})
    if _safe_bool(exec_ctx.get("sqzmom_white_reversal_short")):
        events.append({"type": "SQZMOM_WHITE", "dir": "Short", "desc": "SQZMOM K线变白（空头动量衰竭）", "key": "sqz_white_short"})

    # 背离 R
    if _safe_bool(exec_ctx.get("has_bot_div")):
        events.append({"type": "DIVERGENCE_R", "dir": "Long", "desc": "底背离 R 出现", "key": "div_bot"})
    if _safe_bool(exec_ctx.get("has_top_div")):
        events.append({"type": "DIVERGENCE_R", "dir": "Short", "desc": "顶背离 R 出现", "key": "div_top"})

    # 接近 OB
    if _safe_bool(exec_ctx.get("near_bullish_ob")):
        events.append({"type": "NEAR_OB", "dir": "Long", "desc": "接近 Bullish OB（潜在做多区）", "key": "ob_bull"})
    if _safe_bool(exec_ctx.get("near_bearish_ob")):
        events.append({"type": "NEAR_OB", "dir": "Short", "desc": "接近 Bearish OB（潜在做空区）", "key": "ob_bear"})

    # Liquidity Sweep
    if _safe_bool(exec_ctx.get("is_bsl_swept")):
        events.append({"type": "LIQUIDITY_SWEEP", "dir": "Short", "desc": "Buyside Liquidity 被扫", "key": "bsl_sweep"})
    if _safe_bool(exec_ctx.get("is_ssl_swept")):
        events.append({"type": "LIQUIDITY_SWEEP", "dir": "Long", "desc": "Sellside Liquidity 被扫", "key": "ssl_sweep"})

    # CHOCH
    swing_high = _safe_float(exec_ctx.get("swing_high", 0))
    swing_low = _safe_float(exec_ctx.get("swing_low", 0))
    if close > swing_high > 0:
        events.append({"type": "CHOCH", "dir": "Long", "desc": "CHOCH 多头结构转变", "key": "choch_long"})
    if close < swing_low > 0:
        events.append({"type": "CHOCH", "dir": "Short", "desc": "CHOCH 空头结构转变", "key": "choch_short"})

    # FVG
    if exec_ctx.get("bullish_fvg"):
        events.append({"type": "FVG", "dir": "Long", "desc": "Bullish FVG 出现", "key": "fvg_long"})
    if exec_ctx.get("bearish_fvg"):
        events.append({"type": "FVG", "dir": "Short", "desc": "Bearish FVG 出现", "key": "fvg_short"})

    return events