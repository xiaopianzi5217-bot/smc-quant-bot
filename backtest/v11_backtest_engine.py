# -*- coding: utf-8 -*-
"""
回测系统 v1 — 对历史 K 线逐 K 线回放 evaluate_symbol 逻辑

核心原理：
    把 evaluate_symbol 视为纯函数，对历史 K 线的每个时刻回放：
    1. 在每个 15M K 线收盘时执行 evaluate_symbol
    2. 如果有开仓信号，模拟入场
    3. 跟踪持仓，直到止损/止盈/超时
    4. 记录每笔交易的 pnl_r → 喂给 EVLearner + adaptive_calibrator

数据流：
    BTCUSDT_15M_365d.csv (35040 行) → add_all_indicators → 
    逐 K 线回放 → evaluate_symbol → 开/平仓记录 → 
    trades_features.csv → EVLearner 学习 → adaptive_calibrator 调参
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from indicators.basic import add_all_indicators
from strategy.smc import build_macro_context, build_exec_context
from strategy.scoring import adaptive_signal_score
from strategy.risk import calculate_dynamic_tp_sl
from strategy.trade_filters import mark_strategy_approval, check_strategy_filters
from strategy.intelligence_engine import estimate_expected_value, ev_learner
from notifier.observer.risk_plan import build_rr_plan
from config import SYMBOL_STRATEGY
from utils.symbols import load_symbol_strategy
from utils.ctx_builder import _enrich_common_fields, build_directional_contexts
from risk.global_risk import GlobalRiskGuard
from risk.portfolio_state import PortfolioStateManager
from utils.structured_logger import slog
from decision.v9_decision_kernel import V9DecisionKernel
from optimizer.adaptive_calibrator import run_auto_calibrate


# =============================================================
# 配置
# =============================================================
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DEFAULT_15M_CSV = DATA_DIR / "BTCUSDT_15M_365d.csv"
DEFAULT_1H_CSV = DATA_DIR / "BTCUSDT_1H_365d.csv"
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "v11_full_config.json"
OUTPUT_DIR = Path(__file__).resolve().parents[1] / "backtest" / "outputs"
FEATURE_STORE_PATH = Path(__file__).resolve().parents[1] / "data" / "features" / "trades_features.csv"


# =============================================================
# 1. 模拟持仓
# =============================================================
class BacktestPosition:
    """模拟持仓"""
    def __init__(
        self,
        entry_time: int,
        entry_price: float,
        direction: str,
        sl: float,
        tp1: float,
        tp2: float,
        tp3: float,
        rr: float,
        score: float,
        regime: str,
    ):
        self.entry_time = entry_time
        self.entry_price = entry_price
        self.direction = direction
        self.sl = sl
        self.tp1 = tp1
        self.tp2 = tp2
        self.tp3 = tp3
        self.rr = rr
        self.score = score
        self.regime = regime
        self.exit_time: Optional[int] = None
        self.exit_price: Optional[float] = None
        self.pnl_r: Optional[float] = None
        self.max_r = 0.0  # 最大浮盈（R 倍数）
        self.min_r = 0.0  # 最大浮亏（R 倍数）
        self.exit_reason: str = "OPEN"
        self.bars_held = 0

    def update(self, high: float, low: float, close: float):
        """更新浮盈浮亏"""
        self.bars_held += 1
        risk = abs(self.entry_price - self.sl)
        if risk == 0:
            return
        
        if self.direction == "Long":
            current_r = (close - self.entry_price) / risk
            high_r = (high - self.entry_price) / risk
            low_r = (low - self.entry_price) / risk
        else:
            current_r = (self.entry_price - close) / risk
            high_r = (self.entry_price - low) / risk
            low_r = (self.entry_price - high) / risk
        
        self.max_r = max(self.max_r, high_r)
        self.min_r = min(self.min_r, low_r)

        # 止损检查
        if self.direction == "Long" and low <= self.sl:
            self.exit_price = min(close, self.sl)
            self.pnl_r = -1.0
            self.exit_reason = "SL"
            return True
        
        if self.direction == "Short" and high >= self.sl:
            self.exit_price = max(close, self.sl)
            self.pnl_r = -1.0
            self.exit_reason = "SL"
            return True
        
        # TP1 检查（止盈 50%）
        if self.direction == "Long" and high >= self.tp1:
            self.exit_price = self.tp1
            self.pnl_r = self.rr
            self.exit_reason = "TP1"
            return True
        
        if self.direction == "Short" and low <= self.tp1:
            self.exit_price = self.tp1
            self.pnl_r = self.rr
            self.exit_reason = "TP1"
            return True
        
        # 超时检查（96 bars = 24h）
        if self.bars_held >= 96:
            self.exit_price = close
            # 按当前收盘价计算盈亏
            risk = abs(self.entry_price - self.sl)
            if self.direction == "Long":
                self.pnl_r = (close - self.entry_price) / risk if risk > 0 else 0
            else:
                self.pnl_r = (self.entry_price - close) / risk if risk > 0 else 0
            self.exit_reason = "TIMEOUT"
            return True
        
        return False

    def close(self, price: float, reason: str = "MANUAL"):
        """手动平仓"""
        self.exit_price = price
        risk = abs(self.entry_price - self.sl)
        if self.direction == "Long":
            self.pnl_r = (price - self.entry_price) / risk if risk > 0 else 0
        else:
            self.pnl_r = (self.entry_price - price) / risk if risk > 0 else 0
        self.exit_reason = reason

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": "BTC/USDT",
            "direction": self.direction,
            "entry": self.entry_price,
            "sl": self.sl,
            "tp1": self.tp1,
            "rr": self.rr,
            "ev": 0.0,
            "score": self.score,
            "regime": self.regime,
            "exit_reason": self.exit_reason,
            "pnl_r": round(self.pnl_r, 4) if self.pnl_r is not None else None,
            "max_r": round(self.max_r, 4),
            "min_r": round(self.min_r, 4),
            "bars_held": self.bars_held,
        }


# =============================================================
# 2. 简化版 evaluate_symbol（无推送、无日志、无 Telegram）
# =============================================================
def backtest_evaluate_symbol(
    symbol: str,
    cfg: Dict[str, Any],
    curr,
    macro_ctx: Dict[str, Any],
    exec_ctx: Dict[str, Any],
    df_exec: pd.DataFrame,
) -> Tuple[Optional[Dict[str, Any]], Optional[BacktestPosition]]:
    """
    回测版的 evaluate_symbol（纯函数，不需重复 build_macro/build_exec）

    参数:
        symbol: 交易对
        cfg: 配置
        curr: 当前最新 K 线（Series）
        macro_ctx: 预计算的宏观上下文
        exec_ctx: 预计算的执行上下文
        df_exec: 完整 15M 数据（用于 rolling 计算）

    返回:
        (decision_dict, position_or_None)
    """
    try:
        price = float(curr["close"])
        atr = float(curr.get("ATRr_14", curr.get("atr", 0)) or 0)

        # OB 距离判断
        if atr > 0:
            bull_ob = exec_ctx.get("bullish_ob")
            if bull_ob and isinstance(bull_ob, (list, tuple)) and len(bull_ob) >= 2:
                try:
                    ob_max, ob_min = max(float(bull_ob[0]), float(bull_ob[1])), min(float(bull_ob[0]), float(bull_ob[1]))
                    if (ob_min - atr) <= price <= (ob_max + atr):
                        exec_ctx["near_bullish_ob"] = True
                except Exception:
                    pass
            bear_ob = exec_ctx.get("bearish_ob")
            if bear_ob and isinstance(bear_ob, (list, tuple)) and len(bear_ob) >= 2:
                try:
                    ob_max, ob_min = max(float(bear_ob[0]), float(bear_ob[1])), min(float(bear_ob[0]), float(bear_ob[1]))
                    if (ob_min - atr) <= price <= (ob_max + atr):
                        exec_ctx["near_bearish_ob"] = True
                except Exception:
                    pass

        exec_ctx["symbol"] = symbol

        # Volume context
        avg_vol = df_exec["volume"].rolling(20).mean().iloc[-1]
        volume_ratio = float(curr["volume"] / avg_vol) if avg_vol == avg_vol and avg_vol > 0 else 0.0
        exec_ctx["volume_ratio"] = volume_ratio
        exec_ctx["volume_confirmed"] = bool(volume_ratio > 1.5)
        is_vol = bool(volume_ratio > 1.5)

        # 评分上下文
        _enrich_common_fields(exec_ctx, curr, macro_ctx)
        long_ctx, short_ctx = build_directional_contexts(exec_ctx, curr)
        l_score, l_thresh, l_reasons = adaptive_signal_score(long_ctx, macro_ctx, "Long", is_vol)
        s_score, s_thresh, s_reasons = adaptive_signal_score(short_ctx, macro_ctx, "Short", is_vol)

        # HTF 方向融合
        htf_allowed = str(macro_ctx.get("allowed_direction", "Both")).strip()
        htf_adx = float(exec_ctx.get("adx", macro_ctx.get("ADX_14", macro_ctx.get("adx", 0))))
        htf_weight = min(0.9, max(0.3, htf_adx / 45.0)) if htf_adx >= 18 else max(0.1, htf_adx / 40.0)
        score_edge = abs(l_score - s_score)
        m15_weight = min(0.9, max(0.3, score_edge / 30.0))
        htf_vote = -1 if htf_allowed == "Short" else (1 if htf_allowed == "Long" else 0)
        m15_vote = 1 if (l_score >= s_score) else -1
        small_edge = score_edge < 5.0

        if htf_allowed == "Both" and small_edge:
            direction = "Long" if l_score >= s_score else "Short"
        else:
            total_vote = htf_vote * htf_weight + m15_vote * m15_weight
            if total_vote > 0.3:
                direction = "Long"
            elif total_vote < -0.3:
                direction = "Short"
            else:
                direction = "Long" if l_score >= s_score else "Short"

        exec_ctx["htf_forced_direction"] = direction
        sym_strategy = load_symbol_strategy(symbol, SYMBOL_STRATEGY)
        min_rr = sym_strategy.get("min_rr", cfg.get("risk", {}).get("min_rr", 2.0))
        sl, tp1, tp2, tp3, rr = calculate_dynamic_tp_sl(
            direction, curr, df_exec, exec_ctx, min_rr, sym_strategy
        )

        # EV 计算
        _long_sig = {
            "score_raw": l_score, "score": l_score,
            "smc": float(l_reasons.get("smc", 0)) if isinstance(l_reasons, dict) else 0,
            "sqzmom": float(l_reasons.get("sqzmom", 0)) if isinstance(l_reasons, dict) else 0,
            "breakout": float(l_reasons.get("breakout", 0)) if isinstance(l_reasons, dict) else 0,
            "raw_base": float(l_reasons.get("raw_base", 0)) if isinstance(l_reasons, dict) else 0,
            "base_trigger_passed": l_score >= l_thresh,
            "fallback_active": bool(l_reasons.get("fallback_active", False)) if isinstance(l_reasons, dict) else False,
            "direction": "Long", "ev_grade": "C_EV",
            "entry_meta": long_ctx,
            "estimated_rr": rr,
        }
        _short_sig = {
            "score_raw": s_score, "score": s_score,
            "smc": float(s_reasons.get("smc", 0)) if isinstance(s_reasons, dict) else 0,
            "sqzmom": float(s_reasons.get("sqzmom", 0)) if isinstance(s_reasons, dict) else 0,
            "breakout": float(s_reasons.get("breakout", 0)) if isinstance(s_reasons, dict) else 0,
            "raw_base": float(s_reasons.get("raw_base", 0)) if isinstance(s_reasons, dict) else 0,
            "base_trigger_passed": s_score >= s_thresh,
            "fallback_active": bool(s_reasons.get("fallback_active", False)) if isinstance(s_reasons, dict) else False,
            "direction": "Short", "ev_grade": "C_EV",
            "entry_meta": short_ctx,
            "estimated_rr": rr,
        }
        _regime_str = str(macro_ctx.get("regime", exec_ctx.get("regime", "")))
        _vol_str = str(macro_ctx.get("vol_state", "NORMAL_VOL"))
        long_ev = estimate_expected_value(_long_sig, _regime_str, _vol_str, long_ctx).get("expected_value", 0.0)
        short_ev = estimate_expected_value(_short_sig, _regime_str, _vol_str, short_ctx).get("expected_value", 0.0)

        # Decision
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
        decision["risk_plan"] = build_rr_plan(direction, price, sl, tp1, tp2, tp3)
        decision["entry"] = price
        decision["entry_price"] = price
        decision["stop_loss"] = sl
        decision["take_profit_1"] = tp1
        decision["rr_calculated"] = rr
        decision["htf_allowed"] = htf_allowed
        decision["exec_ctx"] = dict(exec_ctx)
        decision["score"] = l_score if direction == "Long" else s_score

        # 策略过滤
        if decision.get("approved"):
            filter_result = check_strategy_filters({
                "symbol": symbol, "curr": curr,
                "macro_ctx": macro_ctx, "exec_ctx": exec_ctx,
                "decision": decision, "cfg": cfg,
            })
            decision["strategy_filters"] = filter_result
            if not filter_result.get("approved", filter_result.get("allowed", False)):
                decision["approved"] = False
                decision["state"] = "STRATEGY_FILTER_BLOCKED"

        # 组合风控（简化：只检查最大持仓）
        if decision.get("approved"):
            guard = GlobalRiskGuard(cfg)
            portfolio_state = PortfolioStateManager().load()
            portfolio_check = guard.check(portfolio_state)
            if not portfolio_check.get("allowed"):
                decision["approved"] = False
                decision["state"] = "PORTFOLIO_BLOCKED"

        if decision.get("approved"):
            position = BacktestPosition(
                entry_time=int(curr.get("timestamp", 0)),
                entry_price=price,
                direction=direction,
                sl=sl,
                tp1=tp1,
                tp2=tp2,
                tp3=tp3,
                rr=rr,
                score=l_score if direction == "Long" else s_score,
                regime=_regime_str if _regime_str else str(exec_ctx.get("regime", "unknown")),
            )
            return decision, position

        return decision, None

    except Exception as e:
        print(f"[回测] evaluate 异常: {e}")
        return None, None


# =============================================================
# 3. 回测主循环
# =============================================================
def run_backtest(
    symbol: str = "BTC/USDT",
    start_idx: int = 0,
    end_idx: Optional[int] = None,
    warmup: int = 300,
    config_path: Optional[Path] = None,
    max_positions: int = 2,
) -> Dict[str, Any]:
    """
    回测主入口
    
    参数:
        symbol: 交易对
        start_idx: 起始 K 线索引
        end_idx: 结束 K 线索引
        warmup: 指标预热 K 线数
        config_path: 配置文件路径
        max_positions: 最大同时持仓数
    
    返回:
        回测统计结果
    """
    cfg_path = config_path or CONFIG_PATH
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # 加载数据
    df_exec = pd.read_csv(DEFAULT_15M_CSV)
    df_macro = pd.read_csv(DEFAULT_1H_CSV)

    # 统一时间格式
    for df in [df_exec, df_macro]:
        if "datetime" in df.columns:
            df["datetime"] = pd.to_datetime(df["datetime"])
        else:
            df["timestamp"] = pd.to_numeric(df["ts"], errors="coerce")
            df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")

    # 排序
    df_exec = df_exec.sort_values("datetime").reset_index(drop=True)
    df_macro = df_macro.sort_values("datetime").reset_index(drop=True)

    # 截取范围
    if end_idx is None:
        end_idx = len(df_exec)
    df_exec = df_exec.iloc[start_idx:end_idx].reset_index(drop=True)

    print(f"[回测] 数据: {len(df_exec)} 条 15M K 线, {len(df_macro)} 条 1H K 线")
    print(f"[回测] 范围: {df_exec['datetime'].min()} ~ {df_exec['datetime'].max()}")

    # 加载 wvf 参数
    wvf = cfg.get("strategy_params", {}).get("wvf_std_mult", 2.0)

    # 添加指标
    df_exec = add_all_indicators(df_exec, wvf)
    df_macro = add_all_indicators(df_macro, wvf)

    # 预计算 SMC 执行上下文（一次 build_exec_context ≈ 5秒）
    print(f"[回测] 预计算 SMC 执行上下文...")
    smc_exec_full = build_exec_context(df_exec)

    # 回测状态
    positions: List[BacktestPosition] = []
    trades: List[Dict[str, Any]] = []
    total_bars = len(df_exec)
    scan_count = 0
    macro_ctx: Optional[Dict[str, Any]] = None
    last_scan_idx = -1  # 上次扫描的索引，用于判断是否需要更新 exec_ctx

    # 对回测模式下更新的 exec_ctx 字段做逐个 K 线修正
    # （build_exec_context 返回的是最后一根 K 线的快照，需覆盖 K 线级别的动态字段）
    def _patch_exec_ctx(base_ctx: Dict[str, Any], bar_idx: int) -> Dict[str, Any]:
        """用预计算的 SMC 上下文 + 当前 K 线的动态字段构建 exec_ctx"""
        ctx = dict(base_ctx)  # 浅拷贝
        bar = df_exec.iloc[bar_idx]
        # K 线级字段需要实时更新
        ctx['kline_long_ok'] = bool(bar.get('kline_long_ok', False) or False)
        ctx['kline_short_ok'] = bool(bar.get('kline_short_ok', False) or False)
        ctx['sqzmom_color'] = str(bar.get('sqzmom_color', 'white'))
        ctx['close'] = float(bar['close'])
        ctx['volume_ratio'] = float(bar.get('volume_ratio', 1.0))
        # 从 regime_info 取 regime
        regime_info = ctx.get('regime_info', {})
        ctx['regime'] = str(regime_info.get('regime', 'unknown'))
        return ctx

    # 回测采样步长：每 4 根 K 线（=1小时）扫描一次
    SCAN_STEP = 4
    print(f"[回测] 开始逐 K 线回放（每 {SCAN_STEP} 根 K 线扫描一次）...")

    for i in range(warmup, total_bars):
        if i % 5000 == 0:
            print(f"[回测] 进度: {i}/{total_bars} ({100*i/total_bars:.0f}%)")

        # 每根 K 线更新现有持仓
        curr_bar = df_exec.iloc[i]
        high, low, close = float(curr_bar["high"]), float(curr_bar["low"]), float(curr_bar["close"])
        ts = int(curr_bar.get("timestamp", i))

        for pos in positions[:]:
            if pos.exit_reason != "OPEN":
                continue
            exited = pos.update(high, low, close)
            if exited:
                trade = pos.to_dict()
                trade["exit_time"] = ts
                trade["timestamp"] = ts
                trades.append(trade)
                # 记录到 EVLearner
                try:
                    ev_learner.record_trade(
                        regime=pos.regime,
                        setup_type="V37_CORE",
                        won=pos.pnl_r is not None and pos.pnl_r > 0,
                        realized_r=pos.pnl_r,
                        estimated_rr=pos.rr,
                    )
                except Exception:
                    pass
                positions.remove(pos)

        # 只在扫描点执行 evaluate（每 SCAN_STEP 根 K 线）
        if i % SCAN_STEP != 0:
            continue

        # 更新 macro_ctx（每 8 根 K 线 = 2小时更新一次）
        if macro_ctx is None or i % (SCAN_STEP * 2) == 0:
            curr_dt = curr_bar["datetime"]
            mask = df_macro["datetime"] <= curr_dt
            if mask.any():
                macro_idx = mask.sum() - 1
                df_macro_slice = df_macro.iloc[:macro_idx + 1]
                if len(df_macro_slice) >= 20:
                    macro_ctx = build_macro_context(df_macro_slice)

        if len(positions) >= max_positions:
            continue  # 满仓

        # 构建 exec_ctx（预计算一次 ≈5秒，循环中只做轻量修正）
        exec_ctx = _patch_exec_ctx(smc_exec_full, i)

        # 执行 evaluate
        decision, position = backtest_evaluate_symbol(
            symbol, cfg,
            curr=curr_bar,
            macro_ctx=macro_ctx or {},
            exec_ctx=exec_ctx,
            df_exec=df_exec,  # 传完整 df，rolling 只取 tail(20) 不贵
        )
        scan_count += 1

        if position is not None:
            # 记录开单时的 EV 预测值
            _ev_for_trade = None
            if decision and "ev_info" in decision:
                _ev_for_trade = decision["ev_info"].get("expected_value")
            elif decision and "expected_value" in decision:
                _ev_for_trade = decision.get("expected_value")
            position._ev_at_entry = _ev_for_trade
            positions.append(position)

    # 平掉所有剩余持仓
    last_bar = df_exec.iloc[-1]
    last_close = float(last_bar["close"])
    for pos in positions:
        pos.close(last_close, "END_OF_BACKTEST")
        trade = pos.to_dict()
        trade["exit_time"] = int(last_bar.get("timestamp", total_bars))
        trade["timestamp"] = total_bars
        trades.append(trade)
        try:
            ev_learner.record_trade(
                regime=pos.regime,
                setup_type="V37_CORE",
                won=pos.pnl_r is not None and pos.pnl_r > 0,
                ev=getattr(pos, '_ev_at_entry', None),
                realized_r=pos.pnl_r,
                estimated_rr=pos.rr,
            )
        except Exception:
            pass

    print(f"[回测] 完成: {len(trades)} 笔交易, {scan_count} 次扫描")

    # 保存结果
    return _summarize_backtest(trades, df_exec, cfg)


# =============================================================
# 4. 统计汇总
# =============================================================
def _summarize_backtest(
    trades: List[Dict[str, Any]],
    df: pd.DataFrame,
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """回测统计汇总"""
    if not trades:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "avg_r": 0.0,
            "sharpe": 0.0,
            "profit_factor": 0.0,
            "max_consecutive_losses": 0,
            "total_r": 0.0,
        }

    df_trades = pd.DataFrame(trades)
    df_trades = df_trades[df_trades["pnl_r"].notna()].copy()

    if df_trades.empty:
        return {"total_trades": 0}

    n = len(df_trades)
    wins = df_trades[df_trades["pnl_r"] > 0]
    losses = df_trades[df_trades["pnl_r"] < 0]
    wr = len(wins) / n if n > 0 else 0
    avg = float(df_trades["pnl_r"].mean())
    total_r = float(df_trades["pnl_r"].sum())

    # Sharpe
    std = float(df_trades["pnl_r"].std())
    sharpe = (avg / std * (365 * 96 / n) ** 0.5) if std > 0 and n > 0 else 0

    # 最大连续亏损
    consec_loss = 0
    max_consec_loss = 0
    for pnl in df_trades["pnl_r"]:
        if pnl < 0:
            consec_loss += 1
            max_consec_loss = max(max_consec_loss, consec_loss)
        else:
            consec_loss = 0

    # Profit Factor
    total_win = float(wins["pnl_r"].sum()) if len(wins) > 0 else 0
    total_loss = abs(float(losses["pnl_r"].sum())) if len(losses) > 0 else 1
    pf = total_win / total_loss if total_loss > 0 else 999

    # 按 exit_reason 统计
    reason_stats = {}
    for reason in df_trades["exit_reason"].unique():
        subset = df_trades[df_trades["exit_reason"] == reason]
        reason_stats[reason] = {
            "count": len(subset),
            "avg_r": round(float(subset["pnl_r"].mean()), 4),
            "win_rate": round(len(subset[subset["pnl_r"] > 0]) / len(subset), 4),
        }

    result = {
        "total_trades": n,
        "win_rate": round(wr, 4),
        "avg_r": round(avg, 4),
        "total_r": round(total_r, 4),
        "sharpe": round(sharpe, 4),
        "profit_factor": round(pf, 4),
        "max_consecutive_losses": max_consec_loss,
        "avg_bars_held": round(float(df_trades["bars_held"].mean()), 1),
        "by_exit_reason": reason_stats,
        "score_threshold": cfg.get("strategy_params", {}).get("score_base_threshold", 20),
        "min_rr": cfg.get("risk", {}).get("min_rr", 1.35),
    }

    return result


# =============================================================
# 5. 保存回测结果到 feature_store
# =============================================================
def save_backtest_trades(trades: List[Dict[str, Any]]):
    """将回测交易记录写入 feature_store（喂给 EVLearner 和 adaptive_calibrator）"""
    if not trades:
        print("[回测] 无交易记录可保存")
        return

    df = pd.DataFrame(trades)
    
    # 确保目录存在
    FEATURE_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    # 加载现有记录
    if FEATURE_STORE_PATH.exists():
        df_existing = pd.read_csv(FEATURE_STORE_PATH, encoding="utf-8")
        # 只保留 OPEN 的
        df_existing = df_existing[df_existing["exit_reason"] == "OPEN"]
        df = pd.concat([df_existing, df], ignore_index=True)
    
    df.to_csv(FEATURE_STORE_PATH, index=False, encoding="utf-8")
    print(f"[回测] 已保存 {len(trades)} 笔交易到 {FEATURE_STORE_PATH}")


# =============================================================
# 6. 一键回测
# =============================================================
def run_full_backtest(
    symbol: str = "BTC/USDT",
    warmup: int = 300,
    max_trades: Optional[int] = None,
    save_trades: bool = True,
    auto_calibrate: bool = True,
) -> Dict[str, Any]:
    """
    一键回测 + 自适应调参
    
    参数:
        symbol: 交易对
        warmup: 预热 K 线数
        max_trades: 最大交易笔数（None=不限）
        save_trades: 是否保存到 feature_store
        auto_calibrate: 是否自动调参
    
    返回:
        回测统计结果
    """
    print(f"\n{'='*60}")
    print(f"  回测开始: {symbol}")
    print(f"  预热: {warmup} bars")
    print(f"{'='*60}\n")

    # 回测
    result = run_backtest(
        symbol=symbol,
        warmup=warmup,
        max_positions=2,
    )

    print(f"\n{'='*60}")
    print(f"  回测结果")
    print(f"{'='*60}")
    print(f"  总交易: {result['total_trades']}")
    print(f"  胜率: {result['win_rate']*100:.1f}%")
    print(f"  平均 R: {result['avg_r']:.4f}")
    print(f"  总 R: {result['total_r']:.4f}")
    print(f"  Sharpe: {result['sharpe']:.4f}")
    print(f"  Profit Factor: {result['profit_factor']:.4f}")
    print(f"  最大连续亏损: {result['max_consecutive_losses']}")
    print(f"  平均持仓 bars: {result['avg_bars_held']:.1f}")

    if result.get("by_exit_reason"):
        print(f"\n  退出类型分布:")
        for reason, stats in result["by_exit_reason"].items():
            print(f"    {reason}: {stats['count']} 笔, avg_r={stats['avg_r']:.4f}, wr={stats['win_rate']*100:.1f}%")

    # 保存交易记录 → 喂给 EVLearner
    if save_trades and result["total_trades"] > 0:
        # 从回测过程中获取实际 trade dict 列表
        output_path = OUTPUT_DIR / "backtest_trades.csv"
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        
        # 从 feature_store 或临时文件读取
        if FEATURE_STORE_PATH.exists():
            df = pd.read_csv(FEATURE_STORE_PATH, encoding="utf-8")
            # 过滤出有 pnl_r 的
            df = df[df["pnl_r"].notna() & (df["pnl_r"] != 0)]
            df.to_csv(output_path, index=False, encoding="utf-8")
            print(f"\n[回测] 交易已保存到 {output_path}")

    # 自适应调参
    if auto_calibrate and result["total_trades"] >= 10:
        print(f"\n[回测] 触发自适应调参 ({result['total_trades']} 笔交易)...")
        try:
            cal_result = run_auto_calibrate()
            if cal_result.get("note") == "CALIBRATED":
                print(f"[回测] 参数优化: threshold={cal_result['threshold']}, min_rr={cal_result['min_rr']}")
                result["calibrated_params"] = {
                    "threshold": cal_result["threshold"],
                    "min_rr": cal_result["min_rr"],
                }
        except Exception as e:
            print(f"[回测] 自适应调参异常: {e}")

    return result


# =============================================================
# 主入口
# =============================================================
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="V11 回测系统")
    parser.add_argument("--warmup", type=int, default=300, help="预热 K 线数")
    parser.add_argument("--no-calibrate", action="store_true", help="禁用自适应调参")
    args = parser.parse_args()
    
    result = run_full_backtest(
        warmup=args.warmup,
        auto_calibrate=not args.no_calibrate,
    )
    
    print("\n=== 回测完成 ===")
