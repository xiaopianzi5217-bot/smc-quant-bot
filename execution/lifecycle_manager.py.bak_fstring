# -*- coding: utf-8 -*-
try:
    from notifier.manager import dispatch_execution_event
except Exception:
    dispatch_execution_event = None


class TradeLifecycleManager:
    def __init__(self, cfg, exchange_adapter, portfolio, logger, notifier=None):
        self.cfg = cfg
        self.exchange = exchange_adapter
        self.portfolio = portfolio
        self.logger = logger
        self.notifier = notifier

    def _safe_close(self, symbol, direction, size):
        size = max(0.0, float(size or 0.0))
        if size <= 0:
            return None
        return self.exchange.close_market_order(symbol, direction, size)

    def manage_position(self, symbol, price, atr=None):
        p = self.portfolio.get_position(symbol)
        if not p or p.state != "OPEN":
            return None

        price = float(price)
        atr = float(atr or 0.0)
        direction = p.direction
        remaining = float(getattr(p, "remaining_size", p.size) or 0.0)
        if remaining <= 0:
            self.portfolio.close_position(symbol)
            return {"event": "CLOSED_EMPTY", "symbol": symbol}

        actions = []

        if direction == "Long":
            hit_sl = price <= p.sl
            hit_tp1 = price >= p.tp1
            hit_tp2 = price >= p.tp2
            hit_tp3 = price >= p.tp3
        else:
            hit_sl = price >= p.sl
            hit_tp1 = price <= p.tp1
            hit_tp2 = price <= p.tp2
            hit_tp3 = price <= p.tp3

        if hit_sl:
            close_size = remaining
            self._safe_close(symbol, direction, close_size)
            self.portfolio.reduce_position(symbol, close_size)
            self.portfolio.close_position(symbol)
            self.portfolio.mark_loss_cooldown(symbol, self.cfg["execution"].get("cooldown_minutes_after_loss", 30))
            self.logger.log("STOP_LOSS", symbol=symbol, direction=direction, price=price, size=close_size, raw=p.to_dict())
            event = {"type": "SL_HIT", "symbol": symbol, "message": "止损触发，已关闭剩余持仓", "raw": p.to_dict()}
            if dispatch_execution_event:
                dispatch_execution_event(event)
            elif self.notifier:
                self.notifier(event["message"])
            return {"event": "STOP_LOSS", "symbol": symbol, "price": price, "closed_size": close_size}

        if hit_tp1 and not p.tp1_done:
            close_size = min(remaining, float(p.size) * float(self.cfg["execution"].get("tp1_close_pct", 0.35)))
            self._safe_close(symbol, direction, close_size)
            self.portfolio.reduce_position(symbol, close_size)
            p.tp1_done = True
            if self.cfg["execution"].get("move_sl_to_be_after_tp1", True):
                p.sl = p.entry
            actions.append(f"TP1 已触发，部分止盈 {close_size}，止损移动到开仓价")
            self.logger.log("TP1", symbol=symbol, direction=direction, price=price, size=close_size, raw=p.to_dict())

        remaining = float(getattr(p, "remaining_size", p.size) or 0.0)
        if hit_tp2 and not p.tp2_done and remaining > 0:
            close_size = min(remaining, float(p.size) * float(self.cfg["execution"].get("tp2_close_pct", 0.35)))
            self._safe_close(symbol, direction, close_size)
            self.portfolio.reduce_position(symbol, close_size)
            p.tp2_done = True
            if self.cfg["execution"].get("trail_after_tp2", True) and atr > 0:
                mult = float(self.cfg["execution"].get("trail_atr_mult", 1.2))
                if direction == "Long":
                    p.sl = max(p.sl, price - atr * mult)
                else:
                    p.sl = min(p.sl, price + atr * mult)
            actions.append(f"TP2 已触发，继续保护利润，部分止盈 {close_size}")
            self.logger.log("TP2", symbol=symbol, direction=direction, price=price, size=close_size, raw=p.to_dict())

        remaining = float(getattr(p, "remaining_size", p.size) or 0.0)
        if hit_tp3 and not p.tp3_done and remaining > 0:
            close_size = remaining
            self._safe_close(symbol, direction, close_size)
            self.portfolio.reduce_position(symbol, close_size)
            p.tp3_done = True
            self.portfolio.close_position(symbol)
            actions.append(f"TP3 已触发，关闭剩余仓位 {close_size}，交易完成")
            self.logger.log("TP3", symbol=symbol, direction=direction, price=price, size=close_size, raw=p.to_dict())

        if actions and hasattr(self.portfolio, "save_state"):
            self.portfolio.save_state()

        if actions:
            msg = "\n".join(actions)
            event_type = "POSITION_CLOSED" if p.tp3_done or p.state == "CLOSED" else "POSITION_REDUCED"
            event = {"type": event_type, "symbol": symbol, "message": msg, "raw": p.to_dict()}
            if dispatch_execution_event:
                dispatch_execution_event(event)
            elif self.notifier:
                self.notifier(msg)
            return {"event": "MANAGE", "symbol": symbol, "actions": actions, "remaining_size": getattr(p, "remaining_size", None)}

        return {"event": "HOLD", "symbol": symbol, "price": price, "remaining_size": remaining}