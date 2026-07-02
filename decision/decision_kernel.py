# -*- coding: utf-8 -*-
"""
Decision Kernel — 统一决策入口

职责：
  1. Regime 检测
  2. EV 估算
  3. Feature 记录（入场）
  4. 拒绝决策（LOW_EV / LOW_CONF → RejectAnalytics）
  5. Kelly 仓位计算
  6. Regime 乘数调整
  7. 持仓管理代理（ExitManagerV38）
  8. 交易闭环（Outcome Learning + Feature 带结果保存）

依赖全部使用现有模块，不引入不存在的文件。
"""

from __future__ import annotations

import pandas as pd
from typing import Any, Dict, Optional

from risk.position_sizing import kelly_position_size
from strategy.regime import detect_market_regime, get_regime_multiplier
from analytics.reject_analytics import RejectAnalytics
from strategy.exit_manager_v38 import ExitManagerV38, ExitState
from strategy.intelligence_engine import estimate_expected_value
from feature_store import FeatureStore
from analytics.outcome_learning import OutcomeLearner


def _as_df(indicators: Any) -> Any:
    """统一 indicators 输入：dict → 包装为单行 DataFrame，DataFrame 原样返回"""
    if isinstance(indicators, pd.DataFrame):
        return indicators
    if isinstance(indicators, dict):
        # 包装为单行 DataFrame，满足 detect_market_regime 的 df.iloc[-1] 调用
        return pd.DataFrame([indicators])
    return indicators


class DecisionKernel:
    """统一决策入口：Regime → EV → Position Sizing → Exit"""

    def __init__(self):
        self.reject = RejectAnalytics()
        self.exit_mgr = ExitManagerV38()
        self.feature_store = FeatureStore()
        self.outcome = OutcomeLearner()

    # ------------------------------------------------------------------
    #  决策入口
    # ------------------------------------------------------------------
    def decide(
        self,
        signal: Dict[str, Any],
        indicators: Any,
        balance: float = 10000.0,
    ) -> Dict[str, Any]:
        """决策入口

        参数:
            signal: 信号字典（含 symbol, score, entry_meta 等）
            indicators: 技术指标（dict 或 DataFrame，自动适配）
            balance: 账户余额

        返回:
            {"action": "OPEN"|"REJECT", "reason": str, ...}
        """
        symbol = signal.get("symbol", "UNKNOWN")

        # 1. Regime 检测（适配 dict 和 DataFrame 两种输入）
        df_ind = _as_df(indicators)
        regime_result = detect_market_regime(df_ind)
        regime_str = regime_result.get("regime", "transition")

        # 从原始 indicators 中提取 vol_state（可能是 dict 的 key 或 DataFrame 的列）
        if isinstance(indicators, dict):
            vol_state = indicators.get("vol_state", "NORMAL")
            volatility = indicators.get("volatility", "normal")
        else:
            vol_state = "NORMAL"
            volatility = regime_result.get("volatility", "normal")

        # 2. EV 估算
        ev_info = estimate_expected_value(
            signal, regime_str, vol_state,
        )

        # 3. 构建 Feature Vector（用于记录 + Outcome 学习）
        feature = {**signal, "regime": regime_str}

        # 4. 记录入场 Feature（不带结果）
        self.feature_store.save_trade({
            **feature,
            "exit_reason": "OPEN",
            "symbol": symbol,
            "ev": ev_info.get("expected_value"),
            "score": signal.get("score", signal.get("final_score", 0)),
        })

        # 5. 拒绝逻辑
        # 门槛对标 intelligence_engine 的 D_NEG_EV（EV <= 0.0）
        min_ev = 0.05
        min_conf = 0.30

        # confidence 可能为 None（intelligence_engine 不总是返回它）
        ev_confidence = ev_info.get("confidence")
        if ev_confidence is None:
            ev_confidence = 1.0  # 无 confidence 信息时不以此为由拒绝

        if ev_info["expected_value"] < min_ev or ev_confidence < min_conf:
            self.reject.log(symbol, "LOW_EV_CONF", feature, ev_info)
            return {
                "action": "REJECT",
                "reason": "LOW_EV_CONF",
                "symbol": symbol,
                "regime": regime_str,
                **ev_info,
            }

        # 6. Kelly 仓位计算
        pos = kelly_position_size(
            ev_info["expected_value"],
            ev_info.get("confidence", 0.5),
            balance,
        )

        # 7. Regime 乘数调整
        regime_mult = get_regime_multiplier(regime_str, volatility)
        pos["size_pct"] = round(pos["size_pct"] * regime_mult, 4)
        pos["risk_usd"] = round(balance * pos["size_pct"], 2)

        return {
            "action": "OPEN",
            "symbol": symbol,
            "regime": regime_str,
            "regime_mult": regime_mult,
            "position": pos,
            **ev_info,
        }

    # ------------------------------------------------------------------
    #  持仓管理代理
    # ------------------------------------------------------------------
    def manage_position(
        self,
        state: ExitState,
        current_price: float,
        entry: float,
        risk: float,
        atr: float,
        regime: str,
        impulse_strength: float = 0.5,
    ) -> ExitState:
        """持仓管理代理 → ExitManagerV38"""
        return self.exit_mgr.update(
            state, entry, current_price, risk, atr, regime, impulse_strength,
        )

    # ------------------------------------------------------------------
    #  交易闭环
    # ------------------------------------------------------------------
    def on_trade_close(
        self,
        feature: Dict[str, Any],
        realized_r: float,
        symbol: str,
    ) -> None:
        """交易闭环：Outcome 学习 + Feature 带结果保存"""
        # Outcome Learning（Feature Hash → 聚合统计）
        self.outcome.update_from_trade(feature, realized_r)

        # 带结果 Feature 存储
        self.feature_store.save_trade({
            **feature,
            "exit_reason": "CLOSE",
            "pnl_r": realized_r,
            "symbol": symbol,
        })
