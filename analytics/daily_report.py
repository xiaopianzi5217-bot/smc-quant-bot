"""
Daily report generator for outcomes and events.

Produces a human-readable summary for a given day (default: today).
"""
from pathlib import Path
import json
import sqlite3
import calendar
from collections import defaultdict
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
    防重：进程级_按目标日期 key 去重（_backfilled_date_keys），保证每日期独立执行。
    失败/无数据静默返回 []，不影响原逻辑。
    """
    # [修复] 进程级一次性全局标记问题导致的跨日期失效：
    #   原实现: 单个 _backfilled=True 使首日成功后其它日期永远跳过 DB backfill。
    #   改为按目标日期 key 去重，保证每个日期独立执行 DB 兜底。
    if not hasattr(_backfill_from_cloud_v6_db, "_backfilled_date_keys"):
        _backfill_from_cloud_v6_db._backfilled_date_keys = set()
    try:
        date_key = target_date.date().isoformat() if target_date is not None else start.date().isoformat()
    except Exception:
        date_key = str(start.date())
    # 允许重复查询（日报/质量检查可能多次调用），不再永久跳过
    _backfill_from_cloud_v6_db._backfilled_date_keys.add(date_key)

    db_candidates = [
        Path("data/v6_research.db"),
        Path("/app/data/v6_research.db"),
        Path(__file__).resolve().parent.parent / "data" / "v6_research.db",
    ]
    db_path = next((p for p in db_candidates if p.exists()), db_candidates[0])
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
                            AND exit_reason NOT IN ('MANUAL_CLEANUP_DEPRECATED', 'FORCE_CLOSE_UNKNOWN', 'OPEN_STALE')
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


def _day_bounds_utc8(target_date: datetime = None):
    """按 UTC+8 自然日切分（与 bot 日志时区一致）。返回 (start_utc, end_utc, date_str)。"""
    if target_date is None:
        # 默认：UTC+8 的“昨天”（日报在次日凌晨发前一天）
        now_utc8 = datetime.utcnow() + timedelta(hours=8)
        target_date = (now_utc8 - timedelta(days=1)).replace(tzinfo=None)
    # target_date 视为 UTC+8 日历日
    if getattr(target_date, "tzinfo", None) is not None:
        local = target_date.astimezone(timezone(timedelta(hours=8)))
    else:
        local = target_date
    start_local = datetime(local.year, local.month, local.day)
    end_local = start_local + timedelta(days=1)
    # 转 UTC 用于 timestamp 过滤
    start = (start_local - timedelta(hours=8)).replace(tzinfo=timezone.utc)
    end = (end_local - timedelta(hours=8)).replace(tzinfo=timezone.utc)
    return start, end, start_local.strftime("%Y-%m-%d")


def generate_daily_report(target_date: datetime = None) -> str:
    start, end, date_str = _day_bounds_utc8(target_date)

    event_file = Path("data/events.jsonl")

    # ---- 1) 读取本地 events.jsonl 的 EXIT ----
    all_events = []
    seen_ids = set()
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
                tid = str(ev.get('trade_id') or ev.get('signal_id') or '')
                if tid.startswith(('bad_', 'test_', 'debug_', 'manual_', 'stale_')):
                    continue
                if not tid:
                    tid = f"anon_{ev.get('event_id') or id(ev)}"
                if tid in seen_ids:
                    continue
                seen_ids.add(tid)
                # 统一字段
                if ev.get('profit_r') is None and ev.get('pnl_r') is not None:
                    ev['profit_r'] = ev.get('pnl_r')
                all_events.append(ev)

    # ---- 2) 始终用 v6_research.db 合并补全（不再仅在 events 为空时）----
    try:
        db_events = _backfill_from_cloud_v6_db(target_date, start, end)
        for ev in db_events:
            tid = str(ev.get('trade_id') or '')
            if not tid or tid in seen_ids:
                continue
            if tid.startswith(('bad_', 'test_', 'debug_', 'manual_', 'stale_')):
                continue
            seen_ids.add(tid)
            all_events.append(ev)
    except Exception as _bf_e:
        try:
            from utils.structured_logger import slog
            slog.warning(f"[DailyReport] DB 合并失败: {_bf_e}")
        except Exception:
            pass

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
    gross_win = 0.0
    gross_loss = 0.0
    ev_monitor = EVMonitor()

    for ev in all_events:
        total += 1
        pr = float(ev.get('profit_r') if ev.get('profit_r') is not None else (ev.get('pnl_r') or 0.0))
        if pr > 0:
            gross_win += pr
        elif pr < 0:
            gross_loss += abs(pr)
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
    # 当日 PF：当日毛利 / 当日毛亏（不再用全局 OutcomeDatabase 污染）
    try:
        if total <= 0:
            pf = "N/A"
        elif gross_loss > 1e-12:
            pf = round(gross_win / gross_loss, 2)
        elif gross_win > 0:
            pf = "inf"
        else:
            pf = "N/A"
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
    report.append(f"Date: {date_str}")
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



# ============================================================
#   compatible in-memory DailyReport singleton
#   (legacy counter used by hf_auto_trader / reject_analytics)
# ============================================================
class DailyReport:
    """轻量兼容计数器——被 hf_auto_trader / reject_analytics 依赖。

    注意: 新版 generate_daily_report() 走 events.jsonl + v6_research.db
    双链路生成报告；这里保留的 record_candidate / record_trade /
    record_reject 仅为保持 V56.5 遗留导入不报错，并提供
    candidates/trades/probes/daily 的进程内统计。
    """

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

    def generate(self) -> str:
        """进程内统计的快照文本（供调试/日志）。"""
        lines = []
        lines.append("========== V56 DAILY REPORT (in-memory) ==========")
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
        lines.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        return "\n".join(lines)


# 全局兼容单例，供 hf_auto_trader / reject_analytics 导入
daily_report = DailyReport()
