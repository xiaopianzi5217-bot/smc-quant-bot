# -*- coding: utf-8 -*-
from typing import Any, Dict, Optional, Tuple
import pandas as pd
from utils.safe import safe_float, safe_bool
from strategy.intelligence_engine import grade_from_expected_value

def _fast_no_setup_signal(self, row, regime, vol_state):
    return {
        "direction": None,
        "score_raw": 0.0, "score": 0.0, "confidence": 0.0,
        "expected_value": -1.0, "win_prob": 0.0, "estimated_rr": 0.0,
        "ev_grade": "D_NEG_EV", "size_multiplier": 0.0,
        "ev_reasons": "NO_FEATURE_SETUP_FAST",
        "entry_meta": {"has_any_setup": False, "datetime": row.get("datetime")},
    }

def _build_verdict(self, allow, reason, regime, vol_state, signal,
                   book="", size=0.0, long_sig=None, short_sig=None):
    sig = signal or {}
    entry = sig.get("entry_meta", {})
    close = safe_float(entry.get("close", 0.0))
    atr = max(safe_float(entry.get("ATRr_14", entry.get("atr", 0.0)), 0.0), 1e-12)

    ev_snapshot = {}
    for label, s in [("Long", long_sig), ("Short", short_sig)]:
        if s:
            ev_snapshot[label] = {
                "score": round(safe_float(s.get("score"), 0.0), 2),
                "score_raw": round(safe_float(s.get("score_raw"), 0.0), 2),
                "expected_value": round(safe_float(s.get("expected_value"), 0.0), 4),
                "ev_grade": str(s.get("ev_grade", "?")),
                "base_trigger_passed": bool(s.get("base_trigger_passed", False)),
            }

    l_info = ev_snapshot.get("Long", {})
    s_info = ev_snapshot.get("Short", {})
    long_score = l_info.get("score", 0.0)
    short_score = s_info.get("score", 0.0)
    long_ev = l_info.get("expected_value", 0.0)
    short_ev = s_info.get("expected_value", 0.0)
    score_diff = abs(long_score - short_score)

    if long_score > short_score and long_ev > short_ev:
        adv_detail = "偏多" if long_ev > 0 else "偏多但EV偏弱"
    elif short_score > long_score and short_ev > long_ev:
        adv_detail = "偏空" if short_ev > 0 else "偏空但EV偏弱"
    else:
        adv_detail = "观望等待"

    fvg_dir = str(entry.get("fvg_direction", "None"))
    fvg_mid = entry.get("fvg_mid", None)
    ob_dir = str(entry.get("ob_direction", "None"))
    ob_top = entry.get("ob_top", None)
    ob_bottom = entry.get("ob_bottom", None)

    imbalance_parts = []
    if fvg_dir != "None" and fvg_mid is not None:
        _fvg_mid = float(fvg_mid) if isinstance(fvg_mid, (int, float)) else 0.0
        label_fvg = "多头FVG" if fvg_dir == "Long" else "空头FVG"
        imbalance_parts.append(f"{label_fvg}: {_fvg_mid:.2f}")

    ob_str = "暂无"
    if ob_dir != "None" and ob_top is not None and ob_bottom is not None:
        _ob_top = float(ob_top) if isinstance(ob_top, (int, float)) else 0.0
        _ob_bot = float(ob_bottom) if isinstance(ob_bottom, (int, float)) else 0.0
        label_ob = "买方OB" if ob_dir == "Long" else "卖方OB"
        ob_str = f"{label_ob}: {_ob_bot:.2f}~{_ob_top:.2f}"
        imbalance_parts.append(ob_str)

    imbalance_str = "暂无" if not imbalance_parts else ", ".join(imbalance_parts)

    regime_cn = {"TREND": "趋势", "CHOP": "震荡", "TRANSITION": "过渡", "CRISIS_RISK_OFF": "危机模式", "?": "未知"}.get(regime, regime)
    vol_cn = {"HIGH_VOL": "高波动", "LOW_VOL": "低波动", "MID_VOL": "正常"}.get(vol_state, vol_state)
    sqz_mult = safe_float(entry.get("sqz_mult", 1.0))
    sqz_state = "压缩中" if sqz_mult < 1.0 else ("扩张中" if sqz_mult > 1.5 else "正常")
    volume_ratio = safe_float(entry.get("volume_ratio", 1.0))
    if volume_ratio < 0.5: vol_str = f"{volume_ratio:.2f}x (极度缩量)"
    elif volume_ratio < 0.8: vol_str = f"{volume_ratio:.2f}x (缩量)"
    elif volume_ratio > 2.0: vol_str = f"{volume_ratio:.2f}x (放量)"
    else: vol_str = f"{volume_ratio:.2f}x (正常)"

    mom = safe_float(entry.get("momentum", 0.0))
    rsi = safe_float(entry.get("rsi", 50.0))
    adx = safe_float(entry.get("adx", 0.0))
    macd = safe_float(entry.get("macd", safe_float(entry.get("MACD", 0.0))))
    atr_pct = atr / close * 100 if close > 0 else 0.0
    sqz_white = safe_bool(entry.get("sqzmom_white", False))

    kline_color = "白色 ⚪ (衰竭)" if sqz_white else ("绿色 🟢 (多头)" if mom > 0 else "红色 🔴 (空头)")
    rsi_status = "超买" if rsi > 70 else ("超卖" if rsi < 30 else ("偏强" if rsi > 55 else ("偏弱" if rsi < 45 else "中性")))
    adx_status = "强趋势" if adx >= 25 else ("弱趋势/震荡" if adx >= 15 else "极弱/无趋势")
    macd_status = "偏多" if macd > 0 else "偏空"

    bsl = safe_float(entry.get("last_swing_high", 0.0))
    ssl = safe_float(entry.get("last_swing_low", 0.0))
    bsl_swept = any(safe_bool(entry.get(k, False)) for k in ["buyside_sweep", "buyside_liquidity_taken", "bearish_stop_hunt"])
    ssl_swept = any(safe_bool(entry.get(k, False)) for k in ["sellside_sweep", "sellside_liquidity_taken", "bullish_stop_hunt"])

    liquidity_lines = []
    if bsl > 0:
        bsl_dist = (bsl - close) / close * 100
        liquidity_lines.append(f"BSL: {bsl:.2f}(距离{abs(bsl_dist):.2f}%) | 已扫: {'是' if bsl_swept else '否'}")
    if ssl > 0:
        ssl_dist = (close - ssl) / close * 100
        liquidity_lines.append(f"SSL: {ssl:.2f}(距离{abs(ssl_dist):.2f}%) | 已扫: {'是' if ssl_swept else '否'}")

    notional_dir = str(sig.get("direction", "?")).title()
    dir_emoji = "\U0001f4c8" if notional_dir == "Long" else "\U0001f4c9"
    dir_cn = "多头" if notional_dir == "Long" else "空头"
    entry_price = close
    direction_mult = 1.0 if notional_dir == "Long" else -1.0
    sl = entry_price - direction_mult * 1.5 * atr
    tp1 = entry_price + direction_mult * 1.5 * atr
    tp2 = entry_price + direction_mult * 3.0 * atr
    tp3 = entry_price + direction_mult * 4.5 * atr
    est_rr = safe_float(sig.get("estimated_rr", 1.5))

    verdict = {"allow": allow, "reason": reason, "regime": regime, "vol_state": vol_state}

    if allow:
        verdict["book"] = book
        verdict["size"] = round(float(size), 6)
        verdict["action"] = "OPEN"
        verdict["direction"] = notional_dir

        summary_lines = []
        summary_lines.append(f"━━━ [信号单] {dir_emoji} {dir_cn} {imbalance_str} ━━━")
        summary_lines.append(f"方向: {dir_emoji} {dir_cn} | {imbalance_str}")
        summary_lines.append(f"━━━ 多空博弈 ━━━")
        summary_lines.append(f"多头: {long_score:.1f}分 EV:{long_ev:+.4f}  空头: {short_score:.1f}分 EV:{short_ev:+.4f}  分差: {score_diff:.1f}分")
        summary_lines.append(f"建议: {adv_detail}")
        summary_lines.append(f"━━━ 行情环境 ━━━")
        summary_lines.append(f"趋势: {regime_cn} | 波动: {vol_cn} | 压缩: {sqz_state}")
        summary_lines.append(f"成交量: {vol_str}")
        summary_lines.append(f"━━━ 指标透视 ━━━")
        summary_lines.append(f"K线: {kline_color} | 变色: {'是' if sqz_white else '否'}")
        summary_lines.append(f"RSI: {rsi:.1f}({rsi_status}) ADX: {adx:.1f}({adx_status})")
        summary_lines.append(f"MACD: {macd:.4f}({macd_status}) ATR: {atr:.2f} | {atr_pct:.2f}%")
        summary_lines.append(f"━━━ 流动性/关键位 ━━━")
        for liq in liquidity_lines:
            summary_lines.append(liq)
        summary_lines.append(f"买方OB: {ob_str if ob_dir == 'Long' else '暂无'}")
        summary_lines.append(f"卖方OB: {ob_str if ob_dir == 'Short' else '暂无'}")
        summary_lines.append(f"多头FVG: {float(fvg_mid):.2f}" if (fvg_dir == 'Long' and fvg_mid is not None) else "多头FVG: 暂无")
        summary_lines.append(f"空头FVG: {float(fvg_mid):.2f}" if (fvg_dir == 'Short' and fvg_mid is not None) else "空头FVG: 暂无")
        summary_lines.append(f"参考开单: {dir_emoji} {dir_cn} 入场{entry_price:.2f} SL{sl:.2f} TP1{tp1:.2f} TP2{tp2:.2f} TP3{tp3:.2f} RR{est_rr:.2f}")
        verdict["summary"] = "\n".join(summary_lines)
    else:
        verdict["action"] = "REJECT"
        verdict["summary"] = f"拒绝开单: {reason}"

    reasons_flat = []
    bt = sig.get("base_trigger", {})
    if not bt.get("passed", False):
        reasons_flat.append(f"base_trigger={bt.get('reason', 'NOT_PASSED')}")
    ev_rs = str(sig.get("ev_reasons", "")).strip()
    if ev_rs and ev_rs != "EV_CONTEXT_OK":
        for r in ev_rs.split(";"):
            r = r.strip()
            if r:
                reasons_flat.append(f"ev:{r}")
    cluster_r = str(sig.get("alpha_cluster_reason", "")).strip()
    if cluster_r:
        reasons_flat.append(f"cluster:{cluster_r}")

    verdict["reasons"] = reasons_flat
    verdict["ev_snapshot"] = ev_snapshot
    verdict["signal"] = sig
    return verdict

def decide(self, row, exec_ctx, macro_ctx):
    ok, circuit_reason = self.circuit_breaker()
    if not ok:
        return self._build_verdict(False, circuit_reason, "?", "?", {})

    regime = self.classify_regime(row, exec_ctx)
    vol_state = self.volatility_state(row, exec_ctx)
    exec_ctx = dict(exec_ctx)
    exec_ctx["regime"] = regime
    exec_ctx["vol_state"] = vol_state

    long_sig = self.generate_signal(row, "Long", exec_ctx, macro_ctx)
    short_sig = self.generate_signal(row, "Short", exec_ctx, macro_ctx)
    signal = self.choose_signal(row, exec_ctx, macro_ctx)

    if not signal.get("base_trigger_passed", False):
        return self._build_verdict(False,
            str(signal.get("base_trigger", {}).get("reason", "BASE_TRIGGER_NOT_PASSED")),
            regime, vol_state, signal, long_sig=long_sig, short_sig=short_sig)

    ok, reason = self.tail_filter(signal, regime, vol_state)
    if not ok:
        return self._build_verdict(False, reason, regime, vol_state, signal,
                                   long_sig=long_sig, short_sig=short_sig)

    risk = self.risk_budget(signal, regime, vol_state)
    book, size = self.allocate(signal, risk, regime, vol_state)
    if size <= 0.0:
        return self._build_verdict(False, "PORTFOLIO_SIZE_ZERO", regime, vol_state, signal,
                                   long_sig=long_sig, short_sig=short_sig)

    return self._build_verdict(True,
        f"ALLOW_{regime}_{book}_{signal.get('ev_grade', grade_from_expected_value(signal.get('expected_value', 0.0)))}",
        regime, vol_state, signal, book, size,
        long_sig=long_sig, short_sig=short_sig)

def update_account(self, pnl_r):
    self.account.update(pnl_r)

def state_dict(self):
    return asdict(self.account)
