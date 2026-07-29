# -*- coding: utf-8 -*-
"""
Auto event router for SMC quant bot.

设计目标：
1) 瞬发提醒：SQZMOM / SMC 事件达到条件就自动发 Telegram。
2) 开单提醒：仍然只走系统中枢 dispatch_strategy_decision，不接下单函数。
3) 两套去重池分开，避免 Observer 瞬发拦截 Strategy 开单信息。

重要安全边界：
- 本文件不会、也不应该调用任何交易所下单函数。
- K线变色、背离、SELLSIDE / BUYSIDE 只触发提醒，不触发自动开单。
"""

from __future__ import annotations

import html
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from notifier.telegram import send_telegram
except Exception:  # pragma: no cover
    send_telegram = None  # type: ignore

try:
    from notifier.manager import dispatch_strategy_decision
except Exception:  # pragma: no cover
    dispatch_strategy_decision = None  # type: ignore


# 进程内去重。重启程序后清空，这是有意设计：避免长期状态文件影响实盘。
_INSTANT_SENT: Dict[str, float] = {}
_STRATEGY_SENT: Dict[str, float] = {}

DEFAULT_INSTANT_TTL_SEC = 60 * 12       # 同一事件 12 分钟内不重复刷屏
DEFAULT_STRATEGY_TTL_SEC = 60 * 30      # 同一方向开单提醒 30 分钟内不重复发


def _snapshot_dict(snapshot: Any) -> Dict[str, Any]:
    if snapshot is None:
        return {}
    if isinstance(snapshot, dict):
        return dict(snapshot)
    if hasattr(snapshot, "to_dict"):
        try:
            d = snapshot.to_dict()
            if isinstance(d, dict):
                return dict(d)
        except Exception:
            pass
    if hasattr(snapshot, "__dict__"):
        try:
            return dict(vars(snapshot))
        except Exception:
            pass
    return {}


def _safe_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    s = str(v).strip().lower()
    return s in {"1", "true", "yes", "y", "是", "yes!", "trigger", "triggered"}


def _safe_float(v: Any) -> Optional[float]:
    if v in [None, "", "N/A", "nan", "None"]:
        return None
    try:
        return float(v)
    except Exception:
        return None


def _last_row(df: Any) -> Dict[str, Any]:
    """Return last row from pandas dataframe as dict without requiring pandas import."""
    if df is None:
        return {}
    try:
        if len(df) <= 0:
            return {}
        row = df.iloc[-1]
        if hasattr(row, "to_dict"):
            return row.to_dict()
    except Exception:
        pass
    return {}


def _prev_row(df: Any) -> Dict[str, Any]:
    if df is None:
        return {}
    try:
        if len(df) < 2:
            return {}
        row = df.iloc[-2]
        if hasattr(row, "to_dict"):
            return row.to_dict()
    except Exception:
        pass
    return {}


def _first(keys: Iterable[str], *sources: Dict[str, Any], default: Any = None) -> Any:
    for src in sources:
        if not isinstance(src, dict):
            continue
        for k in keys:
            if k in src and src.get(k) not in [None, "", "N/A", "nan", "None"]:
                return src.get(k)
    return default


def _infer_color_changed(snapshot_data: Dict[str, Any], last: Dict[str, Any], prev: Dict[str, Any]) -> bool:
    """Infer SQZMOM color change when upstream did not pass color_changed=True."""
    if _safe_bool(_first(["color_changed"], snapshot_data, last, default=False)):
        return True

    color_cols = [
        "candle_color",
        "sqzmom_color",
        "squeeze_color",
        "mom_color",
        "momentum_color",
        "bar_color",
        "color",
    ]
    curr = _first(color_cols, last, snapshot_data, default=None)
    old = _first(color_cols, prev, default=None)

    if curr is None or old is None:
        return False
    return str(curr).strip() != str(old).strip()


def _infer_divergence(snapshot_data: Dict[str, Any], last: Dict[str, Any]) -> Tuple[bool, bool]:
    bullish = _safe_bool(_first(
        ["bullish_divergence", "bull_div", "sqzmom_bullish_divergence", "sqzmom_bull_div"],
        snapshot_data, last, default=False
    ))
    bearish = _safe_bool(_first(
        ["bearish_divergence", "bear_div", "sqzmom_bearish_divergence", "sqzmom_bear_div"],
        snapshot_data, last, default=False
    ))
    return bullish, bearish


def _infer_smc_events(snapshot_data: Dict[str, Any], last: Dict[str, Any]) -> Dict[str, bool]:
    return {
        "is_bsl_swept": _safe_bool(_first(["is_bsl_swept", "bsl_swept", "buy_side_swept"], snapshot_data, last, default=False)),
        "is_ssl_swept": _safe_bool(_first(["is_ssl_swept", "ssl_swept", "sell_side_swept"], snapshot_data, last, default=False)),
        "near_buyside": _safe_bool(_first(["near_buyside", "near_buy_side", "near_bsl"], snapshot_data, last, default=False)),
        "near_sellside": _safe_bool(_first(["near_sellside", "near_sell_side", "near_ssl"], snapshot_data, last, default=False)),
    }


def collect_instant_events(snapshot: Any, df: Any = None) -> List[Dict[str, str]]:
    """
    Collect instant Observer events.

    SQZMOM 维度：
    - color_changed
    - bullish_divergence / bearish_divergence

    SMC 维度：
    - is_ssl_swept / is_bsl_swept
    - near_sellside / near_buyside
    """
    data = _snapshot_dict(snapshot)
    last = _last_row(df)
    prev = _prev_row(df)

    events: List[Dict[str, str]] = []

    symbol = str(_first(["symbol"], data, last, default="UNKNOWN"))
    timeframe = str(_first(["timeframe", "tf"], data, last, default=""))
    price = _first(["price", "close"], data, last, default="N/A")
    candle_color = _first(
        ["candle_color", "sqzmom_color", "squeeze_color", "mom_color", "momentum_color", "bar_color", "color"],
        data, last, default="N/A"
    )

    color_changed = _infer_color_changed(data, last, prev)
    bullish_div, bearish_div = _infer_divergence(data, last)
    smc = _infer_smc_events(data, last)

    if color_changed:
        events.append({
            "type": "SQZMOM_COLOR_CHANGED",
            "title": "SQZMOM K线变色",
            "level": "instant",
            "detail": f"K线颜色变化：{candle_color}",
        })

    if bullish_div:
        events.append({
            "type": "SQZMOM_BULLISH_DIVERGENCE",
            "title": "SQZMOM 底背离",
            "level": "instant",
            "detail": "出现底背离，注意下方流动性扫完后的反弹确认。",
        })

    if bearish_div:
        events.append({
            "type": "SQZMOM_BEARISH_DIVERGENCE",
            "title": "SQZMOM 顶背离",
            "level": "instant",
            "detail": "出现顶背离，注意上方流动性扫完后的回落确认。",
        })

    if smc["is_ssl_swept"]:
        events.append({
            "type": "SMC_SELLSIDE_SWEPT",
            "title": "SMC 扫 SELL SIDE",
            "level": "instant",
            "detail": f"下方流动性被扫，SSL={data.get('ssl_level', 'N/A')}",
        })

    if smc["is_bsl_swept"]:
        events.append({
            "type": "SMC_BUYSIDE_SWEPT",
            "title": "SMC 扫 BUY SIDE",
            "level": "instant",
            "detail": f"上方流动性被扫，BSL={data.get('bsl_level', 'N/A')}",
        })

    if smc["near_sellside"]:
        events.append({
            "type": "SMC_NEAR_SELLSIDE",
            "title": "SMC 接近 SELL SIDE",
            "level": "instant",
            "detail": f"价格接近下方流动性池，SSL={data.get('ssl_level', 'N/A')}",
        })

    if smc["near_buyside"]:
        events.append({
            "type": "SMC_NEAR_BUYSIDE",
            "title": "SMC 接近 BUY SIDE",
            "level": "instant",
            "detail": f"价格接近上方流动性池，BSL={data.get('bsl_level', 'N/A')}",
        })

    # 组合事件：动量 + 结构，优先级更高，但仍然只是提醒，不开单。
    if color_changed and (smc["is_ssl_swept"] or smc["is_bsl_swept"]):
        side = "SELLSIDE" if smc["is_ssl_swept"] else "BUYSIDE"
        events.append({
            "type": "COMBO_SQZMOM_SMC",
            "title": f"高价值提醒：SQZMOM变色 + 扫{side}",
            "level": "important",
            "detail": "动量变化与流动性扫单同时出现，建议重点观察系统中枢是否确认 Strategy。",
        })

    # 补充上下文
    for e in events:
        e["symbol"] = symbol
        e["timeframe"] = timeframe
        e["price"] = str(price)

    return events


def _dedupe_key(event: Dict[str, str], bar_time: Any = None) -> str:
    symbol = event.get("symbol", "UNKNOWN")
    tf = event.get("timeframe", "")
    etype = event.get("type", "EVENT")
    # 有 K线时间则按 K线时间去重；没有则按事件类型 + TTL 去重。
    bt = str(bar_time or "")
    return f"{symbol}|{tf}|{etype}|{bt}"


def _allow_once(pool: Dict[str, float], key: str, ttl_sec: int) -> bool:
    now = time.time()
    # 清理旧 key
    old = [k for k, ts in pool.items() if now - ts > max(ttl_sec, 1)]
    for k in old:
        pool.pop(k, None)
    if key in pool and now - pool[key] <= ttl_sec:
        return False
    pool[key] = now
    return True


def _format_instant_message(event: Dict[str, str], snapshot: Any) -> str:
    data = _snapshot_dict(snapshot)
    esc = lambda x: html.escape(str(x), quote=False)

    direction = data.get("direction_bias", data.get("direction", "N/A"))
    long_score = data.get("long_score", "N/A")
    short_score = data.get("short_score", "N/A")
    trend = data.get("trend_result", "N/A")
    squeeze = data.get("squeeze", "N/A")
    regime = data.get("regime", "N/A")

    level_icon = "⚡" if event.get("level") == "instant" else "🔥"

    return (
        f"{level_icon} <b>{esc(event.get('title', 'Observer 瞬发提醒'))}</b>\n"
        f"品种：<b>{esc(event.get('symbol', 'UNKNOWN'))}</b> {esc(event.get('timeframe', ''))}\n"
        f"价格：<b>{esc(event.get('price', 'N/A'))}</b>\n"
        f"事件：{esc(event.get('detail', ''))}\n\n"
        f"<b>上下文</b>\n"
        f"方向：{esc(direction)}\n"
        f"趋势：{esc(trend)}｜行情：{esc(regime)}｜压缩：{esc(squeeze)}\n"
        f"评分：Long {esc(long_score)} / Short {esc(short_score)}\n\n"
        f"说明：这是 Observer 瞬发提醒，只提示结构/动量变化，不代表系统中枢已确认开单。"
    )[:3900]


def dispatch_instant_alerts(
    snapshot: Any,
    df: Any = None,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    ttl_sec: int = DEFAULT_INSTANT_TTL_SEC,
    dry_run: bool = False,
) -> List[Dict[str, str]]:
    """
    Send instant alerts when Observer events are triggered.

    返回已发送或 dry_run 命中的事件列表。
    """
    events = collect_instant_events(snapshot, df=df)
    if not events:
        return []

    data = _snapshot_dict(snapshot)
    last = _last_row(df)
    bar_time = _first(["time", "timestamp", "datetime", "open_time"], data, last, default=None)

    sent: List[Dict[str, str]] = []
    for event in events:
        if symbol:
            event["symbol"] = str(symbol)
        if timeframe:
            event["timeframe"] = str(timeframe)

        key = _dedupe_key(event, bar_time=bar_time)
        if not _allow_once(_INSTANT_SENT, key, ttl_sec):
            continue

        msg = _format_instant_message(event, snapshot)
        if dry_run:
            event["message"] = msg
            sent.append(event)
            continue

        if send_telegram is None:
            continue
        try:
            send_telegram(msg)
            sent.append(event)
        except Exception:
            # 不让提醒失败影响主循环和开单中枢
            continue

    return sent


def _decision_confirmed(decision: Any, snapshot: Any = None) -> bool:
    """判断系统中枢是否确认 Strategy。尽量兼容不同 decision 结构。"""
    if decision is None:
        return False
    if isinstance(decision, dict):
        d = decision
    elif hasattr(decision, "to_dict"):
        try:
            d = decision.to_dict()
        except Exception:
            d = vars(decision) if hasattr(decision, "__dict__") else {}
    elif hasattr(decision, "__dict__"):
        d = vars(decision)
    else:
        d = {}

    if _safe_bool(_first(["confirmed", "is_signal", "can_trade", "tradable", "should_trade"], d, default=False)):
        return True

    action = str(_first(["action", "decision", "signal", "side", "direction"], d, default="")).strip().upper()
    if action in {"LONG", "SHORT", "BUY", "SELL", "OPEN_LONG", "OPEN_SHORT"}:
        return True

    # 兜底：用 snapshot 评分阈值判断是否可能是可执行机会。
    s = _snapshot_dict(snapshot)
    long_score = _safe_float(s.get("long_score"))
    short_score = _safe_float(s.get("short_score"))
    long_th = _safe_float(s.get("threshold_long"))
    short_th = _safe_float(s.get("threshold_short"))
    if long_score is not None and long_th is not None and long_score >= long_th:
        return True
    if short_score is not None and short_th is not None and short_score >= short_th:
        return True

    return False


def dispatch_strategy_alert_if_confirmed(
    snapshot: Any,
    decision: Any,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    ttl_sec: int = DEFAULT_STRATEGY_TTL_SEC,
    dry_run: bool = False,
) -> bool:
    """
    Strategy 开单提醒入口。

    注意：
    - 这里只调用 notifier.manager.dispatch_strategy_decision。
    - 不调用任何自动下单函数。
    - 如果你的主程序已经调用 dispatch_strategy_decision，就不要重复调用本函数。
    """
    if not _decision_confirmed(decision, snapshot=snapshot):
        return False

    data = _snapshot_dict(snapshot)
    sym = symbol or data.get("symbol", "UNKNOWN")
    direction = data.get("direction_bias", data.get("direction", "N/A"))
    key = f"{sym}|{timeframe or data.get('timeframe', '')}|STRATEGY|{direction}"

    if not _allow_once(_STRATEGY_SENT, key, ttl_sec):
        return False

    if dry_run:
        return True

    if dispatch_strategy_decision is None:
        return False

    try:
        dispatch_strategy_decision(snapshot, decision)
        return True
    except TypeError:
        try:
            dispatch_strategy_decision(snapshot=snapshot, decision=decision)
            return True
        except Exception:
            return False
    except Exception:
        return False


__all__ = [
    "collect_instant_events",
    "dispatch_instant_alerts",
    "dispatch_strategy_alert_if_confirmed",
]
