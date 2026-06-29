# -*- coding: utf-8 -*-

def normalize_symbol_key(symbol: str) -> str:
    if not symbol:
        return "DEFAULT"
    coin = symbol.split("/")[0].split(":")[0].upper()
    return f"{coin}USDT"

def load_symbol_strategy(symbol, strategy_map):
    sym_key = normalize_symbol_key(symbol)
    base = dict(strategy_map.get("DEFAULT", {}))
    base.update(strategy_map.get(sym_key, {}))
    return base

def load_threshold(symbol, threshold_config):
    sym_key = normalize_symbol_key(symbol)
    return threshold_config.get(sym_key, threshold_config.get("DEFAULT", {"strong_threshold": 65})).get("strong_threshold", 65)
