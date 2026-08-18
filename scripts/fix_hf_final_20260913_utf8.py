# -*- coding: utf-8 -*-
"""
Fix 4 data integrity issues in hf_auto_trader.py:
1. ATR: use _atr_val (protected) consistently
2. Regime: use HTF-detected _regime_name
3. EV/Score: propagate FeedbackLoop weighted results
4. FeedbackLoop circuit-breaker BEFORE route opening
Safe UTF-8 read/write.
"""
import io
import sys

path = "hf_auto_trader.py"
src = io.open(path, encoding="utf-8").read()

# ============ FIX 1: ATR consistency ============
old_atr = '        "atr": float(curr.get("ATRr_14", exec_ctx.get("atr", 0))),'
new_atr = '        "atr": _atr_val,  # FIX-20260913 use protected ATR consistent with SL/TP'
if old_atr in src:
    src = src.replace(old_atr, new_atr, 1)
    print("[OK] FIX1 ATR")
else:
    print("[MISS] FIX1 ATR - searching variant...")
    import re
    m = re.search(r'        "atr": float\(curr\.get\("ATRr_14"', src)
    if m:
        print("  found at char", m.start())
        # find full line
        line_end = src.find('\n', m.start())
        old_line_full = src[m.start():line_end]
        print("  old_line:", old_line_full)
        new_line_full = '        "atr": _atr_val,  # FIX-20260913 use protected ATR consistent with SL/TP'
        src = src[:m.start()] + new_line_full + src[line_end:]
        print("[OK] FIX1 ATR via regex")
    else:
        print("[FAIL] FIX1 ATR")

# ============ FIX 2: Regime ============
old_regime = '        "regime": best.get("regime", "unknown"),'
new_regime = '        "regime": _regime_name,  # FIX-20260913 use HTF value not backtest engine mixed'
if old_regime in src:
    src = src.replace(old_regime, new_regime, 1)
    print("[OK] FIX2 Regime")
else:
    print("[MISS] FIX2 Regime")

# ============ FIX 3: EV in result dict ============
old_ev = '        "expected_value": round(ev, 4),'
new_ev = '        "expected_value": round(float(_fb_result["ev"]), 4),'
if old_ev in src:
    src = src.replace(old_ev, new_ev, 1)
    print("[OK] FIX3 EV in result")
else:
    print("[MISS] FIX3 EV in result")

# ============ FIX 4: Score in result dict ============
old_score = '        "score": round(_fl_final_score, 2),'
new_score = '        "score": round(float(_fb_result["weighted_score"]), 2),'
if old_score in src:
    src = src.replace(old_score, new_score, 1)
    print("[OK] FIX4 Score in result")
else:
    print("[MISS] FIX4 Score in result")

# ============ FIX 5: Final score in result dict ============
old_final = '        "final_score": round(_fl_final_score, 2),'
new_final = '        "final_score": round(float(_fb_result["weighted_score"]), 2),\n        "v6_weighted_score": round(float(_fb_result["weighted_score"]), 2),'
if old_final in src:
    src = src.replace(old_final, new_final, 1)
    print("[OK] FIX5 Final score + v6_weighted_score")
else:
    print("[MISS] FIX5 Final score")

# ============ FIX 6: EV not overridden by blended_ev ============
old_ev2 = '    ev = blended_ev\n    score = result.get("score", 0.0)'
new_ev2 = '    ev = float(result.get("_feedback_ev", 0.0)) or blended_ev\n    score = result.get("score", 0.0)'
if old_ev2 in src:
    src = src.replace(old_ev2, new_ev2, 1)
    print("[OK] FIX6 EV not overridden")
else:
    print("[MISS] FIX6 EV not overridden")

# ============ FIX 7: FeedbackLoop breaker BEFORE routing ============
old_route = '''    if not check_and_open_v6_with_routing(result):
        return False

    # ===== FeedbackLoop EV decision =====
    _fb_res = result.get("_feedback_result", {})
    if _fb_res.get("should_reject", False):'''
new_route = '''    # ===== FIX-20260913 FeedbackLoop breaker BEFORE routing =====
    _fb_res = result.get("_feedback_result", {})
    if _fb_res.get("should_reject", False):
        slog.warning(f"[{symbol}] FeedbackLoop breaker triggered: ev={_fb_res.get('ev', 0):.4f}, confidence={_fb_res.get('confidence', 0):.3f} < threshold={_fb_res.get('reject_threshold', 0.30)}")
        return False

    if not check_and_open_v6_with_routing(result):
        return False

    # ===== FeedbackLoop EV decision =====
    _fb_res = result.get("_feedback_result", {})
    if _fb_res.get("should_reject", False):'''
if old_route in src:
    src = src.replace(old_route, new_route, 1)
    print("[OK] FIX7 FeedbackLoop breaker before routing")
else:
    print("[MISS] FIX7 FeedbackLoop breaker before routing")

# ============ FIX 8: position_manager score/ev write ============
old_pm = '''            "score": result.get("v6_final_score", score),\n            "confidence": result.get("confidence", 0.5),\n            "regime": str(result.get("regime", "UNKNOWN")),\n            "features": result.get("_feedback_features", []),\n            "ev": ev,'''
new_pm = '''            "score": float(result.get("v6_weighted_score", result.get("v6_final_score", score))),  # FIX-20260913 prefer FeedbackLoop weighted score\n            "confidence": result.get("confidence", 0.5),\n            "regime": str(result.get("regime", "UNKNOWN")),\n            "features": result.get("_feedback_features", []),\n            "ev": float(result.get("_feedback_ev", ev)),'''
if old_pm in src:
    src = src.replace(old_pm, new_pm, 1)
    print("[OK] FIX8 position_manager score/ev")
else:
    print("[MISS] FIX8 position_manager score/ev")

io.open(path, "w", encoding="utf-8").write(src)
print("\n[DONE] All fixes applied")

# Verify syntax
import py_compile
try:
    py_compile.compile(path, doraise=True)
    print("[OK] Syntax check passed")
except Exception as e:
    print(f"[FAIL] Syntax error: {e}")
    sys.exit(1)