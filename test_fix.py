# -*- coding: utf-8 -*-
"""对单个文件测试修复"""

# 读取文件
with open('strategy/probabilistic_breakout.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 提取一段乱码看看
import re
mojibake = content[50:200]
print('MoJiBake sample:', repr(mojibake))
print()

# 尝试修复
for enc in ['gbk', 'gb2312', 'cp936', 'big5', 'gb18030']:
    try:
        fixed = mojibake.encode(enc).decode('utf-8')
        print(f'{enc}: SUCCESS -> {fixed[:100]}')
    except Exception as e:
        print(f'{enc}: FAIL - {str(e)[:60]}')
