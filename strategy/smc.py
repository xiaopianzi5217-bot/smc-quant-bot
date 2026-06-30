# -*- coding: utf-8 -*-
from indicators.pivots import find_pivots, dynamic_pivot_threshold
from strategy.regime import detect_market_regime
from config import PIVOT_PARAMS, STRATEGY_PARAMS
import pandas as pd
import numpy as np

def _calc_vp(df, lookback=100):
    if df is None or len(df) < 10: return 0.0, 0.0, 0.0
    window = df.tail(lookback)
    min_p, max_p = float(window['low'].min()), float(window['high'].max())
    if min_p == max_p or min_p != min_p: return min_p, min_p, max_p
    bins = 20; bin_sz = (max_p - min_p) / bins
    profiles = {i: 0.0 for i in range(bins)}
    for i in range(len(window)):
        row = window.iloc[i]
        t = (float(row['high']) + float(row['low']) + float(row['close'])) / 3.0
        v = float(row.get('volume', 1.0))
        idx = min(bins - 1, int((t - min_p) / bin_sz))
        profiles[idx] += v
    poc_idx = max(profiles, key=profiles.get)
    poc = min_p + poc_idx * bin_sz + bin_sz / 2.0
    tot_v = sum(profiles.values()); tar_v = tot_v * 0.7; cur_v = profiles[poc_idx]
    u, d = poc_idx + 1, poc_idx - 1
    while cur_v < tar_v and (u < bins or d >= 0):
        vu = profiles[u] if u < bins else -1; vd = profiles[d] if d >= 0 else -1
        if vu > vd: cur_v += vu; u += 1
        else: cur_v += vd; d -= 1
    val = min_p + max(0, d + 1) * bin_sz
    vah = min_p + min(bins, u) * bin_sz
    return poc, vah, val

def _calc_trade_channel(df, lookback=20):
    if df is None or len(df) < lookback: return 0.0, 0.0, 0.0, 0.5
    window = df.tail(lookback)
    chan_up = float(window['high'].max())
    chan_lw = float(window['low'].min())
    chan_mid = (chan_up + chan_lw) / 2.0
    close = float(df['close'].iloc[-1])
    width = max(chan_up - chan_lw, 1e-12)
    chan_pos = max(0.0, min(1.0, (close - chan_lw) / width))
    return chan_up, chan_lw, chan_mid, chan_pos

def _check_kline_pattern(df, direction):
    if df is None or len(df) < 2: return False
    curr, prev = df.iloc[-1], df.iloc[-2]
    c_o, c_c, c_h, c_l = float(curr['open']), float(curr['close']), float(curr['high']), float(curr['low'])
    p_o, p_c = float(prev['open']), float(prev['close'])
    body = abs(c_c - c_o)
    u_wick = c_h - max(c_c, c_o)
    l_wick = min(c_c, c_o) - c_l

    is_bull_engulf = (c_c > p_o and c_o < p_c and p_c < p_o) if direction == "Long" else False
    is_bull_pin = (l_wick > 1.5 * body and c_c > c_o) if direction == "Long" else False
    is_bear_engulf = (c_c < p_o and c_o > p_c and p_c > p_o) if direction == "Short" else False
    is_bear_pin = (u_wick > 1.5 * body and c_c < c_o) if direction == "Short" else False
    
    return (is_bull_engulf or is_bull_pin) if direction == "Long" else (is_bear_engulf or is_bear_pin)

def _last_unswept_high(df, pivots, target_idx, eval_idx):
    for p in reversed(pivots):
        if p >= target_idx: continue
        lvl = df['high'].iloc[p]
        if not any(df['high'].iloc[p + 1:eval_idx] > lvl): return p, lvl
    return None, None

def _last_unswept_low(df, pivots, target_idx, eval_idx):
    for p in reversed(pivots):
        if p >= target_idx: continue
        lvl = df['low'].iloc[p]
        if not any(df['low'].iloc[p + 1:eval_idx] < lvl): return p, lvl
    return None, None

def find_fvg_targets(df, target_idx, curr_close, lookback=80):
    bearish_fvg, bullish_fvg = None, None
    for k in range(target_idx, max(2, target_idx - lookback), -1):
        gap_bottom, gap_top = df['high'].iloc[k], df['low'].iloc[k - 2]
        if gap_bottom < gap_top and not any(df['high'].iloc[k + 1:target_idx + 1] >= gap_bottom) and gap_bottom > curr_close:
            bearish_fvg = gap_bottom; break
    for k in range(target_idx, max(2, target_idx - lookback), -1):
        gap_top, gap_bottom = df['low'].iloc[k], df['high'].iloc[k - 2]
        if gap_top > gap_bottom and not any(df['low'].iloc[k + 1:target_idx + 1] <= gap_top) and gap_top < curr_close:
            bullish_fvg = gap_top; break
    return bearish_fvg, bullish_fvg

def find_ob_targets(df, liq_hp, liq_lp, target_idx, curr_close):
    bearish_ob, bullish_ob = None, None
    for p in reversed(liq_lp):
        if p >= target_idx - 2: continue
        lvl = df['low'].iloc[p]
        break_idx = next((m for m in range(p + 1, target_idx + 1) if df['close'].iloc[m] < lvl), None)
        if break_idx:
            for j in range(break_idx - 1, max(0, break_idx - 20), -1):
                if df['close'].iloc[j] > df['open'].iloc[j]:
                    ob_top, ob_bot = df['high'].iloc[j], df['low'].iloc[j]
                    if not any(df['high'].iloc[break_idx + 1:target_idx + 1] >= ob_top) and ob_bot > curr_close: bearish_ob = (ob_top, ob_bot)
                    break
        if bearish_ob: break
    for p in reversed(liq_hp):
        if p >= target_idx - 2: continue
        lvl = df['high'].iloc[p]
        break_idx = next((m for m in range(p + 1, target_idx + 1) if df['close'].iloc[m] > lvl), None)
        if break_idx:
            for j in range(break_idx - 1, max(0, break_idx - 20), -1):
                if df['close'].iloc[j] < df['open'].iloc[j]:
                    ob_top, ob_bot = df['high'].iloc[j], df['low'].iloc[j]
                    if not any(df['low'].iloc[break_idx + 1:target_idx + 1] <= ob_bot) and ob_top < curr_close: bullish_ob = (ob_top, ob_bot)
                    break
        if bullish_ob: break
    return bearish_ob, bullish_ob



def _safe_float(v, default=0.0):
    try:
        if v is None:
            return default
        x = float(v)
        return x if x == x else default
    except Exception:
        return default


def _dist_atr(price, level, atr):
    price = _safe_float(price, 0.0)
    level = _safe_float(level, 0.0)
    atr = max(_safe_float(atr, 0.0), 1e-12)
    if price <= 0 or level <= 0:
        return 999.0
    return abs(price - level) / atr


def _range_mid(rng):
    if not rng:
        return None
    try:
        return (float(rng[0]) + float(rng[1])) / 2.0
    except Exception:
        return None


def _score_quality(direction, *, close_val, atr_val, chan_pos, is_ssl_swept, is_bsl_swept,
                   bullish_ob, bearish_ob, bullish_fvg, bearish_fvg,
                   kline_long_ok, kline_short_ok, has_bot_div, has_top_div,
                   sqzmom_white_reversal_long, sqzmom_white_reversal_short,
                   volume_ratio, adx_val):
    """
    质量分：用于提高 PF 的软过滤字段。
    不直接砍掉信号，而是把高胜率特征写进 exec_ctx，给 decision/scoring/risk 层使用。
    """
    score = 50.0
    reasons = []
    if direction == "Long":
        if is_ssl_swept:
            score += 14; reasons.append("SSL sweep")
        if bullish_ob is not None:
            score += 10; reasons.append("bullish OB")
        if bullish_fvg is not None:
            score += 7; reasons.append("bullish FVG")
        if chan_pos <= 0.40:
            score += 8; reasons.append("discount/channel low")
        if kline_long_ok:
            score += 6; reasons.append("bullish candle")
        if has_bot_div or sqzmom_white_reversal_long:
            score += 7; reasons.append("momentum reversal")
        ob_mid = _range_mid(bullish_ob)
        near_ob = _dist_atr(close_val, ob_mid, atr_val) <= 0.65 if ob_mid else False
        if near_ob:
            score += 6; reasons.append("near bullish OB")
        if is_bsl_swept and not is_ssl_swept:
            score -= 8; reasons.append("opposite sweep risk")
        if chan_pos >= 0.82:
            score -= 7; reasons.append("chasing premium")
    else:
        if is_bsl_swept:
            score += 14; reasons.append("BSL sweep")
        if bearish_ob is not None:
            score += 10; reasons.append("bearish OB")
        if bearish_fvg is not None:
            score += 7; reasons.append("bearish FVG")
        if chan_pos >= 0.60:
            score += 8; reasons.append("premium/channel high")
        if kline_short_ok:
            score += 6; reasons.append("bearish candle")
        if has_top_div or sqzmom_white_reversal_short:
            score += 7; reasons.append("momentum reversal")
        ob_mid = _range_mid(bearish_ob)
        near_ob = _dist_atr(close_val, ob_mid, atr_val) <= 0.65 if ob_mid else False
        if near_ob:
            score += 6; reasons.append("near bearish OB")
        if is_ssl_swept and not is_bsl_swept:
            score -= 8; reasons.append("opposite sweep risk")
        if chan_pos <= 0.18:
            score -= 7; reasons.append("chasing discount")

    # 低流动性不强砍，但降低仓位/质量；强ADX略加分，避免震荡乱开。
    if volume_ratio and volume_ratio < 0.65:
        score -= 4; reasons.append("low volume")
    if adx_val >= 18:
        score += 3; reasons.append("trend strength")
    return max(0.0, min(100.0, round(score, 2))), reasons

def get_color_state(xtl_val):
    if xtl_val > 37: return "藍色 🔵 (看漲)"
    if xtl_val < -37: return "紅色 🔴 (看跌)"
    return "白色 ⚪ (衰竭)"

def get_sqzmom_color(sz, prev_sz=None):
    try:
        z = float(sz)
        p = float(prev_sz) if prev_sz is not None else z
    except Exception:
        return "unknown"
    eps = max(abs(p) * 0.15, 1e-9)
    if abs(z) <= eps: return "white"
    if z > 0: return "lime" if z >= p else "green"
    return "red" if z <= p else "maroon"

def get_price_extreme(series, idx, is_max=True):
    window = series.iloc[max(0, idx - 1):min(len(series), idx + 2)]
    return window.max() if is_max else window.min()

def build_macro_context(df_1h):
    """构建1H级别宏观上下文，包含SQZMOM背离检测、顶部/底部/结构判断。"""
    p = PIVOT_PARAMS['macro']
    liq_hp = find_pivots(df_1h['high'], p['left'], p['right'], True, df_1h['ATRr_14'], p['atr_threshold'], p['min_spacing'])
    liq_lp = find_pivots(df_1h['low'], p['left'], p['right'], False, df_1h['ATRr_14'], p['atr_threshold'], p['min_spacing'])
    bsl = df_1h['high'].iloc[liq_hp[-1]] if liq_hp else df_1h['high'].max()
    ssl = df_1h['low'].iloc[liq_lp[-1]] if liq_lp else df_1h['low'].min()
    curr_price = df_1h['close'].iloc[-1]; curr = df_1h.iloc[-1]
    bearish_ob, bullish_ob = find_ob_targets(df_1h, liq_hp, liq_lp, len(df_1h) - 1, curr_price)

    # 【修复20260701】收紧顶部/底部判断条件，防止错误方向
    # 原条件过于宽松（价格靠近 bsl 0.1% 就算顶部），导致大量误判
    # 现在需要 bsl/ssl + RSI 极值 + 结构至少两个条件同时成立
    is_at_bsl = curr_price >= bsl * 0.998
    is_at_ssl = curr_price <= ssl * 1.002
    is_top_rsi = curr.get('rsi', 50) > STRATEGY_PARAMS['rsi_ob']
    is_bot_rsi = curr.get('rsi', 50) < STRATEGY_PARAMS['rsi_os']
    has_bear_ob = bearish_ob and curr_price >= bearish_ob[1]
    has_bull_ob = bullish_ob and curr_price <= bullish_ob[0]
    adx = float(curr.get('ADX_14', curr.get('adx', 0) or 0))
    has_trend = adx >= 18  # 【修复20260701】趋势强度门槛
    
    # 增强方向判断：至少满足2个条件才认定为顶部/底部
    top_conditions = [is_at_bsl, is_top_rsi, bool(curr.get('is_Inv_FE', False)), has_bear_ob]
    bot_conditions = [is_at_ssl, is_bot_rsi, bool(curr.get('is_FE', False)), has_bull_ob]
    is_top = sum(1 for c in top_conditions if c) >= 2
    is_bot = sum(1 for c in bot_conditions if c) >= 2

    bearish_struct = (df_1h['high'].iloc[liq_hp[-1]] < df_1h['high'].iloc[liq_hp[-2]] and df_1h['low'].iloc[liq_lp[-1]] < df_1h['low'].iloc[liq_lp[-2]]) if len(liq_hp) >= 2 and len(liq_lp) >= 2 else False
    bullish_struct = (df_1h['high'].iloc[liq_hp[-1]] > df_1h['high'].iloc[liq_hp[-2]] and df_1h['low'].iloc[liq_lp[-1]] > df_1h['low'].iloc[liq_lp[-2]]) if len(liq_hp) >= 2 and len(liq_lp) >= 2 else False

    eq = (bsl + ssl) / 2.0

    # ================================================================
    # 【修复20260714】1H SQZMOM 背离检测（用于反向挡停 + 多周期共振加分）
    # ================================================================
    # 检测1H级别的SQZMOM顶背离和底背离
    htf_has_top_div = False      # 1H 顶背离：看跌信号
    htf_has_bot_div = False      # 1H 底背离：看涨信号
    htf_div_dir = "None"         # 背离方向
    htf_div_age = 999            # 背离年龄（1H K线根数）
    htf_div_strength = 0.0       # 背离强度
    htf_div_high_price = 0.0     # 顶背离形成时的最高价（用于判断背离是否失效）
    htf_div_low_price = 0.0      # 底背离形成时的最低价（用于判断背离是否失效）
    
    try:
        # 使用1H的sz（SQZMOM值）和价格数据检测背离
        sz_1h = df_1h['sz'].astype(float)
        high_1h = df_1h['high'].astype(float)
        low_1h = df_1h['low'].astype(float)
        close_1h = df_1h['close'].astype(float)
        atr_1h = df_1h['ATRr_14'].astype(float).replace(0, np.nan).bfill().fillna(close_1h * 0.006)
        
        # 找1H的动量高低点
        mom = PIVOT_PARAMS['momentum']
        mom_hp_1h = find_pivots(df_1h['sz'], mom['left'], mom['right'], True, None, 0.0, mom['min_spacing'])
        mom_lp_1h = find_pivots(df_1h['sz'], mom['left'], mom['right'], False, None, 0.0, mom['min_spacing'])
        
        # 顶背离检测：价格更高高点(HH) + 动量更低高点(LH)
        htf_div_high_idx = -1
        if len(mom_hp_1h) >= 2:
            a_idx, b_idx = mom_hp_1h[-2], mom_hp_1h[-1]
            if sz_1h.iloc[b_idx] > 0:  # 动量在零轴上方
                price_higher = high_1h.iloc[b_idx] > high_1h.iloc[a_idx] + 0.03 * atr_1h.iloc[b_idx]
                momentum_lower = sz_1h.iloc[b_idx] < sz_1h.iloc[a_idx]
                if price_higher and momentum_lower:
                    htf_has_top_div = True
                    htf_div_dir = "Short"
                    htf_div_high_price = float(high_1h.iloc[b_idx])
                    htf_div_high_idx = b_idx
                    htf_div_strength = min(12.0, abs((sz_1h.iloc[a_idx] - sz_1h.iloc[b_idx]) / max(abs(sz_1h.iloc[a_idx]), 1e-9)) * 8.0 + 4.0)
        
        # 底背离检测：价格更低低点(LL) + 动量更高低点(HL)
        htf_div_low_idx = -1
        if len(mom_lp_1h) >= 2:
            a_idx, b_idx = mom_lp_1h[-2], mom_lp_1h[-1]
            if sz_1h.iloc[b_idx] < 0:  # 动量在零轴下方
                price_lower = low_1h.iloc[b_idx] < low_1h.iloc[a_idx] - 0.03 * atr_1h.iloc[b_idx]
                momentum_higher = sz_1h.iloc[b_idx] > sz_1h.iloc[a_idx]
                if price_lower and momentum_higher:
                    htf_has_bot_div = True
                    htf_div_dir = "Long"
                    htf_div_low_price = float(low_1h.iloc[b_idx])
                    htf_div_low_idx = b_idx
                    htf_div_strength = min(12.0, abs((sz_1h.iloc[b_idx] - sz_1h.iloc[a_idx]) / max(abs(sz_1h.iloc[a_idx]), 1e-9)) * 8.0 + 4.0)
        
        # 计算背离年龄：从最近一次背离确认点到现在的1H K线根数
        last_top_idx = htf_div_high_idx if htf_has_top_div else -999
        last_bot_idx = htf_div_low_idx if htf_has_bot_div else -999
        last_div_idx = max(last_top_idx, last_bot_idx)
        if last_div_idx > 0:
            htf_div_age = len(df_1h) - 1 - last_div_idx
        
        # 【背离结束判定】：
        # 条件A：价格突破背离极点（顶背离被价格突破高点 = 空头失效）
        # 条件B：年龄超期（>24根1H K线 ≈ 1天，时间窗口关闭）
        div_expired = False
        if htf_has_top_div and curr_price > htf_div_high_price * 1.001:
            div_expired = True  # 顶背离失效：价格涨破顶背离最高点
        if htf_has_bot_div and curr_price < htf_div_low_price * 0.999:
            div_expired = True  # 底背离失效：价格跌破底背离最低点
        if htf_div_age > 24:
            div_expired = True  # 年龄超期
        
        if div_expired:
            htf_has_top_div = False
            htf_has_bot_div = False
            htf_div_dir = "None"
            htf_div_age = 999
            htf_div_strength = 0.0
    except Exception as exc:
        # 背离检测失败不阻塞主流程
        pass

    # 构建基础返回字典，加入1H背离字段
    base_ctx = {
        'allowed_direction': "Both",
        'macro_trend': "1H neutral",
        'bsl_1h': bsl, 'ssl_1h': ssl,
        'liq_hp_1h': liq_hp, 'liq_lp_1h': liq_lp,
        'htf_has_top_div': htf_has_top_div,
        'htf_has_bot_div': htf_has_bot_div,
        'htf_div_dir': htf_div_dir,
        'htf_div_age': htf_div_age,
        'htf_div_strength': htf_div_strength,
        'htf_div_high_price': htf_div_high_price,
        'htf_div_low_price': htf_div_low_price,
    }

    # 【修复20260701】一票否决改双向投票：
    # - 趋势方向信号强势(顶部/底部/结构) + 有趋势(ADX>=18) -> 强制方向
    # - 趋势方向信号但无趋势(ADX<18) -> 只建议，让 15M 评分自主
    # - 中线偏离信号也在有趋势时才强制方向，否则返回Both
    if is_top and has_trend:
        base_ctx.update({'allowed_direction': "Short", 'macro_trend': "1H TOP with TREND"})
        return base_ctx
    if is_bot and has_trend:
        base_ctx.update({'allowed_direction': "Long", 'macro_trend': "1H BOT with TREND"})
        return base_ctx
    if bearish_struct and has_trend:
        base_ctx.update({'allowed_direction': "Short", 'macro_trend': "1H BEAR STRUCT with TREND"})
        return base_ctx
    if bullish_struct and has_trend:
        base_ctx.update({'allowed_direction': "Long", 'macro_trend': "1H BULL STRUCT with TREND"})
        return base_ctx

    # 无趋势时的方向信号 -> 只建议不强制
    if is_top and not has_trend:
        base_ctx.update({'allowed_direction': "Both", 'macro_trend': "1H TOP weak trend", 'htf_suggest': "Short"})
        return base_ctx
    if is_bot and not has_trend:
        base_ctx.update({'allowed_direction': "Both", 'macro_trend': "1H BOT weak trend", 'htf_suggest': "Long"})
        return base_ctx
    if bearish_struct and not has_trend:
        base_ctx.update({'allowed_direction': "Both", 'macro_trend': "1H BEAR STRUCT weak trend", 'htf_suggest': "Short"})
        return base_ctx
    if bullish_struct and not has_trend:
        base_ctx.update({'allowed_direction': "Both", 'macro_trend': "1H BULL STRUCT weak trend", 'htf_suggest': "Long"})
        return base_ctx

    # 中线偏离判定也加入趋势门槛
    chan_width = bsl - ssl
    if chan_width > 0:
        dist_from_mid = abs(curr_price - eq) / (chan_width / 2)
    else:
        dist_from_mid = 0.0
    
    if dist_from_mid >= 0.25 and curr_price > eq and has_trend:
        base_ctx.update({'allowed_direction': "Short", 'macro_trend': "1H premium zone with trend"})
        return base_ctx
    if dist_from_mid >= 0.25 and curr_price < eq and has_trend:
        base_ctx.update({'allowed_direction': "Long", 'macro_trend': "1H discount zone with trend"})
        return base_ctx
    return base_ctx

def calc_latest_pivot_strength(df, pivot_idx, is_high=True):
    if pivot_idx is None or pivot_idx <= 0 or pivot_idx >= len(df): return 0.0
    atr = df['ATRr_14'].iloc[pivot_idx]
    if atr != atr or atr <= 0: return 0.0
    left = max(0, pivot_idx - 2); right = min(len(df), pivot_idx + 3)
    window = df['high'].iloc[left:right] if is_high else df['low'].iloc[left:right]
    curr = df['high'].iloc[pivot_idx] if is_high else df['low'].iloc[pivot_idx]
    others = window.drop(window.index[min(pivot_idx-left, len(window)-1)], errors='ignore')
    raw = (curr - others.max()) if is_high else (others.min() - curr)
    return round(float(raw / atr), 4) if len(others) and raw == raw else 0.0

def _check_div(mom_idx_list, df, is_top=True):
    if len(mom_idx_list) < 2: return False, False, False, 0.0
    idx, idx1 = mom_idx_list[-1], mom_idx_list[-2]
    sz_ok = (df['sz'].iloc[idx] > 0) if is_top else (df['sz'].iloc[idx] < 0)
    if not (1 <= (idx - idx1) <= 60 and sz_ok): return False, False, False, 0.0
    
    cond_osc = (df['sz'].iloc[idx] < df['sz'].iloc[idx1]) if is_top else (df['sz'].iloc[idx] > df['sz'].iloc[idx1])
    cond_prc = (get_price_extreme(df['close'], idx, True) > get_price_extreme(df['close'], idx1, True)) if is_top else (get_price_extreme(df['close'], idx, False) < get_price_extreme(df['close'], idx1, False))
    cond_vol = df['volume'].iloc[idx] < df['volume'].iloc[idx1] if 'volume' in df.columns else False
    
    target_dist = len(df) - 1 - idx
    has_div = cond_osc and cond_prc and (target_dist <= 6)
    just_div = cond_osc and cond_prc and (target_dist == 1)
    
    rsi_s = df.get('rsi')
    rsi_diff = abs(float(rsi_s.iloc[idx]) - float(rsi_s.iloc[idx1])) if (has_div and rsi_s is not None) else 0.0
    return has_div, just_div, (has_div and cond_vol), rsi_diff

def build_exec_context(df):
    target_idx = len(df) - 1; curr = df.iloc[-1]; prev = df.iloc[-2]
    regime_info = detect_market_regime(df)
    poc, vah, val = _calc_vp(df, 100)
    
    chan_up, chan_lw, chan_mid, chan_pos = _calc_trade_channel(df, 20)
    kline_long_ok = _check_kline_pattern(df, "Long")
    kline_short_ok = _check_kline_pattern(df, "Short")
    
    try: utc_hour = pd.to_datetime(df['datetime'].iloc[-1]).hour if 'datetime' in df.columns else 12
    except: utc_hour = 12

    p = PIVOT_PARAMS['exec']
    atr_threshold = dynamic_pivot_threshold(regime_info, p['atr_threshold_low'], p['atr_threshold_normal'], p['atr_threshold_high'])
    liq_hp = find_pivots(df['high'], p['left'], p['right'], True, df['ATRr_14'], atr_threshold, p['min_spacing'])
    liq_lp = find_pivots(df['low'], p['left'], p['right'], False, df['ATRr_14'], atr_threshold, p['min_spacing'])

    eval_idx = max(0, target_idx - 8)
    bsl_idx, bsl_level = _last_unswept_high(df, liq_hp, target_idx, eval_idx)
    ssl_idx, ssl_level = _last_unswept_low(df, liq_lp, target_idx, eval_idx)
    is_bsl_swept = any(df['high'].iloc[max(bsl_idx + 1, eval_idx):target_idx + 1] >= bsl_level) if bsl_level else False
    is_ssl_swept = any(df['low'].iloc[max(ssl_idx + 1, eval_idx):target_idx + 1] <= ssl_level) if ssl_level else False

    bearish_fvg, bullish_fvg = find_fvg_targets(df, target_idx, curr['close'])
    bearish_ob, bullish_ob = find_ob_targets(df, liq_hp, liq_lp, target_idx, curr['close'])

    mom = PIVOT_PARAMS['momentum']
    mom_hp = find_pivots(df['sz'], mom['left'], mom['right'], True, None, 0.0, mom['min_spacing'])
    mom_lp = find_pivots(df['sz'], mom['left'], mom['right'], False, None, 0.0, mom['min_spacing'])

    has_top_div, just_top, has_top_vol_div, top_div_str = _check_div(mom_hp, df, True)
    has_bot_div, just_bot, has_bot_vol_div, bot_div_str = _check_div(mom_lp, df, False)

    curr_color = get_color_state(curr['xtl_val'])
    curr_sqzmom_color = get_sqzmom_color(curr.get('sz'), prev.get('sz'))
    prev_sqzmom_color = get_sqzmom_color(prev.get('sz'), df.iloc[-3].get('sz') if len(df) >= 3 else prev.get('sz'))
    sqzmom_white_reversal_long = (curr_sqzmom_color == "white" and float(prev.get('sz', 0) or 0) < 0)
    sqzmom_white_reversal_short = (curr_sqzmom_color == "white" and float(prev.get('sz', 0) or 0) > 0)
    adx_val = float(regime_info.get('adx', 0.0) or 0.0)
    atr_val = float(curr.get('ATRr_14', 0.0) or 0.0)
    close_val = float(curr.get('close', 0.0) or 0.0)
    avg_volume_20 = float(df['volume'].rolling(20).mean().iloc[-1]) if 'volume' in df.columns else 0.0
    volume_ratio = (float(curr.get('volume', 0.0) or 0.0) / avg_volume_20) if avg_volume_20 > 0 else 0.0

    long_quality, long_quality_reasons = _score_quality(
        "Long", close_val=close_val, atr_val=atr_val, chan_pos=chan_pos,
        is_ssl_swept=is_ssl_swept, is_bsl_swept=is_bsl_swept,
        bullish_ob=bullish_ob, bearish_ob=bearish_ob,
        bullish_fvg=bullish_fvg, bearish_fvg=bearish_fvg,
        kline_long_ok=kline_long_ok, kline_short_ok=kline_short_ok,
        has_bot_div=has_bot_div, has_top_div=has_top_div,
        sqzmom_white_reversal_long=sqzmom_white_reversal_long,
        sqzmom_white_reversal_short=sqzmom_white_reversal_short,
        volume_ratio=volume_ratio, adx_val=adx_val,
    )
    short_quality, short_quality_reasons = _score_quality(
        "Short", close_val=close_val, atr_val=atr_val, chan_pos=chan_pos,
        is_ssl_swept=is_ssl_swept, is_bsl_swept=is_bsl_swept,
        bullish_ob=bullish_ob, bearish_ob=bearish_ob,
        bullish_fvg=bullish_fvg, bearish_fvg=bearish_fvg,
        kline_long_ok=kline_long_ok, kline_short_ok=kline_short_ok,
        has_bot_div=has_bot_div, has_top_div=has_top_div,
        sqzmom_white_reversal_long=sqzmom_white_reversal_long,
        sqzmom_white_reversal_short=sqzmom_white_reversal_short,
        volume_ratio=volume_ratio, adx_val=adx_val,
    )

    bullish_ob_mid = _range_mid(bullish_ob)
    bearish_ob_mid = _range_mid(bearish_ob)
    near_bullish_ob = _dist_atr(close_val, bullish_ob_mid, atr_val) <= 0.65 if bullish_ob_mid else False
    near_bearish_ob = _dist_atr(close_val, bearish_ob_mid, atr_val) <= 0.65 if bearish_ob_mid else False
    near_bullish_fvg = _dist_atr(close_val, bullish_fvg, atr_val) <= 0.75 if bullish_fvg else False
    near_bearish_fvg = _dist_atr(close_val, bearish_fvg, atr_val) <= 0.75 if bearish_fvg else False

    # 提取波段高低點 (Swing High / Low) 給 OTE 演算法
    sh_val = float(df['high'].iloc[liq_hp[-1]]) if liq_hp else float(df['high'].tail(50).max())
    sl_val = float(df['low'].iloc[liq_lp[-1]]) if liq_lp else float(df['low'].tail(50).min())
    if sh_val <= sl_val:
        sh_val = float(df['high'].tail(50).max())
        sl_val = float(df['low'].tail(50).min())

    # 计算 DMI 方向（用于 grade_entry_quality 评分）
    plus_di = float(curr.get('DMP_14', 0.0) or 0.0)
    minus_di = float(curr.get('DMN_14', 0.0) or 0.0)
    dmi_bull = plus_di >= minus_di and adx_val >= 23.0
    dmi_bear = minus_di > plus_di and adx_val >= 23.0
    
    # 计算 momentum（用于 grade_entry_quality 评分）
    ema_20 = float(df['close'].ewm(span=20, adjust=False).mean().iloc[-1] or 0.0)
    momentum = close_val - ema_20 if ema_20 > 0 else 0.0
    
    # ===== 多空分差字段（供 smc_impulse_score 方向对齐奖励评分） =====
    smc_quality_score_bull = long_quality
    smc_quality_score_bear = short_quality
    sqzmom_bull_strength = (7.0 if momentum > 0 else 0.0) + \
                           (8.0 if sqzmom_white_reversal_long else 0.0) + \
                           (10.0 if has_bot_div else 0.0)
    sqzmom_bear_strength = (7.0 if momentum < 0 else 0.0) + \
                           (8.0 if sqzmom_white_reversal_short else 0.0) + \
                           (10.0 if has_top_div else 0.0)
    bullish_momentum = momentum if momentum > 0 else 0.0
    bearish_momentum = abs(momentum) if momentum < 0 else 0.0
    
    return {
        'swing_high': sh_val, 'swing_low': sl_val,
        'poc': poc, 'vah': vah, 'val': val, 'utc_hour': utc_hour,
        'chan_up': chan_up, 'chan_lw': chan_lw, 'chan_mid': chan_mid, 'chan_pos': chan_pos,
        'at_channel_low': bool(chan_pos <= 0.35), 'at_channel_high': bool(chan_pos >= 0.65),
        'kline_long_ok': kline_long_ok, 'kline_short_ok': kline_short_ok,
        'sqzmom_color': curr_sqzmom_color, 'prev_sqzmom_color': prev_sqzmom_color,
        'sqzmom_white_reversal_long': sqzmom_white_reversal_long,
        'sqzmom_white_reversal_short': sqzmom_white_reversal_short,
        'regime_info': regime_info, 'regime': regime_info.get('regime', 'unknown'),
        'squeeze': regime_info.get('squeeze', 'unknown'), 'volatility': regime_info.get('volatility', 'unknown'),
        'adx': adx_val, 'ADX_14': adx_val, 'atr': atr_val,
        'atr_pct': (atr_val / close_val) if close_val > 0 else 0.0,
        'atr_ratio': regime_info.get('atr_ratio', 1.0),
        'avg_volume_20': avg_volume_20,
        'volume_ratio': volume_ratio,
        'long_quality': long_quality, 'short_quality': short_quality,
        'long_quality_reasons': long_quality_reasons, 'short_quality_reasons': short_quality_reasons,
        'setup_quality': max(long_quality, short_quality),
        'preferred_direction_by_quality': 'Long' if long_quality >= short_quality else 'Short',
        'near_bullish_ob': near_bullish_ob, 'near_bearish_ob': near_bearish_ob,
        'near_bullish_fvg': near_bullish_fvg, 'near_bearish_fvg': near_bearish_fvg,
        'pf_guard_enabled': True,
        'pf_guard_note': 'Prefer setups with sweep + OB/FVG + channel edge; keeps trade count higher than hard filters while protecting PF.',
        'pivot_threshold': atr_threshold, 'liq_hp': liq_hp, 'liq_lp': liq_lp,
        'price_hp_idx': liq_hp[-1] if liq_hp else None, 'price_lp_idx': liq_lp[-1] if liq_lp else None,
        'bsl_idx': bsl_idx, 'ssl_idx': ssl_idx, 'bsl_level': bsl_level, 'ssl_level': ssl_level,
        'is_bsl_swept': is_bsl_swept, 'is_ssl_swept': is_ssl_swept,
        'bearish_fvg': bearish_fvg, 'bullish_fvg': bullish_fvg, 'bearish_ob': bearish_ob, 'bullish_ob': bullish_ob,
        'bearish_ob_valid': bearish_ob is not None, 'bullish_ob_valid': bullish_ob is not None,
        'ob_valid': (bearish_ob is not None) or (bullish_ob is not None),
        'pivot_strength_high': calc_latest_pivot_strength(df, liq_hp[-1] if liq_hp else None, True),
        'pivot_strength_low': calc_latest_pivot_strength(df, liq_lp[-1] if liq_lp else None, False),
        'has_top_div': has_top_div, 'has_bot_div': has_bot_div,
        'has_top_vol_div': has_top_vol_div, 'has_bot_vol_div': has_bot_vol_div,
        'top_div_strength': top_div_str, 'bot_div_strength': bot_div_str,
        'just_confirmed_top': just_top, 'just_confirmed_bot': just_bot,
        'curr_color': curr_color, 'prev_color': get_color_state(prev['xtl_val']),
        'color_changed': curr_color != get_color_state(prev['xtl_val']),
        # 补充 grade_entry_quality 评分所需字段
        'momentum': momentum,
        'plus_di': plus_di,
        'minus_di': minus_di,
        'dmi_bull': dmi_bull,
        'dmi_bear': dmi_bear,
        'ema_20': ema_20,
        'close': close_val,
        'body_pct': float(curr.get('body_pct', 0.0) or 0.0),
        # 多空分差字段（供 smc_impulse_score 方向对齐奖励评分）
        'smc_quality_score_bull': smc_quality_score_bull,
        'smc_quality_score_bear': smc_quality_score_bear,
        'sqzmom_bull_strength': sqzmom_bull_strength,
        'sqzmom_bear_strength': sqzmom_bear_strength,
        'bullish_momentum': bullish_momentum,
        'bearish_momentum': bearish_momentum,
    }
