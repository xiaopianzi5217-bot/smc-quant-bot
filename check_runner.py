# -*- coding: utf-8 -*-
"""V59.5: 检查 runner 中 gate_snapshot 修改状态"""
import io

f = io.open("runner/v11_institutional_runner.py", "r", encoding="utf-8")
content = f.read()
f.close()

checks = [
    ("gate_snapshot 顶层返回", '"gate_snapshot": _gate_snapshot'),
    ("decision 注入", 'decision["gate_snapshot"]'),
    ("open_trade 传参", 'gate_snapshot=_gate_snapshot'),
    ("gate 初始化", '_gate_snapshot = "{}"'),
    ("V59.5 快照构造", '# V59.5: 构造质量门快照'),
]
for name, marker in checks:
    count = content.count(marker)
    print(f"{name}: count={count}")

lines = content.split("\n")
for i, line in enumerate(lines, 1):
    if "gate_snapshot" in line:
        indent = len(line) - len(line.lstrip())
        print(f"{i}: indent={indent} | {line[:120]}")
