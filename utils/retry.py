# -*- coding: utf-8 -*-
"""
retry.py — 自动重试装饰器，支持指数退避

替换当前散落在各处的多重重试 while+try 块（如 hf_auto_trader.py 的 fetch_ohlcv 手动 for attempt in range(3) 循环）。

用法：
    from utils.retry import retry, retry_sync

    # 同步重试
    @retry_sync(max_attempts=3, base_delay=1.0, jitter=True)
    def fetch_data(url):
        return requests.get(url, timeout=10)

    # 异步重试
    @retry(max_attempts=3, base_delay=1.0, jitter=True)
    async def fetch_data_async(url):
        return await async_get(url)

    # 只重试特定异常
    @retry_sync(max_attempts=5, retry_on=(ConnectionError, TimeoutError))
    def fragile_call():
        ...
"""

from __future__ import annotations
import asyncio
import functools
import random
import time
from typing import Any, Callable, Optional, Tuple, Type, Union

from utils.structured_logger import slog


# ── 公共配置 ──────────────────────────────────────────────
_DEFAULT_RETRY_ON = (Exception,)  # 默认重试所有异常


def _compute_delay(attempt: int, base_delay: float = 1.0, max_delay: float = 30.0, jitter: bool = True) -> float:
    """指数退避延时计算"""
    delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
    if jitter:
        delay = delay * (0.5 + random.random() * 0.5)  # 50%-100% 抖动
    return delay


# ── 同步重试 ──────────────────────────────────────────────
def retry_sync(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: bool = True,
    retry_on: Tuple[Type[Exception], ...] = _DEFAULT_RETRY_ON,
    on_retry: Optional[Callable[[int, int, float, Exception], None]] = None,
) -> Callable:
    """
    同步函数重试装饰器
    
    参数:
        max_attempts: 最大重试次数（含首次调用）
        base_delay: 首次重试前等待秒数（指数增长）
        max_delay: 最大等待秒数
        jitter: 是否添加随机抖动
        retry_on: 仅重试这些异常类型
        on_retry: 每次重试前回调（attempt, max_attempts, delay, exception）
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except retry_on as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        slog.error(f"重试耗尽: {func.__name__}",
                                   attempt=attempt, max_attempts=max_attempts,
                                   error=str(exc), exc_info=True)
                        raise
                    delay = _compute_delay(attempt, base_delay, max_delay, jitter)
                    if on_retry:
                        on_retry(attempt, max_attempts, delay, exc)
                    else:
                        slog.warning(f"重试 #{attempt}/{max_attempts}: {func.__name__}",
                                     delay_ms=round(delay * 1000), error=str(exc))
                    time.sleep(delay)
                except Exception as exc:
                    # 非重试范围的异常直接抛出
                    raise exc
            # 不可能到达这里
            raise RuntimeError(f"Unexpected: {func.__name__} exhausted retries without result")
        return wrapper
    return decorator


# ── 异步重试 ──────────────────────────────────────────────
def retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: bool = True,
    retry_on: Tuple[Type[Exception], ...] = _DEFAULT_RETRY_ON,
    on_retry: Optional[Callable[[int, int, float, Exception], None]] = None,
) -> Callable:
    """
    异步函数重试装饰器
    
    参数同 retry_sync。
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except retry_on as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        slog.error(f"重试耗尽: {func.__name__}",
                                   attempt=attempt, max_attempts=max_attempts,
                                   error=str(exc), exc_info=True)
                        raise
                    delay = _compute_delay(attempt, base_delay, max_delay, jitter)
                    if on_retry:
                        on_retry(attempt, max_attempts, delay, exc)
                    else:
                        slog.warning(f"重试 #{attempt}/{max_attempts}: {func.__name__}",
                                     delay_ms=round(delay * 1000), error=str(exc))
                    await asyncio.sleep(delay)
                except Exception as exc:
                    raise exc
            raise RuntimeError(f"Unexpected: {func.__name__} exhausted retries without result")
        return wrapper
    return decorator


# ── 工具函数：手动重试 ────────────────────────────────────
def retry_call_sync(
    func: Callable,
    args: tuple = (),
    kwargs: dict = None,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: bool = True,
    retry_on: Tuple[Type[Exception], ...] = _DEFAULT_RETRY_ON,
) -> Any:
    """
    手动调用式重试（无需装饰器）
    
    用法：
        result = retry_call_sync(requests.get, args=(url,), kwargs={"timeout": 10})
    """
    kwargs = kwargs or {}
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            return func(*args, **kwargs)
        except retry_on as exc:
            last_exc = exc
            if attempt == max_attempts:
                raise
            delay = _compute_delay(attempt, base_delay, max_delay, jitter)
            slog.warning(f"手动重试 #{attempt}/{max_attempts}: {getattr(func, '__name__', str(func))}",
                         delay_ms=round(delay * 1000), error=str(exc))
            time.sleep(delay)
        except Exception as exc:
            raise exc
    raise RuntimeError("retry_call_sync exhausted")
