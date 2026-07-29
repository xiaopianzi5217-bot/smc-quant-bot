# -*- coding: utf-8 -*-
"""
structured_logger.py — 结构化日志工具

替代散落各处的 print(f"[{symbol}] ...")，改为 JSON 格式的结构化日志。
方便后期接入日志收集系统（ELK、Grafana Loki、文件轮转等）。

用法：
    from utils.structured_logger import slog
    
    # 简单日志
    slog.info("策略扫描开始", symbol="BTC/USDT", score=85.2)
    
    # 警告 + 异常
    slog.warning("数据获取失败", symbol="ETH/USDT", error=e, network="bitget")
    
    # 错误 + 堆栈
    slog.error("决策管线崩溃", symbol="SOL/USDT", exc_info=True)
    
    # 不同层级
    slog.debug("调试信息")
    slog.info("普通信息") 
    slog.warning("警告")
    slog.error("错误")
    slog.critical("致命错误")
    
    # 性能计时
    with slog.timer("fetch_ohlcv", symbol="BTC/USDT"):
        df = await fetch_data(...)
"""

from __future__ import annotations
import json
import logging
import sys
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional


# ── 北京时区 ──────────────────────────────────────────────
_BJ_TZ = timezone(timedelta(hours=8))


def _bj_now() -> str:
    """返回北京时间的 ISO 格式字符串"""
    return datetime.now(_BJ_TZ).isoformat(timespec="milliseconds")


# ── JSON 格式化器 ─────────────────────────────────────────
class _JSONFormatter(logging.Formatter):
    """输出 JSON 格式的日志记录"""
    
    def format(self, record: logging.LogRecord) -> str:
        log_entry: Dict[str, Any] = {
            "ts": _bj_now(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        # 附加 extra 字段
        if hasattr(record, "extra_fields") and isinstance(record.extra_fields, dict):
            log_entry.update(record.extra_fields)
        
        # 异常信息
        if record.exc_info and record.exc_info[0]:
            log_entry["exc_type"] = record.exc_info[0].__name__
            log_entry["traceback"] = "".join(traceback.format_exception(*record.exc_info))
        
        # 文件位置
        log_entry["location"] = f"{record.pathname}:{record.lineno}"
        
        return json.dumps(log_entry, ensure_ascii=False, default=str)


# ── 自定义 Logger ─────────────────────────────────────────
class _StructuredLogger:
    """
    结构化日志单例
    
    输出 JSON 到 stdout（stderr 给 ERROR+），同时保留普通 Python logger 兼容。
    """
    
    def __init__(self, name: str = "smc_bot", level: int = logging.DEBUG):
        self._logger = logging.getLogger(name)
        self._logger.setLevel(level)
        self._logger.handlers.clear()
        
        # stdout handler (INFO 及以下)
        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setLevel(logging.DEBUG)
        stdout_handler.addFilter(lambda r: r.levelno < logging.WARNING)
        stdout_handler.setFormatter(_JSONFormatter())
        self._logger.addHandler(stdout_handler)
        
        # stderr handler (WARNING 及以上)
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setLevel(logging.WARNING)
        stderr_handler.setFormatter(_JSONFormatter())
        self._logger.addHandler(stderr_handler)
    
    def _log(self, level: int, msg: str, **kwargs) -> None:
        """附加 extra 字段"""
        # 分离 exc_info
        exc_info = kwargs.pop("exc_info", False)
        
        # 创建一个包含 extra_fields 的 LogRecord
        extra = {"extra_fields": kwargs} if kwargs else None
        
        if extra:
            self._logger.log(level, msg, extra=extra, exc_info=exc_info)
        else:
            self._logger.log(level, msg, exc_info=exc_info)
    
    # ── 快捷方法 ──────────────────────────────────────────
    
    def debug(self, msg: str, **kwargs) -> None:
        self._log(logging.DEBUG, msg, **kwargs)
    
    def info(self, msg: str, **kwargs) -> None:
        self._log(logging.INFO, msg, **kwargs)
    
    def warning(self, msg: str, **kwargs) -> None:
        self._log(logging.WARNING, msg, **kwargs)
    
    def error(self, msg: str, **kwargs) -> None:
        self._log(logging.ERROR, msg, **kwargs)
    
    def critical(self, msg: str, **kwargs) -> None:
        self._log(logging.CRITICAL, msg, **kwargs)
    
    # ── 上下文计时器 ──────────────────────────────────────
    
    @contextmanager
    def timer(self, name: str, **context):
        """
        性能计时上下文
        
        用法：
            with slog.timer("fetch_ohlcv", symbol="BTC/USDT", timeframe="15m"):
                df = await fetch_data(...)
        """
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            self.info(f"[TIMER] {name}", duration_ms=round(elapsed * 1000, 2), **context)
    
    # ── 文件日志持久化配置 ────────────────────────────────
    
    def add_file_handler(self, filepath: str, level: int = logging.DEBUG) -> None:
        """添加文件输出处理器（日志轮转）"""
        from logging.handlers import RotatingFileHandler
        
        handler = RotatingFileHandler(
            filepath, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        handler.setLevel(level)
        handler.setFormatter(_JSONFormatter())
        self._logger.addHandler(handler)
        self.info("文件日志已启用", path=filepath)


# ── 单例 ──
slog = _StructuredLogger()
