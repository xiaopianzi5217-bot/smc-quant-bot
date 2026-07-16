# -*- coding: utf-8 -*-
"""Feature Pipeline — 从 TradeJournal + FeatureStore 提取训练样本。

输入：
  logs/trade_journal.csv  →  90 笔 OPEN + 未来 CLOSE
  logs/feature_store.json  →  每笔交易的 features

输出：
  DataFrame 格式训练集：
    smc_quality, fvg_strength, ob_strength, volume_ratio, atr_pct,
    regime_encoded, sqzmom_state, vwap_distance, adx, rsi, entry_hour,
    weekday, label (win=1 / loss=0)
"""
from __future__ import annotations

import csv
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

logger = logging.getLogger("FeaturePipeline")

JOURNAL_PATH = Path("logs/trade_journal.csv")
FEATURE_STORE_PATH = Path("data/feature_store.json")


class FeaturePipeline:
    """特征工程管线：从原始日志提取 ML 训练集。"""

    def __init__(self):
        self._cache: Optional[pd.DataFrame] = None

    # ════════════════════════════════════════════════════════════
    # 公共方法
    # ════════════════════════════════════════════════════════════

    def build_training_set(self, min_samples: int = 50) -> Optional[pd.DataFrame]:
        """构建完整训练集。

        流程：
          1. 从 TradeJournal 加载已平仓交易
          2. 从 FeatureStore 补充 features
          3. 构建特征 + label 矩阵
          4. 返回 DataFrame 或 None（样本不足）

        Args:
            min_samples: 最少样本数，不足则返回 None

        Returns:
            DataFrame 或 None
        """
        closes = self._load_closed_trades()
        if len(closes) < min_samples:
            logger.info(f"训练样本不足: {len(closes)} < {min_samples}")
            return None

        features = self._load_feature_store()
        df = self._merge_features(closes, features)
        df = self._engineer_features(df)
        df = self._create_labels(df)

        logger.info(f"训练集构建完成: {len(df)} 样本, {len(df.columns)} 列")
        self._cache = df
        return df

    def get_live_features(self, exec_ctx: dict, curr_row: dict,
                          regime: str, features_dict: dict) -> pd.DataFrame:
        """为实时决策构建单行特征向量。

        Args:
            exec_ctx:   build_exec_context 输出
            curr_row:   df_exec.iloc[-1] 的字典
            regime:     "TREND" / "CHOP" / "TRANSITION"
            features_dict: scan_and_decide 中构建的 _features 字典

        Returns:
            单行 DataFrame（与 build_training_set 相同的列结构）
        """
        row = self._single_sample(exec_ctx, curr_row, regime, features_dict)
        df = pd.DataFrame([row])

        # 确保列与训练集一致
        if self._cache is not None:
            train_cols = [c for c in self._cache.columns if c != "label"]
            for col in train_cols:
                if col not in df.columns:
                    df[col] = 0.0
            df = df[train_cols]

        return df

    def get_feature_columns(self) -> List[str]:
        """返回特征列名列表（不含 label）。"""
        if self._cache is not None:
            return [c for c in self._cache.columns if c != "label"]
        # 默认特征列
        return [
            "smc_quality", "fvg_strength", "ob_strength",
            "volume_ratio", "atr_pct",
            "regime_trend", "regime_chop", "regime_transition",
            "sqzmom_state",
            "vwap_distance", "adx", "rsi",
            "entry_hour", "weekday",
        ]

    # ════════════════════════════════════════════════════════════
    # 内部方法 - 数据加载
    # ════════════════════════════════════════════════════════════

    def _load_closed_trades(self) -> List[Dict[str, Any]]:
        """从 TradeJournal 加载已平仓交易。"""
        if not JOURNAL_PATH.exists():
            return []

        closes = []
        with open(JOURNAL_PATH, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("status") == "CLOSE":
                    closes.append(dict(row))

        logger.info(f"TradeJournal 已平仓: {len(closes)} 笔")
        return closes

    def _load_feature_store(self) -> Dict[str, Dict[str, Any]]:
        """从 FeatureStore 加载特征数据。"""
        if not FEATURE_STORE_PATH.exists():
            return {}

        try:
            data = json.loads(FEATURE_STORE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                # List[dict] 格式 → 按 order_id 索引
                return {d.get("order_id", ""): d for d in data if d.get("order_id")}
            logger.info(f"FeatureStore 加载: {len(data)} 条")
            return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.error(f"FeatureStore 加载失败: {e}")
            return {}

    def _merge_features(self, closes: List[Dict], features: Dict) -> pd.DataFrame:
        """合并平仓记录与特征存储。"""
        records = []
        for c in closes:
            oid = c.get("order_id", "")
            feat = features.get(oid, {})
            record = {
                "order_id": oid,
                "symbol": c.get("symbol", ""),
                "direction": c.get("direction", ""),
                "pnl_r": self._safe_float(c.get("pnl_r", 0)),
                "exit_reason": c.get("exit_reason", ""),
                "open_time": c.get("open_time", ""),
                "close_time": c.get("close_time", ""),
                "open_price": self._safe_float(c.get("open_price", 0)),
                "close_price": self._safe_float(c.get("close_price", 0)),
                "volume": self._safe_float(c.get("volume", 0)),
                "score": self._safe_float(c.get("score", 0)),
                "regime": c.get("regime", ""),
                # FeatureStore 字段
                "adx": self._safe_float(feat.get("adx", 0)),
                "atr": self._safe_float(feat.get("atr", 0)),
                "rsi": self._safe_float(feat.get("rsi", 0)),
                "volume_ratio": self._safe_float(feat.get("volume_ratio", 1)),
                "div_count": int(feat.get("div_count", 0)),
                "signal_age": int(feat.get("signal_age", 0)),
                "mfe": self._safe_float(feat.get("mfe", 0)),
                "mae": self._safe_float(feat.get("mae", 0)),
                "entry_price_level": feat.get("entry_price_level", ""),
            }
            records.append(record)

        df = pd.DataFrame(records)

        # 清理无效行
        if "pnl_r" in df.columns:
            df = df[df["pnl_r"].notna() & (df["pnl_r"] != 0)]

        return df

    # ════════════════════════════════════════════════════════════
    # 内部方法 - 特征工程
    # ════════════════════════════════════════════════════════════

    def _engineer_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """从原始数据衍生 ML 特征。"""
        df = df.copy()

        # 1. atr_pct = atr / open_price（波动率归一化）
        df["atr_pct"] = np.where(
            df["open_price"] > 0,
            df["atr"] / df["open_price"],
            0.0,
        )

        # 2. smc_quality — 从 entry_price_level 提取
        #    bsl=xxx_ssl=xxx → 有 BSL/SSL 数据表示结构质量好
        df["smc_quality"] = df["entry_price_level"].apply(
            lambda x: 1.0 if x and ("bsl" in str(x) or "ssl" in str(x)) else 0.5
        )

        # 3. fvg_strength / ob_strength — 从 FeatureStore 的额外字段推断
        #    暂用 div_count 作为结构信号强度的代理
        df["fvg_strength"] = np.clip(df["div_count"] * 0.3, 0, 1)
        df["ob_strength"] = np.clip(df["div_count"] * 0.2, 0, 1)

        # 4. regime 独热编码
        for reg in ["TREND", "CHOP", "TRANSITION", "MUD", "CRISIS_RISK_OFF"]:
            df[f"regime_{reg.lower()}"] = (
                df["regime"].str.upper().str.strip() == reg
            ).astype(float)

        # 5. sqzmom_state — 从 signal_age 近似（>0 表示有信号）
        df["sqzmom_state"] = (df["signal_age"] > 0).astype(float)

        # 6. vwap_distance — 从 entry_price_level 提取
        #    没有 VWAP 数据时用 0
        df["vwap_distance"] = 0.0

        # 7. entry_hour — 从 open_time 提取
        df["entry_hour"] = pd.to_numeric(
            df["open_time"].str[11:13], errors="coerce"
        ).fillna(12).astype(int)

        # 8. weekday
        df["weekday"] = pd.to_numeric(
            pd.to_datetime(df["open_time"], errors="coerce").dt.weekday,
            errors="coerce",
        ).fillna(0).astype(int)

        # 9. 填充缺失值
        fill_cols = [
            "smc_quality", "fvg_strength", "ob_strength",
            "volume_ratio", "atr_pct",
            "vwap_distance", "adx", "rsi",
        ]
        for col in fill_cols:
            if col in df.columns:
                df[col] = df[col].fillna(0.0)

        return df

    def _create_labels(self, df: pd.DataFrame) -> pd.DataFrame:
        """根据 pnl_r 创建分类标签。

        label = 1 (win):  pnl_r > 0.2（扣除交易成本后的正收益）
        label = 0 (loss): pnl_r <= 0.2
        """
        df = df.copy()
        df["label"] = (df["pnl_r"] > 0.2).astype(int)

        # 行权：去掉 pnl_r 极端异常值（超过 ±5R）
        df = df[(df["pnl_r"] >= -5.0) & (df["pnl_r"] <= 5.0)]

        logger.info(f"标签分布: 胜={df['label'].sum()} 负={(1-df['label']).sum()} "
                     f"总={len(df)}")
        return df

    # ════════════════════════════════════════════════════════════
    # 内部方法 - 实时推理单行
    # ════════════════════════════════════════════════════════════

    def _single_sample(self, exec_ctx: dict, curr: dict,
                       regime: str, feats: dict) -> Dict[str, float]:
        """从实时上下文构建单行特征向量。"""
        open_price = float(curr.get("close", exec_ctx.get("close", 0)))
        atr_val = float(curr.get("ATRr_14", exec_ctx.get("atr", 0)))
        adx_val = float(exec_ctx.get("adx", curr.get("adx", 0)))
        rsi_val = float(curr.get("rsi", 50))

        row = {
            "smc_quality": float(feats.get("structure_break", False)),
            "fvg_strength": float(
                bool(exec_ctx.get("bullish_fvg") or exec_ctx.get("bearish_fvg"))
            ),
            "ob_strength": float(
                bool(exec_ctx.get("bullish_ob") or exec_ctx.get("bearish_ob"))
            ),
            "volume_ratio": float(curr.get("volume_ratio", 1)),
            "atr_pct": atr_val / max(open_price, 1e-8),
            "vwap_distance": 0.0,
            "adx": adx_val,
            "rsi": rsi_val,
            "entry_hour": datetime.now().hour,
            "weekday": datetime.now().weekday(),
            "sqzmom_state": float(
                "squeeze" in str(exec_ctx.get("squeeze", "")).lower()
                or bool(exec_ctx.get("sqzmom_white_reversal_long", False))
                or bool(exec_ctx.get("sqzmom_white_reversal_short", False))
            ),
        }

        # regime 独热编码
        _reg = str(regime).upper().strip()
        for reg_key in ["TREND", "CHOP", "TRANSITION", "MUD", "CRISIS_RISK_OFF"]:
            row[f"regime_{reg_key.lower()}"] = float(_reg == reg_key)

        return row

    # ════════════════════════════════════════════════════════════
    # 工具
    # ════════════════════════════════════════════════════════════

    @staticmethod
    def _safe_float(val, default: float = 0.0) -> float:
        try:
            return float(val) if val is not None and val != "" else default
        except (ValueError, TypeError):
            return default


# 全局单例
_feature_pipeline: Optional[FeaturePipeline] = None


def get_feature_pipeline() -> FeaturePipeline:
    global _feature_pipeline
    if _feature_pipeline is None:
        _feature_pipeline = FeaturePipeline()
    return _feature_pipeline
