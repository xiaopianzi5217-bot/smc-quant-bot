"""模拟开启3个过滤器后对当前信号的影响"""
import sys; sys.path.insert(0,'.')
from ops.env_config import load_runtime_config
from runner.v11_institutional_runner import evaluate_symbol
import json

cfg = load_runtime_config('config/v11_full_config.json')
cfg['data_mode'] = 'live'

# 当前信号
res = evaluate_symbol('BTC/USDT', cfg)
dec = res['decision']
ec = dec.get('exec_ctx', {})
mc = dec.get('macro_ctx', dec.get('exec_ctx', {}))

print("=== BTC/USDT 当前信号 ===")
print(f"方向: {dec.get('direction','?')}")
print(f"评分: {dec.get('long_score',0):.1f}/{dec.get('short_score',0):.1f}")
print(f"EV: {dec.get('long_ev',0):.4f}/{dec.get('short_ev',0):.4f}")
print(f"RR: {dec.get('rr_calculated',0):.2f}")
print(f"ADX: {ec.get('adx',0):.1f}")
print(f"Volume Ratio: {ec.get('volume_ratio',0):.2f}")
print(f"Regime: {mc.get('regime','?')}")
print(f"HTF: {mc.get('allowed_direction','?')}")
print(f"Approved: {dec.get('approved',False)}")

print()

# 模拟交易时段过滤
from datetime import datetime, timezone
now_utc = datetime.now(timezone.utc)
blocked_hours = [0, 1, 2, 3]
print(f"当前UTC时间: {now_utc.hour}:{now_utc.minute:02d}")
if now_utc.hour in blocked_hours:
    print(f"[交易时段过滤] UTC {now_utc.hour}点 => BLOCKED (凌晨盘整期)")
else:
    print(f"[交易时段过滤] UTC {now_utc.hour}点 => PASS (正常交易时段)")

print()

# 模拟结构距离过滤
direction = dec.get('direction','')
price = dec.get('entry',0)
atr = ec.get('atr_pct',0)
bsl = ec.get('bsl_level',0)
ssl = ec.get('ssl_level',0)
support = bsl if direction == 'Long' else ssl
distance = abs(price - support) / (atr * price) if atr and price else 999
print(f"当前价格: {price:.2f}")
print(f"支撑位: {support:.2f}")
print(f"距离(ATR): {distance:.2f}")
if distance > 1.6:
    print(f"[结构距离过滤] 追价 {distance:.1f} ATR => BLOCKED (超出1.6 ATR限制)")
else:
    print(f"[结构距离过滤] 追价 {distance:.1f} ATR => PASS")

print()

# 模拟成交量确认过滤
vol_ratio = ec.get('volume_ratio',0)
print(f"Volume Ratio: {vol_ratio:.2f}")
if vol_ratio < 0.75:
    print(f"[成交量确认] vol_ratio={vol_ratio:.2f} < 0.75 => BLOCKED (量能不足)")
elif vol_ratio > 3.0:
    print(f"[成交量确认] vol_ratio={vol_ratio:.2f} > 3.0 => BLOCKED (放量异常)")
else:
    print(f"[成交量确认] vol_ratio={vol_ratio:.2f} => PASS")
