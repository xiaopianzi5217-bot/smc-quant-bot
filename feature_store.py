# -*- coding: utf-8 -*-
"""Feature Store — 交易特征存储与统计

V38.x 系列：
  • V38.0  开单时保存特征 snapshot
  • V38.1  持仓追踪中更新 MFE/MAE，止损/止盈时保存完整记录
  • V38.4  EV 校准统计
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from datetime import datetime

import pandas as pd

logger = logging.getLogger("FeatureStore")


class FeatureStore:
    """交易特征存储与统计。

    工作流程：
      开单时  → save_trade({exit_reason: "OPEN", ...})
      追踪中  → save_trade({exit_reason: "TRAIL", mfe/mae/max_r 更新})
      止盈/损 → save_trade({exit_reason: "TP/SL", pnl_r, ...})
      定时    → update_ev_statistics()  更新 JSON 统计
    """

    def __init__(self):
        self.data_dir = Path("data/features")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.trades_file = self.data_dir / "trades_features.csv"
        self.ev_stats_file = self.data_dir / "ev_statistics.json"
        self._init_csv()

    # ------------------------------------------------------------------
    #  初始化 CSV（仅首次）
    # ------------------------------------------------------------------
    def _init_csv(self):
        if self.trades_file.exists():
            return
        columns = [
            "timestamp", "symbol", "direction",
            "entry", "sl", "tp1", "tp2", "tp3", "rr",
            "ev", "score",
            "regime", "regime2", "book",
            "adx", "atr",
            "div_count",
            "signal_age",
            "mfe", "mae", "max_r", "max_r_before_stop",
            "exit_reason", "pnl_r",
            "weekday", "hour",
            "signal_tier", "score_raw", "entry_price_level",
        ]
        pd.DataFrame(columns=columns).to_csv(self.trades_file, index=False)
        logger.info("FeatureStore CSV 已初始化")

    # ------------------------------------------------------------------
    #  保存 / 更新一条交易记录
    # ------------------------------------------------------------------
    def save_trade(self, data: dict):
        """保存单条交易特征。如果相同 symbol+direction 的 OPEN 记录已存在，
        则使用 exit_reason 字段区分：OPEN→新增，其他→更新。
        """
        data.setdefault("timestamp", datetime.now().isoformat())

                # 如果是 OPEN 记录，直接追加
        if data.get("exit_reason") in ("OPEN", None):
            # 先读已有 CSV，确保列顺序一致
            existing = self.load_history()
            if existing.empty:
                df = pd.DataFrame([data])
            else:
                df = pd.concat([existing, pd.DataFrame([data])], ignore_index=True)
                df = df[existing.columns]  # 对齐列顺序
            df.to_csv(self.trades_file, index=False)
            symbol = data.get("symbol", "?")
            logger.info(f"[Feature] 开单记录: {symbol} {data.get('direction', '?')}")
            return

        # 非 OPEN 记录（TP/SL/TRAIL）：尝试更新已有 OPEN 记录
        df = self.load_history()
        if df.empty:
            logger.warning("[Feature] 历史为空，无法更新非 OPEN 记录")
            return

        symbol = data.get("symbol", "")
        direction = data.get("direction", "")
        # 找同一 symbol+direction 的最后一个 OPEN 记录
        mask = (df["symbol"] == symbol) & (df["direction"] == direction) & (df["exit_reason"] == "OPEN")
        if not mask.any():
            # 没有 OPEN 记录就追加
            df2 = pd.DataFrame([data])
            df2.to_csv(self.trades_file, mode="a", header=False, index=False)
            return

                # 更新最后一条 OPEN 记录
        last_open_idx = df[mask].index[-1]
        # 如果 data 中有新列（如 giveback_ratio），先加到 df
        for col in data:
            if col not in df.columns:
                df[col] = None
        # 更新值
        for col in data:
            if col != "timestamp":
                df.at[last_open_idx, col] = data[col]
        df.at[last_open_idx, "timestamp"] = data.get("timestamp", datetime.now().isoformat())

        # 回写 CSV
        df.to_csv(self.trades_file, index=False)
        logger.info(f"[Feature] 更新记录: {symbol} {direction} → {data.get('exit_reason', '?')}")

    # ------------------------------------------------------------------
    #  加载全部历史
    # ------------------------------------------------------------------
    def load_history(self) -> pd.DataFrame:
        if self.trades_file.exists():
            return pd.read_csv(self.trades_file)
        return pd.DataFrame()

    # ------------------------------------------------------------------
    #  更新 EV 统计（V38.4）
    # ------------------------------------------------------------------
    def update_ev_statistics(self):
        df = self.load_history()
        if len(df) < 3:
            return

        closed = df[df["exit_reason"].isin(["SL", "TP1", "TP2", "TP3", "TRAIL"])].copy()
        if len(closed) < 3:
            return

        stats = {
            "total_trades": int(len(df)),
            "closed_trades": int(len(closed)),
            "win_rate": float(round((closed["pnl_r"] > 0).mean(), 4)),
            "avg_ev": float(round(df["ev"].mean(), 4)),
            "realized_ev": float(round(closed["pnl_r"].mean(), 4)),
            "last_updated": datetime.now().isoformat(),
        }

        # 分 regime 统计
        if "regime" in df.columns:
            by_regime = df.groupby("regime")["pnl_r"].mean()
            stats["by_regime"] = {str(k): float(round(v, 4)) for k, v in by_regime.items() if pd.notna(v)}

        with open(self.ev_stats_file, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)

        logger.info(f"[Feature] EV 统计已更新 ({stats['closed_trades']} 笔已平仓)")


# 全局单例
feature_store = FeatureStore()

