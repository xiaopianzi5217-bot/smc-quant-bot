"""
Apply changes to hf_auto_trader.py:
1. Add reject_audit.log() to all missing return None points in scan_and_decide
2. Add Mud Regime soft penalty before the V56.5 selected print
"""
import os

filepath = "hf_auto_trader.py"
with open(filepath, "r", encoding="utf-8") as f:
    content = f.read()

# ============================================================
# Change 1: 数据不足 (line 457) - after print, before return None
# ============================================================
old1 = '''    if df_exec is None or len(df_exec) < 100:
        print(f"[{symbol}] 数据不足，跳过")
        return None'''

new1 = '''    if df_exec is None or len(df_exec) < 100:
        print(f"[{symbol}] 数据不足，跳过")
        get_reject_audit().log(
            symbol, "DATA_INSUFFICIENT_EXEC",
            score=0.0, ev=0.0, regime="unknown",
            extra={"len_exec": len(df_exec) if df_exec is not None else 0},
        )
        return None'''

assert old1 in content, "Change 1 pattern not found!"
content = content.replace(old1, new1)

# ============================================================
# Change 2: V56指标不足 (line ~467)
# ============================================================
old2 = '''    if df_v56 is None or len(df_v56) < 260:
        print(f"[{symbol}] V56 指标计算后数据不足")
        return None'''

new2 = '''    if df_v56 is None or len(df_v56) < 260:
        print(f"[{symbol}] V56 指标计算后数据不足")
        get_reject_audit().log(
            symbol, "DATA_INSUFFICIENT_V56",
            score=0.0, ev=0.0, regime="unknown",
            extra={"len_v56": len(df_v56) if df_v56 is not None else 0},
        )
        return None'''

assert old2 in content, "Change 2 pattern not found!"
content = content.replace(old2, new2)

# ============================================================
# Change 3: 无候选信号 (line ~477)
# ============================================================
old3 = '''    if candidates is None or candidates.empty:
        print(f"[{symbol}] V56.5 引擎无候选信号")
        return None'''

new3 = '''    if candidates is None or candidates.empty:
        print(f"[{symbol}] V56.5 引擎无候选信号")
        get_reject_audit().log(
            symbol, "NO_CANDIDATES",
            score=0.0, ev=0.0, regime="unknown",
        )
        return None'''

assert old3 in content, "Change 3 pattern not found!"
content = content.replace(old3, new3)

# ============================================================
# Change 4: 仅历史信号 (line ~487)
# ============================================================
old4 = '''        if candidates.empty:
            print(f"[{symbol}] 仅历史信号存在，跳过本轮扫描")
            return None'''

new4 = '''        if candidates.empty:
            print(f"[{symbol}] 仅历史信号存在，跳过本轮扫描")
            get_reject_audit().log(
                symbol, "HISTORY_ONLY",
                score=0.0, ev=0.0, regime="unknown",
                extra={"lookback": LOOKBACK_CANDLES},
            )
            return None'''

assert old4 in content, "Change 4 pattern not found!"
content = content.replace(old4, new4)

# ============================================================
# Change 5: 排重后为空 (line ~505)
# ============================================================
old5 = '''        if candidates.empty:
            print(f"[{symbol}] 排重后无有效候选信号，跳过本轮扫描")
            return None'''

new5 = '''        if candidates.empty:
            print(f"[{symbol}] 排重后无有效候选信号，跳过本轮扫描")
            get_reject_audit().log(
                symbol, "DEDUPED_EMPTY",
                score=0.0, ev=0.0, regime="unknown",
            )
            return None'''

assert old5 in content, "Change 5 pattern not found!"
# Need to find the second occurrence - there are two `if candidates.empty` blocks
# Find all occurrences
import re
occurences = [m.start() for m in re.finditer(re.escape(old5), content)]
if len(occurences) > 1:
    # Replace only the second occurrence (deduped one)
    # The first one is the history_only one we already replaced
    # So the old5 pattern now appears once (the deduped one)
    pass
content = content.replace(old5, new5)

# ============================================================
# Change 6: 无有效方向 (line ~573)
# ============================================================
old6 = '''    if not direction:
        print(f"[{symbol}] 无有效方向")
        return None'''

new6 = '''    if not direction:
        print(f"[{symbol}] 无有效方向")
        get_reject_audit().log(
            symbol, "NO_DIRECTION",
            score=float(best.get("score", 0)), ev=float(best.get("model_ev", 0)),
            regime=str(best.get("regime", "unknown")),
            setup_type=str(best.get("setup_type", "")),
        )
        return None'''

assert old6 in content, "Change 6 pattern not found!"
content = content.replace(old6, new6)

# ============================================================
# Change 7: Mud硬拦截 (line ~622) - after print, before return None
# ============================================================
old7 = '''                print(f"[{symbol}] Mud regime + ADX={_adx_check:.1f} < 18, 无强共振例外, 跳过")
                return None'''

new7 = '''                print(f"[{symbol}] Mud regime + ADX={_adx_check:.1f} < 18, 无强共振例外, 跳过")
                get_reject_audit().log(
                    symbol, "MUD_HARD_BLOCK",
                    score=score, ev=ev, regime=_regime_raw,
                    vol_state=str(exec_ctx.get("volatility", "unknown")),
                    direction=direction or "",
                    setup_type=str(best.get("setup_type", "")),
                    extra={"adx": _adx_check, "strong_exception": _strong_exception},
                )
                return None'''

assert old7 in content, "Change 7 pattern not found!"
content = content.replace(old7, new7)

# ============================================================
# Change 8: SL方向异常 Long (line ~629)
# ============================================================
old8 = '''    if direction == "Long" and sl > entry_price:
        print(f"[{symbol}] SL方向异常(重算后): Long SL({sl:.2f}) > 入场({entry_price:.2f}), atr={_atr_val:.2f} 异常小, 跳过")
        return None'''

new8 = '''    if direction == "Long" and sl > entry_price:
        print(f"[{symbol}] SL方向异常(重算后): Long SL({sl:.2f}) > 入场({entry_price:.2f}), atr={_atr_val:.2f} 异常小, 跳过")
        get_reject_audit().log(
            symbol, "SL_DIRECTION_INVALID",
            score=score, ev=ev, regime=_regime_raw,
            direction=direction or "",
            extra={"entry": entry_price, "sl": sl, "atr": _atr_val, "sl_side": "LONG"},
        )
        return None'''

assert old8 in content, "Change 8 pattern not found!"
content = content.replace(old8, new8)

# ============================================================
# Change 9: SL方向异常 Short (line ~632)
# ============================================================
old9 = '''    if direction == "Short" and sl < entry_price:
        print(f"[{symbol}] SL方向异常(重算后): Short SL({sl:.2f}) < 入场({entry_price:.2f}), atr={_atr_val:.2f} 异常小, 跳过")
        return None'''

new9 = '''    if direction == "Short" and sl < entry_price:
        print(f"[{symbol}] SL方向异常(重算后): Short SL({sl:.2f}) < 入场({entry_price:.2f}), atr={_atr_val:.2f} 异常小, 跳过")
        get_reject_audit().log(
            symbol, "SL_DIRECTION_INVALID",
            score=score, ev=ev, regime=_regime_raw,
            direction=direction or "",
            extra={"entry": entry_price, "sl": sl, "atr": _atr_val, "sl_side": "SHORT"},
        )
        return None'''

assert old9 in content, "Change 9 pattern not found!"
content = content.replace(old9, new9)

# ============================================================
# Change 10: Mud Regime 软惩罚
# Insert after the existing Mud block (after _mud_cut_override = 0.5 / else branches)
# and the SL direction check, before the color consistency check
# ============================================================
# Find the pattern to insert before:
#     # ===== 【修复20260715】K线颜色 + ADX方向一致性检查 =====
insert_target = '        # ===== 【修复20260715】K线颜色 + ADX方向一致性检查 ====='
assert insert_target in content, "Insert target not found!"

# Find the end of the Mud block + SL checks + blank lines
# We'll insert right before the color check comment
mud_soft_penalty = '''
    # ===== 【新增 Mud Regime 软惩罚（不硬拦截，只削弱评分）】=====
    if "mud" in _regime_raw or "chaos" in _regime_raw:
        _orig_score = score
        _orig_ev = ev
        score = max(0.0, score - 10.0)           # 评分 -10 分
        ev = ev * 0.8                             # EV ×0.8
        print(f"[{symbol}] Mud Regime 软惩罚: score={_orig_score:.1f}->{score:.1f} (-10), "
              f"ev={_orig_ev:.4f}->{ev:.4f} (×0.8)")
        get_reject_audit().log(
            symbol, "MUD_SOFT_PENALTY",
            score=score, ev=ev, regime=_regime_raw,
            vol_state=str(exec_ctx.get("volatility", "unknown")),
            direction=direction or "",
            setup_type=str(best.get("setup_type", "")),
            extra={"orig_score": _orig_score, "score_delta": -10.0, "ev_mult": 0.8},
        )

''' + insert_target

content = content.replace(insert_target, mud_soft_penalty)

# ============================================================
# Write the file
# ============================================================
with open(filepath, "w", encoding="utf-8") as f:
    f.write(content)

print("✅ 所有改动已写入 hf_auto_trader.py")
print("改动摘要：")
print("  1. 数据不足 -> DATA_INSUFFICIENT_EXEC")
print("  2. V56指标不足 -> DATA_INSUFFICIENT_V56")
print("  3. 无候选 -> NO_CANDIDATES")
print("  4. 仅历史 -> HISTORY_ONLY")
print("  5. 排重后空 -> DEDUPED_EMPTY")
print("  6. 无方向 -> NO_DIRECTION")
print("  7. Mud硬拦截 -> MUD_HARD_BLOCK")
print("  8. SL方向Long -> SL_DIRECTION_INVALID")
print("  9. SL方向Short -> SL_DIRECTION_INVALID")
print("  10. Mud软惩罚 -> MUD_SOFT_PENALTY (评分-10, EV×0.8)")
