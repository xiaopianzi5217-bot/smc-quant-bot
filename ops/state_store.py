# -*- coding: utf-8 -*-
"""Small JSON state store for HF/local dry-run state.

It is intentionally dependency-free and safe for ephemeral Hugging Face Spaces.
The state is not a replacement for a production database, but it prevents the
runner from being fully stateless during observation and paper trading.
"""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from ops.runtime_paths import STATE_DIR, ensure_runtime_dirs


def _json_default(obj: Any):
    if is_dataclass(obj):
        return asdict(obj)
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


class JsonStateStore:
    def __init__(self, name: str = "runtime_state.json"):
        ensure_runtime_dirs()
        self.path = Path(STATE_DIR) / name

    def load(self, default: Dict[str, Any] | None = None) -> Dict[str, Any]:
        if not self.path.exists():
            return dict(default or {})
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else dict(default or {})
        except Exception:
            return dict(default or {})

    def save(self, data: Dict[str, Any]) -> Path:
        payload = dict(data or {})
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=_json_default)
        tmp.replace(self.path)
        return self.path

    def update(self, **kwargs) -> Dict[str, Any]:
        data = self.load()
        data.update(kwargs)
        self.save(data)
        return data
