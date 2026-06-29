"""
Cluster Engine - V54 核心2: 前置过滤 / Probe 化

加载 cluster_report.csv, 对 bad / fragile 簇进行降权。V54 不再硬阻断
cluster；坏簇进入小仓 probe，避免旧模块在 live/main 路径里绕过新版
AlphaClusterGuard 继续误杀交易。
"""
import os
from typing import Dict, Optional, Set

from core.online_learner import OnlineEVLearner


class ClusterEngine:
    """
    簇过滤引擎 (前置过滤)

    - bad_clusters: 静态黑名单 (小仓 probe)
    - fragile_clusters: 降权 (减少仓位大小和置信度)
    - online_learner: 动态 EV 坏簇 (小仓 probe)
    """

    def __init__(self):
        self.bad_clusters: Set[str] = set()
        self.fragile_clusters: Set[str] = set()
        self._loaded = False
        self.online_learner = OnlineEVLearner()

    def load(self, path: str) -> None:
        """
        从 CSV 加载簇标签。

        CSV 格式要求:
            cluster,label
            HIGH_HIGH_HIGH,BAD
            LOW_LOW_LOW,FRAGILE
            ...

        Args:
            path: CSV 文件路径
        """
        if not os.path.exists(path):
            print(f"[ClusterEngine] [!] 文件不存在: {path}, 跳过加载")
            self._loaded = False
            return

        try:
            import pandas as pd
            df = pd.read_csv(path)

            if "cluster" not in df.columns or "label" not in df.columns:
                print(f"[ClusterEngine] [!] CSV 缺少 cluster 或 label 列: {path}")
                self._loaded = False
                return

            bad = df[df["label"].str.upper() == "BAD"]["cluster"].tolist()
            fragile = df[df["label"].str.upper() == "FRAGILE"]["cluster"].tolist()

            self.bad_clusters = set(bad)
            self.fragile_clusters = set(fragile)
            self._loaded = True

            print(f"[ClusterEngine] [OK] 加载 {len(df)} 条静态规则")
            print(f"   - BAD: {len(self.bad_clusters)} 个簇")
            print(f"   - FRAGILE: {len(self.fragile_clusters)} 个簇")

        except Exception as e:
            print(f"[ClusterEngine] [ERR] 加载失败: {e}")
            self._loaded = False

    def filter(self, signal: Dict) -> Dict:
        """
        对信号进行前置过滤。

        Args:
            signal: {
                "cluster": str,        # 簇标识
                "regime": str,         # 市场状态 (可选, 用于动态 EV)
                "size_factor": float,  # 仓位乘数 (默认 1.0)
                "confidence": float,   # 置信度 (默认 1.0)
            }

        Returns:
            dict: {
                "allow": bool,         # V54 中通常保持 True，靠 size_factor 降权
                "reason": str,         # 阻断原因 (可选)
                "signal": Dict,        # 修改后的信号
            }
        """
        cid = signal.get("cluster", "")
        regime = signal.get("regime", None)
        result_signal = dict(signal)

        # 1. 静态黑名单 - V54 改为小仓 probe，不硬阻断
        if cid in self.bad_clusters:
            result_signal["size_factor"] = result_signal.get("size_factor", 1.0) * 0.30
            result_signal["confidence"] = result_signal.get("confidence", 1.0) * 0.60
            return {"allow": True, "reason": "STATIC_BAD_CLUSTER_PROBE", "signal": result_signal}

        # 2. 动态 EV 坏簇 - V54 改为小仓 probe，不硬阻断
        if self.online_learner.is_cluster_bad(cid, regime):
            result_signal["size_factor"] = result_signal.get("size_factor", 1.0) * 0.30
            result_signal["confidence"] = result_signal.get("confidence", 1.0) * 0.60
            return {"allow": True, "reason": "DYNAMIC_EV_PROBE", "signal": result_signal}

        # 3. 脆弱簇 - 降低仓位和置信度
        if cid in self.fragile_clusters:
            result_signal["size_factor"] = result_signal.get("size_factor", 1.0) * 0.4
            result_signal["confidence"] = result_signal.get("confidence", 1.0) * 0.7

        return {"allow": True, "reason": "PASS", "signal": result_signal}