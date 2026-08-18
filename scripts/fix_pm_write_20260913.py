# -*- coding: utf-8 -*-
"""修复 position_manager 写入时 score/ev 优先透传 FeedbackLoop 加权值"""
import io
import sys

path = "hf_auto_trader.py"
src = io.open(path, encoding="utf-8").read()

# ========== 修复 1: V6 路由持仓写入块（约 1866-1885 行） ==========
old1 = '''            "score": result.get("v6_final_score", score),
            "confidence": result.get("confidence", 0.5),
            "regime": str(result.get("regime", "UNKNOWN")),
            "features": result.get("_feedback_features", []),
            "ev": ev,
            "signal_id": sig_id,
            "atr": float(result.get("atr") or 0.0),'''
new1 = '''            "score": float(result.get("v6_weighted_score", result.get("v6_final_score", score))),  # 【修复20260913】优先写 FeedbackLoop 加权分
            "confidence": result.get("confidence", 0.5),
            "regime": str(result.get("regime", "UNKNOWN")),  # 【修复20260913】已用 HTF 检测值
            "features": result.get("_feedback_features", []),
            "ev": float(result.get("_feedback_ev", ev)),  # 【修复20260913】优先写 FeedbackLoop 校准 EV
            "signal_id": sig_id,
            "atr": float(result.get("atr") or 0.0),'''
if old1 in src:
    src = src.replace(old1, new1, 1)
    print("[OK] V6 路由持仓写入已透传加权分/FeedbackLoop EV")
else:
    print("[MISS] V6 路由持仓写入块未找到")
    # 尝试简化匹配
    probe = '"score": result.get("v6_final_score", score),'
    if probe in src:
        print("  -> 找到 score 行，手动检查上下文")
    else:
        print("  -> score 行内容已变化")

# ========== 修复 2: Strategy open 持仓写入块 ==========
old2 = '''        "score": score,  # 【闭环】用于平仓时回传 Calibrator
        "confidence": result.get("confidence", 0.5),  # 【闭环】
        "regime": str(result.get("regime", "UNKNOWN")),  # 【闭环】用于平仓时更新 RegimeFeatureStats
        "features": result.get("_feedback_features", []),  # 【闭环】用于平仓时更新
        "ev": ev,'''
new2 = '''        "score": float(result.get("v6_weighted_score", score)),  # 【修复20260913】优先写 FeedbackLoop 加权分
        "confidence": result.get("confidence", 0.5),  # 【闭环】
        "regime": str(result.get("regime", "UNKNOWN")),  # 【修复20260913】已用 HTF 检测值
        "features": result.get("_feedback_features", []),  # 【闭环】用于平仓时更新
        "ev": float(result.get("_feedback_ev", ev)),  # 【修复20260913】优先写 FeedbackLoop 校准 EV'''
if old2 in src:
    src = src.replace(old2, new2, 1)
    print("[OK] Strategy open 持仓写入已透传加权分/FeedbackLoop EV")
else:
    print("[MISS] Strategy open 持仓写入块未找到")
    probe2 = '"score": score,  # 【闭环】用于平仓时回传 Calibrator'
    if probe2 in src:
        print("  -> 找到 score 行，检查上下文")
    else:
        print("  -> score 行内容已变化")

io.open(path, "w", encoding="utf-8").write(src)

# 语法检查
import py_compile
try:
    py_compile.compile(path, doraise=True)
    print("[OK] 语法编译通过")
except Exception as e:
    print(f"[FAIL] 语法错误: {e}")
    sys.exit(1)