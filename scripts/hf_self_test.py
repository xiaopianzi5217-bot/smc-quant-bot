# -*- coding: utf-8 -*-
"""Hugging Face Spaces readiness check without launching Gradio."""
import importlib
import json
import pathlib
import py_compile
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main():
    checks = {}
    py_compile.compile(str(ROOT / "app.py"), doraise=True)
    checks["app.py"] = "syntax_ok"
    for mod in ["runner.v11_institutional_runner", "backtest.runner", "ops.env_config", "risk.global_risk", "execution.paper_broker"]:
        importlib.import_module(mod)
        checks[mod] = "ok"
    from runner.v11_institutional_runner import run_once
    checks["dry_run_results"] = run_once()
    print(json.dumps(checks, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
