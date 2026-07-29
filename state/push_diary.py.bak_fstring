# -*- coding: utf-8 -*-
"""Push Diary — 推送消息日志（Telegram / 微信）。

记录每次通过 dispatch_observer_snapshot / dispatch_strategy_decision 
发出去的消息及其触发信号，用于审计和去重。

文件位置：logs/push_diary.csv
"""
from __future__ import annotations

import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("PushDiary")

FIELD_NAMES = [
    "ts",                # 推送时间
    "symbol",            # 品种
    "channel",           # telegram / wechat
    "msg_type",          # observer / strategy_approved / strategy_filtered / error
    "direction",         # 信号方向
    "score",             # 评分
    "ev",                # EV
    "price",             # 价格
    "msg_preview",       # 消息摘要前 120 字符
    "status",            # sent / skipped / failed
    "reason",            # 跳过/失败原因
]

DIARY_DIR = Path("logs")
DIARY_PATH = DIARY_DIR / "push_diary.csv"


class PushDiary:
    """推送日志 — 只追加，不覆盖。"""

    def __init__(self, path: str | Path = DIARY_PATH):
        self.path = Path(path)
        self._init_csv()

    def _init_csv(self):
        if self.path.exists() and self.path.stat().st_size > 0:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELD_NAMES)
            writer.writeheader()
        logger.info(f"PushDiary 已初始化: {self.path}")

    def record(
        self,
        symbol: str,
        channel: str,
        msg_type: str,
        direction: str = "",
        score: float = 0,
        ev: float = 0,
        price: float = 0,
        msg_preview: str = "",
        status: str = "sent",
        reason: str = "",
    ):
        row = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": symbol,
            "channel": channel,
            "msg_type": msg_type,
            "direction": direction or "",
            "score": round(score, 2),
            "ev": round(ev, 4),
            "price": round(price, 2),
            "msg_preview": str(msg_preview or "")[:120].replace(",", ";").replace("\n", " | "),
            "status": status,
            "reason": str(reason or "").replace(",", ";").replace("\n", " | "),
        }
        self._append([row])

    def _append(self, rows: List[Dict[str, Any]]):
        with open(self.path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELD_NAMES)
            for row in rows:
                clean = {k: row.get(k, "") for k in FIELD_NAMES}
                writer.writerow(clean)

    def load_all(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        with open(self.path, "r", encoding="utf-8") as f:
            return list(csv.DictReader(f))


# 全局单例
push_logger = PushDiary()
