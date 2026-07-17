# -*- coding: utf-8 -*-
"""简单事件总线，用于注册和触发轻量级回调。"""

from __future__ import annotations
from typing import Any, Callable

_EVENT_HANDLERS: dict[str, list[Callable[..., Any]]] = {}


def on(event_name: str, handler: Callable[..., Any]) -> None:
    """注册事件处理器。"""
    if event_name not in _EVENT_HANDLERS:
        _EVENT_HANDLERS[event_name] = []
    _EVENT_HANDLERS[event_name].append(handler)


def emit(event_name: str, *args: Any, **kwargs: Any) -> None:
    """触发事件，按注册顺序执行所有处理器。"""
    handlers = _EVENT_HANDLERS.get(event_name, [])
    for handler in handlers:
        try:
            handler(*args, **kwargs)
        except Exception as exc:
            print(f"[EventBus] 事件处理器失败: {event_name} -> {exc}")


def clear(event_name: str | None = None) -> None:
    """清理指定事件或全部事件处理器。"""
    if event_name is None:
        _EVENT_HANDLERS.clear()
    else:
        _EVENT_HANDLERS.pop(event_name, None)
