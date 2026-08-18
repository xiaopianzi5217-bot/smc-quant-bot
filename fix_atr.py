# -*- coding: utf-8 -*-
"""修复 hf_auto_trader.py 中 _atr_val 未赋值问题"""

path = "hf_auto_trader.py"

with open(path, encoding="utf-8") as f:
    src = f.read()

# 精确匹配无交易分支中的 curr 和 regime_name 两行
old = """        curr = df_exec.iloc[-1]
        regime_name = str(get_htf_regime_filter().analyze(df_macro).get("regime", "UNKNOWN")).upper().strip()"""

new = """        curr = df_exec.iloc[-1]
        # 【修复】_atr_val 需在无交易路径也预先赋值，避免 UnboundLocalError
        _atr_val = max(float(curr.get("ATRr_14", exec_ctx.get("atr", 0))), float(curr["close"]) * 0.0025)
        regime_name = str(get_htf_regime_filter().analyze(df_macro).get("regime", "UNKNOWN")).upper().strip()"""

count = src.count(old)
print(f"找到匹配: {count} 处")
if count > 0:
    src = src.replace(old, new, 1)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(src)
    print("修复完成")
else:
    # 尝试另一种方式：找行号来定位
    lines = src.split("\n")
    for i, line in enumerate(lines):
        if "V56.5 选择后无交易" in line:
            print(f"找到目标位置: 行 {i+1}")
            print(f"行 {i+2}: {lines[i+1]}")
            print(f"行 {i+3}: {lines[i+2]}")
            print(f"行 {i+4}: {lines[i+3]}")
            print("---")
            # 在第 i+2 行 (curr 行) 后面插入 _atr_val 初始化
            if lines[i+2].strip().startswith("curr = df_exec.iloc[-1]"):
                insert_at = i + 3  # 在 curr 行之后插入
                lines.insert(insert_at, '        # 【修复】_atr_val 需在无交易路径也预先赋值，避免 UnboundLocalError')
                lines.insert(insert_at + 1, '        _atr_val = max(float(curr.get("ATRr_14", exec_ctx.get("atr", 0))), float(curr["close"]) * 0.0025)')
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write("\n".join(lines))
                print("通过行号插入修复完成")
            else:
                print("行号定位失败，请手动检查")
    else:
        print("未找到 V56.5 选择后无交易 的位置")