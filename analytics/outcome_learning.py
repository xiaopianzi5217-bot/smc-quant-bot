# -*- coding: utf-8 -*-
"""
OutcomeLearner — V38 结果学习器

封装 OutcomeDatabase + FeatureHash，供外部调用。
"""

from analytics.outcome_db import OutcomeDatabase
from analytics.feature_hash import generate_feature_hash
from typing import Dict, Any, Optional


class OutcomeLearner:
    def __init__(self):
        self.db = OutcomeDatabase()

    def update_from_trade(self, feature: Dict[str, Any], realized_r: float):
        if not feature:
            return
        feature_hash = generate_feature_hash(feature)
        self.db.update(feature_hash, realized_r)

    def get_stats(self, feature: Dict[str, Any], min_trades: int = 15) -> Optional[Dict[str, Any]]:
        if not feature:
            return None
        feature_hash = generate_feature_hash(feature)
        return self.db.get_ev(feature_hash, min_trades)
