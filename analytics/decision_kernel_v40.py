# -*- coding: utf-8 -*-
"""
DecisionKernel V40 — 编排层

整合 V40 新增模块 + V38 现有模块的统一决策入口：
  ✅ ConfidenceEngine（新增）— 多因子可信度
  ✅ OutcomeAttribution（新增）— 收益归因（平仓后调用）
  ✅ RejectAnalytics（V38 复用）— 拒绝日志
  ✅ ExitReplayAnalyzer（V38 复用）— 退出参数回放

用法：
    from analytics.decision_kernel_v40 import DecisionKernelV40
    kernel = DecisionKernelV40()

    # 开单前评估
    result = kernel.evaluate(signal, ctx, exec_ctx)
    if result["action"] == "REJECT":
        print(f"拒绝原因: {result['reason']}")
    else:
        print(f"开单, 可信度: {result['confidence']}")

    # 平仓后归因
    kernel.attribute_close(trade, feature, realized_r)
    summary = kernel.attribution_summary()
"""

from __future__ import annotations

from typing import Any, Dict, Optional, List
from dataclasses import dataclass

from analytics.confidence_engine import ConfidenceEngine, ConfidenceResult
from analytics.outcome_attribution import OutcomeAttribution
from analytics.reject_analytics import RejectAnalytics
from analytics.exit_replay_analyzer import ExitReplayAnalyzer, ReplayTrade

from utils.safe import safe_float


@dataclass
class EvaluateResult:
    """V40 决策评估结果"""
    action: str                     # "OPEN" | "REJECT" | "HOLD"
    confidence: float               # 可信度 0~1
    confidence_result: ConfidenceResult  # 详细可信度因子
    reason: str                     # 决策原因
    size_multiplier: float          # 仓位乘数（基于可信度微调）
    extra: Dict[str, Any]           # 附加诊断数据


class DecisionKernelV40:
    """V40 编排层——整合所有 analytics 模块"""

    def __init__(self):
        # V40 新增模块
        self.confidence = ConfidenceEngine()
        self.attribution = OutcomeAttribution()

        # V38 现有模块（复用）
        self.reject = RejectAnalytics()

        # 配置
        self.min_ev_for_open = 0.05
        self.min_confidence = 0.40

    # ------------------------------------------------------------------
    #  开单前评估（核心入口）
    # ------------------------------------------------------------------
    def evaluate(
        self,
        signal: Dict[str, Any],
        ctx: Dict[str, Any],
        exec_ctx: Optional[Dict[str, Any]] = None,
    ) -> EvaluateResult:
        """开单前综合评估

        流程：
          1. 计算多因子可信度
          2. 如果 EV 不足，记录 Reject 日志，返回 REJECT
          3. 否则返回 OPEN（含可信度调整后的仓位乘数）

        参数:
            signal:   V37/V38 generate_signal() 的输出
            ctx:      上下文（含 regime, vol_state, feature 等）
            exec_ctx: 执行上下文（可选）

        返回:
            EvaluateResult
        """
        exec_ctx = exec_ctx or {}

        # ---- 提取关键字段 ----
        ev = safe_float(signal.get("expected_value", 0.0), 0.0)
        score = safe_float(signal.get("score", 0.0), 0.0)
        direction = signal.get("direction", "NONE")
        regime = str(ctx.get("regime", "UNKNOWN")).upper()

        # ---- 从信号中提取特征强度（用于归因和可信度） ----
        feature = self._extract_feature(signal, ctx)

        # ---- 计算可信度 ----
        # 从 signal 提取统计信息（这些应由调用方传入或从历史数据库获取）
        trades = int(safe_float(signal.get("v40_trades", ctx.get("v40_trades", 50)), 50))
        pf = safe_float(signal.get("v40_pf", ctx.get("v40_pf", 1.5)), 1.5)
        std_r = safe_float(signal.get("v40_std_r", ctx.get("v40_std_r", 0.8)), 0.8)
        same_regime = bool(signal.get("v40_same_regime", ctx.get("v40_same_regime", True)))

        conf_result = self.confidence.compute(
            trades=trades,
            pf=pf,
            std_r=std_r,
            same_regime=same_regime,
        )

        # ---- EV 门槛检查 ----
        if ev < self.min_ev_for_open:
            self.reject.log(
                symbol=str(ctx.get("symbol", "UNKNOWN")),
                reason="LOW_EV",
                feature=feature,
                ev_info={"expected_value": ev, "confidence": conf_result.confidence},
                extra={
                    "score": score,
                    "direction": direction,
                    "regime": regime,
                    "confidence": conf_result.confidence,
                    "reasons": conf_result.reasons,
                },
            )
            return EvaluateResult(
                action="REJECT",
                confidence=conf_result.confidence,
                confidence_result=conf_result,
                reason=f"LOW_EV_{round(ev, 4)}",
                size_multiplier=0.0,
                extra={"feature": feature},
            )

        # ---- 可信度检查 ----
        if conf_result.confidence < self.min_confidence:
            self.reject.log(
                symbol=str(ctx.get("symbol", "UNKNOWN")),
                reason="LOW_CONFIDENCE",
                feature=feature,
                ev_info={"expected_value": ev, "confidence": conf_result.confidence},
                extra={
                    "score": score,
                    "direction": direction,
                    "regime": regime,
                    "confidence": conf_result.confidence,
                },
            )
            return EvaluateResult(
                action="REJECT",
                confidence=conf_result.confidence,
                confidence_result=conf_result,
                reason=f"LOW_CONFIDENCE_{round(conf_result.confidence, 4)}",
                size_multiplier=0.0,
                extra={"feature": feature},
            )

        # ---- 通过：基于可信度微调仓位 ----
        # 可信度越高仓位越大，但保留原 size_multiplier 的 0.8~1.2 倍
        raw_mult = safe_float(signal.get("size_multiplier", 1.0), 1.0)
        conf_adjust = 0.8 + conf_result.confidence * 0.4  # 可信度 0.4→0.96, 0.99→1.196
        final_mult = round(raw_mult * conf_adjust, 4)

        return EvaluateResult(
            action="OPEN",
            confidence=conf_result.confidence,
            confidence_result=conf_result,
            reason=f"ALLOW_CONF={round(conf_result.confidence, 4)}_{'_'.join(conf_result.reasons)}",
            size_multiplier=final_mult,
            extra={
                "feature": feature,
                "ev": ev,
                "score": score,
            },
        )

    # ------------------------------------------------------------------
    #  平仓后归因
    # ------------------------------------------------------------------
    def attribute_close(
        self,
        trade: Dict[str, Any],
        feature: Optional[Dict[str, Any]] = None,
        realized_r: Optional[float] = None,
    ) -> Dict[str, float]:
        """平仓后调用：归因收益

        参数:
            trade: 交易记录字典，需含 feature / realized_r / trade_id / symbol / direction
            feature: 可选，覆盖 trade 中的 feature
            realized_r: 可选，覆盖 trade 中的 realized_r

        返回:
            {"smc": 0.94, "sqzmom": 0.63, ...}
        """
        f = feature or trade.get("feature", {})
        r = realized_r if realized_r is not None else safe_float(trade.get("realized_r", 0.0), 0.0)

        return self.attribution.attribute(
            feature=f,
            realized_r=r,
            trade_id=trade.get("trade_id", ""),
            symbol=trade.get("symbol", ""),
            direction=trade.get("direction", ""),
            max_drawdown=safe_float(trade.get("max_drawdown", 0.0), 0.0),
        )

    # ------------------------------------------------------------------
    #  退出参数回放（委托 V38 ExitReplayAnalyzer）
    # ------------------------------------------------------------------
    def replay_exit(
        self,
        entry: float,
        risk: float,
        atr: float,
        regime: str,
        price_history: List[float],
        impulse_strength: float = 0.5,
        trade_id: str = "",
        symbol: str = "",
        real_r: float = 0.0,
    ) -> Dict[str, Any]:
        """对一笔已完成交易的退出参数做回放分析

        直接委托 V38 ExitReplayAnalyzer.grid_search()
        """
        replay_trade = ReplayTrade(
            trade_id=trade_id or "v40_replay",
            symbol=symbol,
            entry=entry,
            risk=risk,
            atr=atr,
            regime=regime.upper(),
            impulse_strength=impulse_strength,
            price_history=price_history,
            real_r=real_r,
        )
        analyzer = ExitReplayAnalyzer(replay_trade)
        return analyzer.grid_search()

    # ------------------------------------------------------------------
    #  汇总查询
    # ------------------------------------------------------------------
    def attribution_summary(self) -> Dict[str, Any]:
        """获取归因汇总统计"""
        return self.attribution.aggregate()

    def get_reject_stats(self, **kwargs) -> Dict[str, Any]:
        """获取拒绝日志统计（委托 V38 RejectAnalytics）"""
        return self.reject.get_stats(**kwargs)

    # ------------------------------------------------------------------
    #  内部工具
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_feature(signal: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        """从 signal 和 ctx 中提取特征强度字典"""
        return {
            "smc": safe_float(signal.get("smc", ctx.get("smc", 0.5)), 0.5),
            "sqzmom": safe_float(signal.get("sqzmom", ctx.get("sqzmom", 0.5)), 0.5),
            "breakout": safe_float(signal.get("breakout", ctx.get("breakout", 0.3)), 0.3),
            "regime": safe_float(ctx.get("regime_score", 1.0), 1.0),
            "score_raw": safe_float(signal.get("score_raw", 0.0), 0.0),
            "score_norm": safe_float(signal.get("score", 0.0), 0.0),
        }
