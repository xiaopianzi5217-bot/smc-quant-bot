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

    容错：mode 列缺失、旧库 schema、脏行均不抛到日报主流程。
    """
    if not hasattr(_backfill_from_cloud_v6_db, "_backfilled_date_keys"):
        _backfill_from_cloud_v6_db._backfilled_date_keys = set()
    try:
        date_key = target_date.date().isoformat() if target_date is not None else start.date().isoformat()
    except Exception:
        date_key = str(getattr(start, "date", lambda: start)())
    try:
        _backfill_from_cloud_v6_db._backfilled_date_keys.add(date_key)
    except Exception:
        pass

    db_candidates = [
        Path("data/v6_research.db"),
        Path("/app/data/v6_research.db"),
        Path(__file__).resolve().parent.parent / "data" / "v6_research.db",
    ]
    db_path = next((p for p in db_candidates if p.exists()), db_candidates[0])
    try:
        if not db_path.exists() or db_path.stat().st_size == 0:
            try:
                from v6_data_engine import pull_database_from_hub
                pull_database_from_hub()
            except Exception:
                pass
    except Exception:
        pass

    if not db_path.exists() or db_path.stat().st_size == 0:
        try:
            slog.warning("[DailyReport] 云端兜底跳过: v6_research.db 不存在或为空")
        except Exception:
            pass
        return []

    try:
        start_ts = int(calendar.timegm(start.timetuple()))
        end_ts = int(calendar.timegm(end.timetuple()))
    except Exception:
        return []

    rows = []
    try:
        conn = sqlite3.connect(str(db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        # 探测列，避免旧库无 mode 直接 OperationalError
        try:
            cur.execute("PRAGMA table_info(trade_snapshots)")
            cols = {str(r[1]) for r in cur.fetchall()}
        except Exception:
            cols = set()
        if not cols:
            conn.close()
            return []

        base_cols = [
            "signal_id", "symbol", "direction", "regime", "exit_reason",
            "exit_timestamp", "exit_price", "pnl_r", "confidence",
            "p_win_calibrated", "feature_hash", "max_forward_r", "max_adverse_r",
        ]
        select_cols = [c for c in base_cols if c in cols]
        has_mode = "mode" in cols
        if has_mode:
            select_cols.insert(4, "mode")  # after regime
        if "signal_id" not in cols:
            conn.close()
            return []

        sql = f"SELECT {', '.join(select_cols)} FROM trade_snapshots WHERE 1=1"
        params = []
        if "exit_reason" in cols:
            sql += " AND exit_reason IS NOT NULL AND exit_reason != '' AND exit_reason != 'OPEN'"
            sql += """ AND exit_reason NOT IN (
                'MANUAL_CLEANUP_DEPRECATED', 'FORCE_CLOSE_UNKNOWN', 'OPEN_STALE', 'STALE_OPEN_TIMEOUT',
                'RESEARCH_SHADOW_CLOSED', 'RESEARCH_OBSERVE', 'RESEARCH_SL', 'RESEARCH_TP1'
            )"""
        if "pnl_r" in cols:
            sql += " AND pnl_r IS NOT NULL"
        if "exit_timestamp" in cols:
            sql += " AND exit_timestamp IS NOT NULL AND exit_timestamp > 0 AND exit_timestamp >= ? AND exit_timestamp < ?"
            params.extend([start_ts, end_ts])
        if has_mode:
            sql += " AND (mode IS NULL OR UPPER(COALESCE(mode, '')) NOT IN ('SHADOW'))"
        if "signal_id" in cols:
            sql += " AND signal_id NOT LIKE 'RES_%' AND signal_id NOT LIKE 'RESEARCH_%'"
        sql += " ORDER BY exit_timestamp ASC" if "exit_timestamp" in cols else ""

        try:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        except Exception as _q_e:
            # 降级：最简查询，Python 侧再过滤
            try:
                slog.warning(f"[DailyReport] 主查询失败，降级简查: {_q_e}")
                simple = """
                    SELECT * FROM trade_snapshots
                    WHERE exit_reason IS NOT NULL AND exit_reason != 'OPEN'
                      AND pnl_r IS NOT NULL
                      AND exit_timestamp IS NOT NULL
                      AND exit_timestamp >= ? AND exit_timestamp < ?
                """
                cur.execute(simple, (start_ts, end_ts))
                rows = cur.fetchall()
            except Exception as _q2:
                slog.warning(f"[DailyReport] 简查也失败: {_q2}")
                rows = []
        conn.close()
    except Exception as e:
        try:
            slog.warning(f"[DailyReport] 云端 v6_research.db 兜底查询失败: {e}")
        except Exception:
            pass
        return []

    _SKIP_REASONS = {
        "OPEN", "MANUAL_CLEANUP_DEPRECATED", "FORCE_CLOSE_UNKNOWN", "OPEN_STALE",
        "STALE_OPEN_TIMEOUT", "RESEARCH_SHADOW_CLOSED", "RESEARCH_OBSERVE",
        "RESEARCH_SL", "RESEARCH_TP1",
    }
    events = []
    for row in rows:
        try:
            def _g(key, default=None):
                try:
                    return row[key]
                except Exception:
                    try:
                        return row[key] if key in row.keys() else default
                    except Exception:
                        return default

            sid = str(_g("signal_id") or "")
            if not sid or sid.startswith(("RES_", "RESEARCH_")):
                continue
            er = str(_g("exit_reason") or "")
            if er in _SKIP_REASONS or not er:
                continue
            mode = str(_g("mode") or "").upper()
            if mode == "SHADOW":
                continue
            try:
                pr = float(_g("pnl_r"))
            except Exception:
                continue
            feats = {}
            _fh = str(_g("feature_hash") or "")
            if _fh:
                feats["feature_hash"] = _fh[-10:] if len(_fh) > 10 else _fh
            ev = {
                "event": "EXIT",
                "trade_id": sid,
                "signal_id": sid,
                "symbol": _g("symbol") or "UNK",
                "direction": _g("direction") or "",
                "profit_r": pr,
                "pnl_r": pr,
                "mfe": _g("max_forward_r"),
                "mae": _g("max_adverse_r"),
                "regime": _g("regime") or "UNKNOWN",
                "features": feats,
                "mode": mode or "NORMAL",
                "exit_reason": er,
                "ev": _g("p_win_calibrated") if _g("p_win_calibrated") is not None else _g("confidence"),
                "cloud_backfill": True,
            }
            events.append(ev)
        except Exception:
            continue

    if events:
        try:
            slog.info(f"[DailyReport] 云端 v6_research.db 兜底读取 {len(events)} 笔已平仓记录")
        except Exception:
            pass
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
    feature_counts = {}  # 出现次数（仅诊断，不作为「最佳」依据）
    feature_pnl_sums = {}  # 当日按代表性 feature 累计 pnl_r
    regime_pnl_sums = {}   # 当日按 regime 累计 pnl_r
    group_sums = {}
    group_counts = {}
    gross_win = 0.0
    gross_loss = 0.0
    ev_monitor = EVMonitor()

    for ev in all_events:
        tid = str(ev.get('trade_id') or ev.get('signal_id') or '')
        if tid.startswith(('RES_', 'RESEARCH_')):
            continue
        # 影子模式 / 科研平仓不计入实盘日报
        _er = str(ev.get('exit_reason') or '')
        if _er in ('RESEARCH_SHADOW_CLOSED', 'RESEARCH_OBSERVE', 'RESEARCH_SL', 'RESEARCH_TP1'):
            continue
        if str(ev.get('mode') or '').upper() == 'SHADOW':
            continue
        pr = float(ev.get('profit_r') if ev.get('profit_r') is not None else (ev.get('pnl_r') or 0.0))
        # 脏 R 过滤：|R|>10 不计入主指标（仍可在 note 中暴露）
        if abs(pr) > 10.0:
            try:
                from utils.structured_logger import slog
                slog.warning(f"[DailyReport] 丢弃异常 profit_r={pr:.2f} tid={tid}")
            except Exception:
                pass
            continue
        total += 1
        if pr > 1e-9:
            gross_win += pr
        elif pr < -1e-9:
            gross_loss += abs(pr)
        # feed EV monitor
        try:
            ev_val = ev.get('ev')
            if ev_val is not None:
                ev_monitor.update(ev_val, pr)
        except Exception:
            pass
        if pr > 1e-9:
            wins += 1
        elif pr < -1e-9:
            losses += 1
        if pr < max_loss:
            max_loss = pr
        mfe = ev.get('mfe')
        mae = ev.get('mae')
        # 统计展示钳制：防止脏 MAE/MFE（如 -159 / +27）污染日报均值。
        # 语义：MFE（最大有利偏移）应 >= 0，MAE（最大不利偏移）应 <= 0。
        #       符号正常时按上限截断（如 +27 -> +5 / -159 -> -5）；
        #       符号异常（MFE<0 或 MAE>0）属脏数据，直接丢弃、不计入均值。
        _MFE_CAP, _MAE_CAP = 5.0, 5.0
        if mfe is not None:
            try:
                _m = float(mfe)
                if _m >= 0.0:
                    sum_mfe += _m if _m <= _MFE_CAP else _MFE_CAP
                    mfe_count += 1
                # 负向 MFE 属符号异常，丢弃不计入均值
            except Exception:
                pass
        if mae is not None:
            try:
                _a = float(mae)
                if _a <= 0.0:
                    sum_mae += _a if _a >= -_MAE_CAP else -_MAE_CAP
                    mae_count += 1
                # 正向 MAE 属符号异常，丢弃不计入均值
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
        # 只用 feature_hash 做组合归因，避免同一笔既按 hash 又按 OB 各计一次
        top_feat = 'NONE'
        if isinstance(features, dict) and features:
            for hash_key in ("feature_hash", "cloud_hash"):
                hv = features.get(hash_key)
                if hv and isinstance(hv, str) and hv:
                    top_feat = hv[-10:] if len(str(hv)) > 10 else str(hv)
                    break
            if top_feat == 'NONE':
                # 无 hash 时用稳定的单一标签，不把每个 True 旗标拆成多行
                for kk, vv in features.items():
                    if vv is True:
                        top_feat = str(kk)
                        break
                if top_feat == 'NONE' and features:
                    top_feat = str(next(iter(features.keys())))
        combo = (sym, rg, top_feat)
        group_sums[combo] = group_sums.get(combo, 0.0) + pr
        group_counts[combo] = group_counts.get(combo, 0) + 1
        # 与组合榜同一批样本：按当日累计 pnl_r 评选最佳 Feature / Regime
        feature_pnl_sums[top_feat] = feature_pnl_sums.get(top_feat, 0.0) + pr
        regime_pnl_sums[regime] = regime_pnl_sums.get(regime, 0.0) + pr

    win_rate = (wins / (wins + losses) * 100.0) if (wins + losses) > 0 else 0.0
    # 当日 PF：当日毛利 / 当日毛亏（不再用全局 OutcomeDatabase 污染）
    try:
        if total <= 0:
            pf = "N/A"
        elif gross_loss > 1e-12:
            pf = round(gross_win / gross_loss, 2)
        elif gross_win > 0:
            pf = "N/A(无亏损)"
        else:
            pf = "N/A"
    except Exception:
        pf = "N/A"

    # 【修复】最佳 Regime/Feature 按「当日累计 pnl_r」评选，与赚钱/亏损组合同一批样本；
    # 不再用出现次数（feature_counts），避免「出现最多但亏损」的 hash 被标成最佳。
    def _argmax_pnl(d):
        if not d:
            return 'N/A', 0.0
        k, v = max(d.items(), key=lambda x: x[1])
        return k, float(v)

    best_regime, best_regime_r = _argmax_pnl(regime_pnl_sums)
    best_feature, best_feature_r = _argmax_pnl(feature_pnl_sums)
    # 生成质量排名：赚钱榜只取 total_R>0，亏损榜只取 total_R<0（禁止正收益混入亏损榜）
    top_combos = []
    worst_combos = []
    try:
        profit_combos = [(k, v) for k, v in group_sums.items() if v > 0]
        loss_combos = [(k, v) for k, v in group_sums.items() if v < 0]
        top_combos = sorted(profit_combos, key=lambda x: x[1], reverse=True)[:5]
        worst_combos = sorted(loss_combos, key=lambda x: x[1])[:5]  # 最亏在前
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
    if best_regime != 'N/A':
        report.append(f"最佳 Regime: {best_regime} (total_R={round(best_regime_r, 4)})")
    else:
        report.append(f"最佳 Regime: N/A")
    if best_feature != 'N/A':
        report.append(f"最佳 Feature: feature_hash={best_feature} (total_R={round(best_feature_r, 4)})")
    else:
        report.append(f"最佳 Feature: N/A")
    report.append(f"最大亏损: {round(max_loss,4)}R")
    report.append(f"平均MFE: {round(sum_mfe / mfe_count,4)}R" if mfe_count else "平均MFE: N/A")
    report.append(f"平均MAE: {round(sum_mae / mae_count,4)}R" if mae_count else "平均MAE: N/A")
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
        for (sym, rg, feat), val in worst_combos:
            report.append(f"{sym} | {rg} | {feat} -> total_R={round(val,4)} count={group_counts.get((sym,rg,feat),0)}")
    else:
        report.append("N/A")
    report.append("")
    report.append("EV -> 性能摘要:")
    try:
        ev_stats = ev_monitor.report()
        if not ev_stats:
            report.append("N/A")
        else:
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
    # 出报前：对账超时仍为 OPEN 的快照，避免 OPEN/EXIT 缺口
    try:
        from v6_data_engine import reconcile_stale_open_snapshots
        reconcile_stale_open_snapshots(max_age_sec=14400)
        try:
            from v6_data_engine import close_research_open_snapshots
            close_research_open_snapshots()
        except Exception:
            pass
    except Exception:
        pass
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
