# -*- coding: utf-8 -*-
"""Funding-rate helpers for live observer messages.

返回值统一为“百分比单位”：0.0100 表示 0.0100%。
"""
from __future__ import annotations

from typing import Any, Iterable


def normalize_swap_symbol(symbol: str) -> str:
    """Normalize common USDT perpetual symbols for ccxt swap markets.

    BTCUSDT        -> BTC/USDT:USDT
    BTC/USDT       -> BTC/USDT:USDT
    BTC/USDT:USDT  -> BTC/USDT:USDT
    """
    s = str(symbol or "BTC/USDT:USDT").strip().upper()
    if not s:
        return "BTC/USDT:USDT"
    if ":" in s:
        return s
    if "/" in s:
        base, quote = s.split("/", 1)
        quote = quote.split(":", 1)[0]
        if quote == "USDT":
            return f"{base}/USDT:USDT"
        return s
    if s.endswith("USDT"):
        return f"{s[:-4]}/USDT:USDT"
    return s


def _safe_float(v: Any):
    if v in [None, "", "N/A", "nan", "None"]:
        return None
    try:
        return float(v)
    except Exception:
        return None


def parse_funding_rate_to_pct(v: Any):
    """Convert exchange raw funding rate to percentage units.

    交易所经常返回 0.0001，实际展示应为 0.0100%。
    如果调用方已经传入 0.01，则视为已经是百分比单位，不再放大。
    """
    x = _safe_float(v)
    if x is None:
        return None
    # 原始 fundingRate 通常绝对值小于 0.001，例如 0.0001 = 0.01%。
    if abs(x) < 0.001:
        x *= 100.0
    return round(float(x), 4)


def _pick_from_dict(data: Any):
    if not isinstance(data, dict):
        return None

    # ccxt 统一字段优先。
    for key in ("fundingRate", "lastFundingRate", "previousFundingRate", "nextFundingRate"):
        pct = parse_funding_rate_to_pct(data.get(key))
        if pct is not None:
            return pct

    # 交易所原始 info 兜底，Bitget/Bybit/Binance 字段名可能不同。
    info = data.get("info") or {}
    if isinstance(info, dict):
        for key in (
            "fundingRate",
            "lastFundingRate",
            "fundingRateInterval",
            "currentFundingRate",
            "rate",
        ):
            pct = parse_funding_rate_to_pct(info.get(key))
            if pct is not None:
                return pct
    return None


def _candidate_symbols(symbol: str) -> Iterable[str]:
    norm = normalize_swap_symbol(symbol)
    raw = str(symbol or "").strip()
    # 有些 exchange 实例已经 load_markets 后只认原始，有些只认 swap 规范名。
    for s in (norm, raw):
        if s:
            yield s


def fetch_funding_rate_safe(exchange, symbol: str):
    """Fetch live funding rate safely. Returns pct float or "N/A".

    不再静默只试一个 symbol；会尝试规范合约名、原始名、fetch_funding_rates 兜底。
    """
    if exchange is None:
        return "N/A"

    last_error = None
    for sym in _candidate_symbols(symbol):
        try:
            data = exchange.fetch_funding_rate(sym)
            pct = _pick_from_dict(data)
            if pct is not None:
                return pct
        except Exception as exc:
            last_error = exc

    # 部分交易所只支持批量接口。
    try:
        rates = exchange.fetch_funding_rates()
        if isinstance(rates, dict):
            norm = normalize_swap_symbol(symbol)
            raw = str(symbol or "").strip()
            for key in (norm, raw, norm.replace(":USDT", "")):
                pct = _pick_from_dict(rates.get(key))
                if pct is not None:
                    return pct
    except Exception as exc:
        last_error = exc

    return "N/A"
