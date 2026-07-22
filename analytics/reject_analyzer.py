# -*- coding: utf-8 -*-
"""
Reject Analytics — 信号拒绝日志与统计（V2）

每条被拒绝的信号记录到 JSONL 文件，支持按原因、交易对、时间范围统计。
V2 新增：趋势分析仪表盘/热力图/Feature Hash 黑名单自动发现。

设计风格与 analytics/outcome_db.py 一致。
"""

import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

from analytics.feature_hash import generate_feature_hash


class RejectAnalytics:
    """信号拒绝日志与统计（V2 仪表盘版）"""

    def __init__(self, log_dir: str = "storage/rejects"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

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
        """记录一条拒绝日志

        参数:
            symbol: 交易对
            reason: 拒绝原因（如 "LOW_EV", "LOW_SCORE", "DIRECTION_AMBIGUOUS"）
            feature: 信号特征字典，用于生成 feature_hash
            ev_info: EV 相关信息（expected_value, confidence 等）
            extra: 额外补充字段
        """
        entry = {
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "reason": reason,
            "feature_hash": generate_feature_hash(feature),
            "ev": round(ev_info.get("expected_value"), 4) if ev_info and ev_info.get("expected_value") is not None else None,
            "confidence": round(ev_info.get("confidence"), 4) if ev_info and ev_info.get("confidence") is not None else None,
        }
        if extra:
            entry["extra"] = extra

        log_path = self._get_log_path()
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

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
        """统计拒绝日志

        参数:
            symbol: 按交易对筛选
            since: 起始日期 (YYYY-MM-DD)，None 表示不限
            until: 结束日期 (YYYY-MM-DD)，None 表示不限

        返回:
            {
                "total": int,
                "by_reason": {"LOW_EV": 12, "LOW_SCORE": 5, ...},
                "by_symbol": {"BTC/USDT": 8, ...},
                "reason_breakdown": [...],
                "avg_ev": float,
                "period": {"since": str, "until": str}
            }
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

    # ------------------------------------------------------------------
    #  V2 新增：趋势分析仪表盘
    # ------------------------------------------------------------------

    def get_trend_dashboard(self, days: int = 7) -> Dict[str, Any]:
        """趋势仪表盘：按天聚合拒绝率/原因热力图

        返回:
            {
                "daily_totals": [{"date": "2026-07-01", "count": 12, "reasons": {...}}, ...],
                "reject_rate_trend": [...],
                "top_reason_today": str,
                "total_period": int,
                "hot_reasons": [{"reason": "...", "total": 12, "avg_daily": 1.7}, ...]
            }
        """
        until = datetime.now().strftime("%Y-%m-%d")
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        records = self._load_records(since=since, until=until)

        # 按天聚合
        daily: Dict[str, Dict[str, Any]] = {}
        for rec in records:
            day = rec.get("timestamp", "")[:10]
            if day not in daily:
                daily[day] = {"date": day, "count": 0, "reasons": {}}
            daily[day]["count"] += 1
            reason = rec.get("reason", "UNKNOWN")
            daily[day]["reasons"][reason] = daily[day]["reasons"].get(reason, 0) + 1

        daily_totals = sorted(daily.values(), key=lambda x: x["date"])

        # 原因热力图
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

    def get_feature_blacklist(
        self,
        min_rejects: int = 3,
        days: int = 30,
    ) -> List[Dict[str, Any]]:
        """Feature Hash 黑名单自动发现

        找出在同一 feature_hash 上被反复拒绝的信号，
        说明这个特征模式本身有问题，应考虑屏蔽。

        参数:
            min_rejects: 最少拒绝次数才算黑名单
            days: 回溯天数

        返回:
            [{"feature_hash": "...", "reject_count": 5, "reasons": [...], "symbols": [...]}, ...]
        """
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        records = self._load_records(since=since)

        # 按 feature_hash 聚合
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

        # 筛选 & 排序
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
        """按小时的热力图：哪些时段拒绝最多

        返回:
            {
                "hours": {"00": 3, "01": 1, ...},
                "peak_hour": "14",
                "quietest_hour": "03",
                "reasons_by_hour": {"00": {"LOW_EV": 2, ...}, ...}
            }
        """
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
