# -*- coding: utf-8 -*-
"""
Event Schema V58 — 统一事件日志系统

所有信号事件（REJECT / TRADE / OUTCOME）统一写入 data/events.jsonl，
提供完整的数据闭环追踪能力。每条记录包含 schema_version、event_id、
event_type、timestamp，以及事件特定的 data 字段。

REJECT 事件：
  - signal_id, symbol, direction, score, ev, confidence
  - regime, feature_hash, features, entry_price, stop_price

TRADE 事件：
  - feature_hash, atr, volatility, squeeze, smc_score
  - symbol, direction, entry_price, regime, score, ev, confidence

OUTCOME 事件：
  - parent_event_id（关联 TRADE 或 REJECT 的 event_id）
  - result_r, mfe_r, mae_r, holding_time

用法：
    from analytics.event_schema import event_logger
    event_id = event_logger.log_event("TRADE", {...})
"""

import json
import os
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

EVENT_LOG = "data/events.jsonl"


class EventLogger:
    """统一事件记录器，每条事件写入 data/events.jsonl"""

    def __init__(self, log_path: str = EVENT_LOG):
        self.log_path = log_path
        os.makedirs(os.path.dirname(log_path), exist_ok=True)

    @staticmethod
    def _json_default(obj: Any) -> str:
        """安全序列化：处理 numpy 类型、ndarray 等不可 JSON 序列化的对象"""
        try:
            return float(obj)
        except (TypeError, ValueError):
            pass
        try:
            return str(obj)
        except Exception:
            return "<unserializable>"

    def log_event(self, event_type: str, data: Dict[str, Any]) -> str:
        """记录一条事件日志并返回 event_id

        Args:
            event_type: 事件类型（"REJECT" / "TRADE" / "OUTCOME"）
            data: 事件数据字典

        Returns:
            event_id: 全局唯一事件 ID（UUID 字符串）
        """
        event_id = str(uuid.uuid4())
        event = {
            "schema_version": "58",
            "event_id": event_id,
            "event_type": event_type,
            "timestamp": datetime.now().isoformat(),
            **data,
        }
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(
                    json.dumps(event, ensure_ascii=False, default=self._json_default) + "\n"
                )
        except OSError as e:
            print(f"[EventLogger] 写入失败 (IO): {e}")
        except Exception as e:
            print(f"[EventLogger] 写入失败 (未知): {e}")

        return event_id

    def get_training_dataset(self):
        """导出训练集 DataFrame

        Returns:
            pd.DataFrame: 包含所有事件的 DataFrame
        """
        import pandas as pd

        if not os.path.exists(self.log_path):
            return pd.DataFrame()
        try:
            return pd.read_json(self.log_path, lines=True)
        except Exception as e:
            print(f"[EventLogger] 读取训练集失败: {e}")
            return pd.DataFrame()

    def get_events_by_type(self, event_type: str) -> list:
        """按事件类型筛选

        Args:
            event_type: "REJECT" / "TRADE" / "OUTCOME"

        Returns:
            匹配该类型的完整事件字典列表
        """
        results = []
        if not os.path.exists(self.log_path):
            return results
        with open(self.log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("event_type") == event_type:
                    results.append(ev)
        return results

    def count_by_type(self) -> Dict[str, int]:
        """各类型事件计数"""
        counts: Dict[str, int] = {}
        if not os.path.exists(self.log_path):
            return counts
        with open(self.log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = ev.get("event_type", "UNKNOWN")
                counts[t] = counts.get(t, 0) + 1
        return counts

    def validate_chain(self) -> list:
        """校验 TRADE→OUTCOME / REJECT→OUTCOME 链路完整性

        Returns:
            断链列表：每条是 {"event_id": ..., "event_type": ..., "missing": ...}
        """
        broken = []
        parent_refs = {}  # parent_event_id -> list of OUTCOME event_ids
        trade_ids = set()
        reject_ids = set()

        if not os.path.exists(self.log_path):
            return broken

        with open(self.log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                etype = ev.get("event_type")
                eid = ev.get("event_id")
                if etype == "TRADE":
                    if eid:
                        trade_ids.add(eid)
                elif etype == "REJECT":
                    if eid:
                        reject_ids.add(eid)
                elif etype == "OUTCOME":
                    pid = ev.get("parent_event_id")
                    if pid:
                        parent_refs.setdefault(pid, []).append(ev.get("event_id", "?"))

        # 找出无 OUTCOME 的 TRADE/REJECT
        all_parents = set(trade_ids | reject_ids)
        for pid in all_parents:
            if pid not in parent_refs:
                broken.append({"event_id": pid, "event_type": "TRADE" if pid in trade_ids else "REJECT", "missing": "OUTCOME"})
        return broken


# 全局单例
event_logger = EventLogger()
