# -*- coding: utf-8 -*-
"""V59.5: hf_auto_trader.py _trigger_stop_loss 激活 DailyPanel + FeedbackLoop"""
import io, sys

src = "hf_auto_trader.py"
with io.open(src, "r", encoding="utf-8") as f:
    content = f.read()

old = """    try:
        _breaker.record_trade(pnl_r)
        _sizer_v2.record_outcome(pnl_r)
    except Exception:
        pass"""

new = """    try:
        _breaker.record_trade(pnl_r)
        _sizer_v2.record_outcome(pnl_r)
    except Exception:
        pass

    # ===== V59.5: 平仓后激活 DailyPanel + FeedbackLoop（历史从未调用，日报数据一直为空） =====
    # pos 中已保存 score/confidence/regime/features/ev（开仓时注入），此处直接读取
    _close_score = float(pos.get("score", 0) or 0)
    _close_conf = float(pos.get("confidence", 0.5) or 0.5)
    _close_regime = str(pos.get("regime", "UNKNOWN"))
    _close_features = pos.get("features", []) or []
    _close_ev = float(pos.get("ev", 0) or 0)
    try:
        _panel.on_trade_closed(
            regime=_close_regime,
            features=_close_features,
            score=_close_score,
            confidence=_close_conf,
            pnl_r=float(pnl_r),
            direction=dir(pos).get("direction", "") if direction is None else str(direction),
        )
    except Exception as _panel_err:
        slog.error(f"[{symbol}] DailyPanel 平仓记录失败: {_panel_err}")

    try:
        _feedback.on_trade_closed(
            regime=_close_regime,
            features=_close_features,
            score=_close_score,
            confidence=_close_conf,
            pnl_r=float(pnl_r),
            direction=str(direction),
        )
    except Exception as _fb_err:
        slog.error(f"[{symbol}] FeedbackLoop 平仓记录失败: {_fb_err}")

    # 每日凌晨跨日时自动推送日报（仅一次）
    try:
        _panel.try_send_report(_safe_send_impl, _panel_today_sent)
    except Exception as _report_err:
        slog.error(f"[{symbol}] DailyPanel 日报推送失败: {_report_err}")"""

if old not in content:
    print("ERROR: anchor not found")
    idx = content.find("_breaker.record_trade")
    if idx >= 0:
        print(repr(content[idx-200:idx+600]))
    sys.exit(1)

content = content.replace(old, new, 1)
with io.open(src, "w", encoding="utf-8") as f:
    f.write(content)
print("[OK] hf_auto_trader.py _trigger_stop_loss 已激活 DailyPanel + FeedbackLoop")
