# -*- coding: utf-8 -*-
"""
微观数据喂价器 (Micro Feeder) — 协程版
======================================
不依赖 ZMQ，不依赖独立进程。
直接通过共享内存字典（self.state）暴露数据，主协程读取即可。

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
    websockets = None  # type: ignore

try:
    import requests
except ImportError:
    requests = None  # type: ignore


def _check_binance_connectivity() -> bool:
    """测试 Binance REST API 是否可达（3秒超时）

    在 Hugging Face 海外节点上，请求 Binance 可能返回 403/451 或被墙超时。
    提前检测可以避免 WS 连接漫长的 10 秒超时等待。

    Returns:
        True = 可达，False = 被拦截/不可达
    """
    if requests is None:
        return True  # 没有 requests 库则无法检测，默认放行让 WS 去试
    try:
        res = requests.get("https://api.binance.com/api/v3/ping", timeout=3)
        if res.status_code in (403, 451):
            print(f"[MicroFeeder] Binance API 返回 {res.status_code}，环境受限")
            return False
        return True
    except requests.ConnectionError:
        print("[MicroFeeder] Binance API 连接失败（ConnectionError），环境受限")
        return False
    except requests.Timeout:
        print("[MicroFeeder] Binance API 超时，环境受限")
        return False
    except Exception as exc:
        print(f"[MicroFeeder] Binance API 检测异常: {exc}，放行由 WS 尝试")
        return True  # 不确定时放行

# ============================================================
# 配置常量
# ============================================================
DEFAULT_SYMBOL = "BTCUSDT"
RECONNECT_DELAY = 3.0


class MicroFeeder:
    """微观数据喂价器（协程版）

    属性:
        state (dict): 共享状态字典，供外部读取
            {"obi": float, "cvd": float, "ts": float, "price": float, ...}

    用法:
        feeder = MicroFeeder("BTCUSDT")
        asyncio.create_task(feeder.run())

        # 在其他协程/函数中直接读取
        obi = feeder.state["obi"]
        cvd = feeder.state["cvd"]
    """

    def __init__(self, symbol: str = DEFAULT_SYMBOL):
        self.symbol = symbol.lower()

        # ---------- 共享状态（外部直接读取） ----------
        self.state: Dict[str, Any] = {
            "obi": 0.0,
            "cvd": 0.0,
            "ts": 0.0,
            "price": 0.0,
            "bids": 0.0,
            "asks": 0.0,
            "tick_count": 0,
            "symbol": self.symbol.upper(),
        }

        # ---------- 内部累加器 ----------
        self._cvd_current: float = 0.0
        self._last_price: float = 0.0
        self._tick_count: int = 0

        if websockets is None:
            raise ImportError(
                "websockets is required. Run: pip install websockets>=11.0.3"
            )

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------
    async def run(self):
        """启动 WebSocket 连接，持续更新 self.state

        异常时自动重连，永不退出（除非被取消）。
        """
        _ws_urls = [
            f"wss://stream.binance.com:9443/ws/{self.symbol}@depth10@100ms/{self.symbol}@aggTrade",
        ]
        print(f"[MicroFeeder] 正在连接 WS: {self.symbol}")

        # 联通性预检查：避免被墙时等 10 秒超时
        if not _check_binance_connectivity():
            print(f"[MicroFeeder] 跳过 WS 连接（环境受限），微观数据不可用")
            return

        for url in _ws_urls:
            try:
                async with websockets.connect(url, ping_interval=20, open_timeout=10) as ws:
                    print(f"[MicroFeeder] WS 已连接: {self.symbol}")
                    async for raw_message in ws:
                        self._process_message(json.loads(raw_message))
            except asyncio.CancelledError:
                print("[MicroFeeder] 协程已取消")
                return
            except Exception:
                print(f"[MicroFeeder] WS 连接失败: {url[:65]}...")
        # 所有地址都失败，静默退出（不阻塞主循环）
        print(f"[MicroFeeder] 无法连接到 {self.symbol} (环境受限)，微观数据不可用")

    def get_snapshot(self) -> Dict[str, Any]:
        """获取当前微观状态快照（线程安全，纯读取）"""
        return dict(self.state)

    # ------------------------------------------------------------------
    # 内部：WS 消息处理
    # ------------------------------------------------------------------
    def _process_message(self, data: Dict[str, Any]):
        """解析 WS 消息，更新 state"""
        event_type = data.get("e", "")

        if event_type == "aggTrade":
            self._handle_agg_trade(data)
        elif "bids" in data or "asks" in data:
            self._handle_depth(data)

    def _handle_agg_trade(self, data: Dict[str, Any]):
        """处理聚合交易：更新 CVD 和最后价格"""
        price = float(data.get("p", 0))
        qty = float(data.get("q", 0))
        is_buyer_maker = data.get("m", True)

        self._last_price = price
        self._tick_count += 1

        if not is_buyer_maker:
            self._cvd_current += qty
        else:
            self._cvd_current -= qty

        self.state["cvd"] = round(self._cvd_current, 4)
        self.state["price"] = round(price, 2) if price else 0.0
        self.state["tick_count"] = self._tick_count
        self.state["ts"] = time.time()

    def _handle_depth(self, data: Dict[str, Any]):
        """处理深度快照：更新 OBI"""
        raw_bids = data.get("bids", [])
        raw_asks = data.get("asks", [])

        bids_total = sum(float(b[1]) for b in raw_bids if len(b) >= 2)
        asks_total = sum(float(a[1]) for a in raw_asks if len(a) >= 2)

        self.state["bids"] = round(bids_total, 2)
        self.state["asks"] = round(asks_total, 2)

        total = bids_total + asks_total
        if total > 0:
            self.state["obi"] = round((bids_total - asks_total) / total, 4)
        else:
            self.state["obi"] = 0.0

        self.state["ts"] = time.time()
