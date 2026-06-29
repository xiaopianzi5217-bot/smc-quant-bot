"""
OnlineEVLearner — 在线学习器 (V53.2)

根据实际盈亏动态调整每个簇的期望值 (EV)，
实现自学习 + concept drift 检测。

核心机制:
  1. 每个 cluster 维护一个 EV (期望值) 和交易次数
  2. 使用 EMA (指数移动平均) 更新 EV
  3. tanh 归一化 PNL 防止极端值影响
  4. 冷启动保护 (不足10笔交易的簇置信度打折)
  5. 持久化到 JSON 文件，重启不丢失记忆
"""
import json
import os
import numpy as np
from typing import Optional


class OnlineEVLearner:
    """
    在线期望值学习器

    每个 cluster 维护:
        - ev: 期望值 (指数移动平均)
        - trades: 交易次数 (用于冷启动保护)

    用法:
        learner = OnlineEVLearner(memory_file="data/dynamic_ev_memory.json")

        # 每次平仓后调用
        learner.update_cluster("TRANSITION_Long_C2", realized_pnl=-0.05, regime="TRANSITION")

        # 在 ClusterEngine.filter 中调用
        if learner.is_cluster_bad("TRANSITION_Long_C2", regime="TRANSITION"):
            # 拒绝交易
    """

    def __init__(self, memory_file: str = "data/dynamic_ev_memory.json", alpha: float = 0.15):
        """
        初始化在线学习器。

        Args:
            memory_file: JSON 记忆文件路径
            alpha: EMA 平滑系数 (0~1, 越大对新数据越敏感)
        """
        self.memory_file = memory_file
        self.alpha = alpha
        self.memory = self._load_memory()

    def _load_memory(self) -> dict:
        """从 JSON 文件加载记忆。"""
        if os.path.exists(self.memory_file):
            try:
                with open(self.memory_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                print(f"[OnlineEVLearner] [!] 记忆文件损坏, 重置: {self.memory_file}")
                return {}
        return {}

    def _save_memory(self) -> None:
        """保存记忆到 JSON 文件。"""
        os.makedirs(os.path.dirname(self.memory_file), exist_ok=True)
        with open(self.memory_file, 'w', encoding='utf-8') as f:
            json.dump(self.memory, f, indent=4, ensure_ascii=False)

    def _normalize_pnl(self, pnl: float) -> float:
        """
        使用 tanh 稳定化 PNL。

        将任意范围的 PNL 映射到 (-1, 1)，
        对极端值具有天然抗性。
        """
        return float(np.tanh(pnl))

    def update_cluster(self, cluster_id: str, realized_pnl: float, regime: Optional[str] = None) -> float:
        """
        更新指定簇的期望值 (EV)。

        Args:
            cluster_id: 簇标识 (如 "TRANSITION_Long_C2")
            realized_pnl: 实际盈亏 (如 -0.05 表示 -5%)
            regime: 市场状态 (可选, 用于组合 key)

        Returns:
            float: 更新后的期望值
        """
        key = f"{regime}_{cluster_id}" if regime else cluster_id

        # 初始化新簇
        if key not in self.memory:
            self.memory[key] = {"ev": 0.1, "trades": 0}

        old_ev = self.memory[key]["ev"]

        # tanh 归一化 PNL
        pnl_norm = self._normalize_pnl(realized_pnl)

        # 指数移动平均 (EMA)
        new_ev = (1 - self.alpha) * old_ev + self.alpha * pnl_norm

        self.memory[key]["ev"] = round(new_ev, 4)
        self.memory[key]["trades"] += 1

        # 持久化
        self._save_memory()

        return new_ev

    def is_cluster_bad(self, cluster_id: str, regime: Optional[str] = None,
                       bad_threshold: float = -0.05) -> bool:
        """
        判断簇是否应该被标记为 BAD (动态 EV 熔断)。

        Args:
            cluster_id: 簇标识
            regime: 市场状态 (可选)
            bad_threshold: EV 低于此阈值视为 BAD (默认 -0.05)

        Returns:
            bool: True 表示该簇表现差, 应拒绝交易
        """
        key = f"{regime}_{cluster_id}" if regime else cluster_id

        if key not in self.memory:
            return False

        data = self.memory[key]
        ev = data["ev"]
        trades = data["trades"]

        # 冷启动保护: 交易次数不足时打折
        confidence = min(1.0, trades / 10)
        effective_ev = ev * confidence

        # 至少需要 3 笔交易才触发动态熔断
        if trades >= 3:
            return effective_ev < bad_threshold

        return False

    def get_cluster_stats(self, cluster_id: str, regime: Optional[str] = None) -> Optional[dict]:
        """
        获取簇的统计信息。

        Returns:
            dict | None: {"ev": float, "trades": int} 或 None
        """
        key = f"{regime}_{cluster_id}" if regime else cluster_id
        return self.memory.get(key, None)