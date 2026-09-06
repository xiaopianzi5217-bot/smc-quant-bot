"""一次性拉取 HF 云端的 v6_research.db 并列出表/行数"""
import os
from pathlib import Path

# 加载 .env 中的 HF_TOKEN
_env = Path(".env")
if _env.exists():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("HF_TOKEN="):
            val = line.split("=", 1)[1].strip().strip("'\"")
            if val:
                os.environ["HF_TOKEN"] = val
                break

if not os.environ.get("HF_TOKEN"):
    print("NO_HF_TOKEN")
else:
    print(f"HF_TOKEN_OK token_len={len(os.environ['HF_TOKEN'])}")

import v6_data_engine
v6_data_engine.pull_database_from_hub()
print("PULL_DONE")

db = v6_data_engine._get_db_path()
print(f"DB_PATH={db}")
print(f"DB_EXISTS={db.exists()}")
if db.exists():
    import sqlite3
    conn = sqlite3.connect(str(db))
    # 列出全部列名
    cols = [r[1] for r in conn.execute("PRAGMA table_info(trade_snapshots)")]
    print(f"COLUMNS={cols}")
    # 打印全部行（用可读形式）
    for row in conn.execute("SELECT rowid, * FROM trade_snapshots ORDER BY rowid").fetchall():
        print("ROW_START")
        for cname, cval in zip(["rowid"] + cols, row):
            print(f"  {cname}={cval}")
        print("ROW_END")
    conn.close()