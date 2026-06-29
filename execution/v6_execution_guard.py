# -*- coding: utf-8 -*-
class V6ExecutionGuard:
    def __init__(self, params=None):
        self.params = params or {}

    def check(self, curr, direction, recent_trades=None, bar_index=None):
        return {'allowed': True, 'reason_cn': '执行检查通过', 'direction': direction}
