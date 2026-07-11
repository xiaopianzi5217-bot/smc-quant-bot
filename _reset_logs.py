# -*- coding: utf-8 -*-
"""清理 trade_journal.csv，保留备份"""
import csv
from pathlib import Path
from datetime import datetime

p = Path("logs/trade_journal.csv")

# 备份
if p.exists():
    with open(p, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    print(f"旧日志: {len(rows)} 条记录")
    
    backup_name = f"trade_journal_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    backup_path = p.with_name(backup_name)
    p.rename(backup_path)
    print(f"已备份 -> {backup_name}")
else:
    print("无 trade_journal.csv")

# 重建空日志
p.parent.mkdir(parents=True, exist_ok=True)
with open(p, "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow([
        "order_id", "symbol", "direction", "status", "open_time",
        "open_price", "sl", "tp1", "tp2", "tp3", "rr", "score",
        "regime", "volume", "close_time", "close_price", "pnl_r",
        "pnl_usdt", "exit_reason", "mfe_r", "mae_r",
        "max_r_before_stop", "note"
    ])
print("已重建空 trade_journal.csv ✅")
