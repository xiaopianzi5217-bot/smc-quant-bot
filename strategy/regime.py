# -*- coding: utf-8 -*-
import pandas as pd

def detect_market_regime(df):
    curr = df.iloc[-1]
    adx = curr.get('ADX_14', float('nan'))
    if pd.isna(adx):
        regime = 'transition'
    elif adx < 20:
        regime = 'mud'
    elif adx <= 25:
        regime = 'transition'
    else:
        regime = 'trend'

    squeeze = 'building' if curr.get('highsqz') or curr.get('midsqz') or curr.get('lowsqz') else 'released'

    atr = curr.get('ATRr_14', float('nan'))
    atr_ma = df['ATRr_14'].rolling(20).mean().iloc[-1]
    if pd.isna(atr) or pd.isna(atr_ma) or atr_ma <= 0:
        atr_ratio = 1.0
        volatility = 'normal'
    else:
        atr_ratio = float(atr / atr_ma)
        if atr_ratio < 0.8:
            volatility = 'low'
        elif atr_ratio > 1.5:
            volatility = 'high'
        else:
            volatility = 'normal'

    return {'regime': regime, 'squeeze': squeeze, 'volatility': volatility, 'adx': float(adx) if not pd.isna(adx) else 0.0, 'atr_ratio': atr_ratio}


# ------------------------------------------------------------------
#  Regime 乘数映射（供 DecisionKernel 等模块统一调用）
# ------------------------------------------------------------------
REGIME_MULTIPLIERS = {
    "trend": 1.3,
    "transition": 1.0,
    "mud": 0.7,
}
VOLATILITY_MULTIPLIERS = {
    "high": 0.6,
    "normal": 1.0,
    "low": 1.1,
}


def get_regime_multiplier(regime: str, volatility: str = "normal") -> float:
    """获取环境综合仓位乘数"""
    base = REGIME_MULTIPLIERS.get(regime, 1.0)
    vol = VOLATILITY_MULTIPLIERS.get(volatility, 1.0)
    return round(base * vol, 3)
