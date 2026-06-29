# -*- coding: utf-8 -*-
import os

class SafetyGuard:
    def __init__(self, config):
        self.config = config or {}
        self.daily_loss = 0.0
        self.consecutive_losses = 0
        self.kill_switch = False

    def live_allowed(self):
        mode = self.config.get("mode", "dry_run")
        return mode == "live" and os.getenv("ENABLE_LIVE_TRADING", "false").lower() == "true"

    def can_trade(self):
        if self.kill_switch:
            return False, "系统熔断已开启"
        max_daily_loss = abs(float(self.config.get("max_daily_loss_usdt", 0)))
        if max_daily_loss > 0 and abs(self.daily_loss) >= max_daily_loss:
            return False, "达到每日最大亏损限制"
        max_losses = int(self.config.get("max_consecutive_losses", 0))
        if max_losses > 0 and self.consecutive_losses >= max_losses:
            return False, "连续止损次数过多，暂停交易"
        return True, "允许交易"

    def record_trade(self, pnl):
        pnl = float(pnl)
        self.daily_loss += min(0.0, pnl)
        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

    def emergency_stop(self, reason="manual"):
        self.kill_switch = True
        return f"已触发紧急停止: {reason}"
