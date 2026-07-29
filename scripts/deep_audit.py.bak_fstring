# -*- coding: utf-8 -*-
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from validation.system_audit import audit_project
from monitoring.runtime_report import write_json_report

if __name__ == "__main__":
    result = audit_project(ROOT)
    path = write_json_report("deep_audit.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"saved: {path}")
    raise SystemExit(0 if result["status"] == "PASS" else 1)
