# -*- coding: utf-8 -*-
"""
Dynamic Signal Analysis Tool
=============================
分析 V56 引擎在实际 K 线数据上到底为什么信号少：
  1) 扫描每根 K 线，计算所有信号的"触发状态"（满足/接近/不满足）
  2) 量化每个信号条件被满足了多少个（百分比）
  3) 识别 near-miss 信号（只差 1-2 个条件）—— 这些是放宽条件的首要候选
  4) 检查信号之间的逻辑冲突（同一时刻Long+Short同时触发）
  5) 输出完整分析报告，指导哪些参数值得放宽

用法：
    from analysis.signal_dynamics import SignalDynamicsAnalyzer
    analyzer = SignalDynamicsAnalyzer(df_signals, cfg)
    report = analyzer.analyze()
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd


@dataclass
class ConditionResult:
    """单个条件的状态评估"""
    name: str                    # 条件名称，如 'low_sweep_ll20'
    passed: bool                 # 是否完全满足
    actual_value: float          # 实际测量值
    threshold: float             # 阈值（如果适用）
    distance_to_pass: float      # 距离通过还需多少（0=已通过）
    is_boolean: bool = False     # 是否为布尔条件（非数值阈值）


@dataclass
class SignalCandidate:
    """一个接近触发的信号候选"""
    idx: int
    setup_type: str              # 信号类型名称
    direction: str               # 'Long' 或 'Short'
    passed_conditions: int       # 已满足的条件数
    total_conditions: int        # 总条件数
    failed_conditions: List[ConditionResult]  # 未满足条件的详情
    current_score: float         # 当前已有信号的分数（0=尚未触发）


class SignalDynamicsAnalyzer:
    """动态信号分析器：扫描信号为什么少、差多少"""
    
    def __init__(self, df: pd.DataFrame, cfg=None):
        """
        Args:
            df: 已添加 v56 指标的数据（通过 add_v56_indicators）
            cfg: V56Config 实例（从 v56_production_engine 导入）
        """
        from final_forge.v56_production_engine import V56Config, add_v56_indicators, load_ohlcv
        
        if cfg is None:
            cfg = V56Config()
        self.cfg = cfg
        
        # 确保指标已添加
        if "atr" not in df.columns or "ema20" not in df.columns:
            df = add_v56_indicators(load_ohlcv(df))
        self.df = df
        
        # 存储所有信号定义
        self.signal_definitions = {
            "LIQUIDITY_SWEEP_LONG": {
                "conditions": [
                    {"name": "low < ll20", "type": "numeric", "col": "low", "op": "<", "ref_col": "ll20"},
                    {"name": "close > ll20", "type": "numeric", "col": "close", "op": ">", "ref_col": "ll20"},
                    {"name": "close > open", "type": "bool", "col": "close", "op": ">", "ref_col": "open"},
                ],
                "base_score": 73.0,
            },
            "LIQUIDITY_SWEEP_SHORT": {
                "conditions": [
                    {"name": "high > hh20", "type": "numeric", "col": "high", "op": ">", "ref_col": "hh20"},
                    {"name": "close < hh20", "type": "numeric", "col": "close", "op": "<", "ref_col": "hh20"},
                    {"name": "close < open", "type": "bool", "col": "close", "op": "<", "ref_col": "open"},
                ],
                "base_score": 73.0,
            },
            "WEAK_BOS_LONG": {
                "conditions": [
                    {"name": "close > hh20", "type": "numeric", "col": "close", "op": ">", "ref_col": "hh20"},
                    {"name": "body_pct > 0.45", "type": "numeric", "col": "body_pct", "op": ">", "threshold": 0.45},
                ],
                "base_score": 46.0,
            },
            "WEAK_BOS_SHORT": {
                "conditions": [
                    {"name": "close < ll20", "type": "numeric", "col": "close", "op": "<", "ref_col": "ll20"},
                    {"name": "body_pct > 0.45", "type": "numeric", "col": "body_pct", "op": ">", "threshold": 0.45},
                ],
                "base_score": 46.0,
            },
            "REAL_CHOCH_LONG": {
                "conditions": [
                    {"name": "low < ll20", "type": "numeric", "col": "low", "op": "<", "ref_col": "ll20"},
                    {"name": "close > hh20", "type": "numeric", "col": "close", "op": ">", "ref_col": "hh20"},
                    {"name": "ema20 > ema50", "type": "bool", "col": "ema20", "op": ">", "ref_col": "ema50"},
                    {"name": "rsi > 50", "type": "numeric", "col": "rsi", "op": ">", "threshold": 50},
                ],
                "base_score": 66.0,
            },
            "REAL_CHOCH_SHORT": {
                "conditions": [
                    {"name": "high > hh20", "type": "numeric", "col": "high", "op": ">", "ref_col": "hh20"},
                    {"name": "close < ll20", "type": "numeric", "col": "close", "op": "<", "ref_col": "ll20"},
                    {"name": "ema20 < ema50", "type": "bool", "col": "ema20", "op": "<", "ref_col": "ema50"},
                    {"name": "rsi < 50", "type": "numeric", "col": "rsi", "op": "<", "threshold": 50},
                ],
                "base_score": 66.0,
            },
            "FVG_TOUCH_LONG": {
                "conditions": [
                    {"name": "i>=3", "type": "structural", "n_bars_back": 3},
                    {"name": "prev_3_low > prev_1_high", "type": "structural", "description": "3bar前低 > 1bar前高（缺口）"},
                    {"name": "close > prev_3_high", "type": "structural", "description": "当前收 > 3bar前高"},
                    {"name": "close > open", "type": "bool", "col": "close", "op": ">", "ref_col": "open"},
                ],
                "base_score": 58.0,
            },
            "FVG_TOUCH_SHORT": {
                "conditions": [
                    {"name": "i>=3", "type": "structural", "n_bars_back": 3},
                    {"name": "prev_3_high < prev_1_low", "type": "structural", "description": "3bar前高 < 1bar前低（向下缺口）"},
                    {"name": "close < prev_3_low", "type": "structural", "description": "当前收 < 3bar前低"},
                    {"name": "close < open", "type": "bool", "col": "close", "op": "<", "ref_col": "open"},
                ],
                "base_score": 58.0,
            },
            "ORDERBLOCK_REACTION_LONG": {
                "conditions": [
                    {"name": "recent_6_bars>=4_up", "type": "structural", "description": "最近6根有4根收阳"},
                    {"name": "ema20 > ema50", "type": "bool", "col": "ema20", "op": ">", "ref_col": "ema50"},
                    {"name": "low <= ema50", "type": "numeric", "col": "low", "op": "<=", "ref_col": "ema50"},
                    {"name": "close > ema50", "type": "numeric", "col": "close", "op": ">", "ref_col": "ema50"},
                    {"name": "close > open", "type": "bool", "col": "close", "op": ">", "ref_col": "open"},
                    {"name": "rsi > 38", "type": "numeric", "col": "rsi", "op": ">", "threshold": 38},
                ],
                "base_score": 57.0,
            },
            "ORDERBLOCK_REACTION_SHORT": {
                "conditions": [
                    {"name": "recent_6_bars>=4_dn", "type": "structural", "description": "最近6根有4根收阴"},
                    {"name": "ema20 < ema50", "type": "bool", "col": "ema20", "op": "<", "ref_col": "ema50"},
                    {"name": "high >= ema50", "type": "numeric", "col": "high", "op": ">=", "ref_col": "ema50"},
                    {"name": "close < ema50", "type": "numeric", "col": "close", "op": "<", "ref_col": "ema50"},
                    {"name": "close < open", "type": "bool", "col": "close", "op": "<", "ref_col": "open"},
                    {"name": "rsi < 62", "type": "numeric", "col": "rsi", "op": "<", "threshold": 62},
                ],
                "base_score": 57.0,
            },
            "TREND_PULLBACK_LONG": {
                "conditions": [
                    {"name": "ema20 > ema50", "type": "bool", "col": "ema20", "op": ">", "ref_col": "ema50"},
                    {"name": "ema50 > ema100", "type": "bool", "col": "ema50", "op": ">", "ref_col": "ema100"},
                    {"name": "low <= ema20", "type": "numeric", "col": "low", "op": "<=", "ref_col": "ema20"},
                    {"name": "close > ema20", "type": "numeric", "col": "close", "op": ">", "ref_col": "ema20"},
                    {"name": "42 < rsi < 68", "type": "range", "col": "rsi", "low": 42, "high": 68},
                ],
                "base_score": 59.0,
            },
            "TREND_PULLBACK_SHORT": {
                "conditions": [
                    {"name": "ema20 < ema50", "type": "bool", "col": "ema20", "op": "<", "ref_col": "ema50"},
                    {"name": "ema50 < ema100", "type": "bool", "col": "ema50", "op": "<", "ref_col": "ema100"},
                    {"name": "high >= ema20", "type": "numeric", "col": "high", "op": ">=", "ref_col": "ema20"},
                    {"name": "close < ema20", "type": "numeric", "col": "close", "op": "<", "ref_col": "ema20"},
                    {"name": "32 < rsi < 58", "type": "range", "col": "rsi", "low": 32, "high": 58},
                ],
                "base_score": 59.0,
            },
            "ENHANCED_BUY": {
                "conditions": [
                    {"name": "demand_zone", "type": "bool", "col": "demand_zone"},
                    {"name": "stoch_cross_up", "type": "bool", "col": "stoch_cross_up"},
                    {"name": "is_volume_spike", "type": "bool", "col": "is_volume_spike"},
                    {"name": "above_vwap", "type": "bool", "col": "above_vwap"},
                ],
                "base_score": 64.0,
            },
        }
    
    def _check_bool_condition(self, r: pd.Series, cond: Dict) -> bool:
        """检查布尔条件"""
        col = cond["col"]
        if pd.isna(r.get(col, np.nan)):
            return False
        val = float(r[col])
        if "op" in cond and "ref_col" in cond:
            ref_val = float(r[cond["ref_col"]])
            if cond["op"] == ">":
                return val > ref_val
            elif cond["op"] == "<":
                return val < ref_val
            elif cond["op"] == ">=":
                return val >= ref_val
            elif cond["op"] == "<=":
                return val <= ref_val
            return bool(val)
        return bool(val)
    
    def _check_numeric_condition(self, r: pd.Series, cond: Dict) -> Tuple[bool, float, float]:
        """检查数值条件，返回 (是否通过, 实际值, 阈值)"""
        col = cond["col"]
        if pd.isna(r.get(col, np.nan)):
            return False, np.nan, cond.get("threshold", cond.get("ref_col", np.nan))
        
        val = float(r[col])
        threshold = None
        
        if "threshold" in cond:
            threshold = float(cond["threshold"])
        elif "ref_col" in cond and cond["ref_col"] in r.index:
            threshold = float(r[cond["ref_col"]])
        else:
            return False, val, np.nan
        
        op = cond["op"]
        if op == ">":
            return val > threshold, val, threshold
        elif op == "<":
            return val < threshold, val, threshold
        elif op == ">=":
            return val >= threshold, val, threshold
        elif op == "<=":
            return val <= threshold, val, threshold
        elif op == "==":
            return val == threshold, val, threshold
        return False, val, threshold
    
    def _check_range_condition(self, r: pd.Series, cond: Dict) -> Tuple[bool, float, float]:
        """检查范围条件 (low < val < high)"""
        col = cond["col"]
        low = float(cond["low"])
        high = float(cond["high"])
        if pd.isna(r.get(col, np.nan)):
            return False, np.nan, (low, high)
        val = float(r[col])
        return (low < val < high), val, float((low + high) / 2)
    
    def _check_structural_condition(self, df: pd.DataFrame, i: int, cond: Dict) -> Tuple[bool, float, float]:
        """检查结构性条件（需要回看 K 线）"""
        r = df.iloc[i]
        
        if cond.get("name") == "i>=3":
            return i >= 3, float(i), 3.0
        
        if cond.get("name") == "prev_3_low > prev_1_high":
            if i < 3:
                return False, np.nan, np.nan
            c_high = float(df.iloc[i - 3]["high"])
            b_low = float(df.iloc[i - 1]["low"])
            return b_low > c_high, b_low - c_high, 0.0
        
        if cond.get("name") == "close > prev_3_high":
            if i < 3:
                return False, np.nan, np.nan
            prev_high = float(df.iloc[i - 3]["high"])
            close = float(r["close"])
            return close > prev_high, close - prev_high, 0.0
        
        if cond.get("name") == "prev_3_high < prev_1_low":
            if i < 3:
                return False, np.nan, np.nan
            c_low = float(df.iloc[i - 3]["low"])
            b_high = float(df.iloc[i - 1]["high"])
            return b_high < c_low, c_low - b_high, 0.0
        
        if cond.get("name") == "close < prev_3_low":
            if i < 3:
                return False, np.nan, np.nan
            prev_low = float(df.iloc[i - 3]["low"])
            close = float(r["close"])
            return close < prev_low, prev_low - close, 0.0
        
        if cond.get("name") == "recent_6_bars>=4_up":
            if i < 6:
                return False, 0.0, 4.0
            recent = df.iloc[max(0, i - 6): i]
            n_up = int((recent["close"] > recent["open"]).sum())
            return n_up >= 4, float(n_up), 4.0
        
        if cond.get("name") == "recent_6_bars>=4_dn":
            if i < 6:
                return False, 0.0, 4.0
            recent = df.iloc[max(0, i - 6): i]
            n_dn = int((recent["close"] < recent["open"]).sum())
            return n_dn >= 4, float(n_dn), 4.0
        
        return False, np.nan, np.nan
    
    def _scan_signal(self, df: pd.DataFrame, i: int, signal_name: str) -> Tuple[bool, List[ConditionResult]]:
        """扫描单个信号在所有K线上的触发状态"""
        r = df.iloc[i]
        definition = self.signal_definitions[signal_name]
        conditions = definition["conditions"]
        results: List[ConditionResult] = []
        passed_count = 0
        
        for cond in conditions:
            ctype = cond.get("type", "bool")
            
            if ctype == "bool" and "op" not in cond and "ref_col" not in cond:
                # 纯布尔列
                col = cond["col"]
                val = float(r.get(col, 0.0)) if pd.notna(r.get(col, np.nan)) else 0.0
                passed = bool(val)
                results.append(ConditionResult(
                    name=cond["name"],
                    passed=passed,
                    actual_value=val,
                    threshold=1.0,
                    is_boolean=True,
                ))
                if passed:
                    passed_count += 1
            
            elif ctype == "bool":
                passed = self._check_bool_condition(r, cond)
                results.append(ConditionResult(
                    name=cond["name"],
                    passed=passed,
                    actual_value=float(r.get(cond["col"], 0.0)),
                    threshold=float(r.get(cond.get("ref_col", ""), 0.0)),
                    is_boolean=False,
                ))
                if passed:
                    passed_count += 1
            
            elif ctype == "numeric":
                passed, val, thr = self._check_numeric_condition(r, cond)
                results.append(ConditionResult(
                    name=cond["name"],
                    passed=passed,
                    actual_value=val,
                    threshold=thr,
                    is_boolean=False,
                ))
                if passed:
                    passed_count += 1
            
            elif ctype == "range":
                passed, val, thr = self._check_range_condition(r, cond)
                results.append(ConditionResult(
                    name=cond["name"],
                    passed=passed,
                    actual_value=val,
                    threshold=thr,
                    is_boolean=False,
                ))
                if passed:
                    passed_count += 1
            
            elif ctype == "structural":
                passed, val, thr = self._check_structural_condition(df, i, cond)
                results.append(ConditionResult(
                    name=cond["name"],
                    passed=passed,
                    actual_value=val,
                    threshold=thr,
                    is_boolean=False,
                ))
                if passed:
                    passed_count += 1
        
        # 完全通过 = 信号触发
        all_passed = passed_count == len(conditions)
        return all_passed, results
    
    def analyze(self) -> Dict[str, Any]:
        """
        主分析入口：扫描所有K线，评估每个信号的触发情况。
        返回完整报告。
        """
        df = self.df
        n_bars = len(df)
        warmup = max(self.cfg.warmup_bars, 260)
        
        # 统计结果
        signal_stats: Dict[str, Dict[str, Any]] = {}
        # 逐K线逐信号扫描
        for signal_name in self.signal_definitions:
            signal_stats[signal_name] = {
                "triggered": 0,
                "near_miss_1": 0,     # 差1个条件
                "near_miss_2": 0,     # 差2个条件
                "completely_failed": 0,
                "total_evaluated": 0,
                "trigger_rate": 0.0,
                "near_miss_rate_1": 0.0,
                "near_miss_rate_2": 0.0,
                "most_blocking_conditions": {},  # 哪个条件最容易挡住信号
                "failed_condition_counts": {},   # 每个条件失败的次数
                "examples_near_miss": [],        # 示例
                "examples_triggered": [],
            }
        
        # 全数据扫描（可能慢，分批处理）
        step = max(1, n_bars // 5000)  # 限制样本量
        scan_indices = list(range(warmup, n_bars - 1, step))
        
        for i in scan_indices:
            for signal_name, definition in self.signal_definitions.items():
                triggered, results = self._scan_signal(df, i, signal_name)
                n_cond = len(results)
                n_passed = sum(1 for r in results if r.passed)
                n_failed = n_cond - n_passed
                
                stats = signal_stats[signal_name]
                stats["total_evaluated"] += 1
                
                if triggered:
                    stats["triggered"] += 1
                    if len(stats["examples_triggered"]) < 3:
                        stats["examples_triggered"].append({
                            "idx": i,
                            "datetime": str(df.iloc[i]["datetime"]),
                            "close": round(float(df.iloc[i]["close"]), 4),
                            "score_estimate": definition["base_score"],
                        })
                else:
                    # 记录失败条件
                    for res in results:
                        if not res.passed:
                            cname = res.name
                            stats["failed_condition_counts"][cname] = stats["failed_condition_counts"].get(cname, 0) + 1
                    
                    if n_failed == 1:
                        stats["near_miss_1"] += 1
                        if len(stats["examples_near_miss"]) < 5:
                            failed = [r for r in results if not r.passed]
                            stats["examples_near_miss"].append({
                                "idx": i,
                                "datetime": str(df.iloc[i]["datetime"]),
                                "close": round(float(df.iloc[i]["close"]), 4),
                                "failed_conditions": [
                                    {
                                        "name": r.name,
                                        "actual": round(float(r.actual_value), 4),
                                        "threshold": round(float(r.threshold), 4),
                                        "distance": round(float(r.distance_to_pass), 4),
                                    } for r in failed
                                ],
                            })
                    elif n_failed == 2:
                        stats["near_miss_2"] += 1
                    else:
                        stats["completely_failed"] += 1
        
        # 计算统计量
        for name, stats in signal_stats.items():
            total = stats["total_evaluated"]
            if total > 0:
                stats["trigger_rate"] = round(stats["triggered"] / total, 6)
                stats["near_miss_rate_1"] = round(stats["near_miss_1"] / total, 6)
                stats["near_miss_rate_2"] = round(stats["near_miss_2"] / total, 6)
            
            # 最阻塞条件：失败次数最多的条件
            if stats["failed_condition_counts"]:
                sorted_fc = sorted(
                    stats["failed_condition_counts"].items(),
                    key=lambda x: x[1],
                    reverse=True,
                )
                stats["most_blocking_conditions"] = [
                    {"condition": k, "fail_count": v, "fail_rate": round(v / stats["total_evaluated"], 4) if stats["total_evaluated"] else 0.0}
                    for k, v in sorted_fc[:5]
                ]
        
        # 信号冲突检测
        conflict_stats = self._detect_conflicts(scan_indices)
        
        # 汇总
        total_near_miss_1 = sum(s["near_miss_1"] for s in signal_stats.values())
        total_near_miss_2 = sum(s["near_miss_2"] for s in signal_stats.values())
        total_triggered = sum(s["triggered"] for s in signal_stats.values())
        
        # 汇总瓶颈分析（哪个条件跨多个信号最常失败）
        cross_signal_bottlenecks: Dict[str, int] = {}
        for name, stats in signal_stats.items():
            for cond, count in stats["failed_condition_counts"].items():
                cross_signal_bottlenecks[cond] = cross_signal_bottlenecks.get(cond, 0) + count
        
        sorted_bottlenecks = sorted(cross_signal_bottlenecks.items(), key=lambda x: x[1], reverse=True)[:15]
        
        report = {
            "dataset_info": {
                "bars": n_bars,
                "warmup": warmup,
                "evaluated_bars": len(scan_indices),
                "start": str(df["datetime"].iloc[warmup]) if warmup < n_bars else "N/A",
                "end": str(df["datetime"].iloc[-1]),
            },
            "summary": {
                "total_triggered": total_triggered,
                "total_near_miss_1": total_near_miss_1,
                "total_near_miss_2": total_near_miss_2,
                "estimated_signal_gain_if_relax_1_condition": total_triggered + total_near_miss_1,
                "estimated_signal_gain_if_relax_2_conditions": total_triggered + total_near_miss_1 + total_near_miss_2,
            },
            "cross_signal_bottlenecks": [
                {"condition": k, "fail_count": v} for k, v in sorted_bottlenecks
            ],
            "per_signal": signal_stats,
            "conflicts": conflict_stats,
        }
        
        return report
    
    def _detect_conflicts(self, scan_indices: List[int]) -> Dict[str, Any]:
        """检测同一K线上 Long 和 Short 信号同时触发的冲突"""
        df = self.df
        conflicts = {
            "total_evaluated": 0,
            "both_directions_triggered": 0,
            "examples": [],
        }
        
        for i in scan_indices:
            conflicts["total_evaluated"] += 1
            has_long = False
            has_short = False
            triggered_names = []
            
            for signal_name in self.signal_definitions:
                if "LONG" in signal_name or signal_name == "ENHANCED_BUY":
                    triggered, _ = self._scan_signal(df, i, signal_name)
                    if triggered:
                        has_long = True
                        triggered_names.append(signal_name)
                elif "SHORT" in signal_name:
                    triggered, _ = self._scan_signal(df, i, signal_name)
                    if triggered:
                        has_short = True
                        triggered_names.append(signal_name)
            
            if has_long and has_short:
                conflicts["both_directions_triggered"] += 1
                if len(conflicts["examples"]) < 5:
                    conflicts["examples"].append({
                        "idx": i,
                        "datetime": str(df.iloc[i]["datetime"]),
                        "close": round(float(df.iloc[i]["close"]), 4),
                        "signals": triggered_names,
                    })
        
        if conflicts["total_evaluated"] > 0:
            conflicts["conflict_rate"] = round(
                conflicts["both_directions_triggered"] / conflicts["total_evaluated"], 6
            )
        
        return conflicts
    
    def print_report(self, report: Optional[Dict] = None) -> str:
        """格式化输出报告"""
        if report is None:
            report = self.analyze()
        
        lines = []
        lines.append("=" * 80)
        lines.append("V56 动态信号分析报告")
        lines.append("=" * 80)
        
        # 数据集信息
        di = report["dataset_info"]
        lines.append(f"\n📊 数据集: {di['bars']} 根K线, 评估 {di['evaluated_bars']} 根")
        lines.append(f"   区间: {di['start']} → {di['end']}")
        
        # 汇总
        s = report["summary"]
        lines.append(f"\n📈 信号汇总:")
        lines.append(f"   已触发: {s['total_triggered']}")
        lines.append(f"   差1条件: {s['total_near_miss_1']}")
        lines.append(f"   差2条件: {s['total_near_miss_2']}")
        lines.append(f"   💡 若放宽1个条件,预计增加 {s['estimated_signal_gain_if_relax_1_condition'] - s['total_triggered']} 个信号")
        lines.append(f"   💡 若放宽2个条件,预计增加 {s['estimated_signal_gain_if_relax_2_conditions'] - s['total_triggered']} 个信号")
        
        # 跨信号瓶颈
        lines.append(f"\n🔍 跨信号最常见失败条件 (Top 10):")
        for item in report["cross_signal_bottlenecks"][:10]:
            lines.append(f"   {item['condition']}: {item['fail_count']} 次")
        
        # 各信号详情
        lines.append(f"\n{'信号类型':<30} {'触发':<6} {'差1':<6} {'差2':<6} {'触发率':<10} {'差1率':<10} '主要阻塞条件'")
        lines.append("-" * 80)
        for name, stats in report["per_signal"].items():
            blocking = stats["most_blocking_conditions"][0]["condition"] if stats["most_blocking_conditions"] else "-"
            lines.append(
                f"{name:<30} {stats['triggered']:<6} {stats['near_miss_1']:<6} {stats['near_miss_2']:<6} "
                f"{stats['trigger_rate']:<10.6f} {stats['near_miss_rate_1']:<10.6f} {blocking}"
            )
        
        # 冲突检测
        c = report["conflicts"]
        lines.append(f"\n⚠️ 方向冲突检测:")
        lines.append(f"   评估K线: {c['total_evaluated']}")
        lines.append(f"   Long+Short同触发: {c['both_directions_triggered']} ({c.get('conflict_rate', 0):.6f})")
        if c["examples"]:
            lines.append(f"   示例:")
            for ex in c["examples"][:3]:
                lines.append(f"     idx={ex['idx']} {ex['datetime']} close={ex['close']} signals={ex['signals']}")
        
        # 放宽建议
        lines.append(f"\n💡 放宽建议 (基于瓶颈分析):")
        bottlenecks = report["cross_signal_bottlenecks"]
        if bottlenecks:
            for i, b in enumerate(bottlenecks[:5]):
                lines.append(f"   {i+1}. {b['condition']} (失败 {b['fail_count']} 次)")
        
        return "\n".join(lines)


def run_analysis(df: pd.DataFrame, cfg=None, verbose: bool = True) -> Dict[str, Any]:
    """便捷入口函数"""
    analyzer = SignalDynamicsAnalyzer(df, cfg)
    report = analyzer.analyze()
    if verbose:
        print(analyzer.print_report(report))
    return report


if __name__ == "__main__":
    import sys
    from pathlib import Path
    
    # 用法: python analysis/signal_dynamics.py <data.csv>
    if len(sys.argv) > 1:
        data_path = sys.argv[1]
        df = pd.read_csv(data_path)
        report = run_analysis(df)
    else:
        print("用法: python analysis/signal_dynamics.py <OHLCV csv 文件路径>")
        print("      csv 需要包含: datetime, open, high, low, close, volume")
```

主要功能：

1. **动态扫描所有K线** - 不是只看已生成的信号，而是分析每根K线上每个信号类型的"触发状态"
   
2. **量化 near-miss** - 统计有多少K线只差 1 个或 2 个条件就能触发信号，并列出具体差了什么条件

3. **瓶颈条件分析** - 找出哪个条件最常"挡住"信号（比如 `close > hh20` 最常失败等）

4. **方向冲突检测** - 检查同一根K线是否同时满足 Long 和 Short 信号

5. **筛选增益预测** - 估算如果放宽某个条件，预计能增加多少信号

这样就能知道：
- 到底是哪几个条件导致信号这么少
- 放宽哪些条件收益最大
- 是否真的有"方向冲突"问题

让我现在把它连接到您的测试脚本中实际运行看看效果。先检查一下数据在哪：

```tool
TOOL_NAME: ls
BEGIN_ARG: dirPath
.