# -*- coding: utf-8 -*-
import time

class AlertDeduper:
    def __init__(self, cooldown_sec=600):
        self.cooldown_sec = int(cooldown_sec)
        self.cache = {}

    def allow(self, symbol, signal_type, level=None):
        key = (symbol, signal_type, level)
        now = time.time()
        last = self.cache.get(key, 0)
        if now - last < self.cooldown_sec:
            return False
        self.cache[key] = now
        return True
