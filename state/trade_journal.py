# -*- coding: utf-8 -*-
"""Trade Journal — 订单全生命周期日志（开仓→持仓→平仓）。

与 FeatureStore 的区别：
  FeatureStore     → EV 校准、策略特征研究，按 exit_reason 更新同一行
  TradeJournal     → 订单审计、盈亏复盘，每次开仓一行、平仓一行（不可变追加）

文件位置：logs/trade_journal.csv
格式：每条记录不可变，平仓通过新行记录 open/close 状态
"""
from __future__ import annotations

import csv
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("TradeJournal")

FIELD_NAMES = [
    "order_id",          # UUID 或 递增 ID
    "symbol",            # BTC/USDT
    "direction",         # Long / Short
    "status",            # OPEN / CLOSE
    "open_time",         # 开仓时间
    "open_price",        # 入场价
    "sl",                # 止损
    "tp1",               # TP1
    "tp2",               # TP2
    "tp3",               # TP3
    "rr",                # 预期赔率 (tp2)
    "score",             # 开仓评分
    "regime",            # 行情状态
    "volume",            # 仓位（张数或 USDT）
    "close_time",        # 平仓时间（空=持仓中）
    "close_price",       # 平仓价（空=未平）
    "pnl_r",             # 盈亏 R 倍数（空=未平）
    "pnl_usdt",          # 盈亏 USDT（空=未平）
    "exit_reason",       # TP1/TP2/TP3/SL/TRAIL/MANUAL/持仓中
    "mfe_r",             # 最大有利波动 R
    "mae_r",             # 最大不利波动 R
    "max_r_before_stop", # 止前最高 R
    "note",              # 备注
]

JOURNAL_DIR = Path("logs")
JOURNAL_PATH = JOURNAL_DIR / "trade_journal.csv"


class TradeJournal:
    """交易日志 — 只追加，不覆盖，不修改已有行。

    用法：
        from state.trade_journal import journal
        journal.open_trade(...)    # 开仓
        journal.close_trade(...)   # 平仓
    """

    def __init__(self, path: str | Path = JOURNAL_PATH):
        self.path = Path(path)
        self._init_csv()

    def _init_csv(self):
        """首次创建时写表头"""
        if self.path.exists() and self.path.stat().st_size > 0:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELD_NAMES)
            writer.writeheader()
        logger.info(f"TradeJournal 已初始化: {self.path}")

    def _next_id(self) -> str:
        """生成简单递增 ID（基于时间戳+计数器）"""
        if self.path.exists() and self.path.stat().st_size > 0:
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    rows = list(csv.DictReader(f))
                if rows:
                    last_id = int(rows[-1].get("order_id", 0))
                    return str(last_id + 1)
            except (ValueError, IndexError, StopIteration):
                pass
        ts = datetime.now().strftime("%y%m%d%H%M%S")
        return f"{ts}001"

    def open_trade(
        self,
        symbol: str,
        direction: str,
        open_price: float,
        sl: float = 0,
        tp1: float = 0,
        tp2: float = 0,
        tp3: float = 0,
        rr: float = 0,
        score: float = 0,
        regime: str = "",
        volume: float = 0,
        note: str = "",
    ) -> str:
        """记录开仓。返回 order_id。"""
        order_id = self._next_id()
        now = datetime.now().isoformat()
        row = {
            "order_id": order_id,
            "symbol": symbol,
            "direction": direction,
            "status": "OPEN",
            "open_time": now,
            "open_price": round(open_price, 2),
            "sl": round(sl, 2),
            "tp1": round(tp1, 2),
            "tp2": round(tp2, 2),
            "tp3": round(tp3, 2),
            "rr": round(rr, 4),
            "score": round(score, 2),
            "regime": regime,
            "volume": round(volume, 4),
            "close_time": "",
            "close_price": "",
            "pnl_r": "",
            "pnl_usdt": "",
            "exit_reason": "",
            "mfe_r": "",
            "mae_r": "",
            "max_r_before_stop": "",
            "note": note,
        }
        self._append_rows([row])
        logger.info(f"[TradeJournal] 开仓 {order_id}: {symbol} {direction} @ {open_price}")
        return order_id

    def close_trade(
        self,
        order_id: str,
        close_price: float,
        pnl_r: float,
        pnl_usdt: float = 0,
        exit_reason: str = "",
        mfe_r: float = 0,
        mae_r: float = 0,
        max_r_before_stop: float = 0,
        note: str = "",
    ):
        """记录平仓（追加新行，不修改开仓行）。"""
        # 先找开仓记录补充信息
        open_row = self._find_open(order_id)
        now = datetime.now().isoformat()
        row = {
            "order_id": order_id,
            "symbol": open_row.get("symbol", "") if open_row else "",
            "direction": open_row.get("direction", "") if open_row else "",
            "status": "CLOSE",
            "open_time": open_row.get("open_time", "") if open_row else "",
            "open_price": open_row.get("open_price", "") if open_row else "",
            "sl": "",
            "tp1": "",
            "tp2": "",
            "tp3": "",
            "rr": "",
            "score": open_row.get("score", "") if open_row else "",
            "regime": open_row.get("regime", "") if open_row else "",
            "volume": open_row.get("volume", "") if open_row else "",
            "close_time": now,
            "close_price": round(close_price, 2),
            "pnl_r": round(pnl_r, 4),
            "pnl_usdt": round(pnl_usdt, 2),
            "exit_reason": exit_reason,
            "mfe_r": round(mfe_r, 4) if mfe_r else "",
            "mae_r": round(mae_r, 4) if mae_r else "",
            "max_r_before_stop": round(max_r_before_stop, 4) if max_r_before_stop else "",
            "note": note,
        }
        self._append_rows([row])
        logger.info(f"[TradeJournal] 平仓 {order_id}: {exit_reason} @ {close_price} R={pnl_r:.2f}")

    def _append_rows(self, rows: List[Dict[str, Any]]):
        with open(self.path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELD_NAMES)
            for row in rows:
                # 只写存在的字段，缺失用空串
                clean = {k: row.get(k, "") for k in FIELD_NAMES}
                writer.writerow(clean)

    def _find_open(self, order_id: str) -> Optional[Dict[str, Any]]:
        """按 order_id 找开仓记录"""
        if not self.path.exists():
            return None
        with open(self.path, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("order_id") == order_id and row.get("status") == "OPEN":
                    return dict(row)
        return None

    def get_open_positions(self) -> List[Dict[str, Any]]:
        """获取所有持仓中（OPEN 且没有对应 CLOSE 记录）的订单"""
        if not self.path.exists():
            return []
        order_ids_open = set()
        order_ids_closed = set()
        with open(self.path, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                oid = row.get("order_id", "")
                if row.get("status") == "OPEN":
                    order_ids_open.add(oid)
                elif row.get("status") == "CLOSE":
                    order_ids_closed.add(oid)
        open_ids = order_ids_open - order_ids_closed
        result = []
        with open(self.path, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("order_id") in open_ids and row.get("status") == "OPEN":
                    result.append(dict(row))
        return result

    def load_all(self) -> List[Dict[str, Any]]:
        """加载全部记录"""
        if not self.path.exists():
            return []
        with open(self.path, "r", encoding="utf-8") as f:
            return list(csv.DictReader(f))


# 全局单例
journal = TradeJournal()
