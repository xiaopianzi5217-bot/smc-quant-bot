"""
Daily report generator for outcomes and events.

Produces a human-readable summary for a given day (default: today).
"""
from pathlib import Path
import json
from datetime import datetime, timedelta
from analytics.outcome_db import OutcomeDatabase
from analytics import data_quality_check
from analytics.ev_monitor import EVMonitor
try:
    from notifier.telegram import send_telegram
except Exception:
    send_telegram = None


def _parse_iso(ts: str):
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        try:
            return datetime.utcfromtimestamp(float(ts))
        except Exception:
            return None


def generate_daily_report(target_date: datetime = None) -> str:
    if target_date is None:
        target_date = datetime.utcnow()

    start = datetime(target_date.year, target_date.month, target_date.day)
    end = start + timedelta(days=1)

    event_file = Path("data/events.jsonl")
    if not event_file.exists():
        return "No events file found."

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
            # 选取一个代表性 feature 名称（尽量挑布尔/标志类），否则第一个 key
            top_feat = 'NONE'
            if isinstance(features, dict) and features:
                # 尝试找到值为 True 或非空字符串的 key
                found = None
                for kk, vv in features.items():
                    if vv is True or (isinstance(vv, str) and vv):
                        found = kk
                        break
                if not found:
                    # 选择第一个 key
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
        summary = (
            f"\n\n数据质量:\nOPEN数量: {dq.get('open_count')}\nEXIT数量: {dq.get('exit_count')}\n缺失: {dq.get('missing_open_without_exit')}\ntrade_id重复: {dq.get('duplicate_trade_ids')}\nfeatures为空: {dq.get('features_empty')}\n"
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