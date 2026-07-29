# -*- coding: utf-8 -*-
"""
safe_invoke.py — 安全调用包装器

替代零散的 try/except/pass 块，提供统一的安全调用模式：

    from utils.safe_invoke import safe_invoke, safe_call, default_result

    # 1. 函数级安全调用（带默认返回值）
    data = safe_invoke(fetch_api_data, "BTC/USDT", default=None)

    # 2. 装饰器风格
    @safe_call("data")
    def fetch_data(url):
        return requests.get(url)

    # 3. 作为上下文管理器
    with default_result({}):
        return risky_function()

    # 4. 静默模式（不打印任何日志）
    data = safe_invoke(fetch_api_data, silent=True, default=[])
"""

from __future__ import annotations
import functools
import traceback
from contextlib import contextmanager
from typing import Any, Callable, Optional, Type, Union

from utils.structured_logger import slog


# ── 默认返回值 ────────────────────────────────────────────
_DEFAULT = object()  # sentinel


# ── 函数调用安全包装 ─────────────────────────────────────
def safe_invoke(
    func: Callable,
    *args,
    default: Any = _DEFAULT,
    silent: bool = False,
    log_level: str = "warning",
    on_error: Optional[Callable[[Exception], Any]] = None,
    **kwargs,
) -> Any:
    """
    调用一个函数，如果抛出异常则返回 default 值。

    参数:
        func: 要调用的函数
        default: 异常时返回的默认值（不设置则仍抛出异常）
        silent: 静默模式（不打印日志）
        log_level: 日志级别（debug/info/warning/error）
        on_error: 异常时的自定义回调（返回值优先于 default）
    
    用法:
        # 有默认值
        data = safe_invoke(requests.get, url, timeout=10, default=None)
        
        # 无默认值（仍会抛出）
        data = safe_invoke(critical_func, default=raise_exception)
        
        # 静默模式
        result = safe_invoke(fragile_func, silent=True, default=[])
    """
    try:
        return func(*args, **kwargs)
    except Exception as exc:
        if on_error is not None:
            return on_error(exc)
        if default is not _DEFAULT:
            if not silent:
                log_method = getattr(slog, log_level, slog.warning)
                log_method(f"安全调用失败: {getattr(func, '__name__', str(func))}",
                           error=str(exc), default=str(default)[:80])
            return default
        raise


# ── 装饰器风格 ────────────────────────────────────────────
def safe_call(default: Any = None, silent: bool = False, log_level: str = "warning"):
    """
    装饰器：将函数的异常转换为默认返回值

    用法:
        @safe_call(default=[])
        def parse_data(raw):
            return json.loads(raw)
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                if not silent:
                    log_method = getattr(slog, log_level, slog.warning)
                    log_method(f"安全调用装饰器捕获: {func.__name__}",
                               error=str(exc))
                return default
        return wrapper
    return decorator


# ── 上下文管理器 ──────────────────────────────────────────
@contextmanager
def default_result(default: Any = None, silent: bool = False):
    """
    上下文管理器：包裹代码块，异常时返回默认值

    用法:
        with default_result([]):
            result = risky_operation()
    """
    try:
        yield
    except Exception as exc:
        if not silent:
            slog.warning(f"安全上下文捕获异常", error=str(exc))
        # 将默认值设为上下文的结果
        # 注意：调用方需要用 with ... as result:
        yield default
        return


# ── 错误分类辅助 ──────────────────────────────────────────
class ErrorCategory:
    """错误分类标记，帮助判断错误严重性"""
    RETRYABLE = ("timeout", "connection", "reset", "ssl", "eagain", "rate limit", "too many requests")
    IGNORABLE = ("no data", "not found", "404", "empty")
    CRITICAL = ("auth", "forbidden", "403", "401", "insufficient")
    
    @classmethod
    def classify(cls, error_msg: str) -> str:
        lower = str(error_msg).lower()
        for keyword in cls.CRITICAL:
            if keyword in lower:
                return "critical"
        for keyword in cls.RETRYABLE:
            if keyword in lower:
                return "retryable"
        for keyword in cls.IGNORABLE:
            if keyword in lower:
                return "ignorable"
        return "unknown"
