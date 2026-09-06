"""Test the aggregation logic in daily_report.py"""
import sys
import json
import sqlite3

sys.stdout.reconfigure(encoding="utf-8")

# Connect to v6_research.db
conn = sqlite3.connect("data/v6_research.db")
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute(
    """SELECT signal_id, symbol, direction, regime, mode,
       exit_reason, exit_timestamp, exit_price, pnl_r,
       feature_hash, max_forward_r, max_adverse_r
    FROM trade_snapshots
    WHERE exit_reason IS NOT NULL
      AND exit_reason != ''
      AND exit_reason != 'OPEN'
      AND pnl_r IS NOT NULL
      AND exit_timestamp IS NOT NULL
      AND exit_timestamp > 0"""
)
rows = cur.fetchall()
conn.close()

print(f"Valid rows: {len(rows)}")
print()

# Replicate the logic from _backfill_from_cloud_v6_db
events = []
for row in rows:
    feats = {}
    _fh = str(row["feature_hash"] or "")
    if _fh:
        feats["feature_hash"] = _fh[-10:]
    _mode = str(row["mode"] or "NORMAL")
    if _mode and _mode != "NORMAL":
        feats["mode"] = _mode
    if not feats:
        feats["cloud"] = True
    ev = {
        "event": "EXIT",
        "timestamp": int(row["exit_timestamp"]),
        "trade_id": row["signal_id"],
        "symbol": row["symbol"],
        "profit_r": float(row["pnl_r"] or 0.0),
        "regime": row["regime"] or "UNKNOWN",
        "features": feats,
    }
    events.append(ev)

# Show what the events look like
print("Events generated from v6_research.db:")
for ev in events:
    print(f"  {ev['trade_id']} | symbol={ev['symbol']} | regime={ev['regime']} | features={ev['features']} | pnl={ev['profit_r']}")

print()

# Now replicate the aggregation logic from generate_daily_report
group_sums = {}
group_counts = {}

for ev in events:
    pr = float(ev.get('profit_r') or 0.0)
    sym = ev.get('symbol') or 'UNK'
    rg = ev.get('regime') or 'UNKNOWN'
    features = ev.get('features') or {}
    
    # Extract top feature
    top_feat = 'NONE'
    if isinstance(features, dict) and features:
        found = None
        # 1) feature_hash / cloud_hash first
        for hash_key in ("feature_hash", "cloud_hash"):
            hv = features.get(hash_key)
            if hv and isinstance(hv, str) and hv:
                found = hv
                break
        # 2) bool/flag keys
        if not found:
            for kk, vv in features.items():
                if vv is True or (isinstance(vv, str) and vv and kk not in ("feature_hash", "cloud_hash")):
                    found = kk
                    break
        # 3) first key
        if not found:
            found = next(iter(features.keys()))
        top_feat = found
    
    combo = (sym, rg, top_feat)
    group_sums[combo] = group_sums.get(combo, 0.0) + pr
    group_counts[combo] = group_counts.get(combo, 0) + 1

print("Group results:")
for combo, val in sorted(group_sums.items(), key=lambda x: x[1], reverse=True):
    count = group_counts.get(combo, 0)
    print(f"  {combo[0]} | {combo[1]} | {combo[2]} -> total_R={val:.4f} count={count}")

print("\nDistinct top_feat values:", set(c[2] for c in group_sums.keys()))
print(f"Total groups: {len(group_sums)}")
print(f"Total events: {len(events)}")
print("\nCONCLUSION: Feature handling looks CORRECT" if len(set(c[2] for c in group_sums.keys())) == len(events) 
      else "\nCONCLUSION: Feature handling has PROBLEMS - features are being collapsed!")