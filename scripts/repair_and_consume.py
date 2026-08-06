"""
小工具：补全 data/events.jsonl 中缺少字段的 OPEN 记录，并运行 outcome_consumer 进行一次性消费。
用法：
    python scripts/repair_and_consume.py

该脚本会：
- 读取 data/events.jsonl
- 对 OPEN 事件补全 `features, regime, score, ev, confidence, trade_id` 等字段（若缺失）
- 删除 data/processed_trade_ids.json（备份后删除）
- 调用 analytics.outcome_consumer.process_events_once() 并打印消费结果
"""

import json
import sys
import os
from pathlib import Path
from datetime import datetime

# 将项目根加入 sys.path，保证以脚本方式运行时能导入本地包
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EVENT_FILE = Path("data/events.jsonl")
PROCESSED = Path("data/processed_trade_ids.json")
BACKUP_PROCESSED = Path("data/processed_trade_ids.json.bak")


def repair_events():
    if not EVENT_FILE.exists():
        print("no events file")
        return 0
    lines = []
    with EVENT_FILE.open('r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if ev.get('event') == 'OPEN':
                # 补全字段
                ev['trade_id'] = ev.get('trade_id') or ev.get('event_id') or f"repair-{int(datetime.utcnow().timestamp())}"
                ev['features'] = ev.get('features') or {}
                ev['regime'] = ev.get('regime') or {}
                ev['score'] = ev.get('score') or 0.0
                ev['ev'] = ev.get('ev') or 0.0
                ev['confidence'] = ev.get('confidence') or 0.0
            lines.append(ev)
    # 覆写
    with EVENT_FILE.open('w', encoding='utf-8') as f:
        for ev in lines:
            f.write(json.dumps(ev, ensure_ascii=False) + '\n')
    print(f"repaired {len(lines)} events")
    return len(lines)


def clear_processed_backup():
    if PROCESSED.exists():
        try:
            PROCESSED.replace(BACKUP_PROCESSED)
            print("moved processed_trade_ids.json -> processed_trade_ids.json.bak")
        except Exception as e:
            print("backup failed", e)
    else:
        print("no processed file to backup")


if __name__ == '__main__':
    repaired = repair_events()
    clear_processed_backup()
    # 调用 consumer
    try:
        from analytics import outcome_consumer
        n = outcome_consumer.process_events_once()
        print(f"consumer returned: {n}")
    except Exception as e:
        print("consumer execution failed:", e)
