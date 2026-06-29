# -*- coding: utf-8 -*-
"""线程安全的全局持仓管理器"""
import threading

class PositionManager:
    def __init__(self):
        self._positions = {}
        self._lock = threading.Lock()

    def update(self, symbol: str, pos: dict):
        with self._lock:
            self._positions[symbol] = pos

    def get(self, symbol: str = None):
        with self._lock:
            if symbol:
                return self._positions.get(symbol)
            return dict(self._positions)

    def remove(self, symbol: str):
        with self._lock:
            self._positions.pop(symbol, None)

    def exists(self, symbol: str) -> bool:
        with self._lock:
            return symbol in self._positions

    def all_symbols(self) -> list:
        with self._lock:
            return list(self._positions.keys())

    def __len__(self):
        with self._lock:
            return len(self._positions)

    def __repr__(self):
        with self._lock:
            return repr(self._positions)

# 单例
position_manager = PositionManager()
