# -*- coding: utf-8 -*-
"""
OutcomeLearner — V38 结果学习器

封装 OutcomeDatabase + FeatureHash，供外部调用。
"""

from analytics.outcome_db import OutcomeDatabase
from analytics.feature_hash import generate_feature_hash
from typing import Dict, Any, Optional
from pathlib import Path
import json
import time


class OutcomeLearner:
    def __init__(self):
        self.db = OutcomeDatabase()

    def update_from_trade(self, feature: Dict[str, Any], realized_r: float, mode: str = "NORMAL", learning_version: str | None = None):
        """Update from a single trade.

        learning_version: optional string identifying the learner version (e.g., "58.9").
        """
        if not feature:
            return
        feature_hash = generate_feature_hash(feature)
        self.db.update(feature_hash, realized_r, mode=mode)

        # 记录学习运行元信息（轻量），用于后续比较不同 learning_version 的表现
        try:
            meta_path = Path("storage/learning_runs.json")
            meta_path.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            if meta_path.exists():
                try:
                    data = json.loads(meta_path.read_text(encoding='utf-8'))
                except Exception:
                    data = {}
            if learning_version:
                rec = data.get(learning_version, {"count": 0})
                rec["count"] = rec.get("count", 0) + 1
                rec["last_update"] = time.time()
                data[learning_version] = rec
                meta_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception:
            pass

    def get_stats(self, feature: Dict[str, Any], min_trades: int = 15) -> Optional[Dict[str, Any]]:
        if not feature:
            return None
        feature_hash = generate_feature_hash(feature)
        return self.db.get_ev(feature_hash, min_trades)
