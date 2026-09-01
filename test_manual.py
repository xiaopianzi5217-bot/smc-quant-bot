# -*- coding: utf-8 -*-
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 测试：单独修复一个片段
segment = '绐佺牬姒傜巼璇勫垎锛'

# 方法：逐个字符编码
byte_buffer = bytearray()
for ch in segment:
    gb = ch.encode('gbk')
    byte_buffer.extend(gb)

print(f'字节: {bytes(byte_buffer).hex(" ")}')

try:
    fixed = bytes(byte_buffer).decode('utf-8')
    print(f'修复后: {fixed}')
except Exception as e:
    print(f'修复失败: {e}')

print()
# 看看锛 的GBK编码应该是什么
print(f'锛 (U+951B) GBK编码: {chr(0x951B).encode("gbk").hex(" ")}')

# 实际上，冒号 UTF-8 编码是 ef bc 9a
print(f'冒号 UTF-8: {":".encode("utf-8").hex(" ")}')  # 等等这不是中文冒号
print(f'中文冒号： UTF-8: {"锛".encode("utf-8").hex(" ")}')
