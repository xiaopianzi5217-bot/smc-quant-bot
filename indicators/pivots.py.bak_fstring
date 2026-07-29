# -*- coding: utf-8 -*-
import pandas as pd

def find_pivots(series, left=2, right=1, is_high=True, atr_series=None, atr_threshold=0.5, min_spacing=3):
    if series is None or len(series) < (left + right + 5):
        return []
    if atr_series is not None and len(atr_series) != len(series):
        return []

    pivots = []
    last_pivot_idx = -999

    for i in range(left, len(series) - right):
        curr = series.iloc[i]
        if pd.isna(curr):
            continue

        ok = True
        for j in range(1, left + 1):
            v = series.iloc[i - j]
            if pd.isna(v) or (is_high and curr <= v) or ((not is_high) and curr >= v):
                ok = False
                break
        if not ok:
            continue

        for j in range(1, right + 1):
            v = series.iloc[i + j]
            if pd.isna(v) or (is_high and curr < v) or ((not is_high) and curr > v):
                ok = False
                break
        if not ok:
            continue

        if atr_series is not None:
            atr_val = atr_series.iloc[i]
            if pd.isna(atr_val) or atr_val <= 0:
                continue
            left_price = series.iloc[i - left]
            right_price = series.iloc[i + right]
            if pd.isna(left_price) or pd.isna(right_price):
                continue
            strength = abs(curr - ((left_price + right_price) / 2))
            if strength < atr_val * atr_threshold:
                continue

        if i - last_pivot_idx < min_spacing:
            continue

        pivots.append(i)
        last_pivot_idx = i

    return pivots

def dynamic_pivot_threshold(regime_info, low=0.3, normal=0.35, high=0.7):
    vol = regime_info.get('volatility', 'normal')
    if vol == 'high':
        return high
    if vol == 'low':
        return low
    return normal
