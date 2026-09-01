import sys
sys.path.insert(0, '.')
from strategy.v565_quality_gate import _is_blocked_hour, BLOCKED_HOURS
import json

# 测试 1: 从完整 config 传入 (跟引擎一样的调用方式)
full_cfg = json.load(open('config/v11_full_config.json', encoding='utf-8'))
v565_cfg = full_cfg.get('v565_gate', {})
print('v565_gate config:', v565_cfg)
print('hard_block_hours:', v565_cfg.get('hard_block_hours'))

# 测试 2: 被阻塞小时
for h in [4, 6, 7, 23]:
    blocked = _is_blocked_hour(h, v565_cfg)
    print(f'hour={h}: blocked={blocked}')

# 测试 3: 非阻塞小时
for h in [0, 2, 3, 8, 12, 17, 21]:
    blocked = _is_blocked_hour(h, v565_cfg)
    print(f'hour={h}: blocked={blocked}')

# 测试 4: 没有配置时回退默认
print('default BLOCKED_HOURS:', BLOCKED_HOURS)
print('no config fallback hour 4:', _is_blocked_hour(4, {}))
print('no config fallback hour 10:', _is_blocked_hour(10, {}))
