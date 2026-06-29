# -*- coding: utf-8 -*-
"""订单追踪器 - 为实盘下单准备的执行层组件"""
import time
import threading
from typing import Dict, Optional
from state.position_manager import position_manager

class OrderTracker:
    """追踪交易所订单状态，更新持仓管理器"""
    
    def __init__(self):
        self._active_orders: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self._exchange = None

    def attach_exchange(self, exchange):
        self._exchange = exchange

    def place_order(self, symbol: str, side: str, amount: float,
                    price: Optional[float] = None,
                    order_type: str = 'limit') -> Optional[dict]:
        if self._exchange is None:
            print("[OrderTracker] 未 attach exchange，无法下单")
            return None
        try:
            order = self._exchange.create_order(symbol, order_type, side, amount, price)
            with self._lock:
                self._active_orders[order['id']] = order
            print(f"[OrderTracker] 下单成功: {side} {amount} {symbol}")
            return order
        except Exception as e:
            print(f"[OrderTracker] 下单失败: {e}")
            return None

    def cancel_order(self, order_id: str) -> bool:
        if self._exchange is None:
            return False
        try:
            self._exchange.cancel_order(order_id)
            with self._lock:
                self._active_orders.pop(order_id, None)
            return True
        except Exception as e:
            print(f"[OrderTracker] 撤单失败: {e}")
            return False

    def poll_open_orders(self):
        """轮询所有活跃订单状态，更新持仓"""
        with self._lock:
            for oid in list(self._active_orders.keys()):
                try:
                    order = self._exchange.fetch_order(oid)
                    if order['status'] in ('closed', 'canceled', 'expired'):
                        self._active_orders.pop(oid, None)
                        symbol = order.get('symbol', '')
                        # 成交后更新持仓管理器中的订单ID
                        pos = position_manager.get(symbol)
                        if pos:
                            pos['order_status'] = order['status']
                            pos['filled'] = order.get('filled', 0)
                            position_manager.update(symbol, pos)
                except Exception:
                    pass

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._active_orders)


# 单例
_order_tracker = OrderTracker()

def get_order_tracker() -> OrderTracker:
    return _order_tracker
