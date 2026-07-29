#!/usr/bin/env python3

"""Fix slog f-string prefixes in all .py files"""

from pathlib import Path
import re
import ast

def fix_file(filepath):
    p = Path(filepath)
    if not p.exists():
        print(f"❌ 找不到: {filepath}")
        return 0
    
    text = p.read_text(encoding="utf-8")
    backup = p.with_suffix(p.suffix + ".bak_fstring")
    if not backup.exists():
        backup.write_text(text, encoding="utf-8")
        print(f"  已备份: {backup}")
    
    # 匹配 slog.info/warning/error/debug("...{var}...") 且尚未是 f"
    pat = re.compile(
        r'\b(slog\.(?:info|warning|error|debug))\("([^"]*\{[^"]*)"\)'
    )
    
    def repl(m):
        fn = m.group(1)
        body = m.group(2)
        # 避免重复加 f（如果已经是 f" 不会匹配这个正则）
        return f'{fn}(f"{body}")'
    
    new_text, n = pat.subn(repl, text)
    
    if n == 0:
        print(f"  ✓ 无需修复 ({filepath})")
        return 0
    
    # 语法检查
    try:
        ast.parse(new_text)
    except SyntaxError as e:
        print(f"  ❌ 语法错误，已回滚: {e}")
        p.write_text(text, encoding="utf-8")
        return -1
    
    p.write_text(new_text, encoding="utf-8")
    print(f"  ✅ 已修复 {n} 处 ({filepath})")
    return n


def main():
    # 找到所有 .py 文件
    project_root = Path(".")
    py_files = sorted(project_root.rglob("*.py"))
    
    total = 0
    fixed_files = 0
    
    for f in py_files:
        # 跳过备份文件和缓存
        if f.suffix == ".bak_fstring":
            continue
        if "__pycache__" in str(f):
            continue
        
        n = fix_file(str(f))
        if n > 0:
            total += n
            fixed_files += 1
    
    print(f"\n📊 总计: 修复 {fixed_files} 个文件, {total} 处 f-string")
    
    # 抽查示例
    print("\n📝 示例 (前5处修复):")
    count = 0
    for f in py_files:
        if f.suffix == ".bak_fstring" or "__pycache__" in str(f):
            continue
        text = f.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if 'slog.' in stripped and 'f"' in stripped and '{' in stripped:
                print(f"  {f}: {stripped[:120]}")
                count += 1
                if count >= 5:
                    break
        if count >= 5:
            break

if __name__ == "__main__":
    main()