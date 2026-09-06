"""拉取最新云端DB并列出全部平仓记录时间分布"""
import os, sys

# 加载 token
if os.path.exists('.env'):
    for line in open('.env', encoding='utf-8').read().splitlines():
        if line.startswith('HF_TOKEN='):
            val = line.split('=',1)[1].strip().strip('\'"')
            if val:
                os.environ['HF_TOKEN'] = val

# 强制拉取最新云端版本(绕过本地确认缓存)
import v6_data_engine, inspect
print('pull signature:', inspect.signature(v6_data_engine.pull_database_from_hub))
