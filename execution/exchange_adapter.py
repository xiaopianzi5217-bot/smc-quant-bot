# -*- coding: utf-8 -*-
import os


class ExchangeAdapter:
    def __init__(self, exchange_name="bitget", dry_run=True, leverage=1):
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
            raise ModuleNotFoundError("ccxt is required for live exchange access. Run: pip install -r requirements.txt") from exc
        api_key = os.getenv("EXCHANGE_API_KEY", "")
        secret = os.getenv("EXCHANGE_SECRET", "")
        password = os.getenv("EXCHANGE_PASSWORD", "")
        common = {"enableRateLimit": True, "apiKey": api_key, "secret": secret}
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

    def fetch_balance_usdt(self):
        if self.dry_run:
            return float(os.getenv("DRY_RUN_BALANCE", "1000"))
        bal = self.exchange.fetch_balance()
        total = bal.get("total", {}) or {}
        return float(total.get("USDT", 0.0))

    def fetch_ticker_price(self, symbol):
        if self.exchange is None:
            raise RuntimeError("exchange client is unavailable in dry_run without ccxt")
        ticker = self.exchange.fetch_ticker(symbol)
        return float(ticker.get("last") or ticker.get("close") or 0.0)

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
        return self.exchange.create_order(symbol, "market", side, amount, None, params)

    def fetch_order_safe(self, order_id, symbol):
        if self.dry_run or self.exchange is None or not order_id:
            return None
        try:
            return self.exchange.fetch_order(order_id, symbol)
        except Exception:
            return None

    def close_market_order(self, symbol, direction, amount):
        close_direction = "Short" if direction == "Long" else "Long"
        return self.create_market_order(symbol, close_direction, amount, reduce_only=True)