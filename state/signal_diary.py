# -*- coding: utf-8 -*-
"""Signal Diary — 每次扫描的完整信号明细日志（不可变追加）。

记录每次 evaluate_symbol 的完整决策链路，包括：
  - 原始评分（多/空）
  - 预期 EV（多/空）
  - 决策方向、审批结果
  - Reasons 明细
  - HTF 方向对齐
  - SL/TP/RR

文件位置：logs/signal_diary.csv
"""
from __future__ import annotations

import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("SignalDiary")

FIELD_NAMES = [
    "ts",                # 扫描时间
    "symbol",            # BTC/USDT
    "direction",         # 选定方向 Long/Short
    "approved",          # 是否批准开单
    "state",             # 状态 HOLD/APPROVED/STRATEGY_FILTER_BLOCKED/PORTFOLIO_BLOCKED
    "reason",            # 原因简述
    "long_score",        # 多头评分
    "short_score",       # 空头评分
    "edge",              # 分差
    "long_ev",           # 多头 EV
    "short_ev",          # 空头 EV
    "price",             # 当前价格
    "sl",                # 止损 (批准时)
    "tp1",               # TP1 (批准时)
    "rr",                # 预期赔率
    "regime",            # 市场状态
    "htf_allowed",       # HTF 允许方向
    "volume_ratio",      # 成交量比
    "adx",               # ADX
    "atr_pct",           # ATR%
    "squeeze",           # 波动压缩状态
    "has_bot_div",       # 底背离
    "has_top_div",       # 顶背离
    "is_ssl_swept",      # 卖方流动性扫取
    "is_bsl_swept",      # 买方流动性扫取
    "score_reasons",     # 评分明细 reasons
    "ev_reasons",        # EV 明细 reasons
    "funding_rate",      # 资金费率
    "long_score_raw",    # 多头原始分
    "short_score_raw",   # 空头原始分
]

DIARY_DIR = Path("logs")
DIARY_PATH = DIARY_DIR / "signal_diary.csv"


class SignalDiary:
    """信号明细日志 — 只追加，不覆盖。

    用法：
        from state.signal_diary import diary
        diary.record(...)
    """

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
        logger.info(f"SignalDiary 已初始化: {self.path}")

    def record(
        self,
        symbol: str,
        direction: str,
        approved: bool,
        state: str,
        reason: str,
        long_score: float = 0,
        short_score: float = 0,
        long_ev: float = 0,
        short_ev: float = 0,
        price: float = 0,
        sl: float = 0,
        tp1: float = 0,
        rr: float = 0,
        regime: str = "",
        htf_allowed: str = "",
        volume_ratio: float = 0,
        adx: float = 0,
        atr_pct: float = 0,
        squeeze: str = "",
        has_bot_div: bool = False,
        has_top_div: bool = False,
        is_ssl_swept: bool = False,
        is_bsl_swept: bool = False,
        score_reasons: str = "",
        ev_reasons: str = "",
        funding_rate: float = 0,
        long_score_raw: float = 0,
        short_score_raw: float = 0,
    ):
        edge = abs(long_score - short_score)
        row = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": symbol,
            "direction": direction or "",
            "approved": str(approved),
            "state": state or "",
            "reason": str(reason or "").replace(",", ";").replace("\n", " | "),
            "long_score": round(long_score, 2),
            "short_score": round(short_score, 2),
            "edge": round(edge, 2),
            "long_ev": round(long_ev, 4),
            "short_ev": round(short_ev, 4),
            "price": round(price, 2),
            "sl": round(sl, 2),
            "tp1": round(tp1, 2),
            "rr": round(rr, 2),
            "regime": regime,
            "htf_allowed": htf_allowed,
            "volume_ratio": round(volume_ratio, 4),
            "adx": round(adx, 1),
            "atr_pct": round(atr_pct, 4),
            "squeeze": squeeze,
            "has_bot_div": str(has_bot_div),
            "has_top_div": str(has_top_div),
            "is_ssl_swept": str(is_ssl_swept),
            "is_bsl_swept": str(is_bsl_swept),
            "score_reasons": str(score_reasons or "").replace(",", ";").replace("\n", " | "),
            "ev_reasons": str(ev_reasons or "").replace(",", ";").replace("\n", " | "),
            "funding_rate": round(funding_rate, 6),
            "long_score_raw": round(long_score_raw, 2),
            "short_score_raw": round(short_score_raw, 2),
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
diary = SignalDiary()
