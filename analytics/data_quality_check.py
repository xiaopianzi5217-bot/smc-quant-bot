"""
Data quality checks for events.jsonl

Provides counts for OPEN/EXIT, missing OPEN/EXIT pairs, duplicate trade_ids, and features-empty counts.
"""
from pathlib import Path
import json
from datetime import datetime, timedelta, timezone


def _parse_iso(ts: str):
    """Parse timestamp to aware-UTC datetime.

    Handles: ISO-8601 with/without timezone offset or 'Z' suffix,
    epoch seconds (int/float str). Naive parsed strings are assumed
    to represent UTC (semantic fix: all logs written in UTC).

    Returns aware datetime in UTC, or None on failure.
    """
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
    except Exception:
        try:
            # epoch float seconds
            return datetime.fromtimestamp(float(ts), timezone.utc)
        except Exception:
            return None
    if dt.tzinfo is None:
        # naive assumed UTC
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def run_data_quality_check(target_date: datetime = None) -> dict:
    if target_date is None:
        target_date = datetime.utcnow()

    start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)

    event_file = Path("data/events.jsonl")
    if not event_file.exists():
        return {}

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

    with event_file.open('r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            ts = _parse_iso(ev.get('timestamp') or ev.get('time') or '')
            if not ts or not (start <= ts < end):
                continue
            tid = ev.get('trade_id') or ev.get('event_id')
            if tid:
                dup_counts[tid] = dup_counts.get(tid, 0) + 1
            if ev.get('event') == 'OPEN':
                open_count += 1
                if tid:
                    open_tids[tid] = ev
            elif ev.get('event') == 'EXIT':
                exit_count += 1
                if tid:
                    exit_tids[tid] = ev
                features = ev.get('features') or {}
                if not features:
                    features_empty += 1
                # 学习字段完整性检查
                if ev.get('profit_r') in (None, '', []):
                    profit_r_empty += 1
                if ev.get('ev') in (None, '', []):
                    ev_empty += 1
                if ev.get('confidence') in (None, '', []):
                    confidence_empty += 1
                if not ev.get('regime'):
                    regime_empty += 1

    # missing: open tids not in exit tids
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
    }


if __name__ == '__main__':
    print(run_data_quality_check())
