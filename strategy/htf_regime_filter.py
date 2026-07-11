# -*- coding: utf-8 -*-
"""
HTF Regime Filter — 优化2: 1H 高级别趋势过滤

当前最大的缺陷：没有 HTF（Higher Time Frame）方向验证。
震荡/逆势信号也能进入，导致大量垃圾交易。

解决方案：
  用 1H 的 EMA50/EMA200 判断大方向，
  只有符合大方向的信号才允许进入。

用法:
  from strategy.htf_regime_filter import HTFRegimeFilter, htf_regime_filter
  state = htf_regime_filter.analyze(df_1h)
  # state["regime"] = "BULL" | "BEAR" | "RANGE"
  # state["allow_long"] = True/False
  # state["allow_short"] = True/False
"""

from __future__ import annotations

from typing import Dict, Any, Optional
import pandas as pd


class HTFRegimeFilter:
    """高级别趋势过滤器

    使用 EMA50 和 EMA200 判断市场当前的大方向，
    只有与 HTF 方向一致的信号才允许开单。
    """

    def __init__(self, ema_fast: int = 50, ema_slow: int = 200):
        """
        参数:
            ema_fast: 快线周期（默认 50）
            ema_slow: 慢线周期（默认 200）
        """
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow

    def analyze(self, df: pd.DataFrame) -> Dict[str, Any]:
        """分析 HTF 市场状态

        参数:
            df: 1H (或更高周期) OHLCV 数据，需含 "close" 列

        返回:
            {
                "regime": "BULL" | "BEAR" | "RANGE",
                "allow_long": bool,
                "allow_short": bool,
                "price": float,
                "ema_fast": float | None,
                "ema_slow": float | None,
                "trend_strength": float,  # 趋势强度 (0~1)
                "bars_available": int,
            }
        """
        if df is None or len(df) < self.ema_slow:
            # 数据不足时，默认全部允许（保守降级）
            return {
                "regime": "RANGE",
                "allow_long": True,
                "allow_short": True,
                "price": 0.0,
                "ema_fast": None,
                "ema_slow": None,
                "trend_strength": 0.0,
                "bars_available": len(df) if df is not None else 0,
            }

        close = df["close"]
        price = float(close.iloc[-1])

        ema_fast_val = float(close.ewm(span=self.ema_fast).mean().iloc[-1])
        ema_slow_val = float(close.ewm(span=self.ema_slow).mean().iloc[-1])

        # ---- 趋势判断 ----
        # BULL: 价格 > EMA50 > EMA200
        # BEAR: 价格 < EMA50 < EMA200
        # RANGE: 其他（交叉、纠缠等）
        if price > ema_fast_val > ema_slow_val:
            regime = "BULL"
            allow_long = True
            allow_short = False
        elif price < ema_fast_val < ema_slow_val:
            regime = "BEAR"
            allow_long = False
            allow_short = True
        else:
            regime = "RANGE"
            allow_long = True
            allow_short = True

        # ---- 趋势强度估算 ----
        # 用价格与 EMA50 的距离比例衡量趋势强度
        atr_est = (close.iloc[-20:].max() - close.iloc[-20:].min()) / 20.0 if len(close) >= 20 else 0.0
        if atr_est > 0:
            trend_strength = min(1.0, abs(price - ema_fast_val) / (atr_est * 3))
        else:
            trend_strength = 0.0

        return {
            "regime": regime,
            "allow_long": allow_long,
            "allow_short": allow_short,
            "price": price,
            "ema_fast": round(ema_fast_val, 2),
            "ema_slow": round(ema_slow_val, 2),
            "trend_strength": round(trend_strength, 4),
            "bars_available": len(df),
        }


# 全局单例
_htf_regime_filter = HTFRegimeFilter()


def get_htf_regime_filter() -> HTFRegimeFilter:
    return _htf_regime_filter
