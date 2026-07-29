# -*- coding: utf-8 -*-
"""
Exit Replay Analyzer — 退出参数回放分析

基于真实的 ExitManagerV38 退出逻辑做参数遍历回放，而不是乘数简化。
可用于：
  1. 对已完成交易的不同 exit 参数做反事实推演
  2. 对比 ATR 倍数 / 保本触发R / 不同 regime 下最优 trailing 参数
  3. 输出最优参数建议

设计原则：
  - 复用 ExitManagerV38.update() 的完整退出逻辑
  - 逐 tick/K 线模拟退出过程，而非直接乘 mean_r
  - 输出可行动的参数建议
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Callable

from strategy.exit_manager_v38 import ExitManagerV38, ExitState


# ================================================================
#  回放配置
# ================================================================

DEFAULT_PARAM_GRID = {
    "trail_atr_mult": [1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0],      # ATR 倍数
    "lock_trigger_r": [1.2, 1.5, 1.8, 2.0, 2.5],                  # 保本触发 R
    "lock_cushion_r": [0.02, 0.05, 0.10],                          # 保本后留多少空间
    "trail_regime_transition_mult": [2.0, 2.5, 3.0, 4.0, 5.0],    # TRANSITION 下的 trailing
    "trail_regime_chop_mult": [1.5, 2.0, 2.5, 3.0],               # CHOP 下的 trailing
}


@dataclass
class ReplayTrade:
    """一笔待回放的历史交易"""
    trade_id: str
    symbol: str
    entry: float
    risk: float          # 入场 - 初始止损
    atr: float
    regime: str
    impulse_strength: float
    # 价格序列（K 线收盘价或 tick 序列）
    price_history: List[float]
    # 真实实现 R
    real_r: float = 0.0


@dataclass
class ReplayResult:
    """单个参数组合的回放结果"""
    param_name: str
    params: Dict[str, Any]
    simulated_r: float
    max_drawdown: float  # 最大回撤（R 单位）
    exit_bar: int        # 在第几根 K 线退出
    locked: bool         # 是否触发了保本
    peak_r: float        # 周期内最高 R


class ExitReplayAnalyzer:
    """退出参数回放分析器"""

    def __init__(self, trade: ReplayTrade):
        self.trade = trade
        self.exit_mgr = ExitManagerV38()

    # ------------------------------------------------------------------
    #  单参数跑模拟（核心）
    # ------------------------------------------------------------------
    def simulate(
        self,
        trail_atr_mult: float = 5.0,
        lock_trigger_r: float = 1.9,
        lock_cushion_r: float = 0.05,
        trail_regime_transition_mult: float = 3.0,
        trail_regime_chop_mult: float = 2.0,
    ) -> ReplayResult:
        """用指定参数跑一次完整退出模拟

        模拟逻辑：
          1. 从 ExitState(stop_price=entry - risk) 开始
          2. 遍历 price_history 的每个价格
          3. 每次调用 ExitManagerV38.update() —— 但需要修改 trail_mult/lock 参数
          4. 价格穿透 stop_price 时退出，记录 R

        因为我们不能直接改 ExitManagerV38.update() 的硬编码参数，
        这里重新实现相同的退出逻辑，但使用可配置参数。
        """
        t = self.trade
        if not t.price_history:
            return ReplayResult(
                param_name="custom",
                params={},
                simulated_r=t.real_r,
                max_drawdown=0.0,
                exit_bar=0,
                locked=False,
                peak_r=0.0,
            )

        # 初始状态
        entry = t.entry
        risk = t.risk
        state = ExitState(stop_price=entry - risk)

        peak_r = 0.0
        exit_bar = len(t.price_history)  # 默认模拟到结束
        locked = False

        for i, price in enumerate(t.price_history):
            pnl_r = (price - entry) / risk if risk > 0 else 0.0
            peak_r = max(peak_r, pnl_r)

            # --- 保本锁仓（参数化版本） ---
            if pnl_r >= lock_trigger_r and not locked:
                state.stop_price = entry + risk * lock_cushion_r
                locked = True

            # --- 自适应 Trailing（参数化版本） ---
            regime_u = str(t.regime).upper()
            if regime_u == "TREND":
                trail_mult = trail_atr_mult
                if t.impulse_strength > 0.75:
                    trail_mult = trail_atr_mult * 1.3  # 按原逻辑比例放大
                state.stop_price = max(state.stop_price, price - t.atr * trail_mult)
            elif regime_u == "TRANSITION":
                trail = max(
                    t.atr * trail_regime_transition_mult,
                    peak_r * 0.15 * risk,
                )
                state.stop_price = max(state.stop_price, price - trail)
            else:  # CHOP / MUD / 其他
                trail = t.atr * trail_regime_chop_mult
                state.stop_price = max(state.stop_price, price - trail)

            # --- 止损检查 ---
            if price <= state.stop_price:
                exit_bar = i
                break

            # 更新 peak_r 到 state
            state.peak_r = peak_r

        # 计算最终 R
        if exit_bar < len(t.price_history):
            exit_price = t.price_history[exit_bar]
            simulated_r = (exit_price - entry) / risk if risk > 0 else 0.0
        else:
            simulated_r = (t.price_history[-1] - entry) / risk if risk > 0 else 0.0

        # 计算最大回撤（从 peak 到 exit）
        max_drawdown = peak_r - simulated_r

        return ReplayResult(
            param_name="custom",
            params={
                "trail_atr_mult": trail_atr_mult,
                "lock_trigger_r": lock_trigger_r,
                "lock_cushion_r": lock_cushion_r,
                "trail_regime_transition_mult": trail_regime_transition_mult,
                "trail_regime_chop_mult": trail_regime_chop_mult,
            },
            simulated_r=round(simulated_r, 4),
            max_drawdown=round(max_drawdown, 4),
            exit_bar=exit_bar,
            locked=locked,
            peak_r=round(peak_r, 4),
        )

    # ------------------------------------------------------------------
    #  参数网格搜索
    # ------------------------------------------------------------------
    def grid_search(
        self,
        param_grid: Optional[Dict[str, List[float]]] = None,
    ) -> Dict[str, Any]:
        """对参数网格进行全面搜索

        返回最佳参数组合 + 完整结果
        """
        if param_grid is None:
            param_grid = DEFAULT_PARAM_GRID

        results: List[ReplayResult] = []
        best_r = -999.0
        best_result = None

        # 遍历网格（谨慎控制组合数，防止爆炸）
        for trail_atr_mult in param_grid.get("trail_atr_mult", [5.0]):
            for lock_trig in param_grid.get("lock_trigger_r", [1.9]):
                for lock_cush in param_grid.get("lock_cushion_r", [0.05]):
                    for tr_mult in param_grid.get("trail_regime_transition_mult", [3.0]):
                        for chop_mult in param_grid.get("trail_regime_chop_mult", [2.0]):
                            result = self.simulate(
                                trail_atr_mult=trail_atr_mult,
                                lock_trigger_r=lock_trig,
                                lock_cushion_r=lock_cush,
                                trail_regime_transition_mult=tr_mult,
                                trail_regime_chop_mult=chop_mult,
                            )
                            results.append(result)
                            if result.simulated_r > best_r:
                                best_r = result.simulated_r
                                best_result = result

        # 按 simulated_r 排序
        results.sort(key=lambda x: x.simulated_r, reverse=True)

        improvement = round(best_r - self.trade.real_r, 4) if best_result else 0.0

        return {
            "trade_id": self.trade.trade_id,
            "symbol": self.trade.symbol,
            "regime": self.trade.regime,
            "real_r": round(self.trade.real_r, 4),
            "total_simulations": len(results),
            "best_result": {
                "simulated_r": round(best_result.simulated_r, 4) if best_result else None,
                "params": best_result.params if best_result else {},
                "improvement": improvement,
                "max_drawdown": round(best_result.max_drawdown, 4) if best_result else None,
                "locked": best_result.locked if best_result else False,
            },
            "top_5": [
                {
                    "simulated_r": r.simulated_r,
                    "params": r.params,
                    "max_drawdown": r.max_drawdown,
                    "locked": r.locked,
                }
                for r in results[:5]
            ],
            "improvement": improvement,
        }

    # ------------------------------------------------------------------
    #  ATR 倍数敏感度分析
    # ------------------------------------------------------------------
    def atr_sensitivity(self, atr_range: Optional[List[float]] = None) -> Dict[str, Any]:
        """固定其他参数，变化 ATR 倍数看效果"""
        if atr_range is None:
            atr_range = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0]

        results = []
        for mult in atr_range:
            result = self.simulate(trail_atr_mult=mult)
            results.append({
                "atr_mult": mult,
                "simulated_r": result.simulated_r,
                "max_drawdown": result.max_drawdown,
                "exit_bar": result.exit_bar,
                "locked": result.locked,
            })

        # 找最优
        best = max(results, key=lambda x: x["simulated_r"])

        return {
            "trade_id": self.trade.trade_id,
            "symbol": self.trade.symbol,
            "regime": self.trade.regime,
            "real_r": round(self.trade.real_r, 4),
            "best_atr_mult": best["atr_mult"],
            "best_simulated_r": best["simulated_r"],
            "improvement": round(best["simulated_r"] - self.trade.real_r, 4),
            "results": results,
        }


# ================================================================
#  批量分析器
# ================================================================

class BatchExitReplayAnalyzer:
    """批量退出参数分析

    对多笔交易做统一回放，汇总出可行动参数建议。
    """

    def __init__(self, trades: List[ReplayTrade]):
        self.trades = trades

    # ------------------------------------------------------------------
    #  按 regime 分组分析最优 ATR 倍数
    # ------------------------------------------------------------------
    def analyze_by_regime(self) -> Dict[str, Any]:
        """按 regime 分组，统计每个 regime 下的最优参数"""
        from collections import defaultdict

        regime_groups: Dict[str, List[float]] = defaultdict(list)
        regime_best: Dict[str, List[Dict]] = defaultdict(list)

        for trade in self.trades:
            analyzer = ExitReplayAnalyzer(trade)
            sens = analyzer.atr_sensitivity()
            regime = trade.regime.upper()
            regime_groups[regime].append(sens["best_atr_mult"])
            regime_best[regime].append({
                "trade_id": trade.trade_id,
                "best_atr_mult": sens["best_atr_mult"],
                "improvement": sens["improvement"],
                "real_r": trade.real_r,
            })

        # 汇总
        suggestions = {}
        for regime, mults in regime_groups.items():
            avg_mult = sum(mults) / len(mults) if mults else 5.0
            # 找最常出现的最优值
            from collections import Counter
            most_common = Counter(mults).most_common(1)
            mode_mult = most_common[0][0] if most_common else 5.0

            total_improvement = sum(b["improvement"] for b in regime_best[regime])
            suggestions[regime] = {
                "sample_trades": len(mults),
                "avg_best_atr_mult": round(avg_mult, 1),
                "mode_best_atr_mult": round(mode_mult, 1),
                "total_improvement": round(total_improvement, 4),
            }

        return {
            "by_regime_suggestions": suggestions,
            "current_default": {
                "TREND": 5.0,
                "TRANSITION": 3.0,
                "CHOP": 2.0,
            },
            "suggested_update": {
                regime: {
                    "trail_atr_mult": info["mode_best_atr_mult"],
                }
                for regime, info in suggestions.items()
            } if suggestions else {},
        }

    # ------------------------------------------------------------------
    #  批量生成报告
    # ------------------------------------------------------------------
    def generate_report(self, output_path: Optional[str] = None) -> Dict[str, Any]:
        """生成完整批量的回放报告"""
        individual: List[Dict] = []
        for trade in self.trades:
            analyzer = ExitReplayAnalyzer(trade)
            sens = analyzer.atr_sensitivity()
            grid = analyzer.grid_search()
            individual.append({
                "trade_id": trade.trade_id,
                "real_r": trade.real_r,
                "best_atr_mult": sens["best_atr_mult"],
                "best_simulated_r": sens["best_simulated_r"],
                "improvement": sens["improvement"],
                "grid_best_param": grid["best_result"],
            })

        regime_analysis = self.analyze_by_regime()

        report = {
            "total_trades": len(self.trades),
            "individual_results": individual,
            "regime_analysis": regime_analysis,
            "aggregate_improvement": round(
                sum(r["improvement"] for r in individual), 4
            ),
            "parameters_used": DEFAULT_PARAM_GRID,
        }

        if output_path:
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, ensure_ascii=False)

        return report
