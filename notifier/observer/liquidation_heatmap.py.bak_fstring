# -*- coding: utf-8 -*-
"""基于 Bitget WebSocket 爆仓数据的本地热力图模块。
不需要 API Key，直接订阅 Bitget 公开 WebSocket 流。

数据源：Bitget 公用 WebSocket wss://ws.bitget.com/v2/ws/public
订阅频道：liquidation
采集爆仓数据后按价格区间（粒度 = 当前价格的 0.3%）聚合，
生成爆仓热力图层次，供 Observer 消息中的"热力图位置"使用。

用法：
    from notifier.observer.liquidation_heatmap import build_liquidation_heatmap
    hm = build_liquidation_heatmap("BTC/USDT")
    # hm = {"levels": "...", "analysis": "..."}
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, List
from collections import defaultdict

try:
    import websocket
except ImportError:
    websocket = None


# ============================================================
#  全局爆仓缓存
# ============================================================
# symbol -> {"price_bin": total_liquidation_usd}
_liquidation_cache: Dict[str, Dict[int, float]] = {}
_cache_lock = threading.Lock()
_last_update: Dict[str, float] = {}


def _price_bin(price: float, bin_size_pct: float = 0.003) -> int:
    """将价格映射到离散桶（例如 0.3% 粒度）"""
    if price <= 0:
        return 0
    size = price * bin_size_pct
    if size <= 0:
        return int(price)
    return round(price / size) * int(size)


def _start_ws_listener():
    """后台启动 Bitget WebSocket 监听爆仓数据（全局只启动一次）"""
    if websocket is None:
        return

    def _on_message(ws, message):
        try:
            data = json.loads(message)
            if "arg" in data and data.get("arg", {}).get("channel") == "liquidation":
                raw = data.get("data", [])
                if isinstance(raw, dict):
                    raw = [raw]
                for item in raw:
                    symbol = item.get("instId", item.get("instId", "BTCUSDT"))
                    price = float(item.get("px", item.get("price", 0)))
                    usd_val = float(item.get("sz", item.get("size", 0)))
                    side = item.get("side", "").lower()
                    if price <= 0 or usd_val <= 0:
                        continue
                    norm_symbol = symbol.replace("USDT", "/USDT") if "USDT" in symbol and "/" not in symbol else symbol
                    bin_key = _price_bin(price)
                    with _cache_lock:
                        _liquidation_cache.setdefault(norm_symbol, defaultdict(float))
                        _liquidation_cache[norm_symbol][bin_key] += usd_val
                        _last_update[norm_symbol] = time.time()
        except Exception:
            pass

    def _on_error(ws, error):
        pass

    def _on_close(ws, close_status_code, close_msg):
        time.sleep(5)
        _start_ws_listener()

    def _on_open(ws):
        subscribe_msg = json.dumps({
            "op": "subscribe",
            "args": [{"instType": "UMCBL", "channel": "liquidation", "instId": "default"}]
        })
        ws.send(subscribe_msg)

    ws = websocket.WebSocketApp(
        "wss://ws.bitget.com/v2/ws/public",
        on_open=_on_open,
        on_message=_on_message,
        on_error=_on_error,
        on_close=_on_close,
    )
    wst = threading.Thread(target=ws.run_forever, daemon=True)
    wst.start()


# 懒启动全局 WebSocket
_ws_started = False


def _ensure_ws():
    global _ws_started
    if not _ws_started and websocket is not None:
        _ws_started = True
        _start_ws_listener()


# ============================================================
#  API 降级方案：如果 WebSocket 不可用，用 Coinglass 公开接口
# ============================================================
def _fetch_coinglass_liquidation(symbol: str) -> List[Dict]:
    """尝试从 Coinglass 公开接口获取爆仓数据"""
    sym_label = symbol.replace("/", "").replace("USDT", "") if "USDT" in symbol else symbol
    try:
        import requests
        h = {"Accept": "application/json"}
        r = requests.get(
            f"https://open-api.coinglass.com/public/v2/liquidation_chart?symbol={sym_label}&exchange=Bitget&timeType=1",
            headers=h, timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            if data.get("code") == "00000" or data.get("success"):
                return data.get("data", [])
    except Exception:
        pass
    return []


# ============================================================
#  主入口：构建爆仓热力图
# ============================================================
def build_liquidation_heatmap(
    symbol: str,
    price: float | None = None,
    max_levels: int = 5,
) -> Dict[str, Any]:
    """返回格式与 heatmap.py 的 build_local_liquidity_heatmap 一致。

    如果有 WebSocket 缓存，直接用缓存数据；
    否则尝试 Coinglass API 兜底；
    如果都没有，返回 N/A（回退到本地热力图）。
    """
    _ensure_ws()

    norm_symbol = symbol.upper().strip()
    if "/" not in norm_symbol and "USDT" in norm_symbol:
        norm_symbol = norm_symbol.replace("USDT", "/USDT")

    # 先看 WebSocket 缓存
    with _cache_lock:
        cache = dict(_liquidation_cache.get(norm_symbol, {}))

    # 如果 WS 缓存没有或太旧，尝试 Coinglass
    if not cache or (time.time() - _last_update.get(norm_symbol, 0) > 120):
        coinglass_data = _fetch_coinglass_liquidation(symbol)
        if coinglass_data:
            for item in coinglass_data:
                if isinstance(item, dict):
                    px = float(item.get("price", 0))
                    vol = float(item.get("volume", 0))
                    bin_key = _price_bin(px)
                    if px > 0 and vol > 0:
                        cache[bin_key] = cache.get(bin_key, 0) + vol

    if not cache:
        return {"levels": "N/A", "analysis": "暂无外部爆仓数据，使用本地热力图"}

    if price is None or price <= 0:
        return {"levels": "N/A", "analysis": "价格缺失"}

    # 按距离排序，取最近的 max_levels 个
    sorted_bins = sorted(cache.items(), key=lambda x: abs(x[0] - price))
    top_bins = sorted_bins[:max_levels]

    total_vol = sum(v for _, v in top_bins)
    if total_vol <= 0:
        return {"levels": "N/A", "analysis": "爆仓数据不足"}

    text_parts = []
    for bin_price, vol in top_bins:
        side = "上方" if bin_price >= price else "下方"
        dist = abs(bin_price - price) / price * 100.0
        vol_m = vol / 1_000_000
        text_parts.append(f"{side} ${bin_price:.0f} (距{dist:.2f}%, 爆{vol_m:.1f}M)")

    above = [(p, v) for p, v in top_bins if p >= price]
    below = [(p, v) for p, v in top_bins if p < price]

    if above and below:
        nearest_above_p, _ = min(above, key=lambda x: abs(x[0] - price))
        nearest_below_p, _ = min(below, key=lambda x: abs(x[0] - price))
        analysis = (
            f"上方爆仓密集 ${nearest_above_p:.0f}，下方爆仓密集 ${nearest_below_p:.0f}。"
            f"爆仓数据表示该价位曾有大仓位被强平，是真实流动性参考。"
        )
    elif above:
        nearest_above_p, _ = min(above, key=lambda x: abs(x[0] - price))
        analysis = f"爆仓在上方集中 ${nearest_above_p:.0f}，谨防价格上扫爆空后再回落。"
    else:
        nearest_below_p, _ = min(below, key=lambda x: abs(x[0] - price))
        analysis = f"爆仓在下方集中 ${nearest_below_p:.0f}，谨防价格下扫爆多后再反弹。"

    return {
        "levels": " | ".join(text_parts),
        "analysis": analysis,
    }
