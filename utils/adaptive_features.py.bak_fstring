# -*- coding: utf-8 -*-
"""
Adaptive feature weighter module.
Dynamically adjusts signal feature weights based on historical P&L.
"""
import json
import os


class AdaptiveFeatureWeighter:
    """Adaptive feature weighting based on historical P&L."""

    def __init__(self, window=200, save_path="data/feature_stats.json"):
        self.window = window
        self.save_path = save_path
        self.feature_stats = self._load_stats()
        self.history = []
        self.samples = {}

    def _load_stats(self):
        """Load stats from disk, return defaults if file missing."""
        if os.path.exists(self.save_path):
            try:
                with open(self.save_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "OB": {"wins": 0, "trades": 0, "avg_r": 0.0, "weight": 1.15},
            "FVG": {"wins": 0, "trades": 0, "avg_r": 0.0, "weight": 1.0},
            "CHOCH": {"wins": 0, "trades": 0, "avg_r": 0.0, "weight": 1.12},
            "SQZMOM": {"wins": 0, "trades": 0, "avg_r": 0.0, "weight": 1.25},
            "DIVERGENCE": {"wins": 0, "trades": 0, "avg_r": 0.0, "weight": 1.35},
            "LIQUIDITY": {"wins": 0, "trades": 0, "avg_r": 0.0, "weight": 1.00},
            "VOLATILITY": {"wins": 0, "trades": 0, "avg_r": 0.0, "weight": 1.00},
            "REGIME": {"wins": 0, "trades": 0, "avg_r": 0.0, "weight": 1.00},
            "VWAP": {"wins": 0, "trades": 0, "avg_r": 0.0, "weight": 1.00},
        }

    def update(self, features, outcome_r):
        """Update feature stats with a trade outcome.

        Args:
            features: List of activated feature names
            outcome_r: Final P&L in R-multiple
        """
        if abs(outcome_r) >= 0.2:
            self.history.append((features, outcome_r))
            if len(self.history) > self.window:
                self.history.pop(0)
            for feat in features:
                if feat not in self.feature_stats:
                    continue
                s = self.feature_stats[feat]
                self.samples[feat] = self.samples.get(feat, 0) + 1
                if self.samples[feat] < 30:
                    continue
                s["trades"] += 1
                if outcome_r > 0.2:
                    s["wins"] += 1
                prev_total = s.get("avg_r", 0) * (s["trades"] - 1)
                s["avg_r"] = (prev_total + outcome_r) / s["trades"]
                win_rate = s["wins"] / s["trades"] if s["trades"] > 0 else 0.5
                new_weight = 0.6 * s.get("weight", 1.0) + 0.4 * (win_rate * 1.8 + s["avg_r"] * 0.8)
                _new_features = {"LIQUIDITY", "VOLATILITY", "REGIME", "VWAP"}
                if feat in _new_features:
                    new_weight = max(0.85, min(new_weight, 1.15))
                else:
                    new_weight = max(0.70, min(new_weight, 1.30))
                s["weight"] = new_weight
        self._save_stats()

    def _save_stats(self):
        """Persist feature stats to disk."""
        try:
            with open(self.save_path, 'w', encoding='utf-8') as f:
                json.dump(self.feature_stats, f, indent=2)
        except Exception:
            pass

    def get_weighted_score(self, raw_scores):
        """Calculate weighted total score."""
        total = 0.0
        factor = 1.0
        for feat, value in raw_scores.items():
            weight = self.feature_stats.get(feat, {}).get("weight", 1.0)
            total += value * weight
            factor *= weight
        factor = max(0.85, min(factor, 1.15))
        return round(total * factor, 2)

    def get_weight(self, feature):
        """Get current weight for a single feature."""
        return self.feature_stats.get(feature, {}).get("weight", 1.0)


# ===== Module-level convenience functions (for V56.5 Engine) =====
_weighter = AdaptiveFeatureWeighter()


def get_weight(feature):
    """Module-level: get current weight for a feature."""
    return _weighter.get_weight(feature)


def update_feature(feature, outcome_r):
    """Module-level: update a feature's weight with trade outcome."""
    _weighter.update([feature], outcome_r)


feature_weighter = _weighter
