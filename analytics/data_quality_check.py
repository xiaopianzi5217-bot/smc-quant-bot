# -*- coding: utf-8 -*-
"""Data quality check for daily OPEN/EXIT events + v6_research.db."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _parse_iso(ts):
    if ts is None or ts == "":
        return None
    try:
        if isinstance(ts, (int, float)) or (isinstance(ts, str) and ts.replace(".", "", 1).isdigit()):
            return datetime.fromtimestamp(float(ts), timezone.utc)
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    except Exception:
        return None


def _day_bounds_utc8(target_date: datetime = None):
    if target_date is None:
        now_utc8 = datetime.utcnow() + timedelta(hours=8)
        target_date = (now_utc8 - timedelta(days=1)).replace(tzinfo=None)
    if getattr(target_date, "tzinfo", None) is not None:
        local = target_date.astimezone(timezone(timedelta(hours=8)))
    else:
        local = target_date
    start_local = datetime(local.year, local.month, local.day)
    end_local = start_local + timedelta(days=1)
    start = (start_local - timedelta(hours=8)).replace(tzinfo=timezone.utc)
    end = (end_local - timedelta(hours=8)).replace(tzinfo=timezone.utc)
    return start, end


def run_data_quality_check(target_date: datetime = None) -> dict:
    start, end = _day_bounds_utc8(target_date)
    start_ts = int(start.timestamp())
    end_ts = int(end.timestamp())

    open_count = 0
    exit_count = 0
    open_tids = {}
    exit_tids = {}
    features_empty = 0
    profit_r_empty = 0
    ev_empty = 0
    confidence_empty = 0
    regime_empty = 0
    dup_counts = {}

    event_file = Path("data/events.jsonl")
    if event_file.exists():
        with event_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                ts = _parse_iso(ev.get("timestamp") or ev.get("time") or "")
                if not ts or not (start <= ts < end):
                    continue
                tid = ev.get("trade_id") or ev.get("signal_id") or ev.get("event_id")
                if tid:
                    dup_counts[tid] = dup_counts.get(tid, 0) + 1
                if ev.get("event") == "OPEN":
                    open_count += 1
                    if tid:
                        open_tids[tid] = ev
                elif ev.get("event") == "EXIT":
                    exit_count += 1
                    if tid:
                        exit_tids[tid] = ev
                    features = ev.get("features") or {}
                    if not features:
                        features_empty += 1
                    if ev.get("profit_r") in (None, "", []):
                        profit_r_empty += 1
                    if ev.get("ev") in (None, "", []):
                        ev_empty += 1
                    if ev.get("confidence") in (None, "", []):
                        confidence_empty += 1
                    if not ev.get("regime") or ev.get("regime") in ({}, []):
                        regime_empty += 1

    # 用 v6_research.db 补全 OPEN/EXIT 计数（events 缺失时）
    db_candidates = [
        Path("data/v6_research.db"),
        Path("/app/data/v6_research.db"),
        Path(__file__).resolve().parent.parent / "data" / "v6_research.db",
    ]
    db_path = next((p for p in db_candidates if p.exists()), None)
    db_open = 0
    db_exit = 0
    if db_path is not None:
        try:
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute(
                """
                SELECT COUNT(*) FROM trade_snapshots
                WHERE timestamp >= ? AND timestamp < ?
                """,
                (start_ts, end_ts),
            )
            db_open = int(cur.fetchone()[0] or 0)
            cur.execute(
                """
                SELECT COUNT(*) FROM trade_snapshots
                WHERE exit_reason IS NOT NULL AND exit_reason != '' AND exit_reason != 'OPEN'
                  AND pnl_r IS NOT NULL
                  AND exit_timestamp IS NOT NULL AND exit_timestamp > 0
                  AND exit_timestamp >= ? AND exit_timestamp < ?
                """,
                (start_ts, end_ts),
            )
            db_exit = int(cur.fetchone()[0] or 0)
            conn.close()
        except Exception:
            pass

    # 取 max，避免 events 漏记时显示 0
    open_count = max(open_count, db_open)
    exit_count = max(exit_count, db_exit)

    missing_open = [t for t in open_tids.keys() if t not in exit_tids]
    duplicate_trade_ids = sum(1 for c in dup_counts.values() if c > 1)

    return {
        "open_count": open_count,
        "exit_count": exit_count,
        "missing_open_without_exit": len(missing_open),
        "duplicate_trade_ids": duplicate_trade_ids,
        "features_empty": features_empty,
        "profit_r_empty": profit_r_empty,
        "ev_empty": ev_empty,
        "confidence_empty": confidence_empty,
        "regime_empty": regime_empty,
        "db_open": db_open,
        "db_exit": db_exit,
    }


if __name__ == "__main__":
    print(run_data_quality_check())
