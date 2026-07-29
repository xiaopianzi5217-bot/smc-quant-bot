# -*- coding: utf-8 -*-
"""Runtime report writer for signals, backtests, and smoke checks."""
import json
from datetime import datetime, timezone
from pathlib import Path
from ops.runtime_paths import REPORTS_DIR, ensure_runtime_dirs


def write_json_report(name, payload):
    ensure_runtime_dirs()
    p = Path(REPORTS_DIR) / name
    body = {"generated_at": datetime.now(timezone.utc).isoformat(), "payload": payload}
    with open(p, "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False, indent=2, default=str)
    return str(p)


def write_markdown_report(name, title, sections):
    ensure_runtime_dirs()
    p = Path(REPORTS_DIR) / name
    lines = [f"# {title}", "", f"Generated at: {datetime.now(timezone.utc).isoformat()}", ""]
    for heading, content in sections:
        lines += [f"## {heading}", "", str(content), ""]
    p.write_text("\n".join(lines), encoding="utf-8")
    return str(p)
