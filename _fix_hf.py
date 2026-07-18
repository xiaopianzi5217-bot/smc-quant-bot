# -*- coding: utf-8 -*-
import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))

with open('hf_auto_trader.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_func = '''def async_background_task(coro):
    """Schedule an async coroutine to run in the background."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
    except RuntimeError:
        threading.Thread(target=lambda: asyncio.run(coro), daemon=True).start()'''

new_func = '''def async_background_task(coro_or_func, *args, **kwargs):
    """Unified background task dispatcher. Compatible with coroutines & sync functions."""
    if asyncio.iscoroutine(coro_or_func):
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro_or_func)
        except RuntimeError:
            threading.Thread(target=lambda: asyncio.run(coro_or_func), daemon=True).start()
        return
    if asyncio.iscoroutinefunction(coro_or_func):
        coro = coro_or_func(*args, **kwargs)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro)
        except RuntimeError:
            threading.Thread(target=lambda: asyncio.run(coro), daemon=True).start()
        return
    try:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, lambda: coro_or_func(*args, **kwargs))
    except RuntimeError:
        threading.Thread(target=lambda: coro_or_func(*args, **kwargs), daemon=True).start()'''

count1 = content.count(old_func)
print(f'async_background_task occurrences: {count1}')
assert count1 == 1, "not unique"
content = content.replace(old_func, new_func, 1)
print("Step 1 done")

old_call = '''    # 【修复】_bg_weixin_push 是同步函数，用 run_in_executor 放入线程池
    _weixin_msg = f"V6 分级开仓通知\n级别: {level} ({score}分)\n标的: {symbol}\n实盘仓位: {trade_size}"
    try:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _bg_weixin_push, _weixin_msg)
    except RuntimeError:
        threading.Thread(target=_bg_weixin_push, args=(_weixin_msg,), daemon=True).start()
    return True'''

new_call = '''    _weixin_msg = f"V6 分级开仓通知\n级别: {level} ({score}分)\n标的: {symbol}\n实盘仓位: {trade_size}"
    async_background_task(_bg_weixin_push, _weixin_msg)
    return True'''

count2 = content.count(old_call)
print(f'call site occurrences: {count2}')
assert count2 == 1, "call site not unique"
content = content.replace(old_call, new_call, 1)
print("Step 2 done")

with open('hf_auto_trader.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("All done!")
