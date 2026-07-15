# -*- coding: utf-8 -*-
"""
微观数据喂价器 (Micro Feeder) - 协程版 - Bitget Edition
======================================================
改用 Bitget 公开 WebSocket (wss://ws.bitget.com/v2/ws/public)
替代 Binance，解决中国大陆网络环境无法访问 Binance 的问题。

数据源:
  - 深度流: 订阅 UMCBL 的 books 频道 => 计算 OBI
  - 成交流: 订阅 UMCBL 的 trade 频道  => 计算 CVD

不依赖 ZMQ，不依赖独立进程。
直接通过共享内存字典（self.state）暴露数据。

用法:
    feeder = MicroFeeder("BTCUSDT")
    asyncio.create_task(feeder.run())
    # 任何地方访问 feeder.state["obi"], feeder.state["cvd"]
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict

try:
    import websockets
except ImportError:
    websockets = None


BITGET_WS_URL = "wss://ws.bitget.com/v2/ws/public"
DEFAULT_SYMBOL = "BTCUSDT"
RECONNECT_DELAY = 5.0
RECONNECT_MAX = 30.0
INST_TYPE = "USDT-FUTURES"


class MicroFeeder:
    def __init__(self, symbol: str = DEFAULT_SYMBOL):
        self.symbol = symbol.upper()
        self.state: Dict[str, Any] = {
            "obi": 0.0, "cvd": 0.0, "ts": 0.0, "price": 0.0,
            "bids": 0.0, "asks": 0.0, "tick_count": 0,
            "symbol": self.symbol,
        }
        self._cvd_current: float = 0.0
        self._last_price: float = 0.0
        self._tick_count: int = 0
        self._has_initial_depth: bool = False

        if websockets is None:
            raise ImportError("websockets is required. Run: pip install websockets>=11.0.3")

    async def run(self):
        print(f"[MicroFeeder] 正在连接 Bitget WS: {self.symbol}")
        retry = 1
        while True:
            try:
                async with websockets.connect(
                    BITGET_WS_URL, ping_interval=15, close_timeout=3
                ) as ws:
                    print(f"[MicroFeeder] Bitget WS 已连接: {self.symbol}")
                    retry = 1  # 重置重试计数
                    subscribe_msg = {
                        "op": "subscribe",
                        "args": [
                            {"instType": INST_TYPE, "channel": "books", "instId": self.symbol},
                            {"instType": INST_TYPE, "channel": "trade", "instId": self.symbol},
                        ],
                    }
                    await ws.send(json.dumps(subscribe_msg))
                    print("[MicroFeeder] 已发送订阅: books + trade")
                    async for raw_message in ws:
                        self._process_message(json.loads(raw_message))

            except asyncio.CancelledError:
                print("[MicroFeeder] 协程已取消")
                return
            except Exception:
                wait = min(RECONNECT_DELAY * retry, RECONNECT_MAX)
                print(f"[MicroFeeder] WS 断开，{wait:.0f}s 后重连 (第{retry}次)...")
                retry += 1
                await asyncio.sleep(wait)

    def get_snapshot(self) -> Dict[str, Any]:
        return dict(self.state)

    def _process_message(self, data: Dict[str, Any]):
        if "event" in data:
            event = data.get("event", "")
            if event == "subscribe":
                channel = data.get("arg", {}).get("channel", "")
                print(f"[MicroFeeder] 订阅成功: {channel}")
            elif event == "error":
                code = data.get("code", "")
                msg_text = data.get("msg", "")
                print(f"[MicroFeeder] 订阅错误: code={code} msg={msg_text}")
            return

        arg = data.get("arg", {})
        channel = arg.get("channel", "")
        raw_data = data.get("data", [])
        if not raw_data:
            return

        if channel == "books":
            self._handle_depth(raw_data)
        elif channel == "trade":
            self._handle_trade(raw_data)

    def _handle_depth(self, data_list: list):
        for item in data_list:
            raw_bids = item.get("bids", [])
            raw_asks = item.get("asks", [])
            bids_total = sum(float(b[1]) for b in raw_bids if len(b) >= 2)
            asks_total = sum(float(a[1]) for a in raw_asks if len(a) >= 2)
            self.state["bids"] = round(bids_total, 2)
            self.state["asks"] = round(asks_total, 2)
            total = bids_total + asks_total
            self.state["obi"] = round((bids_total - asks_total) / total, 4) if total > 0 else 0.0
            self.state["ts"] = time.time()
            if not self._has_initial_depth:
                self._has_initial_depth = True
                print(f"[MicroFeeder] 深度初始快照已接收: OBI={self.state['obi']:.4f}")

    def _handle_trade(self, data_list: list):
        for trade in data_list:
            price = float(trade.get("price", 0))
            qty = float(trade.get("size", 0))
            side = trade.get("side", "").lower()
            if price <= 0 or qty <= 0:
                continue
            self._last_price = price
            self._tick_count += 1
            if side == "buy":
                self._cvd_current += qty
            elif side == "sell":
                self._cvd_current -= qty
            self.state["cvd"] = round(self._cvd_current, 4)
            self.state["price"] = round(price, 2)
            self.state["tick_count"] = self._tick_count
            self.state["ts"] = time.time()
