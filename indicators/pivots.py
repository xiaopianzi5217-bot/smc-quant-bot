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

def find_developing_pivots(series, left=2, right=1, is_high=True, atr_series=None, atr_threshold=0.5, min_spacing=3):
    """
    实时动态枢轴识别（未确认 / Developing Pivot）

    与原 find_pivots 的区别：
    - 针对最新的 right 根 K 线，使用非对称窗口（右侧=0）
    - 不需要等待右侧 K 线确认，只要左侧满足 left 根更高/更低即可标记
    - 用于让 SMC 逻辑实时发现潜在 Swing High/Low，消除右侧确认延迟
    """
    if series is None or len(series) < (left + right + 5):
        return []
    if atr_series is not None and len(atr_series) != len(series):
        return []

    pivots = []
    last_pivot_idx = -999

    # 只扫描最新 right 根 K 线（原逻辑中这些 K 线永远无法被确认为 Pivot）
    start = max(left, len(series) - right)
    for i in range(start, len(series)):
        curr = series.iloc[i]
        if pd.isna(curr):
            continue

        # 左侧确认：必须有 left 根 K 线满足条件
        ok = True
        for j in range(1, left + 1):
            v = series.iloc[i - j]
            if pd.isna(v) or (is_high and curr <= v) or ((not is_high) and curr >= v):
                ok = False
                break
        if not ok:
            continue

        # 【核心】右侧无需确认（非对称窗口 right=0）
        # 只需 ATR 强度过滤，防止噪声小波峰被标记
        if atr_series is not None:
            atr_val = atr_series.iloc[i]
            if pd.isna(atr_val) or atr_val <= 0:
                continue
            left_price = series.iloc[i - left]
            if pd.isna(left_price):
                continue
            strength = abs(curr - left_price)
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
