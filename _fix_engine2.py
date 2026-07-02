# -*- coding: utf-8 -*-
"""Rewrite the tail of alpha_master_engine.py from 'def allocate' to EOF"""
import sys

with open('core/alpha_master_engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

idx = content.find('    def allocate(')
if idx < 0:
    print('ERROR: cannot find allocate')
    sys.exit(1)

head = content[:idx]

tail = r'''    def allocate(self, signal: Dict[str, Any], risk: float, regime: str, vol_state: str) -> Tuple[str, float]:
        ev = safe_float(signal.get("expected_value"), -9.0)
        grade = signal.get("ev_grade", grade_from_expected_value(ev))
        if ev > 0.25:
            book, mult = "CORE", 1.00
        elif ev > 0.15:
            book, mult = "TACTICAL", 0.78
        elif ev >= 0.05:
            book, mult = "SCALP", 0.65
        elif ev >= 0.0:
            book, mult = "PROBE", 0.42
        else:
            book, mult = "DUMPSTER", 0.08
        if grade == "S_EV_HOT":
            mult *= 1.08
        if regime == "CHOP":
            mult *= 0.82
        if vol_state == "HIGH_VOL":
            mult *= 0.80
        return book, max(0.0, min(self.max_position_mult, risk * mult))

    def _fast_no_setup_signal(self, row: Any, regime: str, vol_state: str) -> Dict[str, Any]:
        return {
            "direction": None,
            "score_raw": 0.0,
            "score": 0.0,
            "confidence": 0.0,
            "expected_value": -1.0,
            "win_prob": 0.0,
            "estimated_rr": 0.0,
            "ev_grade": "D_NEG_EV",
            "size_multiplier": 0.0,
            "ev_reasons": "NO_FEATURE_SETUP_FAST",
            "entry_meta": {"has_any_setup": False, "datetime": row.get("datetime")},
        }

    def _build_verdict(
        self,
        allow: bool,
        reason: str,
        regime: str,
        vol_state: str,
        signal: Dict[str, Any],
        book: str = "",
        size: float = 0.0,
        long_sig: Optional[Dict[str, Any]] = None,
        short_sig: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build Chinese-readable verdict signal sheet"""
        sig = signal or {}
        entry = sig.get("entry_meta", {})
        close = safe_float(entry.get("close", 0.0))
        atr = max(safe_float(entry.get("ATRr_14", entry.get("atr", 0.0)), 0.0), 1e-12)

        # Dual-direction EV snapshot
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
            adv_detail = long_ev > 0 and "bullish" or "bullish but EV weak"
        elif short_score > long_score and short_ev > long_ev:
            adv_detail = short_ev > 0 and "bearish" or "bearish but EV weak"
        else:
            adv_detail = "wait and see"

        # Price imbalance zones
        fvg_dir = str(entry.get("fvg_direction", "None"))
        fvg_mid = entry.get("fvg_mid", None)
        ob_dir = str(entry.get("ob_direction", "None"))
        ob_top = entry.get("ob_top", None)
        ob_bottom = entry.get("ob_bottom", None)

        imbalance_parts = []
        if fvg_dir != "None" and fvg_mid is not None:
            _fvg_mid = float(fvg_mid) if isinstance(fvg_mid, (int, float)) else 0.0
            label_fvg = "BULL_FVG" if fvg_dir == "Long" else "BEAR_FVG"
            imbalance_parts.append(f"{label_fvg}: {_fvg_mid:.2f}")

        ob_str = "NONE"
        if ob_dir != "None" and ob_top is not None and ob_bottom is not None:
            _ob_top = float(ob_top) if isinstance(ob_top, (int, float)) else 0.0
            _ob_bot = float(ob_bottom) if isinstance(ob_bottom, (int, float)) else 0.0
            label_ob = "BID_OB" if ob_dir == "Long" else "ASK_OB"
            ob_str = f"{label_ob}: {_ob_bot:.2f}~{_ob_top:.2f}"
            imbalance_parts.append(ob_str)

        imbalance_str = "NONE" if not imbalance_parts else ", ".join(imbalance_parts)

        # Market environment
        regime_cn = {"TREND": "TREND", "CHOP": "CHOP", "TRANSITION": "TRANSITION", "CRISIS_RISK_OFF": "CRISIS", "?": "UNKNOWN"}.get(regime, regime)
        vol_cn = {"HIGH_VOL": "HIGH_VOL", "LOW_VOL": "LOW_VOL", "MID_VOL": "MID_VOL"}.get(vol_state, vol_state)
        sqz_mult = safe_float(entry.get("sqz_mult", 1.0))
        sqz_state = "SQUEEZING" if sqz_mult < 1.0 else ("EXPANDING" if sqz_mult > 1.5 else "NORMAL")
        volume_ratio = safe_float(entry.get("volume_ratio", 1.0))
        if volume_ratio < 0.5:
            vol_str = f"{volume_ratio:.2f}x (EXTREME_LOW)"
        elif volume_ratio < 0.8:
            vol_str = f"{volume_ratio:.2f}x (LOW)"
        elif volume_ratio > 2.0:
            vol_str = f"{volume_ratio:.2f}x (HIGH)"
        else:
            vol_str = f"{volume_ratio:.2f}x (NORMAL)"

        mom = safe_float(entry.get("momentum", 0.0))
        rsi = safe_float(entry.get("rsi", 50.0))
        adx = safe_float(entry.get("adx", 0.0))
        macd = safe_float(entry.get("macd", safe_float(entry.get("MACD", 0.0))))
        atr_pct = atr / close * 100 if close > 0 else 0.0
        sqz_white = safe_bool(entry.get("sqzmom_white", False))

        kline_color = "WHITE (EXHAUST)" if sqz_white else ("GREEN (BULL)" if mom > 0 else "RED (BEAR)")
        rsi_status = "OVERBOUGHT" if rsi > 70 else ("OVERSOLD" if rsi < 30 else ("BULLISH" if rsi > 55 else ("BEARISH" if rsi < 45 else "NEUTRAL")))
        adx_status = "STRONG TREND" if adx >= 25 else ("WEAK/CHOP" if adx >= 15 else "VERY WEAK")
        macd_status = "BULLISH" if macd > 0 else "BEARISH"

        bsl = safe_float(entry.get("last_swing_high", 0.0))
        ssl = safe_float(entry.get("last_swing_low", 0.0))
        bsl_swept = any(safe_bool(entry.get(k, False)) for k in ["buyside_sweep", "buyside_liquidity_taken", "bearish_stop_hunt"])
        ssl_swept = any(safe_bool(entry.get(k, False)) for k in ["sellside_sweep", "sellside_liquidity_taken", "bullish_stop_hunt"])

        liquidity_lines = []
        if bsl > 0:
            bsl_dist = (bsl - close) / close * 100
            liquidity_lines.append(f"BSL: {bsl:.2f}(dist {abs(bsl_dist):.2f}%) | swept: {'YES' if bsl_swept else 'NO'}")
        if ssl > 0:
            ssl_dist = (close - ssl) / close * 100
            liquidity_lines.append(f"SSL: {ssl:.2f}(dist {abs(ssl_dist):.2f}%) | swept: {'YES' if ssl_swept else 'NO'}")

        notional_dir = str(sig.get("direction", "?")).title()
        entry_price = close
        direction_mult = 1.0 if notional_dir == "Long" else -1.0
        sl = entry_price - direction_mult * 1.5 * atr
        tp1 = entry_price + direction_mult * 1.5 * atr
        tp2 = entry_price + direction_mult * 3.0 * atr
        tp3 = entry_price + direction_mult * 4.5 * atr
        est_rr = safe_float(sig.get("estimated_rr", 1.5))

        verdict = {
            "allow": allow,
            "reason": reason,
            "regime": regime,
            "vol_state": vol_state,
        }

        if allow:
            verdict["book"] = book
            verdict["size"] = round(float(size), 6)
            verdict["action"] = "OPEN"
            verdict["direction"] = notional_dir

            lines = []
            lines.append(f"--- [SIGNAL] {notional_dir} ---")
            lines.append(f"[IMBALANCE] {imbalance_str}")
            lines.append(f"Direction: {notional_dir} | {imbalance_str}")
            lines.append(f"--- LONG vs SHORT ---")
            lines.append(f"Long: {long_score:.1f}pt EV:{long_ev:+.4f}  Short: {short_score:.1f}pt EV:{short_ev:+.4f}  delta: {score_diff:.1f}pt")
            lines.append(f"Advice: {adv_detail}")
            lines.append(f"--- ENVIRONMENT ---")
            lines.append(f"Regime: {regime_cn} | Vol: {vol_cn} | Squeeze: {sqz_state}")
            lines.append(f"Volume: {vol_str}")
            lines.append(f"--- INDICATORS ---")
            lines.append(f"K-line: {kline_color} | Color-changed: {'YES' if sqz_white else 'NO'}")
            lines.append(f"RSI: {rsi:.1f}({rsi_status}) ADX: {adx:.1f}({adx_status})")
            lines.append(f"MACD: {macd:.4f}({macd_status}) ATR: {atr:.2f} | {atr_pct:.2f}%")
            lines.append(f"--- LIQUIDITY ---")
            for liq in liquidity_lines:
                lines.append(liq)
            lines.append(f"Bid OB: {ob_str if ob_dir == 'Long' else 'NONE'}")
            lines.append(f"Ask OB: {ob_str if ob_dir == 'Short' else 'NONE'}")
            lines.append(f"Bull FVG: {float(fvg_mid):.2f}" if (fvg_dir == 'Long' and fvg_mid is not None) else "Bull FVG: NONE")
            lines.append(f"Bear FVG: {float(fvg_mid):.2f}" if (fvg_dir == 'Short' and fvg_mid is not None) else "Bear FVG: NONE")
            lines.append(f"Ref order: {notional_dir} entry {entry_price:.2f} SL {sl:.2f} TP1 {tp1:.2f} TP2 {tp2:.2f} TP3 {tp3:.2f} RR {est_rr:.2f}")
            verdict["summary"] = "\n".join(lines)
        else:
            verdict["action"] = "REJECT"
            verdict["summary"] = f"REJECT: {reason}"

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

    def decide(self, row: Any, exec_ctx: Dict[str, Any], macro_ctx: Dict[str, Any]) -> Dict[str, Any]:
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
            return self._build_verdict(
                False,
                str(signal.get("base_trigger", {}).get("reason", "BASE_TRIGGER_NOT_PASSED")),
                regime, vol_state, signal,
                long_sig=long_sig, short_sig=short_sig,
            )
        ok, reason = self.tail_filter(signal, regime, vol_state)
        if not ok:
            return self._build_verdict(
                False, reason, regime, vol_state, signal,
                long_sig=long_sig, short_sig=short_sig,
            )

        risk = self.risk_budget(signal, regime, vol_state)
        book, size = self.allocate(signal, risk, regime, vol_state)
        if size <= 0.0:
            return self._build_verdict(
                False, "PORTFOLIO_SIZE_ZERO", regime, vol_state, signal,
                long_sig=long_sig, short_sig=short_sig,
            )

        return self._build_verdict(
            True,
            f"ALLOW_{regime}_{book}_{signal.get('ev_grade', grade_from_expected_value(signal.get('expected_value', 0.0)))}",
            regime, vol_state, signal, book, size,
            long_sig=long_sig, short_sig=short_sig,
        )

    def update_account(self, pnl_r: float) -> None:
        self.account.update(pnl_r)

    def state_dict(self) -> Dict[str, Any]:
        return asdict(self.account)
'''

new_content = head + tail
with open('core/alpha_master_engine.py', 'w', encoding='utf-8') as f:
    f.write(new_content)

# Verify syntax
try:
    compile(new_content, 'core/alpha_master_engine.py', 'exec')
    print('Syntax OK')
except SyntaxError as e:
    print(f'Syntax error: {e}')
    sys.exit(1)
