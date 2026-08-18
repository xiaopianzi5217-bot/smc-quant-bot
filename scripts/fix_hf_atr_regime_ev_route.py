# -*- coding: utf-8 -*-
"""批量修复 hf_auto_trader.py 的四个数据一致性问题：
1. ATR 不一致：result['atr'] 改用带 max() 保护值的 _atr_val
2. Regime 失真：result['regime'] 改用 HTF 检测的 _regime_name
3. EV/评分未透传：expected_value/score/final_score 改用 FeedbackLoop 加权结果
4. 风控熔断失效：FeedbackLoop should_reject 检查移到路由开仓之前
"""
import io
import re
import sys

path = "hf_auto_trader.py"
src = io.open(path, encoding="utf-8").read()

# ========== 修复 1+2+3：result 字典构建区 ==========
old_block = '''        "_mud_cut": _mud_cut_override,  # mud regime 降仓系数
        "symbol": symbol,
        "direction": direction,
        "expected_value": round(ev, 4),
        "score": round(_fl_final_score, 2),  # 风控否决/调整后的最终分
        "orig_score": round(score, 2),  # 原始进入 V56.5 / Calibration 评分，用于 V6 路由判断
        "final_score": round(_fl_final_score, 2),  # 对外暴露的最终分，供 V6 路由判断'''
new_block = '''        "_mud_cut": _mud_cut_override,  # mud regime 降仓系数
        "symbol": symbol,
        "direction": direction,
        "expected_value": round(float(_fb_result["ev"]), 4),  # 【修复20260913】用 FeedbackLoop 校准后 EV
        "score": round(float(_fb_result["weighted_score"]), 2),  # 【修复20260913】用 FeedbackLoop 加权得分
        "orig_score": round(score, 2),  # 原始进入 V56.5 / Calibration 评分，用于 V6 路由判断
        "final_score": round(float(_fb_result["weighted_score"]), 2),  # 【修复20260913】对外暴露的最终加权分
        "v6_weighted_score": round(float(_fb_result["weighted_score"]), 2),  # 【新增】FeedbackLoop 加权分'''
assert old_block in src, "未找到 result 字典构建块"
src = src.replace(old_block, new_block, 1)

# ========== 修复 2：regime 行 ==========
old_regime = '        "regime": best.get("regime", "unknown"),'
new_regime = '        "regime": _regime_name,  # 【修复20260913】改用 HTF 检测值（RANGE/BULL/BEAR），不再用回测引擎的 mixed'
assert old_regime in src, "未找到 regime 行"
src = src.replace(old_regime, new_regime, 1)

# ========== 修复 1：atr 行（用带保护值的 _atr_val） ==========
old_atr = '        "atr": float(curr.get("ATRr_14", exec_ctx.get("atr", 0))),'
new_atr = '        "atr": _atr_val,  # 【修复20260913】与 SL/TP 重算共用同一 ATR 值（含 max(entry*0.0025) 保护）'
assert old_atr in src, f"未找到 atr 行: {old_atr}"
src = src.replace(old_atr, new_atr, 1)

# ========== 修复 4：FeedbackLoop 熔断提前 ==========
# 当前：check_and_open_v6_with_routing(result) 在 FeedbackLoop reject 检查之前执行
# 修复：把 FeedbackLoop 拒绝检查移到路由调用之前
old_route = '''    if not check_and_open_v6_with_routing(result):
        return False

    # ===== 【闭环】FeedbackLoop EV 决策替代固定阈值 =====
    _fb_res = result.get("_feedback_result", {})
    if _fb_res.get("should_reject", False):
        slog.warning(f"[{symbol}] FeedbackLoop 拒绝: ev={_fb_res.get('ev', 0):.4f}, confidence={_fb_res.get('confidence', 0):.3f} < threshold={_fb_res.get('reject_threshold', 0.30)}")
        return False'''
new_route = '''    # ===== 【修复20260913】FeedbackLoop 熔断必须前置到路由开仓之前 =====
    # 之前 should_reject 检查在 check_and_open_v6_with_routing 之后执行，
    # 导致 ETH reject=True 时 B_GRADE 实盘开单指令已下发，为时已晚。
    _fb_res = result.get("_feedback_result", {})
    if _fb_res.get("should_reject", False):
        slog.warning(f"[{symbol}] FeedbackLoop 熔断: ev={_fb_res.get('ev', 0):.4f}, confidence={_fb_res.get('confidence', 0):.3f} < threshold={_fb_res.get('reject_threshold', 0.30)}")
        return False

    if not check_and_open_v6_with_routing(result):
        return False'''
assert old_route in src, "未找到路由调用区段"
src = src.replace(old_route, new_route, 1)

io.open(path, "w", encoding="utf-8").write(src)
print("[OK] 4 处修复全部完成")

# 语法检查
import py_compile
try:
    py_compile.compile(path, doraise=True)
    print("[OK] 语法编译通过")
except Exception as e:
    print(f"[ERROR] 语法错误: {e}")
    sys.exit(1)