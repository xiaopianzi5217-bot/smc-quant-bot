# -*- coding: utf-8 -*-
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 测试推断缺失字节

# 例1: 绐佺牬姒傜巼璇勫垎锛?~100
# 应该是: 突破概率评分：~100
# 缺失的是中文冒号的最后一个字节 9a

# 我们看看能否猜测
common_utf8_2byte_endings = {
    '9a': '：',  # ef bc 9a
    '82': '。',  # e3 80 82
    '92': '→',   # e2 86 92
}

# 例2: 鈫? -> 箭头 →
# e2 86 92，缺失 92

# 例3: 銆? -> 句号 。  
# e3 80 82，缺失 82

# 测试手动修复一个完整片段
segment = '绐佺牬姒傜巼璇勫垎锛?~100锛夛紝鏇夸唬鏃х殑'

# 先看这个片段中哪些字符可以修复
for ch in segment:
    if '\u4e00' <= ch <= '\u9fff':
        gb = ch.encode('gbk')
        print(f'{ch} -> GBK: {gb.hex(" ")}')
    else:
        print(f'{ch!r} -> ASCII: {ord(ch):#04x}')
