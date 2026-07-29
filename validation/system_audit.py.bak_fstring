# -*- coding: utf-8 -*-
"""Engineering audit checks for the SMC V11+V9 package."""
from __future__ import annotations

import importlib
import json
import py_compile
from pathlib import Path
from typing import Dict, List

REQUIRED_FILES = [
    "app.py",
    "main.py",
    "requirements.txt",
    "config/v11_full_config.json",
    "runner/v11_institutional_runner.py",
    "decision/v9_decision_kernel.py",
    "strategy/smc.py",
    "indicators/basic.py",
    "backtest/runner.py",
    "risk/global_risk.py",
    "execution/paper_broker.py",
    "ops/env_config.py",
]

REQUIRED_IMPORTS = [
    "runner.v11_institutional_runner",
    "decision.v9_decision_kernel",
    "backtest.runner",
    "risk.global_risk",
    "risk.portfolio_state",
    "execution.paper_broker",
    "ops.env_config",
    "ops.state_store",
]


def audit_project(root: str | Path = ".") -> Dict[str, object]:
    root = Path(root)
    issues: List[str] = []
    warnings: List[str] = []
    for f in REQUIRED_FILES:
        if not (root / f).exists():
            issues.append(f"missing required file: {f}")
    cfg_path = root / "config" / "v11_full_config.json"
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        if cfg.get("mode") not in {"dry_run", "paper", "live"}:
            issues.append("config.mode must be dry_run/paper/live")
        if cfg.get("mode") == "live":
            warnings.append("live mode is not recommended on Hugging Face Spaces")
        if not cfg.get("symbols"):
            issues.append("config.symbols is empty")
        risk = cfg.get("risk", {})
        if float(risk.get("risk_per_trade", 0)) > 0.02:
            warnings.append("risk_per_trade is above 2%; consider lowering before live/paper use")
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        try:
            py_compile.compile(str(path), doraise=True)
        except Exception as exc:
            issues.append(f"syntax error: {path.relative_to(root)}: {exc}")
    cwd_added = False
    import sys
    if str(root.resolve()) not in sys.path:
        sys.path.insert(0, str(root.resolve()))
        cwd_added = True
    try:
        for name in REQUIRED_IMPORTS:
            try:
                importlib.import_module(name)
            except Exception as exc:
                issues.append(f"import failed: {name}: {exc}")
    finally:
        if cwd_added and sys.path and sys.path[0] == str(root.resolve()):
            sys.path.pop(0)
    status = "PASS" if not issues else "FAIL"
    return {"status": status, "issues": issues, "warnings": warnings, "required_files": REQUIRED_FILES}
