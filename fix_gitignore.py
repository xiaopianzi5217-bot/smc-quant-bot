# -*- coding: utf-8 -*-
"""修复 .gitignore 文件，移除 UTF-16 乱码并添加 *.pkl 规则"""
from pathlib import Path

# 读取为字节并
raw = Path('.gitignore').read_bytes()

# 找到 UTF-16 编码的 'data/*.bak' 部分开始
marker = b'd\x00a\x00t\x00a\x00/\x00*\x00.\x00b\x00a\x00k\x00'
idx = raw.find(marker)
if idx > 0:
    raw = raw[:idx]
    print(f'移除乱码部分 (位置 {idx})')
else:
    print('未找到 UTF-16 乱码，检查内容...')

# 转换为文本并清理
content = raw.decode('utf-8', errors='replace')
lines = content.split('\r\n') if '\r\n' in content else content.split('\n')
lines = [l.strip() for l in lines if l.strip()]

# 添加 pkl 和模型目录排除
if '*.pkl' not in lines:
    lines.append('*.pkl')
if 'models/*.pkl' not in lines:
    lines.append('models/*.pkl')
if 'models/*.joblib' not in lines:
    lines.append('models/*.joblib')

# 重写文件
Path('.gitignore').write_text('\n'.join(lines) + '\n', encoding='utf-8')
print('\n✅ .gitignore 已修复!')
print('\n完整内容:')
print('---')
print(Path('.gitignore').read_text(encoding='utf-8'))
