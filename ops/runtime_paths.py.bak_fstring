# -*- coding: utf-8 -*-
"""Runtime path helpers for local, server, and Hugging Face Spaces runs."""
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[1]
HF_HOME = Path(os.environ.get("HF_HOME", ROOT))
DATA_DIR = Path(os.environ.get("SMC_DATA_DIR", ROOT / "data"))
REPORTS_DIR = Path(os.environ.get("SMC_REPORTS_DIR", ROOT / "reports"))
LOGS_DIR = Path(os.environ.get("SMC_LOGS_DIR", ROOT / "logs"))
ARTIFACTS_DIR = Path(os.environ.get("SMC_ARTIFACTS_DIR", ROOT / "artifacts"))
STATE_DIR = Path(os.environ.get("SMC_STATE_DIR", ROOT / "state"))
CONFIG_PATH = Path(os.environ.get("SMC_CONFIG", ROOT / "config" / "v11_full_config.json"))


def ensure_runtime_dirs():
    for p in (DATA_DIR, REPORTS_DIR, LOGS_DIR, ARTIFACTS_DIR, STATE_DIR):
        p.mkdir(parents=True, exist_ok=True)
    return {"data": DATA_DIR, "reports": REPORTS_DIR, "logs": LOGS_DIR, "artifacts": ARTIFACTS_DIR, "state": STATE_DIR}
