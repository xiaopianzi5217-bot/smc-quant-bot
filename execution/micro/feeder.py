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
RECONNECT_DELAY_BASE = 10.0     # 【修复】基础重连等待从5→10秒，给Bitget更多恢复时间
RECONNECT_MAX = 60.0            # 最大重连等待
RECONNECT_MAX_ATTEMPTS = 50     # 【修复】从20→50，应对间歇性断连
HEARTBEAT_INTERVAL = 15.0       # 心跳检查间隔（秒）
HEARTBEAT_TIMEOUT = 25.0        # 超过此时间无 Pong 则主动断开
# 【新增】主动向 Bitget 发送 ping 保活
PING_INTERVAL = 15.0            # 每15秒发一次 ping
# 【新增】连续断连惩罚（120秒窗口内断连≥3次，额外等待20秒）
CONSECUTIVE_WINDOW = 120
CONSECUTIVE_THRESHOLD = 3
CONSECUTIVE_PENALTY = 20.0
INST_TYPE = "USDT-FUTURES"
BOOKS_TOPIC = "books"
TRADE_TOPIC = "trade"


class MicroFeeder:
    def __init__(self, symbol: str = DEFAULT_SYMBOL):
        self.symbol = symbol.upper()
        self.state: Dict[str, Any] = {
            "obi": 0.0, "cvd": 0.0, "ts": 0.0, "price": 0.0,
            "bids": 0.0, "asks": 0.0, "tick_count": 0,
            "symbol": self.symbol,
            "is_stale": False,      # 数据是否已过时（断连后标记）
            "error_code": "",       # 最后一次错误码
        }
        self._cvd_current: float = 0.0
        self._last_price: float = 0.0
        self._tick_count: int = 0
        self._has_initial_depth: bool = False

        # ---- 重连控制 ----
        self._consecutive_disconnect_log: list = []  # 【新增】记录断连时间戳，用于连续断连惩罚
        self._retry_count: int = 0

        # ---- 心跳控制 ----
        self._last_pong_ts: float = time.time()
        self._heartbeat_task = None

        if websockets is None:
            raise ImportError("websockets is required. Run: pip install websockets>=11.0.3")

    async def run(self):
        print(f"[MicroFeeder] 启动: {self.symbol}")
        while self._retry_count < RECONNECT_MAX_ATTEMPTS:
            try:
                async with websockets.connect(
                    BITGET_WS_URL,
                    ping_interval=HEARTBEAT_INTERVAL,
                    ping_timeout=HEARTBEAT_TIMEOUT,
                    close_timeout=3,
                    max_size=2**20,   # 1 MB 消息上限
                ) as ws:
                    print(f"[MicroFeeder] Bitget WS 已连接: {self.symbol}")
                    self._retry_count = 0
                    self.state["is_stale"] = True
                    self.state["error_code"] = ""

                    # ---- 发送订阅 ----
                    subscribe_msg = {
                        "op": "subscribe",
                        "args": [
                            {"instType": INST_TYPE, "channel": BOOKS_TOPIC, "instId": self.symbol},
                            {"instType": INST_TYPE, "channel": TRADE_TOPIC, "instId": self.symbol},
                        ],
                    }
                    await ws.send(json.dumps(subscribe_msg))
                    print("[MicroFeeder] 已发送订阅: books + trade")

                    # ---- 启动独立心跳监控 ----
                    if self._heartbeat_task is None or self._heartbeat_task.done():
                        self._last_pong_ts = time.time()
                        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

                    # ---- 【新增】主动 Ping 保活任务 ----
                    _ping_task = asyncio.create_task(self._send_ping_loop(ws))

                    # ---- 消息循环 ----
                    try:
                        async for raw_message in ws:
                            self._process_message(json.loads(raw_message))
                    finally:
                        _ping_task.cancel()
                        try:
                            await _ping_task
                        except asyncio.CancelledError:
                            pass

            except asyncio.CancelledError:
                print("[MicroFeeder] 协程已取消")
                self._cancel_heartbeat()
                return

            except websockets.exceptions.ConnectionClosed as e:
                self._retry_count += 1
                wait = self._backoff_wait()
                # 【新增】连续断连惩罚
                wait = self._apply_consecutive_penalty(wait)
                print(f"[MicroFeeder] WS 连接关闭 (code={e.code}, reason={e.reason}), "
                      f"等待 {wait:.0f}s 后重连 (第{self._retry_count}次)")
                self.state["error_code"] = f"CLOSE_{e.code}"
                self._mark_stale()
                await asyncio.sleep(wait)

            except (OSError, TimeoutError, asyncio.TimeoutError) as e:
                self._retry_count += 1
                wait = self._backoff_wait()
                wait = self._apply_consecutive_penalty(wait)
                print(f"[MicroFeeder] 网络异常 ({type(e).__name__}), "
                      f"等待 {wait:.0f}s 后重连 (第{self._retry_count}次)")
                self.state["error_code"] = f"NET_{type(e).__name__}"
                self._mark_stale()
                await asyncio.sleep(wait)

            except websockets.exceptions.InvalidStatus as e:
                # Bitget 返回 HTTP 错误 —— 致命，不重试
                print(f"[MicroFeeder] 致命 HTTP 错误: {e.response.status_code}")
                self.state["error_code"] = f"HTTP_{e.response.status_code}"
                await self._alert_fatal(f"Bitget WS 返回 HTTP {e.response.status_code}")
                return

            except Exception as e:
                self._retry_count += 1
                wait = self._backoff_wait()
                wait = self._apply_consecutive_penalty(wait)
                err_name = type(e).__name__
                print(f"[MicroFeeder] 未知异常 ({err_name}): {e}, "
                      f"等待 {wait:.0f}s 后重连 (第{self._retry_count}次)")
                self.state["error_code"] = f"UNKNOWN_{err_name}"
                self._mark_stale()
                await asyncio.sleep(wait)

        # 超过最大重连次数
        print(f"[MicroFeeder] 已达到最大重连次数 ({RECONNECT_MAX_ATTEMPTS})，放弃")
        self.state["error_code"] = "MAX_RETRY_EXCEEDED"
        self._cancel_heartbeat()

        # ============================================================
    #  心跳独立监控
    # ============================================================
    async def _heartbeat_loop(self):
        """独立心跳监控：websockets 库自带 ping/pong 机制，
        但 Bitget 服务端可能在长时间无数据时主动踢人。
        本监控检测：如果超过 HEARTBEAT_TIMEOUT 没收到任何数据（含 pong），
        且不在重连中，则触发 fatal 告警。
        """
        _last_data_ts = time.time()
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                now = time.time()
                # 如果超过 HEARTBEAT_TIMEOUT 没有收到任何数据
                if now - self.state["ts"] > HEARTBEAT_TIMEOUT and self.state["ts"] > 0:
                    print(f"[MicroFeeder] 数据静默超时 ({now - self.state['ts']:.0f}s > {HEARTBEAT_TIMEOUT}s)")
                    self.state["error_code"] = "DATA_SILENT_TIMEOUT"
        except asyncio.CancelledError:
            pass

    def _cancel_heartbeat(self):
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()


    # ============================================================
    #  【新增】主动 Ping 保活
    # ============================================================
    async def _send_ping_loop(self, ws):
        """每 PING_INTERVAL 秒向 Bitget 发送 ping，防止服务端因空闲断开连接。"""
        try:
            while True:
                await asyncio.sleep(PING_INTERVAL)
                try:
                    await ws.send("ping")
                except Exception:
                    pass
        except asyncio.CancelledError:
            pass

    # ============================================================
    #  【新增】连续断连惩罚
    # ============================================================
    def _apply_consecutive_penalty(self, base_wait: float) -> float:
        """检查 CONSECUTIVE_WINDOW 秒窗口内断连次数，超过阈值则增加额外等待。"""
        now = time.time()
        self._consecutive_disconnect_log.append(now)
        # 移除窗口外的记录
        self._consecutive_disconnect_log = [
            t for t in self._consecutive_disconnect_log
            if now - t <= CONSECUTIVE_WINDOW
        ]
        if len(self._consecutive_disconnect_log) >= CONSECUTIVE_THRESHOLD:
            extra = CONSECUTIVE_PENALTY
            print(f"[MicroFeeder] 连续断连 {len(self._consecutive_disconnect_log)}次/{(CONSECUTIVE_WINDOW/60):.0f}分钟，额外等待{extra:.0f}s")
            return base_wait + extra
        return base_wait

    # ============================================================
    #  标记 + 告警
    # ============================================================
    # ============================================================
    def _mark_stale(self):
        """标记数据过时，暂停所有开单决策"""
        self.state["is_stale"] = True
        self._has_initial_depth = False

    async def _alert_fatal(self, msg: str):
        """致命错误时发 Telegram 告警并终止"""
        print(f"[MicroFeeder] 致命错误: {msg}")
        self.state["is_stale"] = True
        self.state["error_code"] = "FATAL"
        try:
            from notifier.telegram import send_telegram
            await asyncio.to_thread(send_telegram, f"⚠️ [MicroFeeder] {msg}\n{self.symbol} 数据源终止")
        except Exception:
            pass

    # ============================================================
    #  指数退避计算
    # ============================================================
    def _backoff_wait(self) -> float:
        """指数退避：5s, 10s, 20s, 40s, 60s..."""
        return min(RECONNECT_MAX, RECONNECT_DELAY_BASE * (2 ** (self._retry_count - 1)))

    # ============================================================
    #  数据快照 + 脏数据检测
    # ============================================================
    def get_snapshot(self) -> Dict[str, Any]:
        return dict(self.state)

    @property
    def is_data_ready(self) -> bool:
        """数据是否可用（非脏且有过初始深度快照）"""
        return not self.state.get("is_stale", True) and self._has_initial_depth

    # ============================================================
    #  消息处理
    # ============================================================
    def _process_message(self, data: Dict[str, Any]):
        # ---- 事件类消息 ----
        if "event" in data:
            event = data.get("event", "")
            if event == "subscribe":
                channel = data.get("arg", {}).get("channel", "")
                print(f"[MicroFeeder] 订阅成功: {channel}")
            elif event == "error":
                code = data.get("code", "")
                msg_text = data.get("msg", "")
                print(f"[MicroFeeder] 订阅错误: code={code} msg={msg_text}")
                self.state["error_code"] = f"SUB_{code}"
                # code=40001（签名错误）或 code=429（限流）—— 致命
                if code in ("40001", "429"):
                    asyncio.create_task(self._alert_fatal(f"订阅错误 code={code}: {msg_text}"))
            elif event == "pong":
                # Bitget 返回的是 event: pong
                self._last_pong_ts = time.time()
            return

        arg = data.get("arg", {})
        channel = arg.get("channel", "")
        raw_data = data.get("data", [])
        if not raw_data:
            return

        # ---- 收到数据说明连接正常，解除脏标记 ----
        if self.state["is_stale"]:
            self.state["is_stale"] = False
            print(f"[MicroFeeder] 数据流恢复")

        if channel == BOOKS_TOPIC:
            self._handle_depth(raw_data)
        elif channel == TRADE_TOPIC:
            self._handle_trade(raw_data)

    # ============================================================
    #  深度 & 成交处理
    # ============================================================
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
