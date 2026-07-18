# -*- coding: utf-8 -*-
import pandas as pd
import numpy as np

try:
    import pandas_ta as ta
except Exception:
    ta = None


def _ema(x, length):
    return x.ewm(span=length, adjust=False).mean()

def _sma(x, length):
    return x.rolling(length).mean()

def _stdev(x, length):
    return x.rolling(length).std()

def _true_range(high, low, close):
    prev_close = close.shift(1)
    return pd.concat([(high-low).abs(), (high-prev_close).abs(), (low-prev_close).abs()], axis=1).max(axis=1)

def _atr(high, low, close, length=14):
    return _true_range(high, low, close).rolling(length).mean()

def _rsi(close, length=14):
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(length).mean()
    loss = (-delta.clip(upper=0)).rolling(length).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def _macd(close):
    macd = _ema(close, 12) - _ema(close, 26)
    signal = _ema(macd, 9)
    hist = macd - signal
    return pd.DataFrame({'MACD_12_26_9': macd, 'MACDs_12_26_9': signal, 'MACDh_12_26_9': hist})

def _adx(high, low, close, length=14):
    tr = _true_range(high, low, close)
    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    atr = tr.rolling(length).sum().replace(0, np.nan)
    plus_di = 100 * plus_dm.rolling(length).sum() / atr
    minus_di = 100 * minus_dm.rolling(length).sum() / atr
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)) * 100
    adx = dx.rolling(length).mean()
    return pd.DataFrame({f'ADX_{length}': adx, f'DMP_{length}': plus_di, f'DMN_{length}': minus_di})

def _linreg(series, length=20):
    def calc(y):
        import numpy as np
        x = np.arange(len(y), dtype=float)
        if len(y) < 2 or pd.isna(y).any():
            return np.nan
        a, b = np.polyfit(x, y, 1)
        return a * (len(y)-1) + b
    return series.rolling(length).apply(calc, raw=False)


def calculate_advanced_sqzmom(df: pd.DataFrame, length: int = 20, mult_bb: float = 2.0, mult_kc: float = 1.5) -> dict:
    """
    【V6指标核心升级】精细化 Squeeze Momentum 测速雷达
    返回最新一根 K 线的 SQZMOM 深度多维特征字典
    """
    if df is None or len(df) < length + 5:
        return {
            "released": False,
            "duration": 0,
            "strength": 0.0,
            "vol_ratio": 1.0,
            "volume_confirmed": False,
        }

    close = df['close'].astype(float)
    high = df['high'].astype(float)
    low = df['low'].astype(float)
    volume = df['volume'].astype(float) if 'volume' in df.columns else pd.Series([0.0] * len(df), index=df.index)

    ma = close.rolling(window=length, min_periods=length).mean()
    std = close.rolling(window=length, min_periods=length).std()
    upper_bb = ma + mult_bb * std
    lower_bb = ma - mult_bb * std

    tr = _true_range(high, low, close)
    atr = tr.rolling(window=length, min_periods=length).mean()
    kc_basis = _ema(close, length)
    upper_kc = kc_basis + mult_kc * atr
    lower_kc = kc_basis - mult_kc * atr

    is_squeezing = (upper_bb < upper_kc) & (lower_bb > lower_kc)

    duration = 0
    idx = len(df) - 1
    if idx >= 0 and not bool(is_squeezing.iloc[idx]):
        idx -= 1
    while idx >= 0 and bool(is_squeezing.iloc[idx]):
        duration += 1
        idx -= 1

    hist = (close - kc_basis).fillna(0.0)
    current_hist = float(hist.iloc[-1]) if len(hist) >= 1 else 0.0
    prev_hist = float(hist.iloc[-2]) if len(hist) >= 2 else 0.0

    was_squeezing = bool(is_squeezing.iloc[-2]) if len(is_squeezing) >= 2 else False
    is_squeezing_now = bool(is_squeezing.iloc[-1]) if len(is_squeezing) >= 1 else False
    released = was_squeezing and not is_squeezing_now
    strength = abs(current_hist - prev_hist)

    avg_vol = volume.rolling(window=20, min_periods=5).mean().iloc[-1] if len(volume) >= 20 else volume.mean()
    avg_vol = float(avg_vol) if not pd.isna(avg_vol) and avg_vol > 0 else 1.0
    current_vol = float(volume.iloc[-1]) if len(volume) >= 1 else 0.0
    vol_ratio = current_vol / avg_vol if avg_vol > 0 else 1.0
    volume_confirmed = vol_ratio >= 1.3

    return {
        "released": bool(released),
        "duration": int(duration),
        "strength": round(float(strength), 4),
        "vol_ratio": round(float(vol_ratio), 2),
        "volume_confirmed": bool(volume_confirmed),
    }


def _local_pivot_low(s: pd.Series, left: int = 2, right: int = 1) -> pd.Series:
    out = pd.Series(False, index=s.index)
    for k in range(left, len(s) - right):
        win = s.iloc[k - left:k + right + 1]
        out.iloc[k] = bool(s.iloc[k] == win.min())
    return out

def _local_pivot_high(s: pd.Series, left: int = 2, right: int = 1) -> pd.Series:
    out = pd.Series(False, index=s.index)
    for k in range(left, len(s) - right):
        win = s.iloc[k - left:k + right + 1]
        out.iloc[k] = bool(s.iloc[k] == win.max())
    return out


def add_all_indicators(df: pd.DataFrame, wvf_mult: float = 2.0) -> pd.DataFrame:
    df = df.copy()
    
    if ta is not None:
        df['rsi'] = ta.rsi(df['close'], length=14)
        df['ATRr_14'] = ta.atr(df['high'], df['low'], df['close'], length=14)
        macd_df = ta.macd(df['close'])
        adx_df = ta.adx(df['high'], df['low'], df['close'], length=14)
    else:
        df['rsi'] = _rsi(df['close'], length=14)
        df['ATRr_14'] = _atr(df['high'], df['low'], df['close'], length=14)
        macd_df = _macd(df['close'])
        adx_df = _adx(df['high'], df['low'], df['close'], length=14)
    df = pd.concat([df, macd_df, adx_df], axis=1)

    df['hlc3'] = (df['high'] + df['low'] + df['close']) / 3
    vwma1 = (df['hlc3'] * df['volume']).rolling(26).sum() / df['volume'].rolling(26).sum()
    media = (vwma1 * df['volume']).rolling(26).sum() / df['volume'].rolling(26).sum()
    mad = df['hlc3'].rolling(26).apply(lambda x: abs(x - x.mean()).mean(), raw=True)
    xtl_denom = 0.015 * mad.replace(0, np.nan)
    df['xtl_val'] = (df['hlc3'] - media) / xtl_denom

    df['ohlc4'] = (df['open'] + df['high'] + df['low'] + df['close']) / 4
    length_m = 20
    ma_momentum = ta.sma(df['close'], length=length_m) if ta is not None else _sma(df['close'], length_m)
    hh = df['high'].rolling(length_m).max()
    ll = df['low'].rolling(length_m).min()
    avg_total = ((hh + ll) / 2.0 + ma_momentum) / 2.0
    df['sz'] = ta.linreg(df['close'] - avg_total, length=length_m) if ta is not None else _linreg(df['close'] - avg_total, length_m)

    sqz_len = 20
    df['bb_basis'] = ta.sma(df['ohlc4'], length=sqz_len) if ta is not None else _sma(df['ohlc4'], sqz_len)
    df['bb_dev'] = 2.0 * (ta.stdev(df['ohlc4'], length=sqz_len) if ta is not None else _stdev(df['ohlc4'], sqz_len))
    df['upperBB'] = df['bb_basis'] + df['bb_dev']
    df['lowerBB'] = df['bb_basis'] - df['bb_dev']
    df['tr'] = ta.true_range(df['high'], df['low'], df['close']) if ta is not None else _true_range(df['high'], df['low'], df['close'])
    df['kc_basis'] = ta.ema(df['ohlc4'], length=sqz_len) if ta is not None else _ema(df['ohlc4'], sqz_len)
    df['kc_range'] = ta.ema(df['tr'], length=sqz_len) if ta is not None else _ema(df['tr'], sqz_len)
    df['upperKCl'] = df['kc_basis'] + df['kc_range'] * 2.0
    df['lowerKCl'] = df['kc_basis'] - df['kc_range'] * 2.0
    df['upperKCm'] = df['kc_basis'] + df['kc_range'] * 1.5
    df['lowerKCm'] = df['kc_basis'] - df['kc_range'] * 1.5
    df['upperKCh'] = df['kc_basis'] + df['kc_range'] * 1.0
    df['lowerKCh'] = df['kc_basis'] - df['kc_range'] * 1.0
    df['lowsqz'] = (df['lowerBB'] > df['lowerKCl']) & (df['upperBB'] < df['upperKCl'])
    df['midsqz'] = (df['lowerBB'] > df['lowerKCm']) & (df['upperBB'] < df['upperKCm'])
    df['highsqz'] = (df['lowerBB'] > df['lowerKCh']) & (df['upperBB'] < df['upperKCh'])

    df['highest_c'] = df['close'].rolling(22).max()
    df['wvf'] = ((df['highest_c'] - df['low']) / df['highest_c']) * 100
    df['is_FE'] = df['wvf'] >= df['wvf'].rolling(20).mean() + (wvf_mult * df['wvf'].rolling(20).std())
    df['lowest_c'] = df['close'].rolling(22).min()
    df['inv_wvf'] = ((df['high'] - df['lowest_c']) / df['lowest_c']) * 100
    df['is_Inv_FE'] = df['inv_wvf'] >= df['inv_wvf'].rolling(20).mean() + (wvf_mult * df['inv_wvf'].rolling(20).std())
    
    # ===== 策略核心指标强注入 =====
    # 1. EMA 系列（backtest 引擎和 regime_filter 必须）
    df['ema_20'] = _ema(df['close'], 20)
    df['ema_50'] = _ema(df['close'], 50)
    df['ema_200'] = _ema(df['close'], 200)
    
    # 2. ADX 列名归一化（pandas_ta 输出 ADX_14，_adx 函数也输出 ADX_14）
    if 'ADX_14' in df.columns:
        df['adx'] = df['ADX_14']
    elif 'adx' not in df.columns:
        df['adx'] = _adx(df['high'], df['low'], df['close'], 14)['ADX_14']
    
    # ===== 诊断探针所需字段 =====
    # volume_ratio（vol_z）：当前成交量 / 20 周期均值
    vol_ma20 = df['volume'].rolling(20, min_periods=5).mean()
    df['volume_ratio'] = df['volume'] / vol_ma20.replace(0, np.nan)
    # volume_contracting: 缩量标记
    df['volume_contracting'] = df['volume_ratio'] <= 0.82
    # high_vol_anomaly: 放量异常标记
    df['high_vol_anomaly'] = df['volume_ratio'] > 1.5
    
    # high_20 / low_20：20 周期最高/最低
    df['high_20'] = df['high'].rolling(20, min_periods=5).max()
    df['low_20'] = df['low'].rolling(20, min_periods=5).min()
    
    # ===== Body/K线实体相关 =====
    close = df['close']
    low = df['low']
    high = df['high']
    open_ = df['open']
    # body_pct: 实体占比 (close-open)/range
    rng = (high - low).replace(0, np.nan)
    df['body_pct'] = (close - open_).abs() / rng
    df['bar_range_atr'] = rng / df['ATRr_14'].replace(0, np.nan)
    df['upper_wick_pct'] = (high - df[['open', 'close']].max(axis=1)) / rng
    df['lower_wick_pct'] = (df[['open', 'close']].min(axis=1) - low) / rng
    # EMA slope: 用于趋势判定
    df['ema_slope_20'] = df['ema_20'].diff(5)
    df['ema_slope_50'] = df['ema_50'].diff(8)

    # ===== Squeeze 和动量 =====
    bb_mid = df['bb_basis']
    df['momentum'] = close - bb_mid
    df['momentum_slope_1'] = df['momentum'].diff(1).fillna(0.0)
    df['momentum_signal'] = df['momentum'].rolling(5, min_periods=1).mean()
    df['momentum_strength'] = (df['momentum'] - df['momentum_signal']).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    df['momentum_slope'] = df['momentum'].diff(3).fillna(0.0)
    df['momentum_strength_slope'] = df['momentum_strength'].diff(3).fillna(0.0)
    df['momentum_strength_rising_3'] = df['momentum_strength'].diff().rolling(3, min_periods=1).sum().fillna(0.0)

    # Squeeze on/released
    df['squeeze_on'] = df['lowsqz']
    prev_squeeze_on = df['squeeze_on'].shift(1).astype('boolean').fillna(False).astype(bool)
    df['squeeze_released'] = prev_squeeze_on & (~df['squeeze_on'].astype(bool))
    df['squeeze_level'] = np.select(
        [df['highsqz'], df['midsqz'], df['lowsqz']],
        [3, 2, 1],
        default=0,
    )

    # ===== DMI 信号 =====
    plus_di = df.get('DMP_14', pd.Series(0.0, index=df.index))
    minus_di = df.get('DMN_14', pd.Series(0.0, index=df.index))
    if 'DMP_14' not in df.columns:
        # fallback compute
        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        atr_dmi = df['ATRr_14'].replace(0, np.nan)
        plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1/14, adjust=False).mean() / atr_dmi
        minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1/14, adjust=False).mean() / atr_dmi
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
        if 'adx' not in df.columns or df['adx'].isna().all():
            df['adx'] = dx.ewm(alpha=1/14, adjust=False).mean().fillna(0.0)
    df['plus_di'] = plus_di.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    df['minus_di'] = minus_di.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    df['adx_rising'] = df['adx'].diff(1).fillna(0.0) > 0
    df['dmi_bull'] = (df['plus_di'] >= df['minus_di']) & (df['adx'] >= 23.0)
    df['dmi_bear'] = (df['plus_di'] < df['minus_di']) & (df['adx'] >= 23.0)
    df['dmi_weak'] = (df['adx'] < 23.0) & (df['adx'] > 17.0)

    # ===== 50-bar Equilibrium Price（Premium/Discount） =====
    recent_high_50 = high.rolling(50, min_periods=10).max()
    recent_low_50 = low.rolling(50, min_periods=10).min()
    df['eq_price'] = (recent_high_50 + recent_low_50) / 2.0

    # ===== FE Stop/Reversal 信号（CM Williams Vix Fix V3） =====
    df = _add_vix_fix_fe_signals(df)

    # ===== 简约 Squeeze On / 动量整合 =====
    # smc_quality_score: 简化版
    atr_14 = (high - low).rolling(14).mean().bfill()
    deviation = (close - df['ema_20']) / df['ema_20'].replace(0, np.nan) * 500
    bull_score = pd.Series(36.0, index=df.index)
    bear_score = pd.Series(36.0, index=df.index)
    bull_score += deviation.clip(0, 20) * 0.42
    bear_score += (-deviation).clip(0, 20) * 0.42
    vol_score = (((high - low) / atr_14.replace(0, np.nan) * 10).clip(0, 15) * 0.38)
    bull_score += vol_score
    bear_score += vol_score
    vol_ratio_score = ((df['volume_ratio'] * 10).clip(0, 15) * 0.35)
    bull_score += vol_ratio_score
    bear_score += vol_ratio_score
    body_score = (df['body_pct'].fillna(0.0).clip(0, 1.0) * 6.0)
    bull_score += body_score
    bear_score += body_score
    squeeze_bonus = np.where(df['squeeze_released'], 4.0, 0.0)
    bull_score += squeeze_bonus
    bear_score += squeeze_bonus
    df['smc_quality_score_bull'] = pd.Series(bull_score, index=df.index).replace([np.inf, -np.inf], np.nan).fillna(36.0).clip(0, 100)
    df['smc_quality_score_bear'] = pd.Series(bear_score, index=df.index).replace([np.inf, -np.inf], np.nan).fillna(36.0).clip(0, 100)
    df['smc_quality_score'] = df['smc_quality_score_bull']

    # ===== SQZMOM 背离 + White Bar 反转（核心！） =====
    df = _add_sqzmom_divergence_features(df)

    # ===== 背离简化标签（兼容期） =====
    rsi_14 = df['rsi']
    price_high = df['high'].rolling(5, min_periods=3).max()
    rsi_high = rsi_14.rolling(5, min_periods=3).max()
    df['divergence'] = 0
    top_div = (price_high > price_high.shift(1)) & (rsi_high < rsi_high.shift(1))
    price_low = df['low'].rolling(5, min_periods=3).min()
    rsi_low = rsi_14.rolling(5, min_periods=3).min()
    bot_div = (price_low < price_low.shift(1)) & (rsi_low > rsi_low.shift(1))
    df.loc[top_div, 'divergence'] = -1  # 顶背离
    df.loc[bot_div, 'divergence'] = 1   # 底背离
    
    # ===== 方向性 Marker（用于 liquidity sweep 检测） =====
    # simple cooch / sweeep 检测缩写（forward-fill pivot labels）
    df['sweep_low'] = _local_pivot_low(low, 2, 1) & (low <= low.rolling(10, min_periods=5).min().shift(1))
    df['sweep_high'] = _local_pivot_high(high, 2, 1) & (high >= high.rolling(10, min_periods=5).max().shift(1))
    df['sellside_liquidity_taken'] = df['sweep_low'].astype(bool)
    df['buyside_liquidity_taken'] = df['sweep_high'].astype(bool)
    df['sellside_sweep'] = df['sweep_low'].astype(bool)
    df['buyside_sweep'] = df['sweep_high'].astype(bool)
    # 将 sweep 信号 forward-fill 2 根 K 线
    df['sellside_sweep'] = df['sellside_sweep'].rolling(3, min_periods=1).max().astype(bool)
    df['buyside_sweep'] = df['buyside_sweep'].rolling(3, min_periods=1).max().astype(bool)
    df['sellside_liquidity_taken'] = df['sellside_sweep']
    df['buyside_liquidity_taken'] = df['buyside_sweep']
    # liquidity_sweep （双向通用）
    df['liquidity_sweep'] = df['sellside_sweep'] | df['buyside_sweep']
    df['liquidity_sweep_confirmed'] = df['liquidity_sweep']
    df['liquidity_sweep_long'] = df['sellside_liquidity_taken']
    df['liquidity_sweep_short'] = df['buyside_liquidity_taken']
    df['bullish_stop_hunt'] = df['sellside_sweep']
    df['bearish_stop_hunt'] = df['buyside_sweep']
    
    return df


def _add_vix_fix_fe_signals(out: pd.DataFrame) -> pd.DataFrame:
    """提取自 CM Williams Vix Fix V3 的 FE 止跌/止涨逻辑"""
    pd_len, bbl, mult_vix, lb, ph = 22, 20, 2.0, 50, 0.85
    ltLB, mtLB, str_len = 40, 14, 3

    close = out["close"]
    low = out["low"]
    high = out["high"]

    # --- FE Bottom (Bullish) ---
    hc = close.rolling(pd_len, min_periods=1).max()
    wvf = ((hc - low) / hc) * 100
    midLine = wvf.rolling(bbl, min_periods=1).mean()
    sDev = mult_vix * wvf.rolling(bbl, min_periods=1).std(ddof=0)
    upperBand = midLine + sDev
    rangeHigh = wvf.rolling(lb, min_periods=1).max() * ph

    filtered = ((wvf.shift(1) >= upperBand.shift(1)) | (wvf.shift(1) >= rangeHigh.shift(1))) & (wvf < upperBand) & (wvf < rangeHigh)
    upRange = (low > low.shift(1)) & (close > high.shift(1))
    
    cond_FE_bull = upRange & (close > close.shift(str_len)) & ((close < close.shift(ltLB)) | (close < close.shift(mtLB))) & filtered
    out["fe_bottom"] = cond_FE_bull

    # --- FE Top (Bearish - Inverse Logic) ---
    lc = close.rolling(pd_len, min_periods=1).min()
    wvf_inv = ((high - lc) / lc.replace(0, np.nan)) * 100
    midLine_inv = wvf_inv.rolling(bbl, min_periods=1).mean()
    sDev_inv = mult_vix * wvf_inv.rolling(bbl, min_periods=1).std(ddof=0)
    upperBand_inv = midLine_inv + sDev_inv
    rangeHigh_inv = wvf_inv.rolling(lb, min_periods=1).max() * ph

    filtered_inv = ((wvf_inv.shift(1) >= upperBand_inv.shift(1)) | (wvf_inv.shift(1) >= rangeHigh_inv.shift(1))) & (wvf_inv < upperBand_inv) & (wvf_inv < rangeHigh_inv)
    downRange = (high < high.shift(1)) & (close < low.shift(1))
    
    cond_FE_bear = downRange & (close < close.shift(str_len)) & ((close > close.shift(ltLB)) | (close > close.shift(mtLB))) & filtered_inv
    out["fe_top"] = cond_FE_bear

    # strong_reject (wick rejection)
    out['strong_reject_long'] = out['fe_bottom']
    out['strong_reject_short'] = out['fe_top']
    # effort_no_result
    out['effort_no_result_long'] = False
    out['effort_no_result_short'] = False

    return out


def _add_sqzmom_divergence_features(out: pd.DataFrame) -> pd.DataFrame:
    """SQZMOM 动量背离 + K线White Bar反转信号（移植自旧版 runner）"""
    close = out["close"].astype(float)
    high = out["high"].astype(float)
    low = out["low"].astype(float)
    mom = out["momentum"].astype(float).fillna(0.0)
    
    atr_val = out["ATRr_14"].replace(0, np.nan).bfill().fillna(close * 0.006)

    piv_l = _local_pivot_low(mom, 2, 1)
    piv_h = _local_pivot_high(mom, 2, 1)

    bull_div = pd.Series(False, index=out.index)
    bear_div = pd.Series(False, index=out.index)
    div_age = pd.Series(999, index=out.index, dtype="int64")
    div_dir = pd.Series("None", index=out.index, dtype="object")
    div_strength = pd.Series(0.0, index=out.index)

    low_pivots = []
    high_pivots = []
    last_div_i = None

    for i in range(len(out)):
        if piv_l.iloc[i]:
            low_pivots.append(i)
            if len(low_pivots) >= 2:
                a, b = low_pivots[-2], low_pivots[-1]
                if mom.iloc[b] < 0:
                    price_lower_low = low.iloc[b] < low.iloc[a] - 0.03 * atr_val.iloc[b]
                    mom_higher_low = mom.iloc[b] > mom.iloc[a]
                    if price_lower_low and mom_higher_low:
                        bull_div.iloc[i] = True
                        div_dir.iloc[i] = "Long"
                        last_div_i = i
                        div_strength.iloc[i] = min(12.0, abs((mom.iloc[b] - mom.iloc[a]) / max(abs(mom.iloc[a]), 1e-9)) * 8.0 + 4.0)

        if piv_h.iloc[i]:
            high_pivots.append(i)
            if len(high_pivots) >= 2:
                a, b = high_pivots[-2], high_pivots[-1]
                if mom.iloc[b] > 0:
                    price_higher_high = high.iloc[b] > high.iloc[a] + 0.03 * atr_val.iloc[b]
                    mom_lower_high = mom.iloc[b] < mom.iloc[a]
                    if price_higher_high and mom_lower_high:
                        bear_div.iloc[i] = True
                        div_dir.iloc[i] = "Short"
                        last_div_i = i
                        div_strength.iloc[i] = min(12.0, abs((mom.iloc[a] - mom.iloc[b]) / max(abs(mom.iloc[a]), 1e-9)) * 8.0 + 4.0)

        if last_div_i is not None:
            div_age.iloc[i] = i - last_div_i
            if div_dir.iloc[i] == "None" and div_age.iloc[i] <= 20:
                div_dir.iloc[i] = div_dir.iloc[last_div_i]
                div_strength.iloc[i] = max(0.0, div_strength.iloc[last_div_i] - 0.5 * div_age.iloc[i])

    # Shift forward 1 bar（防 repaint）
    bull_div_confirmed = bull_div.shift(1).astype("boolean").fillna(False).astype(bool)
    bear_div_confirmed = bear_div.shift(1).astype("boolean").fillna(False).astype(bool)
    div_dir_confirmed = div_dir.shift(1).fillna("None")
    div_age_confirmed = div_age.shift(1).fillna(999).astype("int64")
    div_strength_confirmed = div_strength.shift(1).fillna(0.0)

    out["bullish_divergence"] = out.get("bullish_divergence", False) | bull_div_confirmed
    out["bearish_divergence"] = out.get("bearish_divergence", False) | bear_div_confirmed
    out["sqzmom_divergence_dir"] = div_dir_confirmed
    out["sqzmom_divergence_age"] = div_age_confirmed
    out["sqzmom_divergence_strength"] = div_strength_confirmed.round(4)

    # ===== K线 White Bar（SQZMOM白柱衰竭反转） =====
    sc2 = mom < 0
    sc3 = mom >= mom.shift(1)
    sqzmom_white_bull = sc2 & sc3

    sc1 = mom >= 0
    sc4 = mom < mom.shift(1)
    sqzmom_white_bear = sc1 & sc4

    body = (close - out["open"].astype(float))
    body_pct = out.get("body_pct", pd.Series(0.0, index=out.index)).fillna(0.0)

    strong_reject_long = out.get("strong_reject_long", pd.Series(False, index=out.index))
    strong_reject_short = out.get("strong_reject_short", pd.Series(False, index=out.index))
    enr_long = out.get("effort_no_result_long", pd.Series(False, index=out.index))
    enr_short = out.get("effort_no_result_short", pd.Series(False, index=out.index))
    fe_bull = out.get("fe_bottom", pd.Series(False, index=out.index))
    fe_bear = out.get("fe_top", pd.Series(False, index=out.index))

    early_long_triggers = strong_reject_long | enr_long | fe_bull
    early_short_triggers = strong_reject_short | enr_short | fe_bear

    long_confirm = sqzmom_white_bull | ((body > 0) & (body_pct >= 0.28)) | early_long_triggers
    short_confirm = sqzmom_white_bear | ((body < 0) & (body_pct >= 0.28)) | early_short_triggers

    out["sqzmom_reversal_confirm_long"] = long_confirm
    out["sqzmom_reversal_confirm_short"] = short_confirm
    out["bull_div_count_12"] = out["bullish_divergence"].rolling(12, min_periods=1).sum()
    out["bear_div_count_12"] = out["bearish_divergence"].rolling(12, min_periods=1).sum()

    return out
