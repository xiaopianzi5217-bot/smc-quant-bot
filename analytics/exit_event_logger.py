"""
Exit Event Logger
V58.7

负责:
交易退出事件记录

OPEN
        |
        |
        v

EXIT_EVENT

        |
        |
        v

OutcomeDB / Learning

"""

import time
import uuid
import json
import os
from datetime import datetime

EVENT_FILE = "data/events.jsonl"

class ExitEventLogger:

    def __init__(self):

        os.makedirs(
            "data",
            exist_ok=True
        )

    def _write(self, data):

        try:

            with open(
                EVENT_FILE,
                "a",
                encoding="utf-8"
            ) as f:

                f.write(
                    json.dumps(
                        data,
                        ensure_ascii=False
                    )
                    +
                    "\n"
                )

        except Exception as e:

            print(
                "[ExitLogger Error]",
                e
            )

    def log_exit(
            self,
            symbol,
            position,
            exit_price,
            reason,
            action,
            mfe=None,
            mae=None
    ):

        """
        平仓事件

        """

        try:

            entry_price = float(
                position.get(
                    "entry",
                    position.get("entry_price", 0)
                ) or 0
            )

            side = position.get(
                "side",
                position.get("direction", "UNKNOWN")
            )

            size = position.get(
                "size",
                position.get("amount", 0)
            )

            opened_time = position.get(
                "open_time",
                position.get("opened_time", time.time())
            )

            hold_seconds = (
                time.time()
                -
                float(opened_time)
            )

            # ======================
            # 计算R
            # ======================

            sl = float(
                position.get(
                    "stop_loss",
                    position.get("current_sl", entry_price)
                ) or entry_price
            )

            risk = abs(
                entry_price-sl
            )

            if str(side or "").lower().startswith("long"):

                pnl_price = (
                    exit_price-entry_price
                )

            else:

                pnl_price = (
                    entry_price-exit_price
                )

            profit_r = (
                pnl_price/risk
                if risk>0
                else 0
            )

            event = {

                "event":

                    "EXIT",

                "schema_version":

                    "58.7",

                "event_id":

                    str(
                        uuid.uuid4()
                    ),

                "timestamp":

                    datetime.utcnow()
                    .isoformat(),

                # ------------------
                # 基础
                # ------------------

                "symbol":

                    symbol,

                "side":

                    side,

                "action":

                    action,

                "reason":

                    reason,

                # ------------------
                # 持续标识
                # ------------------

                "trade_id":

                    position.get(
                        "trade_id"
                    ),

                # ------------------
                # 价格
                # ------------------

                "entry_price":

                    entry_price,

                "exit_price":

                    exit_price,

                "profit_r":

                    round(
                        profit_r,
                        4
                    ),

                # ------------------
                # 风险数据
                # ------------------

                "initial_sl":

                    sl,

                "mfe":

                    mfe,

                "mae":

                    mae,

                # ------------------
                # 时间
                # ------------------

                "hold_seconds":

                    round(
                        hold_seconds,
                        2
                    ),

                # ------------------
                # 原始snapshot
                # ------------------

                "features":

                    position.get(
                        "features",
                        {}
                    ),

                "regime":

                    position.get(
                        "regime",
                        {}
                    ),

                "score":

                    position.get(
                        "score"
                    ),

                "ev":

                    position.get(
                        "ev"
                    ),

                "confidence":

                    position.get(
                        "confidence"
                    )

            }

            self._write(
                event
            )

            return event

        except Exception as e:

            print(
                "[ExitLogger Failed]",
                e
            )

            return None

    def log_open(self, symbol, position):
        """记录开仓快照（OPEN 事件），包含 trade_id 以便后续关联"""
        try:
            entry_price = float(position.get("entry") or position.get("entry_price") or 0)
            side = position.get("side", position.get("direction", "UNKNOWN"))
            # 确保包含必需字段，避免后续 TrainingValidator 拒绝样本
            trade_id = position.get("trade_id") or str(uuid.uuid4())
            # 支持不同位置字段命名：优先 plain keys，其次 open_* 前缀
            features = position.get("features") or position.get("open_features") or {}
            regime = position.get("regime") or position.get("open_regime") or {}
            score = position.get("score") or position.get("open_score") or 0.0
            ev_val = position.get("ev") or position.get("open_ev") or 0.0
            confidence = position.get("confidence") or position.get("open_confidence") or 0.0

            event = {
                "event": "OPEN",
                "schema_version": "58.7",
                "event_id": str(uuid.uuid4()),
                "timestamp": datetime.utcnow().isoformat(),
                "symbol": symbol,
                "side": side,
                "trade_id": trade_id,
                "entry_price": entry_price,
                "features": features,
                "regime": regime,
                "score": score,
                "ev": ev_val,
                "confidence": confidence,
            }
            self._write(event)
            return event
        except Exception as e:
            print("[ExitLogger Open Failed]", e)
            return None

# 全局实例

exit_logger = ExitEventLogger()
