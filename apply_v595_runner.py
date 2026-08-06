# -*- coding: utf-8 -*-
"""V59.5: runner/v11_institutional_runner.py 精确修改 4 处"""
import io, sys

src = "runner/v11_institutional_runner.py"
with io.open(src, "r", encoding="utf-8") as f:
    content = f.read()

# ===== 修改 1: V56.5 gate 块预初始化变量 =====
old1 = """    # 如果门禁启用且信号未通过，直接 return HOLD，不走 V9 决策流程
    _v565_cfg = cfg.get("v565_gate", {})
    if _v565_cfg.get("enabled", True):"""

new1 = """    # 如果门禁启用且信号未通过，直接 return HOLD，不走 V9 决策流程
    _v565_cfg = cfg.get("v565_gate", {})
    # V59.5: 预初始化 gate 变量，避免 enabled=False 时未定义
    _gate_passed = True
    _gate_meta: dict = {}
    _gate_snapshot = "{}"
    if _v565_cfg.get("enabled", True):"""

if old1 in content:
    content = content.replace(old1, new1, 1)
    print("[OK] 修改1: 预初始化 gate 变量")
else:
    if "_gate_snapshot = \"{}\"" in content:
        print("[SKIP] 修改1: 已应用")
    else:
        print("ERROR: 修改1 锚点未找到")
        sys.exit(1)

# ===== 修改 2: V9 决策前构造 gate_snapshot =====
old2 = """# ===== V9 决策（入口统一，不再区分 V56.5 gate 是否启用） =====
    kernel = V9DecisionKernel(params=cfg)"""

new2 = """    # V59.5: 构造质量门快照（供 TradeJournal 复盘: 什么条件导致亏损）
    # 格式: {"score":82,"min_score_required":78,"override":false,"adx":25,"regime":"TREND","ev":1.5}
    try:
        if _v565_cfg.get("enabled", True) and _gate_passed:
            _gate_snapshot = (
                '{"score":%.1f,"min_score_required":%.1f,"override":%s,"adx":%.1f,"regime":"%s","ev":%.4f}'
                % (
                    _score_for_gate,
                    float(_gate_meta.get("min_score_required", 0)),
                    "true" if _gate_meta.get("override", False) else "false",
                    float(curr.get("adx", 0)),
                    str(macro_ctx.get("regime", "mixed")),
                    _ev_for_gate,
                )
            )
    except Exception:
        _gate_snapshot = "{}"

    # ===== V9 决策（入口统一，不再区分 V56.5 gate 是否启用） =====
    kernel = V9DecisionKernel(params=cfg)"""

if old2 in content:
    content = content.replace(old2, new2, 1)
    print("[OK] 修改2: V9 前构造 gate_snapshot")
else:
    print("ERROR: 修改2 锚点未找到")
    sys.exit(1)

# ===== 修改 3: decision 注入 gate_snapshot =====
old3 = """    decision["exec_ctx"] = dict(exec_ctx)
    decision["exec_ctx"]["htf_allowed"] = htf_allowed"""

new3 = """    decision["exec_ctx"] = dict(exec_ctx)
    decision["exec_ctx"]["htf_allowed"] = htf_allowed
    decision["gate_snapshot"] = _gate_snapshot  # V59.5: 质量门快照供 hf_auto_trader 保存"""

if old3 in content:
    content = content.replace(old3, new3, 1)
    print("[OK] 修改3: decision 注入 gate_snapshot")
else:
    print("ERROR: 修改3 锚点未找到")
    # 搜索近似位置
    idx = content.find('decision["exec_ctx"]')
    if idx >= 0:
        print(repr(content[idx-50:idx+200]))
    sys.exit(1)

# ===== 修改 4: open_trade 传入 gate_snapshot =====
old4 = """            _tj.open_trade(
                symbol=symbol, direction=direction, open_price=price,
                sl=sl, tp1=tp1, tp2=tp2 if tp2 else 0, tp3=tp3 if tp3 else 0,
                rr=rr, score=l_score if direction == "Long" else s_score,
                regime=str(exec_ctx.get("regime", "")),
                note=f"adx={round(float(curr.get('adx',0)),1)} atr={round(atr,1)} vol_ratio={round(volume_ratio,2)}",
            )"""

new4 = """            _tj.open_trade(
                symbol=symbol, direction=direction, open_price=price,
                sl=sl, tp1=tp1, tp2=tp2 if tp2 else 0, tp3=tp3 if tp3 else 0,
                rr=rr, score=l_score if direction == "Long" else s_score,
                regime=str(exec_ctx.get("regime", "")),
                note=f"adx={round(float(curr.get('adx',0)),1)} atr={round(atr,1)} vol_ratio={round(volume_ratio,2)}",
                gate_snapshot=_gate_snapshot,  # V59.5 质量门快照
            )"""

if old4 in content:
    content = content.replace(old4, new4, 1)
    print("[OK] 修改4: open_trade 传入 gate_snapshot")
else:
    print("ERROR: 修改4 锚点未找到")
    sys.exit(1)

# ===== 修改 5: 返回对象顶层加 gate_snapshot =====
old5 = """    return {
        "symbol": symbol,
        "approved": bool(marked.get("approved")),
        "state": marked.get("state") or marked.get("state_name"),
        "reason": marked.get("reason") or marked.get("reason_cn"),
        "decision": marked,
    }"""

new5 = """    return {
        "symbol": symbol,
        "approved": bool(marked.get("approved")),
        "state": marked.get("state") or marked.get("state_name"),
        "reason": marked.get("reason") or marked.get("reason_cn"),
        "decision": marked,
        "gate_snapshot": _gate_snapshot,  # V59.5: 顶层快照，确保 hf_auto_trader 可读取
    }"""

if old5 in content:
    content = content.replace(old5, new5, 1)
    print("[OK] 修改5: 返回顶层 gate_snapshot")
else:
    print("ERROR: 修改5 锚点未找到")
    sys.exit(1)

with io.open(src, "w", encoding="utf-8") as f:
    f.write(content)
print("[ALL OK] runner/v11_institutional_runner.py 修改完成")
