#!/usr/bin/env python3
"""
End-to-end sanity tests for outcome pipeline (consumer -> validator -> learning -> daily report)

Run: python tests/test_outcome_pipeline.py
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import sys
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from analytics.outcome_consumer import process_events_once
from analytics.outcome_db import OutcomeDatabase
from analytics.daily_report import generate_daily_report


def cleanup():
    for p in [
        Path('data/events.jsonl'),
        Path('data/processed_trade_ids.json'),
        Path('data/open_cache.json'),
        Path('storage/outcome_stats.json'),
        Path('storage/learning_runs.json'),
    ]:
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass


def write_events(events):
    evf = Path('data/events.jsonl')
    evf.parent.mkdir(parents=True, exist_ok=True)
    with evf.open('w', encoding='utf-8') as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def read_outcome_db():
    db = OutcomeDatabase()
    return db.data


def case1_normal_trade():
    print('CASE1: normal trade -> should be learned')
    cleanup()
    open_ev = {
        "event": "OPEN",
        "trade_id": "test_win_001",
        "symbol": "BTC/USDT",
        "score": 82,
        "ev": 1.8,
        "features": {"smc": 8, "momentum": 7},
        "regime": "TREND",
        "confidence": 0.85,
        "timestamp": "2026-08-02T00:00:00"
    }
    exit_ev = {
        "event": "EXIT",
        "trade_id": "test_win_001",
        "profit_r": 2.1,
        "timestamp": "2026-08-02T00:01:00",
        "ev": 1.8
    }
    write_events([open_ev, exit_ev])
    n = process_events_once()
    db = read_outcome_db()
    if not db:
        print('FAIL: OutcomeDB empty after normal trade')
        return False
    print('PASS: OutcomeDB has entries:', len(db))
    report = generate_daily_report()
    if 'EV=' in report:
        print('PASS: EV stats present in report')
        return True
    else:
        print('FAIL: EV stats missing in report')
        return False


def case2_force_close_unknown():
    print('\nCASE2: FORCE_CLOSE_UNKNOWN should be skipped by learner')
    cleanup()
    ev = {
        "event": "FORCE_CLOSE_UNKNOWN",
        "trade_id": "unknown_001",
        "profit_r": 0,
        "timestamp": "2026-08-02T01:00:00"
    }
    write_events([ev])
    n = process_events_once()
    db = read_outcome_db()
    if db:
        print('FAIL: OutcomeDB should be empty after FORCE_CLOSE_UNKNOWN')
        return False
    print('PASS: OutcomeDB unchanged')
    return True


def case3_bad_data():
    print('\nCASE3: bad data should be rejected by TrainingValidator')
    cleanup()
    # OPEN without features/score/regime
    open_ev = {
        "event": "OPEN",
        "trade_id": "bad_001",
        "symbol": "ETH/USDT",
        # missing features, score, regime
        "timestamp": "2026-08-02T02:00:00"
    }
    exit_ev = {
        "event": "EXIT",
        "trade_id": "bad_001",
        "profit_r": 1.0,
        "timestamp": "2026-08-02T02:05:00",
    }
    write_events([open_ev, exit_ev])
    n = process_events_once()
    db = read_outcome_db()
    if db:
        print('FAIL: OutcomeDB should be empty for bad data')
        return False
    print('PASS: bad sample rejected')
    return True


def main():
    ok = True
    ok &= case1_normal_trade()
    ok &= case2_force_close_unknown()
    ok &= case3_bad_data()
    if ok:
        print('\nALL TESTS PASSED')
        sys.exit(0)
    else:
        print('\nSOME TESTS FAILED')
        sys.exit(2)


if __name__ == '__main__':
    main()
