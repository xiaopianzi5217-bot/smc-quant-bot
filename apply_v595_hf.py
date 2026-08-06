# -*- coding: utf-8 -*-
"""V59.5: hf_auto_trader.py 接入 gate_snapshot"""
import io, sys

src = "hf_auto_trader.py"
with io.open(src, "r", encoding="utf-8") as f:
    content = f.read()

old = '''        _order_id = trade_journal.open_trade(
            symbol=symbol,
            direction=direction,
            open_price=entry,
            sl=sl,
            tp1=tp1,
            tp2=tp2 if tp2 else 0,
            tp3=tp3 if tp3 else 0,
            rr=rr,
            score=score,
            regime=result.get("regime", ""),
            volume=size,
            note=f"ev={ev:.4f}_adx={result.get('adx',0):.1f}_atr={result.get('atr',0):.1f}_tier={_debug_tier}",
        )'''

new = '''        # V59.5: 构造质量门快照（供 TradeJournal 复盘: 什么条件导致亏损）
        # 优先使用 runner 注入的 gate_snapshot，缺失时用 result 字段构造
        _gate_snapshot = str(result.get("gate_snapshot", "{}"))
        if not _gate_snapshot or _gate_snapshot == "{}":
            try:
                _gate_snapshot = str(result.get("decision", {}).get("gate_snapshot", "{}"))
            except Exception:
                _gate_snapshot = "{}"
        if not _gate_snapshot or _gate_snapshot == "{}":
            _gate_override = False
            try:
                _gate_override = bool(result.get("decision", {}).get("gate_snapshot_override", False)) or bool(result.get("gate_overridden", False))
            except Exception:
                pass
            _gate_snapshot = (
                '{"score":%.1f,"min_score_required":%.1f,"override":%s,"adx":%.1f,"regime":"%s","ev":%.4f}'
                % (
                    float(score),
                    float(result.get("min_score_required", 72.0)),
                    "true" if _gate_override else "false",
                    float(result.get("adx", 0)),
                    str(result.get("regime", "mixed")),
                    float(ev),
                )
            )
        _order_id = trade_journal.open_trade(
            symbol=symbol,
            direction=direction,
            open_price=entry,
            sl=sl,
            tp1=tp1,
            tp2=tp2 if tp2 else 0,
            tp3=tp3 if tp3 else 0,
            rr=rr,
            score=score,
            regime=result.get("regime", ""),
            volume=size,
            note=f"ev={ev:.4f}_adx={result.get('adx',0):.1f}_atr={result.get('atr',0):.1f}_tier={_debug_tier}",
            gate_snapshot=_gate_snapshot,  # V59.5 质量门快照
        )'''

if old not in content:
    print("ERROR: anchor not found")
    idx = content.find("trade_journal.open_trade")
    if idx >= 0:
        print(repr(content[idx-200:idx+800]))
    sys.exit(1)

content = content.replace(old, new, 1)
with io.open(src, "w", encoding="utf-8") as f:
    f.write(content)
print("[OK] hf_auto_trader.py gate_snapshot 接入完成")
