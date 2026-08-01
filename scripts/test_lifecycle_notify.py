# -*- coding: utf-8 -*-
from execution.lifecycle_manager import TradeLifecycleManager
import execution.lifecycle_manager as lm
from portfolio.portfolio_manager import PortfolioManager

# 简单打印型 notifier，用于验证接收的数据类型和内容
def notify(x):
    print("NOTIFIER RECEIVED TYPE:", type(x))
    print("NOTIFIER RECEIVED:", x)

# 模拟环境：禁用全局 dispatch_execution_event，强制走 notifier 分支
lm.dispatch_execution_event = None

cfg = {
    "execution": {
        "cooldown_minutes_after_loss": 1,
        "tp1_close_pct": 0.35,
        "tp2_close_pct": 0.35,
        "move_sl_to_be_after_tp1": True,
        "trail_after_tp2": True,
        "trail_atr_mult": 1.2,
    }
}

# 避免写文件，state_path=None
portfolio = PortfolioManager(state_path=None)
# 创建一个开仓：entry=100, sl=95
p = portfolio.add_position("BTC/USDT", "Long", 10, {"entry": 100, "sl": 95, "tp1": 110, "tp2": 120, "tp3": 130})

lifecycle = TradeLifecycleManager(cfg, None, portfolio, None, notifier=notify)
# 提供一个简单的 DummyExchange 以避免调用真实交易接口
class DummyExchange:
    def close_market_order(self, symbol, direction, size):
        print(f"DummyExchange.close_market_order called: {symbol}, {direction}, {size}")
        return {"ok": True}

lifecycle.exchange = DummyExchange()
# 提供一个简单的 DummyLogger，满足 logger.log 调用
class DummyLogger:
    def log(self, *args, **kwargs):
        print("DummyLogger.log:", args, kwargs)

lifecycle.logger = DummyLogger()

print("Before manage, position:", portfolio.get_position("BTC/USDT").to_dict())
res = lifecycle.manage_position("BTC/USDT", price=94, atr=1)
print("manage result:", res)
print("After manage, position:", portfolio.get_position("BTC/USDT").to_dict())
