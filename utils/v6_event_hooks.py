# -*- coding: utf-8 -*-
"""V6 数据引擎事件钩子封装。

将高维快照与结局同步逻辑统一到独立模块，便于主程序维护和复用。
"""

from v6_data_engine import init_v6_database, record_open_snapshot, record_close_outcome
from utils.event_bus import on


def on_v6_database_init():
    """初始化 V6 云端数据库。"""
    init_v6_database()


def on_record_open_snapshot(result: dict, kelly_size: float = 0.05):
    """记录开单时的高维特征快照。"""
    record_open_snapshot(result, kelly_size=kelly_size)


def on_record_close_outcome(signal_id: str, pnl_r: float, exit_reason: str, max_fwd: float = 0.0, max_adv: float = 0.0):
    """记录平仓结局结果到 V6 数据库。"""
    record_close_outcome(
        signal_id=signal_id,
        pnl_r=pnl_r,
        exit_reason=exit_reason,
        max_fwd=max_fwd,
        max_adv=max_adv,
    )


# 自动注册到通用事件总线
on("v6_database_init", on_v6_database_init)
on("record_open_snapshot", on_record_open_snapshot)
on("record_close_outcome", on_record_close_outcome)
