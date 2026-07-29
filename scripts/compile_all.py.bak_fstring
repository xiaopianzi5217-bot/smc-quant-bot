# -*- coding: utf-8 -*-
import py_compile
import pathlib
import json

ROOT = pathlib.Path(__file__).resolve().parents[1]
checked = 0
errors = []
for p in ROOT.rglob('*.py'):
    if '__pycache__' in p.parts:
        continue
    try:
        py_compile.compile(str(p), doraise=True)
        checked += 1
    except Exception as exc:
        errors.append({"file": str(p.relative_to(ROOT)), "error": f"{type(exc).__name__}: {exc}"})

out = {"checked_py_files": checked, "errors": errors, "status": "PASS" if not errors else "FAIL"}
print(json.dumps(out, ensure_ascii=False, indent=2))
