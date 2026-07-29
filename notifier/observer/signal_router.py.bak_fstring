# -*- coding: utf-8 -*-
"""Legacy observer router kept for compatibility.

New code should use notifier.manager. This module routes only Observer-layer
structure alerts and never sends Strategy or Execution messages.
"""
from notifier.manager import dispatch_observer_snapshot


def route_signal(snapshot, send_all=False):
    return dispatch_observer_snapshot(snapshot, send_all=send_all)
