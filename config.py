# -*- coding: utf-8 -*-
import os

# ================= V38 REAL ENGINE CONFIG =================
# 统一控制层：哪些引擎启用，哪些隔离
VERSION = "V11_V3_INSTITUTIONAL_ROOT_20260918"
PURE_MODE = False  # V54: no legacy book hard whitelist; use probe sizing instead
ENGINES = ["TRANSITION", "CORE", "TREND"]  # 启用哪些 regime 引擎
ISOLATED = ["PROBE"]  # 完全隔离账户的 book
ALLOWED_BOOKS = ["CORE", "TACTICAL", "SCALP", "PROBE"]
ALLOWED_GRADES = ["A_EV", "B_EV", "C_EV"]

SYMBOLS = ['BTC/USDT:USDT', 'ETH/USDT:USDT', 'SOL/USDT:USDT']
TIMEFRAME_MACRO = '1h'
TIMEFRAME_EXEC = '15m'

# ===============================
# Production Risk Guard
# ===============================
# 信号冷却时间（秒）
SIGNAL_COOLDOWN_SECONDS = 900
# 每日最大亏损 R 倍数
MAX_DAILY_LOSS_R = 3
# 每日最大交易次数
MAX_TRADES_DAY = 5
# 最大连续亏损次数
MAX_CONSECUTIVE_LOSS = 3
# 是否启用运行时状态恢复
ENABLE_RUNTIME_RECOVERY = True

# ===============================
# HTF Regime 控制 (GATE-4)
# ===============================
HTF_REGIME_HARD_BLOCK = False          # True=一票否决，False=只扣分
HTF_REGIME_PENALTY_SCORE = 20.0        # 逆势时扣多少分
HTF_ALLOW_COUNTER_TREND = True         # 是否允许逆势信号进入后续流程

# ===============================
# LIQUIDITY_SWEEP 控制
# ===============================
LIQUIDITY_SWEEP_REQUIRE_CHOCH = False     # False=不强制要 CHOCH，只有 momentum 足够就能通过
LIQUIDITY_SWEEP_MIN_VOL_RATIO = 0.3       # 原 1.0，降低门槛
LIQUIDITY_SWEEP_HARD_VETO = False         # False=只扣分，True=一票否决
LIQUIDITY_SWEEP_PENALTY = 15.0

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

# ===============================
# Probe Mode — Observer 转探针交易
# ===============================
# 让 Observer-only 模式的信号以极小仓位执行，积累真实胜率数据
PROBE_MODE = True                          # 是否启用 Probe Mode
PROBE_SIZE_MULTIPLIER = 0.25               # Probe 仓位乘数（相对正常仓位）
PROBE_MIN_SCORE = 55                       # Probe 最低 score 门槛
PROBE_MIN_CONFIDENCE = 0.55                # Probe 最低 confidence 门槛
PROBE_REQUIRED_EVENTS = [                  # Observer 事件白名单
    "CHOCH", "LIQUIDITY_SWEEP", "FVG", "SQUEEZE_RELEASE",
]
PROBE_MIN_STRONG_EVENTS = 2                # 至少满足几个白名单事件


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

