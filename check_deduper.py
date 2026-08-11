# -*- coding: utf-8 -*-
"""临时检查 signal_deduper.json 状态"""
import json
import time
from pathlib import Path

p = Path('state/signal_deduper.json')
if not p.exists():
    print("❌ state/signal_deduper.json 不存在")
    raise SystemExit(1)

data = json.loads(p.read_text(encoding='utf-8'))
print("=== signal_deduper.json 状态 ===")
print("updated_at:", time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(data.get('updated_at', 0))))
print("processed 条数:", len(data.get('processed', {})))
print("cooldowns 条数:", len(data.get('cooldowns', {})))
print("sl_times 条数:", len(data.get('sl_times', {})))

print("\n--- processed 详细 ---")
for k, v in data.get('processed', {}).items():
    dt = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(v))
    ago = int(time.time() - v)
    print(f"  {k}")
    print(f"      → 标记于 {dt} ({ago}s 前)")

print("\n--- cooldowns 详细 ---")
for k, v in data.get('cooldowns', {}).items():
    dt = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(v))
    ago = int(time.time() - v)
    print(f"  {k} → {dt} ({ago}s 前)")

print("\n--- sl_times 详细 ---")
for k, v in data.get('sl_times', {}).items():
    dt = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(v))
    ago = int(time.time() - v)
    print(f"  {k} → {dt} ({ago}s 前)")

# 检查 TTL：默认4小时=14400秒
from state.signal_deduper import DEFAULT_SIGNAL_TTL_SEC
print(f"\n⚠️ 信号 TTL 配置: {DEFAULT_SIGNAL_TTL_SEC}s ({DEFAULT_SIGNAL_TTL_SEC/3600:.1f} 小时)")
print(f"⚠️ 冷却配置: symbol_cooldown={900}s, same_setup_cooldown={375}s, sl_cooldown={300}s")