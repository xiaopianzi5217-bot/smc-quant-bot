# -*- coding: utf-8 -*-
"""
v9 Hard Safety Layer
实盘硬安全：比普通风控更靠前，任何一条触发都禁止交易。
"""

import os


class V9HardSafety:
    def __init__(self, config=None):
        config = config or {}
        self.max_daily_loss_pct = float(config.get("max_daily_loss_pct", 0.03))
        self.max_consecutive_losses = int(config.get("max_consecutive_losses", 3))
        self.max_position_desync_seconds = int(config.get("max_position_desync_seconds", 60))

    def check(self, account_state, system_state):
        reasons = []

        if os.getenv("EMERGENCY_STOP", "false").lower() == "true":
            reasons.append("手动紧急停止已开启")

        if float(account_state.get("daily_loss_pct", 0)) <= -abs(self.max_daily_loss_pct):
            reasons.append("达到单日最大亏损限制")

        if int(account_state.get("consecutive_losses", 0)) >= self.max_consecutive_losses:
            reasons.append("连续亏损次数达到限制")

        if not bool(system_state.get("api_ok", True)):
            reasons.append("交易所 API 异常")

        if not bool(system_state.get("data_ok", True)):
            reasons.append("行情数据异常")

        if bool(system_state.get("position_desync", False)):
            reasons.append("本地持仓与交易所持仓不同步")

        if bool(system_state.get("duplicate_order_risk", False)):
            reasons.append("存在重复下单风险")

        return {
            "allowed": len(reasons) == 0,
            "reasons": reasons,
        }
