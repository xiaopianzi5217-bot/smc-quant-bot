"""Full analysis of daily_report issues"""
import sys
import json
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

print("=" * 60)
print("1. ANALYZE events.jsonl")
print("=" * 60)

with open("data/events.jsonl", encoding="utf-8") as f:
    lines = f.readlines()

print(f"Total lines: {len(lines)}")

events = []
for line in lines:
    try:
        events.append(json.loads(line))
    except Exception:
        pass

print(f"Total events: {len(events)}")

# Count by event type
from collections import Counter
ev_types = Counter(e.get("event") for e in events)
print(f"Event types: {dict(ev_types)}")

# Show EXIT events
exits = [e for e in events if e.get("event") == "EXIT"]
print(f"\nEXIT events: {len(exits)}")
for e in exits[:30]:
    ts = e.get("timestamp") or e.get("time") or ""
    tid = e.get("trade_id") or ""
    prof = e.get("profit_r") or 0
    feats = e.get("features") or {}
    print(f"  trade={tid} | ts={ts} | profit={prof} | feats={json.dumps(feats, ensure_ascii=False)[:100]}")

# Show OPEN events count
opens = [e for e in events if e.get("event") == "OPEN"]
print(f"\nOPEN events: {len(opens)}")
for e in opens[:5]:
    print(f"  trade={e.get('trade_id')} | ts={e.get('timestamp')}")

print()
print("=" * 60)
print("2. TIMEZONE ANALYSIS")
print("=" * 60)
from datetime import datetime, timezone, timedelta

# Simulate generate_daily_report for 2026-08-15 (UTC date)
target_date = datetime(2026, 8, 15)  # as passed by utcnow().date()

# How generate_daily_report constructs start/end
start = datetime(2026, 8, 15)
end = start + timedelta(days=1)

# What backfill uses
start_ts = int(start.timestamp())
end_ts = int(end.timestamp())

print(f"target_date(is naive but from date parts): {start}")
print(f"start.timestamp() = {start_ts}")
print(f"  -> as UTC: {datetime.utcfromtimestamp(start_ts).isoformat()}")
print(f"end.timestamp() = {end_ts}")
print(f"  -> as UTC: {datetime.utcfromtimestamp(end_ts).isoformat()}")
print()

# What it SHOULD be (UTC midnight)
start_utc = int(datetime(2026, 8, 15, tzinfo=timezone.utc).timestamp())
end_utc = int(datetime(2026, 8, 16, tzinfo=timezone.utc).timestamp())
print(f"CORRECT UTC window: [{start_utc}, {end_utc})")
print(f"  -> UTC times: [{datetime.utcfromtimestamp(start_utc).isoformat()}, {datetime.utcfromtimestamp(end_utc).isoformat()})")
print()

# Show actual timestamps in v6_research.db (from prior testing):
db_ts_list = [1786815903, 1786831206, 1786844336, 1786873448]
print("Actual timestamps in v6_research.db:")
for ts in db_ts_list:
    dt_utc = datetime.utcfromtimestamp(ts)
    in_naive_win = start_ts <= ts < end_ts
    in_utc_win = start_utc <= ts < end_utc
    print(f"  ts={ts} -> {dt_utc.isoformat()} | in naive-window={in_naive_win} | in UTC-window={in_utc_win}")

print()
print("=" * 60)
print("3. VERIFY FIX FOR TIMEZONE")
print("=" * 60)
print("The bug: naive datetime.timestamp() treats input as LOCAL time (UTC+8)")
print("Fix: use timezone-aware UTC datetime, or calendar.timegm")
print()
print("Example fix:")
print("  import calendar")
print("  start_ts = calendar.timegm(start.timetuple())")
print("  end_ts = calendar.timegm(end.timetuple())")
print()
print("This treats the naive datetime(2026,8,15) as UTC midnight 00:00")
print("  -> start_ts =", int(datetime(2026,8,15, tzinfo=timezone.utc).timestamp()))
print("  -> end_ts   =", int(datetime(2026,8,16, tzinfo=timezone.utc).timestamp()))