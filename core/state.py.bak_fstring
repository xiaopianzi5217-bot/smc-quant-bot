# -*- coding: utf-8 -*-
from dataclasses import dataclass, asdict
from enum import IntEnum
from typing import Optional, Dict, Any


class TradeStateCode(IntEnum):
    """
    独立整数状态机。

    不再用字符串到处判断，避免 typo 和状态漂移。
    """
    IDLE = 0
    OPENED = 1
    TP1_HIT = 2
    TP2_HIT = 3
    CLOSED = 4


STATE_NAME = {
    TradeStateCode.IDLE: "IDLE",
    TradeStateCode.OPENED: "OPENED",
    TradeStateCode.TP1_HIT: "TP1_HIT",
    TradeStateCode.TP2_HIT: "TP2_HIT",
    TradeStateCode.CLOSED: "CLOSED",
}


def state_name(code: int) -> str:
    try:
        return STATE_NAME[TradeStateCode(int(code))]
    except Exception:
        return "UNKNOWN"


@dataclass
class TradeState:
    symbol: str
    direction: str
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp3: float
    qty: float
    state: int = TradeStateCode.OPENED
    bars_held: int = 0
    opened_at: str = ""
    closed_at: str = ""
    close_price: Optional[float] = None
    close_reason: Optional[str] = None
    open_context: Optional[Dict[str, Any]] = None

    def to_dict(self):
        data = asdict(self)
        data["state"] = int(self.state)
        return data

    @staticmethod
    def from_dict(data):
        # 兼容旧版本字符串状态
        legacy = {
            "active": TradeStateCode.OPENED,
            "tp1_hit": TradeStateCode.TP1_HIT,
            "tp2_hit": TradeStateCode.TP2_HIT,
            "closed": TradeStateCode.CLOSED,
            "stopped": TradeStateCode.CLOSED,
            "time_exit": TradeStateCode.CLOSED,
        }
        state = data.get("state", TradeStateCode.OPENED)
        if isinstance(state, str):
            data["state"] = int(legacy.get(state, TradeStateCode.OPENED))
        else:
            data["state"] = int(state)
        return TradeState(**data)
