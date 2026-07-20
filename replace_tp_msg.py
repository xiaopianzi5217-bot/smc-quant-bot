# -*- coding: utf-8 -*-
"""替换 TP/SL 推送消息为更丰富的版本"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

path = r'C:\Users\Administrator\Desktop\SMC_Bot\hf_auto_trader.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# ===== 1. 替换 SL 推送消息 =====
old_sl = '''    pnl_pct = ((current_price / pos["entry"]) - 1) * 100
    if pos["direction"] == "Short":
        pnl_pct = ((pos["entry"] / current_price) - 1) * 100

    msg = (
        f"\u26d4 [SL] {symbol} {'\u591a\u5934' if pos['direction'] == 'Long' else '\u7a7a\u5934'}\n"
        f"入场: {pos['entry']:.2f} 出场: {current_price:.2f}\n"
        f"盈亏: {pnl_pct:+.2f}%"
    )
    print(f"[{symbol}] \u274c 止损触发 ({pnl_pct:+.2f}%)")
    safe_send(msg, priority="TRADE")'''

new_sl = '''    pnl_pct = ((current_price / pos["entry"]) - 1) * 100
    if pos["direction"] == "Short":
        pnl_pct = ((pos["entry"] / current_price) - 1) * 100

    # -- 计算 R 倍数 --
    _risk_dist_sl = abs(pos["entry"] - pos["current_sl"])
    if _risk_dist_sl > 1e-12:
        if pos["direction"] == "Long":
            profit_r_sl = (current_price - pos["entry"]) / _risk_dist_sl
        else:
            profit_r_sl = (pos["entry"] - current_price) / _risk_dist_sl
    else:
        profit_r_sl = pnl_pct / 100.0

    dir_cn_sl = "\u591a\u5934" if pos["direction"] == "Long" else "\u7a7a\u5934"
    dir_emoji_sl = "\U0001F4C8" if pos["direction"] == "Long" else "\U0001F4C9"
    msg = (
        f"{dir_emoji_sl} [SL] {symbol} {dir_cn_sl}\n"
        f"入场: {pos['entry']:.2f}  出场: {current_price:.2f}\n"
        f"盈亏: {pnl_pct:+.2f}%  |  R倍数: {profit_r_sl:.2f}R\n"
        f"持仓: 入场{pos.get('stage', 0)}阶段  |  MFE: {pos.get('mfe', 0):.2f}R"
    )
    print(f"[{symbol}] \u274c 止损触发 ({pnl_pct:+.2f}% R={profit_r_sl:.2f})")
    safe_send(msg, priority="TRADE")'''

count = 0
if old_sl in content:
    content = content.replace(old_sl, new_sl)
    count += 1
    print('SL 消息替换成功')
else:
    print('SL 消息未找到（可能已替换过）')

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print(f'完成: {count} 处替换')

