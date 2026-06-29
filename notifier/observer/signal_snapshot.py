# -*- coding: utf-8 -*-
"""Compatibility wrapper.

旧代码如果从 notifier.observer.signal_snapshot 导入 build_signal_snapshot，
统一转到新版 signal_collector，避免旧文件缺少导入导致异常。
"""
from notifier.observer.signal_collector import SignalSnapshot, build_signal_snapshot, collect_signal

__all__ = ["SignalSnapshot", "build_signal_snapshot", "collect_signal"]
