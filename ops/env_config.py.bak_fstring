# -*- coding: utf-8 -*-
"""Environment-aware configuration loader.

This keeps the checked-in config safe for Hugging Face while allowing secrets and
runtime overrides to come from Space secrets/environment variables.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
from ops.runtime_paths import CONFIG_PATH


def _split_symbols(value):
    return [x.strip() for x in str(value).split(",") if x.strip()]


def load_runtime_config(path=None, overrides=None):
    p = Path(path) if path else CONFIG_PATH
    if not p.exists():
        p = Path(__file__).resolve().parents[1] / "config" / "v11_full_config.json"
    with open(p, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg = deepcopy(cfg)
    env_map = {
        "SMC_MODE": ("mode", str),
        "SMC_DATA_MODE": ("data_mode", str),
        "SMC_EXCHANGE": ("exchange", str),
        "SMC_EXEC_TIMEFRAME": ("exec_timeframe", str),
        "SMC_MACRO_TIMEFRAME": ("macro_timeframe", str),
    }
    for env, (key, cast) in env_map.items():
        if os.environ.get(env):
            cfg[key] = cast(os.environ[env])
    if os.environ.get("SMC_SYMBOLS"):
        cfg["symbols"] = _split_symbols(os.environ["SMC_SYMBOLS"])
    risk = cfg.setdefault("risk", {})
    for env, key, cast in [
        ("SMC_RISK_PER_TRADE", "risk_per_trade", float),
        ("SMC_MIN_RR", "min_rr", float),
        ("SMC_MAX_OPEN_POSITIONS", "max_open_positions", int),
        ("SMC_MAX_SAME_DIRECTION_POSITIONS", "max_same_direction_positions", int),
        ("SMC_LEVERAGE", "leverage", float),
    ]:
        if os.environ.get(env):
            risk[key] = cast(os.environ[env])
    secrets = cfg.setdefault("secrets", {})
    for env, key in [
        ("EXCHANGE_API_KEY", "api_key"),
        ("EXCHANGE_API_SECRET", "api_secret"),
        ("EXCHANGE_PASSWORD", "password"),
        ("TELEGRAM_BOT_TOKEN", "telegram_bot_token"),
        ("TELEGRAM_CHAT_ID", "telegram_chat_id"),
    ]:
        if os.environ.get(env):
            secrets[key] = "***set_by_env***"
    if overrides:
        for k, v in overrides.items():
            cfg[k] = v
    return cfg


def redact_config(cfg):
    x = deepcopy(cfg)
    for section in ("secrets", "api", "exchange_keys"):
        if isinstance(x.get(section), dict):
            for k in list(x[section].keys()):
                x[section][k] = "***redacted***"
    return x
