"""
Probe Mode 统一配置

所有 Probe 参数集中管理，其他地方不应直接硬编码。
修改 Probe 策略时只改此文件。
"""

PROBE_CONFIG = {
    "min_score": 55,            # 最低 score 门槛
    "min_confidence": 0.55,     # 最低 confidence 门槛
    "size_multiplier": 0.25,    # Probe 仓位乘数（相对正常仓位）
    "min_events": 2,            # 至少满足几个白名单事件
    "required_events": [        # Observer 事件白名单
        "CHOCH",
        "LIQUIDITY_SWEEP",
        "FVG",
        "SQUEEZE_RELEASE",
    ],
    "enable": True,             # 全局开关
}
