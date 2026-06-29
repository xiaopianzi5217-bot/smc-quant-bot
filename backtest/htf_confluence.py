# backtest/htf_confluence.py
# -*- coding: utf-8 -*-
"""
HTF Confluence Engine (1H Macro Bias)
Calculates a macro bias score (-100 to +100) based on 1H Trend, Momentum, and basic Structure.
"""
import numpy as np
import pandas as pd

def compute_htf_macro_score(df: pd.DataFrame) -> pd.DataFrame:
    """
    预处理 1H 数据，计算宏观共振得分。
    """
    if df.empty or len(df) < 200:
        df['htf_macro_score'] = 0.0
        return df

    # 1. 计算 1H 趋势锚点 (EMA 200)
    df['ema_200'] = df['close'].ewm(span=200, adjust=False).mean()
    
    # 2. 计算 1H 动量 (MACD 代替 SQZMOM，因为直接算 SQZ 比较复杂，MACD 足够判断大势背离/衰竭)
    exp1 = df['close'].ewm(span=12, adjust=False).mean()
    exp2 = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = exp1 - exp2
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
    df['macd_hist'] = df['macd'] - df['macd_signal']
    
    # 3. 简单的流动性扫描代理 (价格跌破近期低点后收回)
    # 取前 10 根 K 线的最低点作为局部流动性池
    df['rolling_low_10'] = df['low'].shift(1).rolling(window=10).min()
    df['rolling_high_10'] = df['high'].shift(1).rolling(window=10).max()
    
    scores = []
    for i in range(len(df)):
        if i < 200:
            scores.append(0.0)
            continue
            
        row = df.iloc[i]
        score = 0.0
        
        # --- 维度 1: 趋势基底 (+/- 40) ---
        if row['close'] > row['ema_200']:
            score += 40.0
        else:
            score -= 40.0
            
        # --- 维度 2: 动量方向 (+/- 30) ---
        if row['macd_hist'] > 0:
            score += 30.0
            # 动量增强：如果 MACD 柱在零轴上且还在放大
            if row['macd_hist'] > df['macd_hist'].iloc[i-1]:
                score += 10.0
        elif row['macd_hist'] < 0:
            score -= 30.0
            if row['macd_hist'] < df['macd_hist'].iloc[i-1]:
                score -= 10.0
                
        # --- 维度 3: 流动性猎杀代理 (Sweep) (+/- 20) ---
        # 扫盘：当前 K 线最低价跌破前 10 根最低价，但收盘价收了回来（长下影线）
        if row['low'] < row['rolling_low_10'] and row['close'] > row['rolling_low_10']:
            score += 20.0  # 扫了下方的流动性 (Bullish Sweep)
            
        if row['high'] > row['rolling_high_10'] and row['close'] < row['rolling_high_10']:
            score -= 20.0  # 扫了上方的流动性 (Bearish Sweep)
            
        # 限制在 -100 到 100 之间
        score = max(-100.0, min(100.0, score))
        scores.append(score)
        
    df['htf_macro_score'] = scores
    return df
