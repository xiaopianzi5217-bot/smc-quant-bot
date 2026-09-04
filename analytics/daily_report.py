"""
Daily report generator for outcomes and events.

Produces a human-readable summary for a given day (default: today).
"""
from pathlib import Path
import json
import sqlite3
import calendar
from datetime import datetime, timedelta, timezone
from analytics.outcome_db import OutcomeDatabase
from analytics import data_quality_check
from analytics.ev_monitor import EVMonitor
from utils.structured_logger import slog
try:
    from notifier.telegram import send_telegram
except Exception:
    send_telegram = None


def _parse_iso(ts: str):
    """Parse timestamp to aware-UTC datetime.

    Handles: ISO-8601 with/without timezone offset or 'Z' suffix,
    epoch seconds (int/float str). Naive parsed strings are assumed
    to represent UTC (semantic fix: all logs written in UTC).

    Returns aware datetime in UTC, or None on failure.
    """
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
    except Exception:
        try:
            # epoch float seconds
            return datetime.fromtimestamp(float(ts), timezone.utc)
        except Exception:
            return None
    if dt.tzinfo is None:
        # naive assumed UTC
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def _backfill_from_cloud_v6_db(target_date: datetime, start: datetime, end: datetime) -> list:
    """从本地/云端 v6_research.db 读取目标日期已平仓记录，返回模拟 EXIT 事件 dict 列表。

    数据源：HF 私有数据集 v6_research.db（trade_snapshots 表，真实结果）
    策略：仅当本地 events.jsonl 无当日 EXIT 时才调用。
          查询条件与 events.jsonl 相同的时间窗口 [start, end)，
          过滤 exit_reason != 'OPEN' 且 pnl_r 非空。
    防重：进程内 _backfilled 标记，仅首次执行一次，避免重复查询。
    失败/无数据静默返回 []，不影响原逻辑。
    """
    if getattr(_backfill_from_cloud_v6_db, "_backfilled", False):
        return []
    _backfill_from_cloud_v6_db._backfilled = True

    db_path = Path("data/v6_research.db")
    try:
        # 本地库缺失/为空时，尝试拉取云端最新
        if not db_path.exists() or db_path.stat().st_size == 0:
            try:
                from v6_data_engine import pull_database_from_hub
                pull_database_from_hub()
            except Exception:
                pass
    except Exception:
        pass

    if not db_path.exists() or db_path.stat().st_size == 0:
        slog.warning("[DailyReport] 云端兜底跳过: v6_research.db 不存在或为空")
        return []

        # 时区说明：start/end 是 aware-UTC datetime
        # 日历时间计算时用 timezone.utc 构造 start、end，避免 naive.timestamp() 按本地时区(UTC+8)偏移8小时
        # 对 aware-UTC datetime 调用 .timetuple() 得到 UTC 字段，calendar.timegm 解释为 UTC epoch 一致正确
    try:
        start_ts = calendar.timegm(start.timetuple())
        end_ts = calendar.timegm(end.timetuple())
    except Exception:
        return []

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """
            SELECT signal_id, symbol, direction, regime, mode,
                   exit_reason, exit_timestamp, exit_price, pnl_r,
                   confidence, p_win_calibrated, feature_hash,
                   max_forward_r, max_adverse_r
            FROM trade_snapshots
            WHERE exit_reason IS NOT NULL
              AND exit_reason != ''
              AND exit_reason != 'OPEN'
              AND pnl_r IS NOT NULL
              AND exit_timestamp IS NOT NULL
              AND exit_timestamp > 0
              AND exit_timestamp >= ?
              AND exit_timestamp < ?
            ORDER BY exit_timestamp ASC
            """,
            (start_ts, end_ts),
        )
        rows = cur.fetchall()
        conn.close()
    except Exception as e:
        slog.warning(f"[DailyReport] 云端 v6_research.db 兜底查询失败: {e}")
        return []

    if not rows:
        slog.info("[DailyReport] 云端 v6_research.db 目标日期无已平仓记录")
        return []

    events = []
    for row in rows:
        try:
            feats = {}
            _fh = str(row["feature_hash"] or "")
            if _fh:
                feats["feature_hash"] = _fh[-10:]
            _mode = str(row["mode"] or "NORMAL")
            if _mode and _mode != "NORMAL":
                feats["mode"] = _mode
            if not feats:
                feats["cloud"] = True
            ev = {
                "event": "EXIT",
                "timestamp": int(row["exit_timestamp"]),
                "trade_id": row["signal_id"],
                "symbol": row["symbol"],
                "profit_r": float(row["pnl_r"] or 0.0),
                "mfe": row["max_forward_r"],
                "mae": row["max_adverse_r"],
                "regime": row["regime"] or "UNKNOWN",
                "features": feats,
                "ev": row["p_win_calibrated"] if row["p_win_calibrated"] is not None else row["confidence"],
                "cloud_backfill": True,
            }
            events.append(ev)
        except Exception:
            continue

    if events:
        slog.info(f"[DailyReport] 云端 v6_research.db 兜底读取 {len(events)} 笔已平仓记录")
    return events


def generate_daily_report(target_date: datetime = None) -> str:
    if target_date is None:
        target_date = datetime.utcnow()

    start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)

    event_file = Path("data/events.jsonl")

    # ---- 1) 优先读取本地 events.jsonl ----
    all_events = []
    if event_file.exists():
        with event_file.open('r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                ts = _parse_iso(ev.get('timestamp') or ev.get('time') or '')
                if not ts or not (start <= ts < end):
                    continue
                if ev.get('event') != 'EXIT':
                    continue
                all_events.append(ev)

        # ---- 2) 本地无当日 EXIT → 云端 v6_research.db 兜底 ----
    if not all_events:
        all_events = _backfill_from_cloud_v6_db(target_date, start, end)

    total = 0
    wins = 0
    losses = 0
    max_loss = 0.0
    sum_mfe = 0.0
    sum_mae = 0.0
    mfe_count = 0
    mae_count = 0
    regimes = {}
    feature_counts = {}
    group_sums = {}
    group_counts = {}
    ev_monitor = EVMonitor()

    for ev in all_events:
        total += 1
        pr = float(ev.get('profit_r') or 0.0)
        # feed EV monitor
        try:
            ev_val = ev.get('ev')
            if ev_val is not None:
                ev_monitor.update(ev_val, pr)
        except Exception:
            pass
        if pr > 0:
            wins += 1
        else:
            losses += 1
        if pr < max_loss:
            max_loss = pr
        mfe = ev.get('mfe')
        mae = ev.get('mae')
        if mfe is not None:
            try:
                sum_mfe += float(mfe)
                mfe_count += 1
            except Exception:
                pass
        if mae is not None:
            try:
                sum_mae += float(mae)
                mae_count += 1
            except Exception:
                pass
        regime = ev.get('regime') or 'UNKNOWN'
        regimes[regime] = regimes.get(regime, 0) + 1
        features = ev.get('features') or {}
        for k, v in (features.items() if isinstance(features, dict) else []):
            fv = f"{k}={v}"
            feature_counts[fv] = feature_counts.get(fv, 0) + 1
        # 聚合用于质量排名：按 (symbol, regime, top_feature) 汇总 profit
        sym = ev.get('symbol') or 'UNK'
        rg = ev.get('regime') or 'UNKNOWN'
        # 选取一个代表性特征：优先取 `feature_hash`/`cloud_hash` 键的实际值（哈希），
        # 其次找布尔/标志类 key 名（值 True/非空字符串），最后取第一个 key。
        # 修复: 之前取的是固定 key 名 (如 cloud_hash) 导致所有云端记录归为一类。
        top_feat = 'NONE'
        if isinstance(features, dict) and features:
            found = None
            # 1) 优先从 feature_hash / cloud_hash 键中提取实际哈希值
            for hash_key in ("feature_hash", "cloud_hash"):
                hv = features.get(hash_key)
                if hv and isinstance(hv, str) and hv:
                    found = hv
                    break
            # 2) 再尝试找到值为 True 的布尔标志 key
            if not found:
                for kk, vv in features.items():
                    if vv is True or (isinstance(vv, str) and vv and kk not in ("feature_hash", "cloud_hash")):
                        found = kk
                        break
            # 3) 最后选择第一个 key
            if not found:
                found = next(iter(features.keys()))
            top_feat = found
        combo = (sym, rg, top_feat)
        group_sums[combo] = group_sums.get(combo, 0.0) + pr
        group_counts[combo] = group_counts.get(combo, 0) + 1

    win_rate = (wins / total * 100.0) if total > 0 else 0.0
    pf = "N/A"
    try:
        total_wins_r = 0.0
        total_losses_r = 0.0
        # use OutcomeDatabase to compute PF roughly via get_top_features sample
        db = OutcomeDatabase()
        # approximate: use global sums
        # Not perfect, but provide something useful
        for h, s in db.data.items():
            total_wins_r += s.get('wins_r', 0.0)
            total_losses_r += s.get('losses_r', 0.0)
        if total_losses_r > 0:
            pf = round(total_wins_r / total_losses_r, 2)
    except Exception:
        pf = "N/A"

    best_regime = max(regimes.items(), key=lambda x: x[1])[0] if regimes else 'N/A'
    best_feature = max(feature_counts.items(), key=lambda x: x[1])[0] if feature_counts else 'N/A'
    # 生成质量排名：按组合累计利润排序
    top_combos = []
    worst_combos = []
    try:
        sorted_combos = sorted(group_sums.items(), key=lambda x: x[1], reverse=True)
        top_combos = sorted_combos[:5]
        worst_combos = sorted_combos[-5:]
    except Exception:
        top_combos = []
        worst_combos = []

    report = []
    report.append("======== DAILY REPORT ========")
    report.append(f"Date: {start.date().isoformat()}")
    report.append("")
    report.append(f"交易: {total}")
    report.append(f"胜: {wins}")
    report.append(f"败: {losses}")
    report.append(f"WinRate: {round(win_rate,1)}%")
    report.append(f"PF: {pf}")
    report.append("")
    report.append(f"最佳 Regime: {best_regime}")
    report.append(f"最佳 Feature: {best_feature}")
    report.append(f"最大亏损: {round(max_loss,4)}R")
    report.append(f"平均MFE: {round(sum_mfe / mfe_count,4) if mfe_count else 'N/A'}R")
    report.append(f"平均MAE: {round(sum_mae / mae_count,4) if mae_count else 'N/A'}R")
    report.append("==============================")
    report.append("")
    report.append("Top 赚钱组合（symbol, regime, feature）:")
    if top_combos:
        for (sym, rg, feat), val in top_combos:
            report.append(f"{sym} | {rg} | {feat} -> total_R={round(val,4)} count={group_counts.get((sym,rg,feat),0)}")
    else:
        report.append("N/A")
    report.append("")
    report.append("亏损最多组合：")
    if worst_combos:
        for (sym, rg, feat), val in reversed(worst_combos):
            report.append(f"{sym} | {rg} | {feat} -> total_R={round(val,4)} count={group_counts.get((sym,rg,feat),0)}")
    else:
        report.append("N/A")
    report.append("")
    report.append("EV -> 性能摘要:")
    try:
        ev_stats = ev_monitor.report()
        for evb in sorted(ev_stats.keys(), reverse=True):
            s = ev_stats[evb]
            report.append(
                f"EV={evb}: samples={s['samples']} winrate={s['win_rate']}% avg_R={s['avg_R']} avg_EV={s['avg_EV']} EV_error={s['EV_error']}"
            )
    except Exception:
        report.append("EV stats unavailable")

    out = "\n".join(report)
    out_dir = Path('reports')
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"daily_report_{start.date().isoformat()}.txt"
    out_file.write_text(out, encoding='utf-8')
    return out


if __name__ == '__main__':
    print(generate_daily_report())


def send_report_via_telegram(target_date: datetime = None):
    report = generate_daily_report(target_date)
    # 增加数据质量摘要
    try:
        dq = data_quality_check.run_data_quality_check(target_date)
        # 修复: 所有字段加 or 0 兜底，防止 None 拼入字符串导致显示"None"
        open_count = dq.get('open_count') or 0
        exit_count = dq.get('exit_count') or 0
        missing_count = dq.get('missing_open_without_exit') or 0
        duplicate_count = dq.get('duplicate_trade_ids') or 0
        features_empty_count = dq.get('features_empty') or 0
        summary = (
            f"\n\n数据质量:\n"
            f"OPEN数量: {open_count}\n"
            f"EXIT数量: {exit_count}\n"
            f"缺失: {missing_count}\n"
            f"trade_id重复: {duplicate_count}\n"
            f"features为空: {features_empty_count}\n"
        )
        report = report + summary
    except Exception:
        pass

    from utils.structured_logger import slog
    if send_telegram:
        try:
            send_telegram("📊 SMC BOT DAILY REPORT\n\n" + report)
            slog.info("[REPORT] Telegram report sent")
            return True
        except Exception as _e:
            slog.warning(f"[REPORT] Telegram report send failed: {_e}")
            return False
    slog.warning("[REPORT] Telegram report skipped (send_telegram not available)")
    return False


def start_daily_report_scheduler():
    import threading
    import time as _time
    from datetime import datetime as _dt, timedelta as _td
    from utils.structured_logger import slog
    slog.info("[REPORT] Daily report scheduler started")

    def _worker():
        slog.info("[REPORT] Daily report worker loop started (next send at UTC 00:00)")
        while True:
            now = _dt.utcnow()
            # 下一个 UTC 零点
            nxt = _dt(now.year, now.month, now.day) + _td(days=1)
            wait = (nxt - now).total_seconds()
            if wait > 0:
                _time.sleep(wait)
            try:
                send_report_via_telegram()
            except Exception:
                pass
            # 睡 24 小时
            _time.sleep(24 * 3600)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return t
# analytics/daily_report.py
import time
from collections import defaultdict


class DailyReport:
    def __init__(self):
        self.daily = defaultdict(int)
        self.trades = 0
        self.probes = 0
        self.candidates = 0

    def record_candidate(self):
        self.candidates += 1

    def record_trade(self, mode="NORMAL"):
        self.trades += 1
        if mode == "PROBE":
            self.probes += 1

    def record_reject(self, stage, reason):
        key = f"{stage}:{reason}"
        self.daily[key] += 1

    def generate(self):
        lines = []
        lines.append("========== V56 DAILY REPORT ==========")
        lines.append("")
        lines.append(f"候选信号: {self.candidates}")
        lines.append(f"正式交易: {self.trades - self.probes}")
        lines.append(f"Probe交易: {self.probes}")
        lines.append("")
        lines.append("---- Reject统计 ----")
        total = sum(self.daily.values())
        if total:
            for k, v in sorted(self.daily.items(), key=lambda x: x[1], reverse=True):
                pct = v / total * 100
                lines.append(f"{k}: {v} ({pct:.1f}%)")
        else:
            lines.append("暂无拒绝数据")
        lines.append("")
        lines.append(time.strftime("%Y-%m-%d %H:%M:%S"))
        return "\n".join(lines)


daily_report = DailyReport()