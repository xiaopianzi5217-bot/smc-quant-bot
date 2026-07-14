# -*- coding: utf-8 -*-
"""
微观数据喂价器 (Micro Feeder)
=======================
独立进程：连接交易所 WebSocket，实时计算 OBI（Order Book Imbalance）
和 CVD（Cumulative Volume Delta），通过 ZMQ PUB 广播。

使用方式：作为独立子进程运行，由主进程启动。
     >>> from execution.micro.feeder import run_feeder_process
     >>> import multiprocessing
     >>> p = multiprocessing.Process(target=run_feeder_process, daemon=True)
     >>> p.start()

设计原则：
1. 完全异步 (asyncio) - 不阻塞主进程的事件循环
2. 零外部依赖 (除 pyzmq / websockets)
3. 内存级通信 (ZMQ PUB/SUB, 127.0.0.1)
4. 每秒 2-3 帧广播频率
"""
from __future__ import annotations

import asyncio
import json
import time
import traceback
from typing import Any, Dict, Optional

try:
    import zmq
except ImportError:
    zmq = None  # type: ignore

try:
    import websockets
except ImportError:
    websockets = None  # type: ignore

# ============================================================
# 配置常量
# ============================================================
DEFAULT_ZMQ_BIND = "tcp://127.0.0.1:5555"
DEFAULT_SYMBOL = "BTCUSDT"
RECONNECT_DELAY = 3.0  # WebSocket 断线重连等待秒数
BROADCAST_INTERVAL = 0.35  # ~2.86 Hz 广播间隔


class MicroFeeder:
    """微观数据喂价器

    连接交易所 WebSocket，维护本地 orderbook，计算并广播 OBI / CVD。
    完全独立运行，不依赖主进程的任何状态。
    """

    def __init__(
        self,
        symbol: str = DEFAULT_SYMBOL,
        bind_addr: str = DEFAULT_ZMQ_BIND,
        exchange: str = "binance",
    ):
        self.symbol = symbol.lower()
        self.bind_addr = bind_addr
        self.exchange = exchange.lower()

        # ---------- 微观状态 ----------
        self.cvd: float = 0.0          # 累积成交量差
        self.bids: float = 0.0         # 买盘挂单总量
        self.asks: float = 0.0         # 卖盘挂单总量
        self.obi: float = 0.0          # 盘口不平衡度
        self.last_price: float = 0.0   # 最新成交价
        self.tick_count: int = 0       # 聚合交易计数

        # ---------- ZMQ Publisher ----------
        if zmq is None:
            raise ImportError("pyzmq is required. pip install pyzmq>=25.0.0")
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.PUB)
        self.socket.bind(self.bind_addr)
        # 设置发送超时，防止 ZMQ 积压阻塞
        self.socket.setsockopt(zmq.SNDTIMEO, 1000)
        print(f"[MicroFeeder] ZMQ PUB 已绑定: {bind_addr}")

    # ------------------------------------------------------------------
    # WebSocket 连接管理
    # ------------------------------------------------------------------
    async def connect_binance_ws(self):
        """连接 Binance 归集深度 + 聚合交易流"""
        if websockets is None:
            raise ImportError("websockets is required. pip install websockets>=11.0.3")

        ws_url = (
            f"wss://stream.binance.com:9443/ws/"
            f"{self.symbol}@depth10@100ms/"
            f"{self.symbol}@aggTrade"
        )
        print(f"[MicroFeeder] 正在连接 Binance WS: {self.symbol}")

        while True:
            try:
                async with websockets.connect(ws_url, ping_interval=20) as ws:
                    print(f"[MicroFeeder] ✅ 已连接 {self.symbol} 极速 WebSocket")
                    async for raw_message in ws:
                        self._process_message(json.loads(raw_message))
            except asyncio.CancelledError:
                print("[MicroFeeder] 任务被取消，退出")
                break
            except Exception as exc:
                print(f"[MicroFeeder] WS 连接异常: {exc}")
                print(traceback.format_exc())
                print(f"[MicroFeeder] {RECONNECT_DELAY}s 后重连...")
                await asyncio.sleep(RECONNECT_DELAY)

    async def connect_bitget_ws(self):
        """连接 Bitget 深度 + 交易流 (备用)

        Bitget 的 WS 地址和格式与 Binance 不同，需要独立实现。
        如果您的交易所是 Bitget，可在此实现。
        """
        # TODO: Bitget WS 实现
        # 文档: https://bitgetlimited.github.io/apidoc/en/spot/ws_public
        print("[MicroFeeder] Bitget WS 暂未实现，使用 Binance WS")
        await self.connect_binance_ws()

    # ------------------------------------------------------------------
    # 数据解析与计算
    # ------------------------------------------------------------------
    def _process_message(self, data: Dict[str, Any]):
        """解析 WS 消息，更新微观状态"""
        event_type = data.get("e", "")

        if event_type == "aggTrade":
            self._handle_agg_trade(data)
        elif "lastUpdateId" in data:
            self._handle_depth(data)
        elif "U" in data:  # Bitget 深度快照特征
            self._handle_depth(data)

    def _handle_agg_trade(self, data: Dict[str, Any]):
        """处理聚合交易：更新 CVD 和最后价格"""
        price = float(data.get("p", 0))
        qty = float(data.get("q", 0))
        is_buyer_maker = data.get("m", True)  # True=主动卖出, False=主动买入

        self.last_price = price
        self.tick_count += 1

        if not is_buyer_maker:
            self.cvd += qty  # 主动买入 → CVD 增加
        else:
            self.cvd -= qty  # 主动卖出 → CVD 减少

        # 每秒广播一次（由定时器触发，不会每个 tick 都发）
        self._try_broadcast()

    def _handle_depth(self, data: Dict[str, Any]):
        """处理深度快照：更新 OBI"""
        # 支持两种格式: Binance (bids/asks 数组) 和 Bitget
        raw_bids = data.get("bids", data.get("bids", []))
        raw_asks = data.get("asks", data.get("asks", []))

        # bids/asks 是 [[price, qty], ...] 格式
        self.bids = sum(float(b[1]) for b in raw_bids if len(b) >= 2)
        self.asks = sum(float(a[1]) for a in raw_asks if len(a) >= 2)

        total = self.bids + self.asks
        if total > 0:
            self.obi = (self.bids - self.asks) / total

        # 深度更新也触发广播尝试
        self._try_broadcast()

    # ------------------------------------------------------------------
    # ZMQ 广播
    # ------------------------------------------------------------------
    _last_broadcast: float = 0.0

    def _try_broadcast(self):
        """节流广播：每秒最多发送 BROADCAST_INTERVAL 秒一次"""
        now = time.time()
        if now - self._last_broadcast < BROADCAST_INTERVAL:
            return
        self._last_broadcast = now
        self._broadcast()

    def _broadcast(self):
        """发送当前微观状态到 ZMQ 总线

        消息格式: "SYMBOL {json_payload}"
        订阅方通过 topic (SYMBOL) 过滤。
        """
        payload = {
            "symbol": self.symbol.upper(),
            "ts": time.time(),
            "obi": round(self.obi, 4),
            "cvd": round(self.cvd, 4),
            "bids": round(self.bids, 2),
            "asks": round(self.asks, 2),
            "price": round(self.last_price, 2) if self.last_price else 0,
            "tick_count": self.tick_count,
        }
        try:
            self.socket.send_string(
                f"{self.symbol.upper()} {json.dumps(payload)}",
                flags=zmq.NOBLOCK,
            )
        except zmq.Again:
            pass  # 发送缓冲区满，丢弃这一帧（避免阻塞）

    def close(self):
        """清理 ZMQ 资源"""
        try:
            self.socket.close()
            self.context.term()
        except Exception:
            pass


# ============================================================
# 进程入口
# ============================================================
def run_feeder_process(
    symbol: str = DEFAULT_SYMBOL,
    bind_addr: str = DEFAULT_ZMQ_BIND,
    exchange: str = "binance",
):
    """多进程入口点 - 作为独立子进程运行

    用法:
        >>> import multiprocessing
        >>> from execution.micro.feeder import run_feeder_process
        >>> p = multiprocessing.Process(
        ...     target=run_feeder_process,
        ...     kwargs={"symbol": "BTCUSDT"},
        ...     daemon=True,
        ... )
        >>> p.start()
    """
    print(f"[MicroFeeder] 进程启动 (PID={os.getpid()}, symbol={symbol})")
    feeder = MicroFeeder(symbol=symbol, bind_addr=bind_addr, exchange=exchange)

    try:
        if exchange == "bitget":
            asyncio.run(feeder.connect_bitget_ws())
        else:
            asyncio.run(feeder.connect_binance_ws())
    except KeyboardInterrupt:
        print("[MicroFeeder] 收到中断信号")
    finally:
        feeder.close()
        print("[MicroFeeder] 进程退出")


# ============================================================
# 独立运行
# ============================================================
if __name__ == "__main__":
    import os
    import sys

    symbol = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SYMBOL
    run_feeder_process(symbol=symbol)
