# -*- coding: utf-8 -*-
"""Exchange adapter — ccxt wrapper with dry_run safety and Bitget-aware helpers."""
from __future__ import annotations
import os
from typing import Any, Dict, List, Optional


def _env(*names: str, default: str = "") -> str:
    for name in names:
        val = os.getenv(name, "")
        if val:
            return val
    return default


class ExchangeAdapter:
    def __init__(self, exchange_name: str = "bitget", dry_run: bool = True, leverage: int = 1):
        self.exchange_name = exchange_name.lower().strip()
        self.dry_run = bool(dry_run)
        self.leverage = int(leverage)
        self.exchange = self._build_exchange()

    def _build_exchange(self):
        try:
            import ccxt
        except ModuleNotFoundError as exc:
            if self.dry_run:
                return None
            raise ModuleNotFoundError(
                "ccxt is required for live exchange access. Run: pip install -r requirements.txt"
            ) from exc
        api_key = _env("EXCHANGE_API_KEY", "BITGET_API_KEY")
        secret = _env("EXCHANGE_SECRET", "EXCHANGE_API_SECRET", "BITGET_SECRET")
        password = _env("EXCHANGE_PASSWORD", "BITGET_PASSWORD")
        common: Dict[str, Any] = {
            "enableRateLimit": True,
            "apiKey": api_key,
            "secret": secret,
        }
        if password:
            common["password"] = password
        if self.exchange_name == "bitget":
            common["options"] = {"defaultType": "swap"}
            return ccxt.bitget(common)
        if self.exchange_name == "binance":
            common["options"] = {"defaultType": "future"}
            return ccxt.binance(common)
        if self.exchange_name == "okx":
            common["options"] = {"defaultType": "swap"}
            return ccxt.okx(common)
        raise ValueError(f"Unsupported exchange: {self.exchange_name}")

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        if not symbol:
            return symbol
        s = symbol.strip()
        if ":" in s:
            return s
        if "/" in s:
            base, quote = s.split("/", 1)
            quote = quote.split(":")[0]
            return f"{base}/{quote}:{quote}"
        return s

    @staticmethod
    def _base_symbol(symbol: str) -> str:
        if not symbol:
            return symbol
        return symbol.split(":")[0]

    def fetch_balance_usdt(self) -> float:
        if self.dry_run:
            return float(os.getenv("DRY_RUN_BALANCE", "1000"))
        if self.exchange is None:
            raise RuntimeError("exchange client is unavailable")
        bal = self.exchange.fetch_balance()
        total = bal.get("total", {}) or {}
        return float(total.get("USDT", 0.0))

    def fetch_ticker_price(self, symbol: str) -> float:
        if self.exchange is None:
            raise RuntimeError("exchange client is unavailable in dry_run without ccxt")
        ticker = self.exchange.fetch_ticker(self._normalize_symbol(symbol))
        return float(ticker.get("last") or ticker.get("close") or 0.0)

    def fetch_positions(
        self,
        symbols: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        返回标准化持仓列表:
        symbol / symbol_raw / direction / size / entry /
        unrealized_pnl / leverage / margin_mode / raw
        dry_run 返回 []。
        """
        if self.dry_run:
            return []
        if self.exchange is None:
            raise RuntimeError("exchange client is unavailable; install ccxt or keep dry_run enabled")

        fetch_syms = None
        if symbols:
            fetch_syms = [self._normalize_symbol(s) for s in symbols]

        try:
            raw_positions = self.exchange.fetch_positions(fetch_syms)
        except TypeError:
            raw_positions = self.exchange.fetch_positions()
        except Exception as exc:
            try:
                raw_positions = self.exchange.fetch_positions(
                    fetch_syms,
                    params={"productType": "USDT-FUTURES"},
                )
            except Exception:
                raise RuntimeError(f"fetch_positions failed: {exc}") from exc

        result: List[Dict[str, Any]] = []
        for pos in raw_positions or []:
            try:
                contracts = float(pos.get("contracts") or pos.get("contractSize") or 0)
                if contracts == 0:
                    info = pos.get("info") or {}
                    for key in ("total", "available", "size", "holdVol"):
                        if info.get(key) not in (None, "", "0"):
                            try:
                                contracts = abs(float(info[key]))
                            except (TypeError, ValueError):
                                pass
                    if contracts == 0:
                        continue
                side = (pos.get("side") or "").lower()
                if side in ("long", "buy"):
                    direction = "Long"
                elif side in ("short", "sell"):
                    direction = "Short"
                else:
                    signed = float(pos.get("contracts") or 0)
                    direction = "Long" if signed > 0 else "Short"

                sym_raw = pos.get("symbol") or ""
                entry = float(pos.get("entryPrice") or pos.get("average") or 0.0)
                upnl = float(pos.get("unrealizedPnl") or 0.0)
                lev = float(pos.get("leverage") or self.leverage or 1)

                result.append(
                    {
                        "symbol": self._base_symbol(sym_raw),
                        "symbol_raw": sym_raw,
                        "direction": direction,
                        "size": abs(contracts),
                        "entry": entry,
                        "unrealized_pnl": upnl,
                        "leverage": lev,
                        "margin_mode": pos.get("marginMode")
                        or (pos.get("info") or {}).get("marginMode")
                        or "",
                        "raw": pos,
                    }
                )
            except Exception:
                continue

        if symbols:
            wanted = {self._base_symbol(s) for s in symbols}
            wanted |= {self._normalize_symbol(s) for s in symbols}
            result = [
                r for r in result
                if r["symbol"] in wanted or r["symbol_raw"] in wanted
            ]

        return result

    def create_market_order(self, symbol, direction, amount, reduce_only=False):
        side = "buy" if direction == "Long" else "sell"
        params = {}
        if reduce_only:
            params["reduceOnly"] = True
        amount = float(amount)
        if amount <= 0:
            raise ValueError("order amount must be positive")
        if self.dry_run:
            return {
                "dry_run": True,
                "symbol": symbol,
                "side": side,
                "amount": amount,
                "filled": amount,
                "remaining": 0.0,
                "average": None,
                "status": "closed",
                "reduceOnly": reduce_only,
                "id": "DRY_RUN_ORDER",
            }
        if self.exchange is None:
            raise RuntimeError("exchange client is unavailable; install ccxt or keep dry_run enabled")
        return self.exchange.create_order(
            self._normalize_symbol(symbol), "market", side, amount, None, params
        )

    def fetch_order_safe(self, order_id, symbol):
        if self.dry_run or self.exchange is None or not order_id:
            return None
        try:
            return self.exchange.fetch_order(order_id, self._normalize_symbol(symbol))
        except Exception:
            return None

    def close_market_order(self, symbol, direction, amount):
        close_direction = "Short" if direction == "Long" else "Long"
        return self.create_market_order(symbol, close_direction, amount, reduce_only=True)
