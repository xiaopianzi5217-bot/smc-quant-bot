# -*- coding: utf-8 -*-
"""
Outcome Attribution — 收益归因分析（V40 核心新增）

对每笔已平仓交易，将 realized R 按因子权重分解到 SMC / SQZMOM / Breakout / Regime 等维度。
支持单笔归因和批量汇总，帮助识别哪些因子真正贡献了利润。

设计原则：
  - 归因权重与 smc_impulse_engine 的信号权重对齐
  - 每笔交易归因后存入内存历史，支持 aggregate() 汇总
  - 不读写文件，完全由调用方决定持久化策略

用法：
    from analytics.outcome_attribution import OutcomeAttribution
    oa = OutcomeAttribution()
    # 单笔归因
    contrib = oa.attribute(feature={"smc": 0.72, "sqzmom": 0.55, "breakout": 0.30, "regime": 1.0}, realized_r=2.1)
    # 批量汇总
    summary = oa.aggregate()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
import math

from utils.safe import safe_float


# 基础归因权重（应与 smc_impulse_engine 的权重对齐）
DEFAULT_BASE_WEIGHTS = {
    "smc": 0.45,       # SMC 结构质量
    "sqzmom": 0.30,    # SQZMOM 动量/背离
    "breakout": 0.15,  # 突破概率
    "regime": 0.10,    # 市场状态
}


@dataclass
class AttributionRecord:
    """单笔交易的归因记录"""
    trade_id: str
    symbol: str
    direction: str
    realized_r: float
    max_drawdown: float
    contributions: Dict[str, float]  # 因子名 -> 贡献的 R
    weights_used: Dict[str, float]   # 实际使用的权重
    feature_strengths: Dict[str, float]  # 原始特征强度


class OutcomeAttribution:
    """收益归因分析器"""

    def __init__(self, base_weights: Optional[Dict[str, float]] = None):
        self.base_weights = base_weights or dict(DEFAULT_BASE_WEIGHTS)
        self.history: List[AttributionRecord] = []

    # ------------------------------------------------------------------
    #  单笔归因
    # ------------------------------------------------------------------
    def attribute(
        self,
        feature: Dict[str, Any],
        realized_r: float,
        trade_id: str = "",
        symbol: str = "",
        direction: str = "",
        max_drawdown: float = 0.0,
    ) -> Dict[str, float]:
        """对一笔已平仓交易做收益归因

        参数:
            feature: 信号特征字典，需包含 smc / sqzmom / breakout / regime 等键
            realized_r: 实际实现的 R 值
            trade_id: 交易 ID（可选）
            symbol: 交易对（可选）
            direction: 方向（可选）
            max_drawdown: 最大回撤（可选）

        返回:
            {"smc": 0.94, "sqzmom": 0.63, "breakout": 0.32, "regime": 0.21}
        """
        # 提取各因子强度（带安全转换）
        strengths = {}
        for key in self.base_weights:
            strengths[key] = safe_float(feature.get(key, 0.5), 0.5)

        # 计算裸贡献 = 权重 × 强度 × R
        raw_contrib = {}
        total_raw = 0.0
        for key, weight in self.base_weights.items():
            c = weight * strengths[key] * realized_r
            raw_contrib[key] = c
            total_raw += abs(c)

        # 归一化：确保 sum(contributions) ≈ realized_r，同时保留各因子相对比例
        # 使用 signed normalization 保持正负号
        if total_raw > 1e-12:
            # 按比例缩放使得总贡献 = realized_r
            scale = realized_r / (sum(raw_contrib.values()) if sum(raw_contrib.values()) != 0 else 1.0)
            contributions = {k: round(v * scale, 4) for k, v in raw_contrib.items()}
        else:
            contributions = {k: 0.0 for k in self.base_weights}

        # 记录到历史
        record = AttributionRecord(
            trade_id=trade_id or f"trade_{len(self.history)}",
            symbol=symbol,
            direction=direction,
            realized_r=round(realized_r, 4),
            max_drawdown=round(max_drawdown, 4),
            contributions=contributions,
            weights_used=dict(self.base_weights),
            feature_strengths={k: round(v, 4) for k, v in strengths.items()},
        )
        self.history.append(record)

        return contributions

    # ------------------------------------------------------------------
    #  批量归因（从历史记录列表一次性导入）
    # ------------------------------------------------------------------
    def batch_attribute(
        self,
        trades: List[Dict[str, Any]],
    ) -> List[Dict[str, float]]:
        """批量归因多笔交易

        参数:
            trades: 每笔交易需包含 feature / realized_r / trade_id / symbol / direction

        返回:
            归因结果列表
        """
        results = []
        for t in trades:
            contrib = self.attribute(
                feature=t.get("feature", {}),
                realized_r=safe_float(t.get("realized_r", 0.0), 0.0),
                trade_id=t.get("trade_id", ""),
                symbol=t.get("symbol", ""),
                direction=t.get("direction", ""),
                max_drawdown=safe_float(t.get("max_drawdown", 0.0), 0.0),
            )
            results.append(contrib)
        return results

    # ------------------------------------------------------------------
    #  汇总统计
    # ------------------------------------------------------------------
    def aggregate(self) -> Dict[str, Any]:
        """汇总所有已记录的归因结果

        返回:
            {
                "total_trades": int,
                "total_realized_r": float,
                "total_contributions": {"smc": 12.3, "sqzmom": 8.5, ...},
                "avg_contributions": {"smc": 0.42, ...},
                "contribution_pct": {"smc": 38.2, "sqzmom": 26.4, ...},
                "win_rate_by_factor": {"smc": 0.65, ...},
                "factor_correlation": {...}
            }
        """
        if not self.history:
            return {
                "total_trades": 0,
                "total_realized_r": 0.0,
                "total_contributions": {},
                "avg_contributions": {},
                "contribution_pct": {},
                "win_rate_by_factor": {},
            }

        n = len(self.history)
        total_r = sum(r.realized_r for r in self.history)

        # 各因子累计贡献
        total_contrib: Dict[str, float] = {}
        for r in self.history:
            for k, v in r.contributions.items():
                total_contrib[k] = total_contrib.get(k, 0.0) + v

        avg_contrib = {k: round(v / n, 4) for k, v in total_contrib.items()}

        # 贡献占比（绝对值占比）
        abs_total = sum(abs(v) for v in total_contrib.values()) or 1.0
        contrib_pct = {k: round(abs(v) / abs_total * 100, 1) for k, v in total_contrib.items()}

        # 各因子胜率：该因子贡献 > 0 的交易占比
        win_by_factor: Dict[str, int] = {}
        for r in self.history:
            for k, v in r.contributions.items():
                if v > 0:
                    win_by_factor[k] = win_by_factor.get(k, 0) + 1
        win_rate_by_factor = {k: round(v / n, 4) for k, v in win_by_factor.items()}

        return {
            "total_trades": n,
            "total_realized_r": round(total_r, 4),
            "total_contributions": {k: round(v, 4) for k, v in total_contrib.items()},
            "avg_contributions": avg_contrib,
            "contribution_pct": contrib_pct,
            "win_rate_by_factor": win_rate_by_factor,
        }


    # ------------------------------------------------------------------
    #  内部：从记录列表生成分组统计
    # ------------------------------------------------------------------
    @staticmethod
    def _group_stats(records):
        """从一组记录生成统计字典（内部复用）"""
        if not records:
            return {
                "total_trades": 0,
                "total_realized_r": 0.0,
                "avg_realized_r": 0.0,
                "win_rate": 0.0,
                "total_contributions": {},
                "contribution_pct": {},
                "win_rate_by_factor": {},
            }

        n = len(records)
        total_r = sum(r.realized_r for r in records)
        wins = sum(1 for r in records if r.realized_r > 0)

        total_contrib = {}
        for r in records:
            for k, v in r.contributions.items():
                total_contrib[k] = total_contrib.get(k, 0.0) + v

        abs_total = sum(abs(v) for v in total_contrib.values()) or 1.0
        contrib_pct = {k: round(abs(v) / abs_total * 100, 1) for k, v in total_contrib.items()}

        win_by_factor = {}
        for r in records:
            for k, v in r.contributions.items():
                if v > 0:
                    win_by_factor[k] = win_by_factor.get(k, 0) + 1
        win_rate_by_factor = {k: round(v / n, 4) for k, v in win_by_factor.items()}

        return {
            "total_trades": n,
            "total_realized_r": round(total_r, 4),
            "avg_realized_r": round(total_r / n, 4),
            "win_rate": round(wins / n, 4),
            "total_contributions": {k: round(v, 4) for k, v in total_contrib.items()},
            "contribution_pct": contrib_pct,
            "win_rate_by_factor": win_rate_by_factor,
        }

    # ------------------------------------------------------------------
    #  按币种分组归因
    # ------------------------------------------------------------------
    def aggregate_by_symbol(self):
        """按币种分组归因——识别哪些品种贡献最大"""
        groups = {}
        for r in self.history:
            sym = r.symbol or "UNKNOWN"
            groups.setdefault(sym, []).append(r)
        return {
            sym: self._group_stats(recs)
            for sym, recs in sorted(groups.items(), key=lambda x: -sum(r.realized_r for r in x[1]))
        }

    # ------------------------------------------------------------------
    #  按方向分组归因
    # ------------------------------------------------------------------
    def aggregate_by_direction(self):
        """按方向（LONG / SHORT）分组归因"""
        groups = {}
        for r in self.history:
            d = r.direction or "UNKNOWN"
            groups.setdefault(d.upper(), []).append(r)
        return {
            d: self._group_stats(recs)
            for d, recs in sorted(groups.items(), key=lambda x: -sum(r.realized_r for r in x[1]))
        }

    # ------------------------------------------------------------------
    #  按市场状态分组归因
    # ------------------------------------------------------------------
    def aggregate_by_regime(self):
        """按市场状态（TREND / CHOP / TRANSITION）分组归因"""
        def map_regime(val):
            if isinstance(val, str):
                return val.upper()
            if val >= 0.8:
                return "TREND"
            elif val <= 0.4:
                return "CHOP"
            else:
                return "TRANSITION"

        groups = {}
        for r in self.history:
            raw_regime = r.feature_strengths.get("regime", 0.5)
            regime = map_regime(raw_regime)
            groups.setdefault(regime, []).append(r)

        return {
            regime: self._group_stats(recs)
            for regime, recs in sorted(groups.items(), key=lambda x: -sum(r.realized_r for r in x[1]))
        }
    # ------------------------------------------------------------------
    #  工具方法
    # ------------------------------------------------------------------
    def reset(self) -> None:
        """清空历史记录"""
        self.history.clear()

    def get_history(self) -> List[Dict[str, Any]]:
        """获取所有归因记录（可序列化）"""
        return [
            {
                "trade_id": r.trade_id,
                "symbol": r.symbol,
                "direction": r.direction,
                "realized_r": r.realized_r,
                "max_drawdown": r.max_drawdown,
                "contributions": r.contributions,
                "weights_used": r.weights_used,
                "feature_strengths": r.feature_strengths,
            }
            for r in self.history
        ]


# 全局单例
outcome_attribution = OutcomeAttribution()

