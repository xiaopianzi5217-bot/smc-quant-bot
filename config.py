# -*- coding: utf-8 -*-
import os

# ================= V38 REAL ENGINE CONFIG =================
# 统一控制层：哪些引擎启用，哪些隔离
VERSION = "V54_ALPHA_EXPANSION_20260621"
PURE_MODE = False  # V54: no legacy book hard whitelist; use probe sizing instead
ENGINES = ["TRANSITION", "CORE", "TREND"]  # 启用哪些 regime 引擎
ISOLATED = ["PROBE"]  # 完全隔离账户的 book
ALLOWED_BOOKS = ["CORE", "TACTICAL", "SCALP", "PROBE"]
ALLOWED_GRADES = ["A_EV", "B_EV", "C_EV"]

SYMBOLS = ['BTC/USDT:USDT', 'ETH/USDT:USDT', 'SOL/USDT:USDT']
TIMEFRAME_MACRO = '1h'
TIMEFRAME_EXEC = '15m'

STRATEGY_PARAMS = {
    'rsi_ob': 75,
    'rsi_os': 25,
    'wvf_std_mult': 2.0,
    'funding_extreme_pct': 0.01,
    'score_base_threshold': 5,
    'ob_sl_atr_mult': 1.8,
    'trailing_atr_mult': 2.0,
    'tp1_path_ratio': 0.5,
    'tp3_path_ratio': 1.5,
    'tp1_close_pct': 0.6,
    'max_bars_held': 40,    'vwap_enabled': True,
    'vwap_max_chase_atr': 1.8,
    'vwap_reclaim_atr': 0.25,

}

PIVOT_PARAMS = {
    'macro': {'left': 5, 'right': 3, 'atr_threshold': 0.5, 'min_spacing': 5},
    'exec': {
        'left': 4, 'right': 2,
        'atr_threshold_low': 0.3,
        'atr_threshold_normal': 0.35,
        'atr_threshold_high': 0.7,
        'min_spacing': 2,
    },
    'momentum': {'left': 2, 'right': 1, 'min_spacing': 1},
}

SYMBOL_STRATEGY = {
    "DEFAULT": {
        "trailing_atr_mult": 2.0,
        "tp1_path_ratio": 0.5,
        "tp3_path_ratio": 1.5,
        "tp1_close_pct": 0.6,
        "max_bars_held": 40,
        "ob_sl_atr_mult": 1.8,
    },
    "BTCUSDT": {"trailing_atr_mult": 2.0, "tp1_close_pct": 0.6},
    "ETHUSDT": {"trailing_atr_mult": 2.5, "tp1_close_pct": 0.5},
    "SOLUSDT": {"trailing_atr_mult": 2.2, "tp1_close_pct": 0.55},
}

THRESHOLD_CONFIG = {
    "BTCUSDT": {"strong_threshold": 65},
    "ETHUSDT": {"strong_threshold": 60},
    "DEFAULT": {"strong_threshold": 65},
}

ALERT_RULES = {
    "divergence_top": "high",
    "divergence_bot": "high",
    "liquidity_sweep_bsl": "high",
    "liquidity_sweep_ssl": "high",
    "near_bsl": "medium",
    "near_ssl": "medium",
    "color_change": "low",
    "consensus_extreme": "high",
    "consensus_strong": "medium",
    "open_signal": "high",
    "open_signal_standard": "medium",
}

RISK = {
    'total_capital': 10000.0,
    'risk_per_trade': 0.02,
    'max_leverage_notional': 5.0,
}

PATHS = {
    'active_trades': 'data/active_trades.json',
    'trade_journal': 'data/trade_journal.csv',
    'error_log': 'data/bot_errors.log',
}

TELEGRAM = {
    'bot_token': os.getenv('TG_BOT_TOKEN', ''),
    'chat_id': os.getenv('TG_CHAT_ID', ''),
    'bridge_url': 'https://lingering-breeze-e789.xiaopianzi5217.workers.dev',
}

if __name__ == '__main__':
    print('TG_BOT_TOKEN =', os.getenv('TG_BOT_TOKEN'))
    print('TG_CHAT_ID =', os.getenv('TG_CHAT_ID'))
