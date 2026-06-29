"""
Feature Engine — 特征提取（V52保留）

从原始市场数据中提取 momentum、volatility、compression、direction 等特征。
"""
import numpy as np
from typing import Any, Dict, List


class FeatureEngine:
    """
    特征引擎

    将原始市场数据转换为 ML 友好的特征向量。
    含 direction 字段（用于 DependencyGraph 的方向隔离）。
    """

    def extract(self, data: Any) -> Dict[str, any]:
        """
        从市场数据中提取特征。

        Args:
            data: 包含 OHLCV 及指标的市场数据对象/字典
                   如果 data 是 {"window": [...]}, 则从最后一条提取

        Returns:
            dict: {
                "momentum": float,       # 动量特征 (0~1)
                "volatility": float,     # 波动率特征 (0~1)
                "compression": float,    # 压缩分数 (0~1)
                "trend_strength": float, # 趋势强度 (0~1)
                "direction": str,        # 方向 (Long/Short/NEUTRAL)
                "cluster": str,          # 簇标识
            }
        """
        # 如果 data 包含 window 字段, 从最后一条提取
        if isinstance(data, dict) and "window" in data and data["window"]:
            data = data["window"][-1]

        def _get(key: str, default: float = 0.0) -> float:
            """从 dict 或对象中获取数值"""
            if isinstance(data, dict):
                val = data.get(key, default)
            else:
                val = getattr(data, key, default)
            try:
                return float(val)
            except (ValueError, TypeError):
                return float(default)

        # ===== 动量 =====
        mom_raw = _get("momentum", 0.0)
        momentum = max(0.0, min(1.0, (mom_raw + 1.0) / 2.0))

        # ===== 波动率 =====
        vol_raw = _get("volatility", 0.5)
        volatility = max(0.0, min(1.0, vol_raw))

        # ===== 压缩 =====
        compression = _get("compression", _get("compression_score", 0.0))
        compression = float(np.clip(compression, 0.0, 1.0))

        # ===== 趋势强度 =====
        trend_raw = _get("trend_strength", _get("adx", 25.0))
        if trend_raw > 1.0:
            trend_strength = max(0.0, min(1.0, trend_raw / 100.0))
        else:
            trend_strength = max(0.0, min(1.0, trend_raw))

        # ===== 方向 (字符串) =====
        if isinstance(data, dict):
            direction = data.get("direction", "NEUTRAL")
        else:
            direction = getattr(data, "direction", "NEUTRAL")

        # ===== 簇标识 (字符串) =====
        if isinstance(data, dict):
            cluster = data.get("cluster", self._build_cluster_id(momentum, volatility, compression))
        else:
            cluster = getattr(data, "cluster", self._build_cluster_id(momentum, volatility, compression))

        return {
            "momentum": momentum,
            "volatility": volatility,
            "compression": compression,
            "trend_strength": trend_strength,
            "direction": str(direction),
            "cluster": str(cluster),
        }

    def _build_cluster_id(self, momentum: float, volatility: float, compression: float) -> str:
        """根据特征构建默认簇标识。"""
        mom_level = "HIGH" if momentum > 0.6 else "LOW"
        vol_level = "HIGH" if volatility > 0.5 else "LOW"
        comp_level = "HIGH" if compression > 0.5 else "LOW"
        return f"{mom_level}_{vol_level}_{comp_level}"

    def extract_window(self, data: Any, window_size: int = 30) -> np.ndarray:
        """
        从数据中提取窗口特征 (用于 HMM 输入)。

        Args:
            data: 市场数据, 支持:
                  1. dict 含 "window" 字段 (列表)
                  2. 单条 dict (自动补全到 window_size)
            window_size: 窗口大小 (默认 30)

        Returns:
            np.ndarray: shape (window_size, n_features)
                        n_features = [momentum, volatility]
        """
        if isinstance(data, dict) and "window" in data:
            # 从窗口数据提取
            raw_window = data["window"]
            if not raw_window:
                # 空窗口, 返回默认
                return np.zeros((window_size, 2))

            features_list = []
            for row in raw_window:
                f = self.extract(row)
                features_list.append([
                    f["momentum"],
                    f["volatility"],
                ])

            arr = np.array(features_list, dtype=float)

            # 如果不足 window_size, 向前填充
            if len(arr) < window_size:
                pad = np.tile(arr[0:1], (window_size - len(arr), 1))
                arr = np.vstack([pad, arr])

            # 只取最近 window_size 条
            return arr[-window_size:]

        else:
            # 单条数据, 复制到 window_size
            f = self.extract(data)
            row = np.array([[f["momentum"], f["volatility"]]], dtype=float)
            return np.tile(row, (window_size, 1))