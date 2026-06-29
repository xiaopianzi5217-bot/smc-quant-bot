# -*- coding: utf-8 -*-
"""V11 institutional runner built on the V9 execution/decision stack."""
import pandas as pd
import traceback

from decision.v9_decision_kernel import V9DecisionKernel
from monitoring.runtime_report import write_json_report
from ops.env_config import load_runtime_config
from risk.global_risk import GlobalRiskGuard
from risk.portfolio_state import PortfolioStateManager
from indicators.basic import add_all_indicators
from strategy.smc import build_macro_context, build_exec_context
from strategy.scoring import adaptive_signal_score
from strategy.risk import calculate_dynamic_tp_sl
from strategy.trade_filters import mark_strategy_approval, check_strategy_filters
from notifier.observer.risk_plan import build_rr_plan
from notifier.observer.signal_collector import build_signal_snapshot
from strategy.intelligence_engine import estimate_expected_value
from config import SYMBOL_STRATEGY
from utils.symbols import load_symbol_strategy
from analytics.filter_audit import FilterAuditLogger
from utils.time_utils import now_bj, series_ms_to_bj
try:
    import ccxt  # type: ignore
except ModuleNotFoundError:  # optional dependency for live data only
    ccxt = None

try:
    from notifier.manager import dispatch_observer_snapshot, dispatch_strategy_decision
except ImportError:
    dispatch_observer_snapshot = None
    dispatch_strategy_decision = None


def load_config(path="config/v11_full_config.json"):
    return load_runtime_config(path)


def make_sample_ohlcv(rows=320, start=100.0):
    data = []
    price = start
    end_time = pd.Timestamp(now_bj()).floor("15min")
    start_time = end_time - pd.Timedelta(minutes=15 * (rows - 1))

    for i in range(rows):
        drift = (i % 37 - 18) * 0.015
        open_ = price
        close = max(1, price + drift + (0.25 if i % 53 == 0 else 0))
        high = max(open_, close) + 0.8 + (i % 5) * 0.05
        low = min(open_, close) - 0.8 - (i % 7) * 0.04
        volume = 1000 + (i % 29) * 30 + (500 if i % 67 == 0 else 0)
        dt = start_time + pd.Timedelta(minutes=15 * i)
        data.append([i, open_, high, low, close, volume, dt.to_pydatetime()])
        price = close

    return pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume", "datetime"])


def fetch_live_ohlcv(symbol: str, timeframe: str = "15m", limit: int = 320):
    """Fetch real OHLCV data from Bitget swap (合约) API via requests."""
    import requests as _req
    
    # Bitget 合约 API 时间格式：1m,3m,5m,15m,30m,1H,4H,6H,12H,1D,1W,1M
    tf_map = {"1h": "1H", "4h": "4H", "6h": "6H", "12h": "12H", "1d": "1D", "1w": "1W"}
    gran = tf_map.get(timeframe.lower(), timeframe)
    
    sym = symbol.upper().strip().replace("/", "")
    url = "https://api.bitget.com/api/v2/mix/market/candles"
    params = {
        "symbol": sym,
        "granularity": gran,
        "limit": str(limit),
        "productType": "UMCBL",
    }
    resp = _req.get(url, params=params, timeout=15)
    data = resp.json()
    
    if data.get("code") != "00000":
        raise Exception(f"Bitget API error: code={data.get('code')} msg={data.get('msg')}")
    
    candles = data.get("data", [])
    if not candles or len(candles) < 50:
        raise Exception(f"Bitget returned too few candles: {len(candles)}")
    
    rows = []
    for c in candles:
        ts = int(c[0]) // 1000
        o, h, l, cl, v = float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])
        rows.append([ts, o, h, l, cl, v])
    
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["datetime"] = series_ms_to_bj(df["timestamp"] * 1000)
    print(f"  ✅ 成功获取 {symbol} {timeframe} 合约数据 ({len(candles)} 根K线, 最新价:{candles[-1][4]})")
    return df


def _enrich_volume_context(df_exec, curr, exec_ctx):
    avg_vol = df_exec["volume"].rolling(20).mean().iloc[-1]
    volume_ratio = float(curr["volume"] / avg_vol) if avg_vol == avg_vol and avg_vol > 0 else 0.0
    exec_ctx["avg_volume_20"] = float(avg_vol) if avg_vol == avg_vol else 0.0
    exec_ctx["volume_ratio"] = volume_ratio
    exec_ctx["volume_confirmed"] = bool(volume_ratio > 1.5)
    return volume_ratio


def evaluate_symbol(symbol, cfg):
    params = cfg.get("strategy_params", {})
    wvf = params.get("wvf_std_mult", 2.0)

    # 根据 data_mode 决定使用真实数据还是模拟数据
    data_mode = str(cfg.get("data_mode", "sample_data")).lower()
    if data_mode == "live":
        # 分别获取 exec 和 macro 数据，各自处理失败情况
        try:
            df_exec = add_all_indicators(fetch_live_ohlcv(symbol, cfg.get("exec_timeframe", "15m"), 320), wvf)
            print(f"[{symbol}] 使用真实数据 (exec)")
        except Exception as e:
            print(f"[{symbol}] exec 数据获取失败: {e}，回退到模拟数据")
            df_exec = add_all_indicators(make_sample_ohlcv(start=100.0), wvf)
        try:
            df_macro = add_all_indicators(fetch_live_ohlcv(symbol, cfg.get("macro_timeframe", "1h"), 320), wvf)
            print(f"[{symbol}] 使用真实数据 (macro)")
        except Exception as e:
            print(f"[{symbol}] macro 数据获取失败: {e}，回退到模拟数据")
            df_macro = add_all_indicators(make_sample_ohlcv(start=102.0), wvf)
    else:
        df_exec = add_all_indicators(make_sample_ohlcv(start=100.0), wvf)
        df_macro = add_all_indicators(make_sample_ohlcv(start=102.0), wvf)

    curr = df_exec.iloc[-1]
    price = float(curr["close"])
    atr = float(curr.get("ATRr_14", curr.get("atr", 0)) or 0)
    
    macro_ctx = build_macro_context(df_macro)
    exec_ctx = build_exec_context(df_exec)
    
    # --- OB 距离判断逻辑 ---
    if atr > 0:
        bull_ob = exec_ctx.get("bullish_ob")
        if bull_ob and isinstance(bull_ob, (list, tuple)) and len(bull_ob) >= 2:
            try:
                ob_max, ob_min = max(float(bull_ob[0]), float(bull_ob[1])), min(float(bull_ob[0]), float(bull_ob[1]))
                if (ob_min - atr) <= price <= (ob_max + atr):
                    exec_ctx["near_bullish_ob"] = True
            except Exception: pass
        
        bear_ob = exec_ctx.get("bearish_ob")
        if bear_ob and isinstance(bear_ob, (list, tuple)) and len(bear_ob) >= 2:
            try:
                ob_max, ob_min = max(float(bear_ob[0]), float(bear_ob[1])), min(float(bear_ob[0]), float(bear_ob[1]))
                if (ob_min - atr) <= price <= (ob_max + atr):
                    exec_ctx["near_bearish_ob"] = True
            except Exception: pass
    # ---------------------------------
    
    exec_ctx["symbol"] = symbol

    volume_ratio = _enrich_volume_context(df_exec, curr, exec_ctx)
    is_vol = bool(volume_ratio > 1.5)

    # 获取资金费率（Binance 公开 API，免 Key）
    try:
        import requests as _req_fr
        fr_resp = _req_fr.get(
            "https://fapi.binance.com/fapi/v1/premiumIndex",
            params={"symbol": "BTCUSDT"},
            timeout=10,
        )
        if fr_resp.status_code == 200:
            fr_val = float(fr_resp.json().get("lastFundingRate", 0))
            exec_ctx["funding_rate"] = round(fr_val * 100, 4)  # 转成百分比
        else:
            exec_ctx["funding_rate"] = 0.0
    except Exception:
        exec_ctx["funding_rate"] = 0.0

    # ===== 补充评分系统所需字段（grade_entry_quality 需要这些字段才能正确评分） =====
    # 方向相关
    exec_ctx["htf_direction"] = macro_ctx.get("allowed_direction", "")
    exec_ctx["setup_type"] = "ob" if exec_ctx.get("ob_valid") else ("fvg" if exec_ctx.get("bearish_fvg") or exec_ctx.get("bullish_fvg") else "")
    
    # SMC 区域
    exec_ctx["smc_zone_score"] = float(exec_ctx.get("pivot_strength_high", 0) or 0) + float(exec_ctx.get("pivot_strength_low", 0) or 0)
    exec_ctx["has_valid_zone"] = bool(exec_ctx.get("ob_valid")) or bool(exec_ctx.get("bullish_fvg")) or bool(exec_ctx.get("bearish_fvg"))
    
    # K线形态
    body = abs(float(curr.get("close", 0)) - float(curr.get("open", 0)))
    hilo = float(curr.get("high", 0)) - float(curr.get("low", 0))
    exec_ctx["body_pct"] = body / hilo if hilo > 0 else 0.0
    
    # 其他
    exec_ctx["macro_conflict"] = False
    exec_ctx["too_extended"] = False
    exec_ctx["fe_bottom"] = bool(curr.get("is_FE", False))
    exec_ctx["fe_top"] = bool(curr.get("is_Inv_FE", False))
    exec_ctx["same_side_div_count_12"] = 0.0
    exec_ctx["vwap_align"] = None  # 由后续逻辑判断
    exec_ctx["rr"] = 1.0  # 占位，后面 calculate_dynamic_tp_sl 会计算真实 rr
    exec_ctx["distance_atr"] = 0.0
    exec_ctx["ob_strength"] = float(exec_ctx.get("pivot_strength_high", 0) or 0)
    exec_ctx["fvg_quality"] = 1.0 if (exec_ctx.get("bearish_fvg") or exec_ctx.get("bullish_fvg")) else 0.0
    exec_ctx["displacement"] = float(exec_ctx.get("pivot_strength_low", 0) or 0)
    exec_ctx["liquidity"] = 1.0 if (exec_ctx.get("is_bsl_swept") or exec_ctx.get("is_ssl_swept")) else 0.0
    # ==========================================

    # 构建多头和空头各自的评分上下文（方向相关字段分开设置）
    long_ctx = dict(exec_ctx)
    short_ctx = dict(exec_ctx)
    
    # 多头方向相关字段
    long_ctx["direction"] = "Long"
    long_ctx["divergence_confirmed"] = bool(curr.get("has_bot_div", False))  # 底背离 = 多头
    long_ctx["sqzmom_divergence_dir"] = "Long" if bool(curr.get("has_bot_div", False)) else ""
    long_ctx["sqzmom_divergence_age"] = int(float(curr.get("bot_div_age", 999) or 999))
    long_ctx["sqzmom_divergence_strength"] = float(curr.get("bot_div_strength", 0) or 0)
    long_ctx["sqzmom_white_confirm"] = bool(curr.get("sqzmom_white_reversal_long", False))
    long_ctx["sqzmom_momentum_confirm"] = bool(curr.get("sqzmom_white_reversal_long", False))
    long_ctx["sqzmom_reversal_confirm_long"] = bool(curr.get("sqzmom_white_reversal_long", False))
    long_ctx["sqzmom_reversal_confirm_short"] = False
    long_ctx["sqzmom_dmi_aligned"] = bool(curr.get("dmi_bull", False))
    long_ctx["sqzmom_trigger_ok"] = bool(curr.get("dmi_bull", False))
    long_ctx["dmi_bull"] = bool(curr.get("dmi_bull", False))
    long_ctx["dmi_bear"] = False
    long_ctx["momentum"] = float(curr.get("momentum", 0) or 0)
    long_ctx["liquidity_sweep_confirmed"] = bool(curr.get("is_ssl_swept", False))  # sellside sweep = 多头信号
    long_ctx["liquidity_wrong_side"] = bool(curr.get("is_bsl_swept", False))  # buyside sweep = 空头信号，对多头是反方向
    # 【修复20260625】补充 sqzmom_score 字段（smc_impulse_engine 的 _sqzmom_score 依赖此字段）
    # 计算方式与 core/alpha_master_engine.py 的 build_entry_snapshot 一致
    _long_sqz = 0.0
    if float(curr.get("momentum", 0) or 0) > 0: _long_sqz += 7.0
    if float(curr.get("momentum_slope", 0) or 0) > 0: _long_sqz += 6.0
    if bool(curr.get("sqzmom_white_reversal_long", False)): _long_sqz += 8.0
    if bool(curr.get("dmi_bull", False)) or float(curr.get("plus_di", 0) or 0) >= float(curr.get("minus_di", 0) or 0): _long_sqz += 7.0
    if bool(curr.get("squeeze_released", False)): _long_sqz += 6.0
    if str(curr.get("sqzmom_divergence_dir", "None")) == "Long" and int(float(curr.get("bot_div_age", 999) or 999)) <= 18: _long_sqz += 10.0
    long_ctx["sqzmom_score"] = max(0.0, min(44.0, _long_sqz))
    
    # 空头方向相关字段
    short_ctx["direction"] = "Short"
    short_ctx["divergence_confirmed"] = bool(curr.get("has_top_div", False))  # 顶背离 = 空头
    short_ctx["sqzmom_divergence_dir"] = "Short" if bool(curr.get("has_top_div", False)) else ""
    short_ctx["sqzmom_divergence_age"] = int(float(curr.get("top_div_age", 999) or 999))
    short_ctx["sqzmom_divergence_strength"] = float(curr.get("top_div_strength", 0) or 0)
    short_ctx["sqzmom_white_confirm"] = bool(curr.get("sqzmom_white_reversal_short", False))
    short_ctx["sqzmom_momentum_confirm"] = bool(curr.get("sqzmom_white_reversal_short", False))
    short_ctx["sqzmom_reversal_confirm_long"] = False
    short_ctx["sqzmom_reversal_confirm_short"] = bool(curr.get("sqzmom_white_reversal_short", False))
    short_ctx["sqzmom_dmi_aligned"] = bool(curr.get("dmi_bear", False))
    short_ctx["sqzmom_trigger_ok"] = bool(curr.get("dmi_bear", False))
    short_ctx["dmi_bull"] = False
    short_ctx["dmi_bear"] = bool(curr.get("dmi_bear", False))
    short_ctx["momentum"] = float(curr.get("momentum", 0) or 0)
    short_ctx["liquidity_sweep_confirmed"] = bool(curr.get("is_bsl_swept", False))  # buyside sweep = 空头信号
    short_ctx["liquidity_wrong_side"] = bool(curr.get("is_ssl_swept", False))  # sellside sweep = 多头信号，对空头是反方向
    # 【修复20260625】补充 sqzmom_score 字段（smc_impulse_engine 的 _sqzmom_score 依赖此字段）
    _short_sqz = 0.0
    if float(curr.get("momentum", 0) or 0) < 0: _short_sqz += 7.0
    if float(curr.get("momentum_slope", 0) or 0) < 0: _short_sqz += 6.0
    if bool(curr.get("sqzmom_white_reversal_short", False)): _short_sqz += 8.0
    if bool(curr.get("dmi_bear", False)) or float(curr.get("minus_di", 0) or 0) > float(curr.get("plus_di", 0) or 0): _short_sqz += 7.0
    if bool(curr.get("squeeze_released", False)): _short_sqz += 6.0
    if str(curr.get("sqzmom_divergence_dir", "None")) == "Short" and int(float(curr.get("top_div_age", 999) or 999)) <= 18: _short_sqz += 10.0
    short_ctx["sqzmom_score"] = max(0.0, min(44.0, _short_sqz))

    l_score, l_thresh, l_reasons = adaptive_signal_score(long_ctx, macro_ctx, "Long", is_vol)
    s_score, s_thresh, s_reasons = adaptive_signal_score(short_ctx, macro_ctx, "Short", is_vol)

    # ===== 【修复20260701】计算真实 EV（期望值）=====
    def _extract_from_reasons(reasons, field, default=0):
        """从 reasons dict 或 list 中提取字段值"""
        if isinstance(reasons, dict):
            return float(reasons.get(field, 0) or 0)
        if isinstance(reasons, (list, tuple)):
            for r in reasons:
                if isinstance(r, str) and f"{field}=" in r:
                    try: return float(r.split("=")[1].split(",")[0].strip())
                    except: pass
        return default

    _long_sig = {
        "score_raw": l_score, "score": l_score,
        "smc": _extract_from_reasons(l_reasons, "smc"),
        "sqzmom": _extract_from_reasons(l_reasons, "sqzmom"),
        "breakout": 0, "raw_base": 0,
        "base_trigger_passed": l_score >= l_thresh,
        "fallback_active": False, "direction": "Long", "ev_grade": "C_EV",
        "entry_meta": long_ctx,
    }
    _short_sig = {
        "score_raw": s_score, "score": s_score,
        "smc": _extract_from_reasons(s_reasons, "smc"),
        "sqzmom": _extract_from_reasons(s_reasons, "sqzmom"),
        "breakout": 0, "raw_base": 0,
        "base_trigger_passed": s_score >= s_thresh,
        "fallback_active": False, "direction": "Short", "ev_grade": "C_EV",
        "entry_meta": short_ctx,
    }

    _regime_str = str(macro_ctx.get("regime", ""))
    _vol_str = str(macro_ctx.get("vol_state", ""))
    _long_ev_result = estimate_expected_value(_long_sig, _regime_str, _vol_str, long_ctx)
    _short_ev_result = estimate_expected_value(_short_sig, _regime_str, _vol_str, short_ctx)
    long_ev = _long_ev_result.get("expected_value", 0.0)
    short_ev = _short_ev_result.get("expected_value", 0.0)
    # ==================================================

    # ===== 【修复20260625】HTF 方向强制对齐 =====
    # 问题：决策方向只靠 long_score >= short_score 比较，
    # 没有考虑 1H 级别 allowed_direction 的方向限制，
    # 导致经常开反方向（1H 看空但 15M 开多）
        #
    # 修复：加权投票融合 HTF + 15M 评分
    htf_allowed = str(macro_ctx.get("allowed_direction", "Both")).strip()
    htf_suggest = macro_ctx.get("htf_suggest", "")
    
    # 【修复20260701】科学方向融合：HTF 投票 vs 15M 评分
    # - HTF 权重取决于 ADX(趋势强度): ADX 18=>0.6, 30=>0.75, 45=>0.9
    # - 15M 权重取决于 score_edge: edge 10=>0.5, 20=>0.7, 30=>0.9
    # - 分歧投票总分>0.3=>Long, <-0.3=>Short, 中间=>允许双方向但让评分优先
    htf_adx = float(exec_ctx.get("adx", macro_ctx.get("ADX_14", macro_ctx.get("adx", 0))))
    htf_weight = min(0.9, max(0.3, htf_adx / 45.0)) if htf_adx >= 18 else max(0.1, htf_adx / 40.0)
    
    score_edge = abs(l_score - s_score)
    m15_weight = min(0.9, max(0.3, score_edge / 30.0))
    
    # HTF 投票：Short=-1, Long=+1, Both=0
    htf_vote = -1 if htf_allowed == "Short" else (1 if htf_allowed == "Long" else 0)
    # 15M 投票：基于评分差异
    dir_by_score = "Long" if l_score >= s_score else "Short"
    m15_vote = 1 if dir_by_score == "Long" else -1
    
    # 加权总分
    total_vote = htf_vote * htf_weight + m15_vote * m15_weight
    
    # 融合方向决策
    if total_vote > 0.3:
        direction = "Long"
        exec_ctx["htf_fusion"] = f"Long(total_vote={total_vote:.2f},htf_w={htf_weight:.2f},m15_w={m15_weight:.2f})"
    elif total_vote < -0.3:
        direction = "Short"
        exec_ctx["htf_fusion"] = f"Short(total_vote={total_vote:.2f},htf_w={htf_weight:.2f},m15_w={m15_weight:.2f})"
    else:
        # 分歧区：HTF 有明确方向但 15M 强烈反对 -> 放权给 15M 评分
        if htf_allowed != "Both" and abs(htf_vote * htf_weight - m15_vote * m15_weight) < 0.15:
            direction = dir_by_score  # 轻微分歧，15M 优先
            exec_ctx["htf_fusion"] = f"DisagreeMin({direction},vote={total_vote:.2f})"
        else:
            direction = dir_by_score  # 大分歧也按 15M 评分
            exec_ctx["htf_fusion"] = f"DisagreeStrong({direction},vote={total_vote:.2f})"
    
    # 保留 htf_allowed 供风控参考（不参与开单决策，只用于日志）
    exec_ctx["htf_allowed"] = htf_allowed
    
    # 记录方向对齐信息
    exec_ctx["htf_forced_direction"] = direction
    exec_ctx["htf_allowed"] = htf_allowed
    sym_strategy = load_symbol_strategy(symbol, SYMBOL_STRATEGY)
    min_rr = sym_strategy.get("min_rr", cfg.get("risk", {}).get("min_rr", 2.0))
    sl, tp1, tp2, tp3, rr = calculate_dynamic_tp_sl(
        direction, curr, df_exec, exec_ctx, min_rr, sym_strategy
    )
    rr_plan = build_rr_plan(direction, price, sl, tp1, tp2, tp3)
    snapshot = build_signal_snapshot(
        symbol=symbol,
        df=df_exec,
        macro_ctx=macro_ctx,
        exec_ctx=exec_ctx,
        long_score=l_score,
        long_threshold=l_thresh,
        long_reasons=l_reasons,
        short_score=s_score,
        short_threshold=s_thresh,
        short_reasons=s_reasons,
        rr_plan=rr_plan,
        funding_rate=exec_ctx.get("funding_rate"),
        long_ev=long_ev,
        short_ev=short_ev,
    )

    tg_cfg = cfg.get("telegram", {})
    print(f"[{symbol}] Telegram 配置: send_observer={tg_cfg.get('send_observer', False)}, send_approved={tg_cfg.get('send_approved', False)}")
    observer_sent = False
    if dispatch_observer_snapshot and tg_cfg.get("send_observer", False):
        try:
            result = dispatch_observer_snapshot(snapshot, send_all=bool(tg_cfg.get("send_observer_all", False)))
            print(f"[{symbol}] Observer 消息发送结果: {result}")
            observer_sent = True
        except Exception as e:
            print(f"[{symbol}] Observer顺发消息触发异常: {e}")
    else:
        if not dispatch_observer_snapshot:
            print(f"[{symbol}] dispatch_observer_snapshot 不可用 (导入失败)")
        if not tg_cfg.get("send_observer", False):
            print(f"[{symbol}] send_observer 配置为 False，跳过 Observer 消息")

    # 记录 Observer 推送日记
    try:
        from state.push_diary import push_logger as _pd
        _pd.record(
            symbol=symbol, channel="telegram", msg_type="observer",
            direction="", score=l_score, ev=long_ev, price=price,
            msg_preview=f"observer|symbol={symbol}|score={l_score:.1f}/{s_score:.1f}"[:120],
            status="sent" if observer_sent else "skipped",
            reason="" if observer_sent else ("dispatch_unavailable" if not dispatch_observer_snapshot else "no_structural_change"),
        )
    except Exception as _pd_err:
        print(f"[{symbol}] PushDiary observer 记录失败: {_pd_err}")

    kernel = V9DecisionKernel(params=cfg)
    decision = kernel.decide(
        curr=curr,
        macro_ctx=macro_ctx,
        exec_ctx=exec_ctx,
        long_score=l_score,
        long_threshold=l_thresh,
        long_reasons=l_reasons,
        short_score=s_score,
        short_threshold=s_thresh,
        short_reasons=s_reasons,
        symbol=symbol,
        cfg=cfg,
        min_rr=cfg.get("risk", {}).get("min_rr", 2.0),
        rr=rr,
        direction=direction,
        entry=price,
        sl=sl,
        tp1=tp1,
        tp2=tp2,
        tp3=tp3,
        long_ev=long_ev,
        short_ev=short_ev,
    )

    # 【修复20260701】将风险计划注入 decision 字典
    decision["risk_plan"] = rr_plan
    decision["entry"] = price
    decision["entry_price"] = price
    decision["stop_loss"] = sl
    decision["take_profit_1"] = tp1
    decision["take_profit_2"] = tp2
    decision["take_profit_3"] = tp3
    decision["rr_calculated"] = rr

    # Strategy filters are post-decision guards: they may only downgrade/block
    # an already approved decision. They must never turn HOLD into approved.
    if decision.get("approved"):
        filter_result = check_strategy_filters({
            "symbol": symbol,
            "curr": curr,
            "macro_ctx": macro_ctx,
            "exec_ctx": exec_ctx,
            "decision": decision,
            "cfg": cfg,
        })
        decision["strategy_filters"] = filter_result
        if not filter_result.get("approved", filter_result.get("allowed", False)):
            decision["approved"] = False
            decision["decision_approved"] = False
            decision["is_approved"] = False
            decision["state"] = "STRATEGY_FILTER_BLOCKED"
            decision["state_name"] = "策略过滤拦截"
            decision["reason"] = filter_result.get("reason") or "Strategy filter blocked"
            decision["reason_cn"] = decision["reason"]

    guard = GlobalRiskGuard(cfg)
    portfolio_state = PortfolioStateManager().load()
    portfolio_check = guard.check(portfolio_state)

    if decision.get("approved") and not portfolio_check.get("allowed"):
        portfolio_reasons = portfolio_check.get("reasons") or []
        decision["approved"] = False
        decision["state"] = "PORTFOLIO_BLOCKED"
        decision["state_name"] = "组合风控拦截"
        decision["reason"] = "; ".join(portfolio_reasons) or "组合风控不允许开仓"
        decision["reason_cn"] = decision["reason"]

    if decision.get("approved") and dispatch_strategy_decision and cfg.get("telegram", {}).get("send_approved", False):
        try:
            result = dispatch_strategy_decision(snapshot, decision)
            print(f"[{symbol}] Strategy 消息发送结果: {result}")
            # 记录 Strategy 推送日记
            try:
                from state.push_diary import push_logger as _pd2
                _pd2.record(
                    symbol=symbol, channel="telegram", msg_type="strategy_approved",
                    direction=direction, score=l_score if direction == "Long" else s_score,
                    ev=long_ev if direction == "Long" else short_ev, price=price,
                    msg_preview=str(result)[:120] if result else "sent",
                    status="sent",
                )
            except Exception as _pd2_err:
                print(f"[{symbol}] PushDiary strategy 记录失败: {_pd2_err}")
        except Exception as e:
            print(f"[{symbol}] Strategy 消息发送异常: {e}")
    elif decision.get("approved"):
        print(f"[{symbol}] 决策已批准但未发送 Telegram: dispatch_strategy_decision={'可用' if dispatch_strategy_decision else '不可用'}, send_approved={cfg.get('telegram', {}).get('send_approved', False)}")
    else:
        print(f"[{symbol}] 决策未批准: {decision.get('reason', '未知原因')}")
    
    # 【Debug 增强】打印完整评分和 EV 诊断
    _pri_score = l_score if direction == "Long" else s_score
    print(f"[{symbol}] DIAG: score={l_score:.1f}/{s_score:.1f} | dir={direction} | EV={long_ev:.4f}/{short_ev:.4f} | "
          f"edge=±{abs(l_score-s_score):.1f} | HTF={htf_allowed} | vol_ratio={volume_ratio:.2f} | "
          f"ADX={float(curr.get('adx',0)):.1f} | squeeze={curr.get('squeeze','none')}")

    # 【增强】SignalDiary 记录
    try:
        from state.signal_diary import diary as _sd
        _sd.record(
            symbol=symbol, direction=direction,
            approved=bool(decision.get("approved")),
            state=decision.get("state", "HOLD"),
            reason=decision.get("reason", ""),
            long_score=l_score, short_score=s_score,
            long_ev=long_ev, short_ev=short_ev,
            price=price, sl=sl, tp1=tp1, rr=rr,
            regime=str(macro_ctx.get("regime", "")),
            htf_allowed=htf_allowed,
            volume_ratio=volume_ratio,
            adx=float(curr.get('adx',0)),
            atr_pct=float(curr.get('atr_pct',0)),
            squeeze=str(curr.get('squeeze','')),
            has_bot_div=bool(curr.get('has_bot_div', False)),
            has_top_div=bool(curr.get('has_top_div', False)),
            is_ssl_swept=bool(curr.get('is_ssl_swept', False)),
            is_bsl_swept=bool(curr.get('is_bsl_swept', False)),
            score_reasons=str(l_reasons) if direction == "Long" else str(s_reasons),
            ev_reasons=long_ev if direction == "Long" else short_ev,
            funding_rate=float(exec_ctx.get("funding_rate", 0)),
            long_score_raw=l_score, short_score_raw=s_score,
        )
    except Exception as _sd_err:
        print(f"[{symbol}] SignalDiary 记录失败: {_sd_err}")

    marked = mark_strategy_approval({
        "symbol": symbol,
        "curr": curr,
        "macro_ctx": macro_ctx,
        "exec_ctx": exec_ctx,
        "decision": decision,
        "cfg": cfg,
    })
    if marked is None:
        marked = {
            "symbol": symbol,
            "approved": False,
            "state": "MARK_APPROVAL_ERROR",
            "state_name": "MARK_APPROVAL_ERROR",
            "reason": "mark_strategy_approval returned None",
        }

    # Hard invariant: a non-approved DecisionKernel result cannot be re-approved
    # by downstream filters or audit formatting.
    if not decision.get("approved"):
        marked["approved"] = False

    FilterAuditLogger().record(symbol, curr, marked)

    # 决策批准时写入 feature_store（开单特征记录）
    if marked.get("approved"):
        try:
            from feature_store import feature_store as _fs
            _fs.save_trade({
                "symbol": symbol,
                "direction": direction,
                "entry": price,
                "sl": sl,
                "tp1": tp1,
                "rr": rr,
                "ev": 0.0,
                "score": l_score if direction == "Long" else s_score,
                "regime": exec_ctx.get("regime", ""),
                "regime2": "",
                "book": decision.get("book", ""),
                "adx": exec_ctx.get("adx", 0),
                "atr": atr,
                "div_count": 0,
                "signal_age": 0,
                "mfe": 0.0,
                "mae": 0.0,
                "max_r": 0.0,
                "max_r_before_stop": 0.0,
                "exit_reason": "OPEN",
                "pnl_r": None,
                "weekday": __import__("datetime").datetime.now().weekday(),
                "hour": __import__("datetime").datetime.now().hour,
            })
        except Exception as _fs_err:
            print(f"[{symbol}] FeatureStore 写入失败: {_fs_err}")

        # 写入交易日志（Trade Journal）
        try:
            from state.trade_journal import journal as _tj
            _tj.open_trade(
                symbol=symbol,
                direction=direction,
                open_price=price,
                sl=sl,
                tp1=tp1,
                tp2=tp2 if tp2 else 0,
                tp3=tp3 if tp3 else 0,
                rr=rr,
                score=l_score if direction == "Long" else s_score,
                regime=str(exec_ctx.get("regime", "")),
                note=f"adx={round(float(curr.get('adx',0)),1)} atr={round(atr,1)} vol_ratio={round(volume_ratio,2)}",
            )
        except Exception as _tj_err:
            print(f"[{symbol}] TradeJournal 写入失败: {_tj_err}")

    return {
        "symbol": symbol,
        "approved": bool(marked.get("approved")),
        "state": marked.get("state") or marked.get("state_name"),
        "reason": marked.get("reason") or marked.get("reason_cn"),
        "decision": marked,
    }


def run_once(cfg=None):
    cfg = cfg or load_config()
    symbols = cfg.get("symbols", ["BTC/USDT", "ETH/USDT"])
    blacklist = cfg.get("symbol_blacklist", [])
    symbols = [s for s in symbols if s not in blacklist]
    results = []
    for symbol in symbols:
        try:
            results.append(evaluate_symbol(symbol, cfg))
        except Exception as e:
            # 【这里是核心绝杀修改】
            # 把完整的异常堆栈当做字符串，直接塞给前端的 JSON！
            err_stack = traceback.format_exc()
            results.append({
                "symbol": symbol,
                "approved": False,
                "state": "ERROR",
                "reason": err_stack,
            })
    write_json_report("latest_v11_run.json", results)
    # 【修复20260625】追加 CSV 数据日志（只追加，不覆盖）
    _append_csv_log(results, cfg)
    return results


def _append_csv_log(results, cfg):
    """将每次扫描结果追加到 CSV 日志文件（含表头自动创建）"""
    import os, csv
    from datetime import datetime
    
    log_dir = cfg.get("log_dir", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "v11_scan_log.csv")
    
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fieldnames = [
        "timestamp", "symbol", "direction", "approved", "long_score", "short_score",
        "edge", "entry", "sl", "tp1", "rr",
        "regime", "adx", "atr_pct", "volume_ratio",
        "bsl_level", "ssl_level", "is_bsl_swept", "is_ssl_swept",
        "bearish_ob", "bullish_ob", "bearish_fvg", "bullish_fvg",
        "has_bot_div", "has_top_div", "squeeze",
        "htf_allowed", "state", "reason"
    ]
    
    file_exists = os.path.isfile(log_path)
    with open(log_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists or os.path.getsize(log_path) == 0:
            writer.writeheader()
        
        for r in results:
            dec = r.get("decision", {})
            row = {
                "timestamp": now_str,
                "symbol": r.get("symbol", ""),
                "direction": dec.get("direction", ""),
                "approved": r.get("approved", False),
                "long_score": dec.get("long_score", 0),
                "short_score": dec.get("short_score", 0),
                "edge": abs(float(dec.get("long_score", 0)) - float(dec.get("short_score", 0))),
                "entry": dec.get("price", dec.get("entry", 0)),
                "sl": dec.get("risk_plan", {}).get("sl", 0),
                "tp1": dec.get("risk_plan", {}).get("tp1", 0),
                "rr": dec.get("rr", 0),
                "regime": dec.get("regime", dec.get("exec_ctx", {}) if isinstance(dec.get("regime", ""), str) else ""),
                "adx": dec.get("exec_ctx", {}).get("adx", 0) if isinstance(dec.get("exec_ctx"), dict) else 0,
                "atr_pct": dec.get("exec_ctx", {}).get("atr_pct", 0) if isinstance(dec.get("exec_ctx"), dict) else 0,
                "volume_ratio": dec.get("exec_ctx", {}).get("volume_ratio", 0) if isinstance(dec.get("exec_ctx"), dict) else 0,
                "bsl_level": dec.get("exec_ctx", {}).get("bsl_level", 0) if isinstance(dec.get("exec_ctx"), dict) else 0,
                "ssl_level": dec.get("exec_ctx", {}).get("ssl_level", 0) if isinstance(dec.get("exec_ctx"), dict) else 0,
                "is_bsl_swept": dec.get("exec_ctx", {}).get("is_bsl_swept", False) if isinstance(dec.get("exec_ctx"), dict) else False,
                "is_ssl_swept": dec.get("exec_ctx", {}).get("is_ssl_swept", False) if isinstance(dec.get("exec_ctx"), dict) else False,
                "bearish_ob": dec.get("exec_ctx", {}).get("bearish_ob", "") if isinstance(dec.get("exec_ctx"), dict) else "",
                "bullish_ob": dec.get("exec_ctx", {}).get("bullish_ob", "") if isinstance(dec.get("exec_ctx"), dict) else "",
                "bearish_fvg": dec.get("exec_ctx", {}).get("bearish_fvg", "") if isinstance(dec.get("exec_ctx"), dict) else "",
                "bullish_fvg": dec.get("exec_ctx", {}).get("bullish_fvg", "") if isinstance(dec.get("exec_ctx"), dict) else "",
                "has_bot_div": dec.get("exec_ctx", {}).get("has_bot_div", False) if isinstance(dec.get("exec_ctx"), dict) else False,
                "has_top_div": dec.get("exec_ctx", {}).get("has_top_div", False) if isinstance(dec.get("exec_ctx"), dict) else False,
                "squeeze": dec.get("exec_ctx", {}).get("squeeze", "") if isinstance(dec.get("exec_ctx"), dict) else "",
                "htf_allowed": dec.get("exec_ctx", {}).get("htf_allowed", "") if isinstance(dec.get("exec_ctx"), dict) else "",
                "state": r.get("state", ""),
                "reason": r.get("reason", ""),
            }
            writer.writerow(row)
    print(f"  📝 CSV 日志已追加: {log_path} ({len(results)} 条)")


def main():
    results = run_once()
    for item in results:
        print(item)
    return results