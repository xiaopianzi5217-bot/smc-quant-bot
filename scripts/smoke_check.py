# -*- coding: utf-8 -*-
"""Project smoke check: syntax, selected imports, dry-run, and quick backtest. This smoke check intentionally imports only stable runtime modules. Importing all .py files in the repository is unsafe because patch scripts and UI modules can have side effects or optional dependencies. """
from __future__ import annotations

import importlib
import os
import pathlib
import py_compile
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RUNTIME_IMPORTS = [
    "backtest.runner",
    "backtest.runner_v4_3_hard_prune",
    "decision.v6_decision_kernel",
    "decision.v9_decision_kernel",
    "execution.exchange_adapter",
    "execution.live_engine",
    "execution.lifecycle_manager",
    "portfolio.portfolio_manager",
    "risk.v6_risk_engine",
    "risk.global_risk",
    "risk.position_sizing",
    "strategy.trade_filters",
    "alpha_validator.avs_engine",
]

OPTIONAL_RUNTIME_IMPORTS = [
    "runner.v11_institutional_runner",
    "runner.v7_live_runner",
]


def compile_all():
    for p in ROOT.glob("**/*.py"):
        rel_parts = p.relative_to(ROOT).parts
        if "__pycache__" in rel_parts:
            continue
        # Uploaded archives sometimes contain a nested duplicate repository;
        # the runtime package is the repository root only.
        if rel_parts and rel_parts[0] == "SMC_Bot":
            continue
        if p.name.startswith("_test") or p.name.endswith(".bak"):
            continue
        py_compile.compile(str(p), doraise=True)


def import_runtime_modules():
    errors = []
    optional_skipped = []
    for mod in RUNTIME_IMPORTS:
        try:
            importlib.import_module(mod)
        except Exception as exc:
            errors.append((mod, type(exc).__name__, str(exc)))
    for mod in OPTIONAL_RUNTIME_IMPORTS:
        try:
            importlib.import_module(mod)
        except ModuleNotFoundError as exc:
            optional_skipped.append((mod, str(exc)))
        except Exception as exc:
            errors.append((mod, type(exc).__name__, str(exc)))
    if optional_skipped:
        print(f"OPTIONAL_IMPORT_SKIPPED: {optional_skipped}")
    if errors:
        raise RuntimeError(errors)


def run_cmd(args):
    subprocess.run([sys.executable, *args], cwd=ROOT, check=True)


def main():
    compile_all()
    import_runtime_modules()
    # Live/main scanner can trigger network calls and notifier side effects.
    # Run it only when explicitly requested.
    if os.getenv("SMC_SMOKE_RUN_LIVE") == "1":
        run_cmd(["main.py"])
    run_cmd([
        "run_backtest.py",
        "--exec-csv", "data/BTCUSDT_15M_365d.csv",
        "--macro-csv", "data/BTCUSDT_1H_365d.csv",
        "--out", "data/backtest_smoke.csv",
        "--max-rows", "160",
        "--warmup", "80",
    ])
    run_cmd([
        "scripts/run_alpha_validation.py",
        "--trades", "data/backtest_smoke.csv",
        "--out-dir", "outputs",
        "--prefix", "avs_smoke",
    ])
    print("SMOKE_CHECK_OK")


if __name__ == "__main__":
    main()