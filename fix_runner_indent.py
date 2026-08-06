# -*- coding: utf-8 -*-
"""修复 runner 缩进错误：V56.5 gate 块从 12 格修复为 4 格"""
import io

src = "runner/v11_institutional_runner.py"
with io.open(src, "r", encoding="utf-8") as f:
    lines = f.readlines()

# 修复 355-362 行（index 354-361）缩进从 12 → 4
# 前提：这些行原本是顶层（函数体内 4 格缩进）
for i in range(354, 362):
    if i < len(lines):
        line = lines[i]
        stripped = line.lstrip()
        # 跳过空行
        if not stripped.strip():
            continue
        indent = len(line) - len(stripped)
        if indent == 12:
            lines[i] = "    " + stripped
            print(f"Fixed line {i+1}: {stripped[:80]}")
        elif indent == 12 and "# ===== V56.5" in line:
            lines[i] = "    " + stripped
            print(f"Fixed line {i+1}: {stripped[:80]}")

with io.open(src, "w", encoding="utf-8") as f:
    f.writelines(lines)
print("[OK] 缩进修复完成")
