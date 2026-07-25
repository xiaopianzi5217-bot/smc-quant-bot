# -*- coding: utf-8 -*-
"""
Reject Analytics — 信号拒绝日志与统计（V3）

V3 新增：
  - 分阶段追踪（REGIME → SCORE → CONFIDENCE → RISK → EXECUTION）
  - 内存统计 + 文件日志双轨
  - 实时 report() 看瓶颈在哪
  - 兼容 V2 的 get_stats() 接口

设计风格与 analytics/outcome_db.py 一致。
"""

import json
import time
from collections import defaultdict
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from analytics.daily_report import daily_report

from analytics.feature_hash import generate_feature_hash


# ============================================================
#   Stage order — 交易决策链的标准阶段
# ============================================================
STAGE_ORDER = [
    "CANDIDATE",     # 候选信号生成
    "REGIME",        # 市场状态过滤
    "SCORE",         # 评分门槛
    "CONFIDENCE",    # 置信度/EV门槛
    "RISK",          # 风险上限/每日限制
    "LIQUIDITY",     # 流动性惩罚（新增）
    "EXECUTION",     # 执行层（滑点/重叠等）
    "OBSERVER",      # Observer-only / Probe
    "FINAL_TRADE",   # 成功交易
]


class RejectAnalytics:
    """
    信号拒绝日志与统计（V3 分阶段版）

    用法:
        from analytics.reject_analytics import reject_analytics

        # 记录一次拒绝
        reject_analytics.record(
            symbol="BTC/USDT",
            signal_id="sig_001",
            stage="SCORE",
            reason="LOW_SCORE",
            score=62.0,
            confidence=0.45,
            regime="range",
            extra={"min_score": 72.0}
        )

        # 查看实时统计
        print(reject_analytics.report())

        # 查看瓶颈
        print(reject_analytics.bottleneck())
    """

    def __init__(self, log_dir: str = "storage/rejects"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # 内存计数器（实时统计，不清零）
        self.stats: Dict[str, int] = defaultdict(int)
        # 按阶段×原因的二维计数 (stage -> reason -> count)
        self.stage_reason_stats: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        # 历史明细（内存中保留最近 N 条）
        self.history: List[Dict[str, Any]] = []
        self._max_history = 10000

    # ---- V2 兼容接口 ----

    def _get_log_path(self, date_str: Optional[str] = None) -> Path:
        """每天一个文件，按日期切割"""
        if date_str is None:
            date_str = datetime.now().strftime("%Y%m%d")
        return self.log_dir / f"rejects_{date_str}.jsonl"

    def log(
        self,
        symbol: str,
        reason: str,
        feature: Dict[str, Any],
        ev_info: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """V2 兼容接口 — 记录一条拒绝日志到文件

        参数:
            symbol: 交易对
            reason: 拒绝原因（如 "LOW_EV", "LOW_SCORE"）
            feature: 信号特征字典
            ev_info: EV 相关信息
            extra: 额外补充字段
        """
        entry = {
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "reason": reason,
            "feature_hash": generate_feature_hash(feature) if feature else "NONE",
            "ev": round(ev_info.get("expected_value"), 4) if ev_info and ev_info.get("expected_value") is not None else None,
            "confidence": round(ev_info.get("confidence"), 4) if ev_info and ev_info.get("confidence") is not None else None,
        }
        if extra:
            entry["extra"] = extra

        log_path = self._get_log_path()
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ---- V3 分阶段追踪 ----

    def record(
        self,
        symbol: str,
        signal_id: str,
        stage: str,
        reason: str,
        score: Optional[float] = None,
        confidence: Optional[float] = None,
        regime: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        记录一个信号在决策链某个阶段的处理结果。

        参数:
            symbol: 交易对
            signal_id: 信号唯一 ID（如 idx 或哈希）
            stage: 处理阶段，参见 STAGE_ORDER
            reason: 具体原因（如 "LOW_SCORE", "BAD_REGIME"）
            score: 当前评分
            confidence: 当前置信度
            regime: 市场状态
            extra: 额外补充字段（dict）
        """
        key = f"{stage}:{reason}"
        self.stats[key] += 1
        self.stage_reason_stats[stage][reason] += 1

        entry = {
            "time": time.time(),
            "symbol": symbol,
            "signal_id": str(signal_id),
            "stage": stage,
            "reason": reason,
            "score": score,
            "confidence": confidence,
            "regime": regime,
        }
        if extra:
            entry["extra"] = extra

        self.history.append(entry)
        if len(self.history) > self._max_history:
            self.history = self.history[-self._max_history:]

    def summary(self) -> Dict[str, int]:
        """返回所有 stage:reason 的计数"""
        return dict(self.stats)

    def stage_summary(self) -> Dict[str, int]:
        """按阶段聚合：每个阶段的总拒绝次数"""
        result: Dict[str, int] = defaultdict(int)
        for key, count in self.stats.items():
            stage = key.split(":")[0]
            result[stage] += count
        return dict(result)

    def bottleneck(self, top_n: int = 3) -> List[Dict[str, Any]]:
        """
        找出当前最大的决策瓶颈（拒绝最多的阶段）。

        返回:
            [{"stage": "REGIME", "total": 210, "pct": 52.5, "top_reason": "BAD_REGIME"}, ...]
        """
        total = sum(self.stats.values())
        if total == 0:
            return []

        stage_totals = self.stage_summary()
        bottlenecks = []
        for stage in STAGE_ORDER:
            if stage not in stage_totals:
                continue
            stage_count = stage_totals[stage]
            reasons = self.stage_reason_stats.get(stage, {})
            top_reason = max(reasons, key=reasons.get) if reasons else ""
            bottlenecks.append({
                "stage": stage,
                "total": stage_count,
                "pct": round(stage_count / total * 100, 1),
                "top_reason": top_reason,
            })

        bottlenecks.sort(key=lambda x: -x["total"])
        return bottlenecks[:top_n]

    def report(self) -> str:
        """生成可读的拒绝分析报告"""
        total = sum(self.stats.values())
        if total == 0:
            return "📊 Reject Analytics: 暂无拒绝数据"

        lines = [
            "=" * 50,
            "📊 Reject Analytics Report",
            f"Total Events: {total}",
            "-" * 50,
        ]

        # 按阶段顺序排列
        for stage in STAGE_ORDER:
            if stage not in self.stage_reason_stats:
                continue
            reasons = self.stage_reason_stats[stage]
            stage_total = sum(reasons.values())
            if stage_total == 0:
                continue
            lines.append(f"\n  [{stage}]  Total: {stage_total} ({stage_total/total*100:.1f}%)")
            for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
                lines.append(f"    {reason}: {count} ({count/stage_total*100:.1f}%)")

        # 瓶颈提示
        bottlenecks = self.bottleneck(2)
        if bottlenecks:
            lines.append("")
            lines.append("-" * 50)
            lines.append("🔍 Top Bottleneck(s):")
            for b in bottlenecks:
                lines.append(f"  {b['stage']}: {b['total']} ({b['pct']}%) — top reason: {b['top_reason']}")

        lines.append("=" * 50)
        return "\n".join(lines)

    # ---- V2 兼容统计 ----

    def _load_records(
        self,
        symbol: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """加载符合条件的原始记录"""
        records: List[Dict[str, Any]] = []

        for fpath in sorted(self.log_dir.glob("rejects_*.jsonl")):
            if since or until:
                date_str = fpath.stem.replace("rejects_", "")
                try:
                    file_date = datetime.strptime(date_str, "%Y%m%d").date()
                except ValueError:
                    continue
                if since and file_date < datetime.strptime(since, "%Y-%m-%d").date():
                    continue
                if until and file_date > datetime.strptime(until, "%Y-%m-%d").date():
                    continue

            with open(fpath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if symbol and rec.get("symbol") != symbol:
                        continue
                    records.append(rec)

        return records

    def get_stats(
        self,
        symbol: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> Dict[str, Any]:
        """V2 兼容接口：统计拒绝日志

        参数:
            symbol: 按交易对筛选
            since: 起始日期 (YYYY-MM-DD)
            until: 结束日期 (YYYY-MM-DD)

        返回:
            {"total": int, "by_reason": {...}, "by_symbol": {...}, ...}
        """
        records = self._load_records(symbol, since, until)
        if not records:
            return {
                "total": 0,
                "by_reason": {},
                "by_symbol": {},
                "reason_breakdown": [],
                "avg_ev": None,
                "period": {"since": since or "all", "until": until or "all"},
            }

        by_reason: Dict[str, int] = {}
        by_symbol: Dict[str, int] = {}
        ev_values: List[float] = []

        for rec in records:
            reason = rec.get("reason", "UNKNOWN")
            by_reason[reason] = by_reason.get(reason, 0) + 1
            sym = rec.get("symbol", "UNKNOWN")
            by_symbol[sym] = by_symbol.get(sym, 0) + 1
            ev = rec.get("ev")
            if ev is not None:
                ev_values.append(ev)

        total = len(records)
        reason_breakdown = [
            {"reason": r, "count": c, "pct": round(c / total * 100, 1)}
            for r, c in sorted(by_reason.items(), key=lambda x: -x[1])
        ]

        return {
            "total": total,
            "by_reason": by_reason,
            "by_symbol": by_symbol,
            "reason_breakdown": reason_breakdown,
            "avg_ev": round(sum(ev_values) / len(ev_values), 4) if ev_values else None,
            "period": {"since": since or "all", "until": until or "all"},
        }

    # ---- V2 仪表盘接口 ----

    def get_trend_dashboard(self, days: int = 7) -> Dict[str, Any]:
        """V2 兼容：趋势仪表盘"""
        until = datetime.now().strftime("%Y-%m-%d")
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        records = self._load_records(since=since, until=until)

        daily: Dict[str, Dict[str, Any]] = {}
        for rec in records:
            day = rec.get("timestamp", "")[:10]
            if day not in daily:
                daily[day] = {"date": day, "count": 0, "reasons": {}}
            daily[day]["count"] += 1
            reason = rec.get("reason", "UNKNOWN")
            daily[day]["reasons"][reason] = daily[day]["reasons"].get(reason, 0) + 1

        daily_totals = sorted(daily.values(), key=lambda x: x["date"])

        hot_reasons: Dict[str, Dict[str, Any]] = {}
        for rec in records:
            reason = rec.get("reason", "UNKNOWN")
            if reason not in hot_reasons:
                hot_reasons[reason] = {"reason": reason, "total": 0, "by_day": {}}
            hot_reasons[reason]["total"] += 1
            day = rec.get("timestamp", "")[:10]
            hot_reasons[reason]["by_day"][day] = hot_reasons[reason]["by_day"].get(day, 0) + 1

        n_days = max(1, (datetime.now() - datetime.strptime(since, "%Y-%m-%d")).days)
        hot_reasons_list = [
            {
                "reason": r["reason"],
                "total": r["total"],
                "avg_daily": round(r["total"] / n_days, 1),
                "peak_day": max(r["by_day"].items(), key=lambda x: x[1])[0] if r["by_day"] else None,
                "peak_count": max(r["by_day"].values()) if r["by_day"] else 0,
            }
            for r in sorted(hot_reasons.values(), key=lambda x: -x["total"])
        ]

        today_str = datetime.now().strftime("%Y-%m-%d")
        today_data = daily.get(today_str, {})
        top_reason_today = ""
        if today_data:
            reasons = today_data.get("reasons", {})
            if reasons:
                top_reason_today = max(reasons, key=reasons.get)

        return {
            "period_days": days,
            "daily_totals": daily_totals,
            "total_period": len(records),
            "hot_reasons": hot_reasons_list,
            "top_reason_today": top_reason_today,
        }

    def get_feature_blacklist(self, min_rejects: int = 3, days: int = 30) -> List[Dict[str, Any]]:
        """V2 兼容：Feature Hash 黑名单"""
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        records = self._load_records(since=since)

        by_hash: Dict[str, Dict[str, Any]] = {}
        for rec in records:
            fh = rec.get("feature_hash", "UNKNOWN")
            if fh not in by_hash:
                by_hash[fh] = {
                    "feature_hash": fh,
                    "reject_count": 0,
                    "reasons": {},
                    "symbols": set(),
                    "last_rejected": "",
                }
            bh = by_hash[fh]
            bh["reject_count"] += 1
            reason = rec.get("reason", "UNKNOWN")
            bh["reasons"][reason] = bh["reasons"].get(reason, 0) + 1
            sym = rec.get("symbol", "")
            if sym:
                bh["symbols"].add(sym)
            ts = rec.get("timestamp", "")
            if ts > bh["last_rejected"]:
                bh["last_rejected"] = ts

        blacklist = [
            {
                "feature_hash": bh["feature_hash"],
                "reject_count": bh["reject_count"],
                "top_reason": max(bh["reasons"], key=bh["reasons"].get),
                "reasons": bh["reasons"],
                "symbols": list(bh["symbols"]),
                "last_rejected": bh["last_rejected"],
            }
            for bh in by_hash.values()
            if bh["reject_count"] >= min_rejects
        ]
        blacklist.sort(key=lambda x: -x["reject_count"])
        return blacklist

    def get_hourly_heatmap(self, days: int = 7) -> Dict[str, Any]:
        """V2 兼容：按小时热力图"""
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        records = self._load_records(since=since)

        hours: Dict[str, int] = {f"{h:02d}": 0 for h in range(24)}
        reasons_by_hour: Dict[str, Dict[str, int]] = {f"{h:02d}": {} for h in range(24)}

        for rec in records:
            ts = rec.get("timestamp", "")
            try:
                hr = datetime.fromisoformat(ts).strftime("%H")
            except Exception:
                continue
            hours[hr] = hours.get(hr, 0) + 1
            reason = rec.get("reason", "UNKNOWN")
            rh = reasons_by_hour.setdefault(hr, {})
            rh[reason] = rh.get(reason, 0) + 1

        peak_hour = max(hours, key=hours.get) if any(hours.values()) else ""
        quietest_hour = min(hours, key=hours.get) if any(hours.values()) else ""

        return {
            "hours": hours,
            "peak_hour": peak_hour,
            "quietest_hour": quietest_hour,
            "reasons_by_hour": reasons_by_hour,
        }


# 全局单例，供外部直接使用
reject_analytics = RejectAnalytics()
