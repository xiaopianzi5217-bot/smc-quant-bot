import pathlib

path = pathlib.Path("core/alpha_master_engine.py")
content = path.read_bytes()

old = b"from strategy.scorecard_system import evaluate_base_trigger, build_scorecard, dumps_compact\nfrom cluster_engine import ClusterEngineV38\ntry:"

new = b"""from strategy.scorecard_system import evaluate_base_trigger, build_scorecard, dumps_compact

# Inline lightweight ClusterEngineV38 (was from cluster_engine import ClusterEngineV38)
class ClusterEngineV38:
    def compute_weights(self, clusters: list) -> list:
        import numpy as np
        scores = []
        for c in clusters:
            s = (c.get("mean_r", 0) * 10 + c.get("win_rate", 0) * 5 +
                 min(1.0, c.get("trades", 0) / 100) * 3 +
                 c.get("stability", 0.5) * 4 -
                 c.get("max_dd", 0) * 2)
            scores.append(max(0.0, s))
        arr = np.array(scores, dtype=float)
        max_val = arr.max()
        exp = np.exp(arr - max_val) if max_val > -np.inf else np.ones_like(arr)
        total = exp.sum()
        if total <= 0:
            return [0.25] * len(clusters)
        return (exp / total).tolist()

try:"""

if old in content:
    content = content.replace(old, new, 1)
    path.write_bytes(content)
    print("SUCCESS: Replaced ClusterEngineV38 import with inline class")
else:
    print("ERROR: Old text not found!")
    # Debug
    idx = content.find(b"cluster_engine")
    if idx >= 0:
        start = max(0, idx - 80)
        end = min(len(content), idx + 60)
        print(f"Found at byte {idx}:")
        print(content[start:end])
    else:
        print("'cluster_engine' not found in file at all!")
