# -*- coding: utf-8 -*-
"""
P0-1: 持仓对账系统

职责：
  1. 启动时：将交易所持仓恢复至本地 PositionManager（防止重启丢仓）
  2. 运行中：定期巡查本地与交易所持仓差异，告警幽灵仓/缺失仓
  3. 不自动修正（只报告），由运维人员或外部 handler 处理

依赖：
  - pathlib, json, os, time, logging
  - requests (直连 Bitget API，避免 ccxt 阻塞)
"""

import json
import os
import time
import traceback
import logging
from pathlib import Path
from typing import Optional

import requests as _rq

from state.position_manager import position_manager

logger = logging.getLogger("PositionReconciler")


BITGET_API_BASE = "https://api.bitget.com"


class PositionReconciler:
    """
    持仓对账器。

    用法:
        reconciler = PositionReconciler()
        reconciler.run_full_reconciliation()
    """

    def __init__(self):
        self._api_key: Optional[str] = os.getenv("BITGET_API_KEY", "")
        self._api_secret: Optional[str] = os.getenv("BITGET_SECRET", "")
        self._api_passphrase: Optional[str] = os.getenv("BITGET_PASSPHRASE", "")

        if not self._api_key or not self._api_secret:
            logger.warning("BITGET_API_KEY 或 BITGET_SECRET 未设置，对账功能不可用")

        # 本地 sym -> 统一格式: "BTC/USDT:USDT"
        # 交易所 sym -> 原始格式: "BTCUSDT"

    # ── 交易所持仓获取 ──────────────────────────────────────

    def _sign_request(self, method: str, path: str, params: dict) -> dict:
        """Bitget V2 签名（简化版，仅用于 GET 持仓）"""
        from datetime import datetime

        import hmac
        import hashlib

        timestamp = str(int(time.time() * 1000))
        sign_payload = f"{timestamp}{method.upper()}{path}"
        if params:
            sorted_keys = sorted(params.keys())
            query_string = "&".join(f"{k}={params[k]}" for k in sorted_keys)
            sign_payload += f"?{query_string}"

        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            sign_payload.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).hexdigest()

        return {
            "ACCESS-KEY": self._api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": self._api_passphrase,
            "Content-Type": "application/json",
        }

    def fetch_exchange_positions(self, product_type: str = "umcbl") -> dict:
        """
        从 Bitget 拉取真实持仓。
        返回: {"BTCUSDT": {"hold": 0.01, "direction": "long", "entry": 60000.0, "upnl": 0.0}, ...}
        """
        if not self._api_key:
            return {}

        path = "/api/v2/mix/account/positions"
        params = {"productType": product_type}
        url = f"{BITGET_API_BASE}{path}"

        try:
            headers = self._sign_request("GET", path, params)
            resp = _rq.get(url, params=params, headers=headers, timeout=10)
            if resp.status_code != 200:
                logger.warning("交易所持仓查询 HTTP %s", resp.status_code)
                return {}
            data = resp.json()
            if data.get("code") != "00000":
                logger.warning("交易所持仓查询 API 错误: %s", data.get("msg"))
                return {}

            positions = {}
            for item in data.get("data", []):
                hold = float(item.get("total", "0"))
                if hold <= 0:
                    continue
                sym_exchange = item.get("symbol", "")
                direction = "long" if item.get("holdSide", "") == "long" else "short"
                entry = float(item.get("openPriceAvg", "0"))
                upnl = float(item.get("unrealizedPL", "0"))

                # 统一格式: BTCUSDT -> BTC/USDT:USDT
                sym_local = self._to_local_symbol(sym_exchange)
                positions[sym_local] = {
                    "hold": hold,
                    "direction": direction,
                    "entry": entry,
                    "upnl": upnl,
                }
            return positions
        except Exception as e:
            logger.error("获取交易所持仓异常: %s", e)
            traceback.print_exc()
            return {}

    @staticmethod
    def _to_local_symbol(sym_exchange: str) -> str:
        """BTCUSDT -> BTC/USDT:USDT"""
        # 常见模式：BTCUSDT, ETHUSDT, SOLUSDT
        s = sym_exchange.strip().upper()
        if s.endswith("USDT"):
            base = s[:-4]
            return f"{base}/USDT:USDT"
        return sym_exchange  # fallback

    # ── 对账核心 ────────────────────────────────────────────

    def run_full_reconciliation(self) -> dict:
        """
        执行一次完整对账。
        返回报告字典，包含:
          - "ok": 无差异
          - "ghost": 本地有、交易所无（幽灵仓）
          - "missing": 交易所无、本地有（缺失仓）
          - "mismatch": 方向/数量不匹配
        """
        report: dict = {"ok": [], "ghost": [], "missing": [], "mismatch": []}

        exchange_positions = self.fetch_exchange_positions()
        local_positions = position_manager.get()

        if not exchange_positions and not local_positions:
            report["ok"].append("无持仓，双方一致")
            return report

        if not exchange_positions and local_positions:
            # 交易所无持仓 but 本地有（可能是刚启动未恢复，或断线后本地未清除）
            for sym, pos in local_positions.items():
                report["ghost"].append(f"{sym}: local_hold={pos.get('entry', '?')}")
            return report

        if exchange_positions and not local_positions:
            for sym, pos in exchange_positions.items():
                report["missing"].append(f"{sym}: exchange_hold={pos['hold']}")
            return report

        # 双方都有持仓，逐品种对比
        all_symbols = set(local_positions.keys()) | set(exchange_positions.keys())
        for sym in sorted(all_symbols):
            local = local_positions.get(sym)
            exchange = exchange_positions.get(sym)

            if local and not exchange:
                report["ghost"].append(f"{sym}: 本地存在但交易所无")
                continue
            if not local and exchange:
                report["missing"].append(f"{sym}: 交易所存在但本地无 (hold={exchange['hold']})")
                continue
            if not local and not exchange:
                continue

            # 双方都有 -> 对比方向和大致数量
            local_dir = local.get("direction", "?").lower()[:4]
            exchange_dir = exchange.get("direction", "?").lower()[:4]
            local_hold = float(local.get("entry", 0))
            exchange_hold = float(exchange["hold"])

            if local_dir != exchange_dir or abs(local_hold - exchange_hold) > 0.01:
                report["mismatch"].append(
                    f"{sym}: dir本地={local_dir} vs 交易所={exchange_dir}, "
                    f"hold本地={local_hold:.4f} vs 交易所={exchange_hold:.4f}"
                )
            else:
                report["ok"].append(f"{sym}: 一致")

        return report

    # ── 启动恢复 ────────────────────────────────────────────

    def recover_from_exchange(self) -> list:
        """
        启动时：用交易所持仓覆盖本地 PositionManager。
        仅在本地无持仓且交易所有时执行。
        返回恢复的 sym 列表。
        """
        recovered = []
        exchange_positions = self.fetch_exchange_positions()
        if not exchange_positions:
            return recovered

        for sym, pos in exchange_positions.items():
            if not position_manager.exists(sym):
                position_manager.update(sym, {
                    "direction": "Long" if pos["direction"] == "long" else "Short",
                    "entry": pos["entry"],
                    "current_sl": pos["entry"] * 0.98 if pos["direction"] == "long" else pos["entry"] * 1.02,
                    "tp1": None,
                    "tp2": None,
                    "stage": 0,
                    "recovered": True,
                    "upnl": pos.get("upnl", 0.0),
                })
                recovered.append(sym)
                logger.info("从交易所恢复持仓: %s", sym)
        return recovered

    # ── 巡查（供后台线程调用） ──────────────────────────────

    def periodic_check(self) -> Optional[str]:
        """
        快速巡查（每 5 分钟调用一次）。
        仅在发现严重不一致时返回告警消息（供 Telegram 推送），否则返回 None。
        """
        report = self.run_full_reconciliation()
        if report["ghost"] or report["missing"] or report["mismatch"]:
            lines = ["🔍 持仓一致告警"]
            for k in ("ghost", "missing", "mismatch"):
                for detail in report[k]:
                    lines.append(f"  [{k}] {detail}")
            return "\n".join(lines)
        return None


# 单例
position_reconciler = PositionReconciler()
