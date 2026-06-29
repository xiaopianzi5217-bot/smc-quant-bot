# -*- coding: utf-8 -*-
import csv
import json
import os
from pathlib import Path
from datetime import datetime, timezone

JOURNAL_COLUMNS = [
    "timestamp", "event", "symbol", "direction",
    "entry", "sl", "tp1", "tp2", "tp3",
    "entry_reason", "exit_reason",
    "score", "score_long", "score_short", "threshold",
    "allowed_direction", "regime", "volatility", "squeeze", "adx", "atr_ratio",
    "ob_valid", "bullish_ob_valid", "bearish_ob_valid",
    "pivot_strength", "pivot_threshold", "bsl_level", "ssl_level",
    "is_bsl_swept", "is_ssl_swept",
    "strategy_name", "min_rr",
    "reasons", "context_json",
    "exit_price", "pnl_r",
]


def ensure_journal(path: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=JOURNAL_COLUMNS)
            writer.writeheader()


def _safe_json(value):
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return "{}"


def log_event(path: str, event: str, payload: dict):
    ensure_journal(path)
    row = {col: "" for col in JOURNAL_COLUMNS}
    row["timestamp"] = datetime.now(timezone.utc).isoformat()
    row["event"] = event

    context = payload.get("context") or {}

    # 先写 payload 显式字段
    for key, value in payload.items():
        if key in row:
            row[key] = value

    # 再从 context 自动补字段
    for key in [
        "entry_reason", "score", "score_long", "score_short", "threshold",
        "allowed_direction", "regime", "volatility", "squeeze", "adx", "atr_ratio",
        "ob_valid", "bullish_ob_valid", "bearish_ob_valid",
        "pivot_strength", "pivot_threshold", "bsl_level", "ssl_level",
        "is_bsl_swept", "is_ssl_swept", "strategy_name", "min_rr",
    ]:
        if row.get(key, "") == "" and key in context:
            row[key] = context.get(key)

    if isinstance(row.get("reasons"), (list, tuple)):
        row["reasons"] = "|".join(str(x) for x in row["reasons"])
    elif row.get("reasons", "") == "" and isinstance(context.get("reasons"), (list, tuple)):
        row["reasons"] = "|".join(str(x) for x in context.get("reasons"))

    row["context_json"] = _safe_json(context)

    # 兼容 exit_reason
    if row.get("exit_reason", "") == "" and payload.get("close_reason"):
        row["exit_reason"] = payload.get("close_reason")

    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=JOURNAL_COLUMNS)
        writer.writerow(row)
