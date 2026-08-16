# -*- coding: utf-8 -*-
"""
每日监控面板 (Daily Panel)
每天 UTC+8 0点输出交易统计数据到 Telegram
"""
import json
import os
import time
import math
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Any
from utils.structured_logger import slog


class DailyPanel:
    """日交易统计面板"""

    def __init__(self, panel_path: str = "data/daily_panel.json",
                 history_path: str = "data/daily_panel_history.json"):
        self.panel_path = Path(panel_path)
        self.history_path = Path(history_path)
        self.panel_path.parent.mkdir(parents=True, exist_ok=True)

        # 当日累计数据
        self.data: Dict[str, Any] = self._load_or_init()
        self._last_report_date: str = ""

        # 历史汇总（跨日 KV）
        self.history: Dict[str, Dict] = self._load_history()

        # 概率准确度跟踪
        self.probability_bins: Dict[str, Dict] = defaultdict(
            lambda: {"correct": 0, "total": 0}
        )

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def _load_or_init(self) -> dict:
        if self.panel_path.exists():
            try:
                return json.loads(self.panel_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return self._empty_data()

    def _empty_data(self) -> dict:
        return {
            "date": self._today_str(),
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "total_r": 0.0,
            "best_r": -99.0,
            "worst_r": 99.0,
            "feature_wins": defaultdict(int),
            "feature_losses": defaultdict(int),
            "feature_total_r": defaultdict(float),
            "feature_regime_wins": defaultdict(int),
            "feature_regime_losses": defaultdict(int),
            "feature_regime_total_r": defaultdict(float),
            "regime_trades": defaultdict(lambda: {"wins": 0, "losses": 0, "r": 0.0}),
            # V59.5: Score 区间统计（亏损集中分析）
            "score_bucket_stats": defaultdict(lambda: {"wins": 0, "losses": 0, "total": 0, "r": 0.0}),
        }

    def _save(self):
        try:
            self.panel_path.write_text(
                json.dumps(dict(self.data), indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
        except Exception as e:
            slog.error(f"[DailyPanel] save failed: {e}")

    def _load_history(self) -> dict:
        if self.history_path.exists():
            try:
                return json.loads(self.history_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save_history(self):
        try:
            self.history_path.write_text(
                json.dumps(self.history, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
        except Exception as e:
            slog.error(f"[DailyPanel] save_history failed: {e}")

    # ------------------------------------------------------------------
    # 日期工具
    # ------------------------------------------------------------------
    @staticmethod
    def _today_str() -> str:
        """返回 UTC+8 日期字符串 YYYY-MM-DD"""
        # 注意：time.time() 是 UTC，加 8 小时到 UTC+8
        utc8_ts = time.time() + 28800
        import datetime
        return datetime.datetime.fromtimestamp(utc8_ts).strftime("%Y-%m-%d")

    def _check_new_day(self):
        """检测是否跨日，跨日则固化当天数据并重置"""
        today = self._today_str()
        current_date = self.data.get("date", "")
        if current_date and current_date != today:
            # 固化到历史
            if current_date not in self.history:
                self.history[current_date] = dict(self.data)
                # 转换 defaultdict 为普通 dict
                self.history[current_date] = json.loads(
                    json.dumps(self.history[current_date], default=dict)
                )
                self._save_history()
            # 重置
            self.data = self._empty_data()
            self.probability_bins.clear()
            self._last_report_date = ""
        self.data["date"] = today

    # ------------------------------------------------------------------
    # 交易记录接口
    # ------------------------------------------------------------------
    def on_trade_closed(self, regime: str, features: List[str],
                        score: float, confidence: float,
                        pnl_r: float, direction: str = ""):
        """每次平仓时调用"""
        self._check_new_day()

        d = self.data
        d["total_trades"] += 1
        d["total_r"] = round(d.get("total_r", 0.0) + pnl_r, 4)

        if pnl_r > 0.2:  # 噪音过滤
            d["wins"] += 1
        elif pnl_r < -0.2:
            d["losses"] += 1

        # 最佳/最差 R
        d["best_r"] = max(d.get("best_r", -99.0), pnl_r)
        d["worst_r"] = min(d.get("worst_r", 99.0), pnl_r)

        # 特征统计
        feat_key = "+".join(sorted(features)) if features else "NONE"
        if pnl_r > 0.2:
            d["feature_wins"][feat_key] = d["feature_wins"].get(feat_key, 0) + 1
        elif pnl_r < -0.2:
            d["feature_losses"][feat_key] = d["feature_losses"].get(feat_key, 0) + 1
        d["feature_total_r"][feat_key] = d["feature_total_r"].get(feat_key, 0.0) + pnl_r

        # 特征+Regime 统计
        feat_regime_key = f"{feat_key}|{regime}"
        if pnl_r > 0.2:
            d["feature_regime_wins"][feat_regime_key] = d["feature_regime_wins"].get(feat_regime_key, 0) + 1
        elif pnl_r < -0.2:
            d["feature_regime_losses"][feat_regime_key] = d["feature_regime_losses"].get(feat_regime_key, 0) + 1
        d["feature_regime_total_r"][feat_regime_key] = d["feature_regime_total_r"].get(feat_regime_key, 0.0) + pnl_r

                # Regime 统计
        reg = d["regime_trades"].get(regime, {"wins": 0, "losses": 0, "r": 0.0})
        reg["r"] += pnl_r
        if pnl_r > 0.2:
            reg["wins"] += 1
        elif pnl_r < -0.2:
            reg["losses"] += 1
        d["regime_trades"][regime] = reg

        # V59.5: Score 区间统计（亏损集中分析）
        # 区间划分: 0-60, 60-70, 70-75, 75-78, 78-80, 80-85, 85-90, 90-100
        _score_floor = 0
        if score >= 60: _score_floor = 60
        if score >= 70: _score_floor = 70
        if score >= 75: _score_floor = 75
        if score >= 78: _score_floor = 78
        if score >= 80: _score_floor = 80
        if score >= 85: _score_floor = 85
        if score >= 90: _score_floor = 90
        _bucket_key = f"{_score_floor}-{_score_floor + 5 if _score_floor < 90 else 100}"
        if _score_floor == 60: _bucket_key = "60-70"
        elif _score_floor == 70: _bucket_key = "70-75"
        elif _score_floor == 75: _bucket_key = "75-78"
        elif _score_floor == 78: _bucket_key = "78-80"
        elif _score_floor == 80: _bucket_key = "80-85"
        elif _score_floor == 85: _bucket_key = "85-90"
        elif _score_floor == 90: _bucket_key = "90-100"
        b = d["score_bucket_stats"].get(_bucket_key, {"wins": 0, "losses": 0, "total": 0, "r": 0.0})
        b["total"] += 1
        b["r"] += pnl_r
        if pnl_r > 0.2:
            b["wins"] += 1
        elif pnl_r < -0.2:
            b["losses"] += 1
        d["score_bucket_stats"][_bucket_key] = b

        # 概率准确度
        bin_key = str(int(score // 10) * 10)
        p_bin = self.probability_bins[bin_key]
        p_bin["total"] += 1
        # confidence > 0.5 且 pnl_r > 0 为正确；confidence < 0.5 且 pnl_r < 0 也为正确
        prob_correct = (confidence > 0.5 and pnl_r > 0) or (confidence < 0.5 and pnl_r < 0)
        if abs(pnl_r) > 0.2:  # 只在有明确结果时统计
            if prob_correct:
                p_bin["correct"] += 1

        self._save()

    # ------------------------------------------------------------------
    # 云端兜底：从 HF v6_research.db 拉取真实已平仓记录
    # ------------------------------------------------------------------
    def _backfill_from_cloud_v6(self) -> int:
        """本地无平仓事件时，从云端 v6_research.db 兜底读取已平仓记录。

        数据源：云端私有数据集 v6_research.db（trade_snapshots 表，真实结果）
        策略：读取最近7天 exit_reason != 'OPEN' 且 pnl_r 非空的记录灌入面板。
              放宽为7天窗口是因为云端同步/重启可能有延迟，
              避免“今日无记录就永远空”的死锁。
        防重：进程内 _cloud_backfilled 标记，仅首次执行一次。
        失败/无数据静默返回 0，不影响原逻辑。
        """
        if getattr(self, "_cloud_backfilled", False):
            return 0
        self._cloud_backfilled = True

        # 1) 本地 v6_research.db 缺失/为空时，尝试拉取云端最新
        db_path = Path("data/v6_research.db")
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
            slog.warning("[DailyPanel] 云端兜底跳过: v6_research.db 不存在或为空")
            return 0

        # 2) 最近7天 UTC epoch 窗口
        import datetime as _dt
        try:
            _now = _dt.datetime.utcnow()
            _start = _now - _dt.timedelta(days=7)
            _start_ts = _start.timestamp()
            _end_ts = _now.timestamp() + 86400
        except Exception:
            return 0

        # 3) 查询最近7天已平仓记录
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                SELECT signal_id, symbol, direction, regime, mode,
                       exit_reason, exit_timestamp, exit_price, pnl_r,
                       confidence, p_win_calibrated, feature_hash
                FROM trade_snapshots
                WHERE exit_reason IS NOT NULL
                  AND exit_reason != ''
                  AND exit_reason != 'OPEN'
                  AND pnl_r IS NOT NULL
                  AND exit_timestamp IS NOT NULL
                  AND exit_timestamp > 0
                  AND exit_timestamp >= ?
                  AND exit_timestamp <= ?
                ORDER BY exit_timestamp ASC
                """,
                (_start_ts, _end_ts),
            )
            rows = cur.fetchall()
            conn.close()
        except Exception as e:
            slog.warning(f"[DailyPanel] 云端 v6_research.db 兜底查询失败: {e}")
            return 0

        if not rows:
            slog.info("[DailyPanel] 云端 v6_research.db 最近7天无已平仓记录")
            return 0

        # 4) 灌入面板（复用 on_trade_closed 相同统计逻辑）
        added = 0
        for row in rows:
            try:
                pnl_r = float(row["pnl_r"] or 0.0)
                regime = str(row["regime"] or "UNKNOWN")
                confidence = float(row["confidence"] or 0.5)
                score = float(row["p_win_calibrated"] or 0.0) * 100.0
                feats = []
                _fh = str(row["feature_hash"] or "")
                if _fh:
                    feats.append(f"h{_fh[-10:]}")
                _mode = str(row["mode"] or "NORMAL")
                if _mode and _mode != "NORMAL":
                    feats.append(_mode)
                if not feats:
                    feats.append("CLOUD_V6")
                self.on_trade_closed(
                    regime=regime,
                    features=feats,
                    score=score,
                    confidence=confidence,
                    pnl_r=pnl_r,
                    direction=str(row["direction"] or "Long"),
                )
                added += 1
            except Exception:
                continue

        if added > 0:
            slog.info(f"[DailyPanel] 云端 v6_research.db 兜底灌入 {added} 笔已平仓记录")
        return added

    # ------------------------------------------------------------------
    # 报告生成
    # ------------------------------------------------------------------
    def generate_report(self) -> Optional[str]:
        """生成当日统计摘要（用于推送）"""
        self._check_new_day()
        d = self.data
        total = d.get("total_trades", 0)
        # 【修复 20260814】本地无平仓事件时，云端 v6_research.db 兜底（防重）
        if total == 0:
            self._backfill_from_cloud_v6()
            d = self.data
            total = d.get("total_trades", 0)
        if total == 0:
            return None

        wins = d.get("wins", 0)
        losses = d.get("losses", 0)
        total_r = d.get("total_r", 0.0)
        best_r = d.get("best_r", 0.0)
        worst_r = d.get("worst_r", 0.0)

        # 胜率
        winrate = wins / max(wins + losses, 1) * 100

        # PF
        gross_win = sum(v for v in d.get("feature_total_r", {}).values() if v > 0)
        gross_loss = abs(sum(v for v in d.get("feature_total_r", {}).values() if v < 0))
        pf = round(gross_win / max(gross_loss, 0.0001), 2)

        # Average R
        avg_r = round(total_r / total, 4)

        # Best / Worst 特征组合
        feat_winrate = {}
        all_feats = set(list(d.get("feature_wins", {}).keys()) + list(d.get("feature_losses", {}).keys()))
        for fk in all_feats:
            fw = d.get("feature_wins", {}).get(fk, 0)
            fl = d.get("feature_losses", {}).get(fk, 0)
            ft = fw + fl
            if ft >= 3:  # 至少 3 笔才有统计意义
                feat_winrate[fk] = (fw / ft, fw, fl, d.get("feature_total_r", {}).get(fk, 0.0))

        # 特征+Regime Best / Worst
        feat_regime_winrate = {}
        all_feat_regime = set(
            list(d.get("feature_regime_wins", {}).keys()) +
            list(d.get("feature_regime_losses", {}).keys())
        )
        for frk in all_feat_regime:
            frw = d.get("feature_regime_wins", {}).get(frk, 0)
            frl = d.get("feature_regime_losses", {}).get(frk, 0)
            frt = frw + frl
            if frt >= 3:
                feat_regime_winrate[frk] = (
                    frw / frt, frw, frl, d.get("feature_regime_total_r", {}).get(frk, 0.0)
                )

        # Best feature (max winrate, min 3 trades)
        best_feat = "N/A"
        worst_feat = "N/A"
        if feat_winrate:
            best_feat = max(feat_winrate, key=lambda k: feat_winrate[k][0])
            worst_feat = min(feat_winrate, key=lambda k: feat_winrate[k][0])

        # Best feature+regime
        best_feat_regime = "N/A"
        worst_feat_regime = "N/A"
        if feat_regime_winrate:
            best_feat_regime = max(feat_regime_winrate, key=lambda k: feat_regime_winrate[k][0])
            worst_feat_regime = min(feat_regime_winrate, key=lambda k: feat_regime_winrate[k][0])

        # 概率准确度
        total_correct = sum(pb["correct"] for pb in self.probability_bins.values())
        total_prob = sum(pb["total"] for pb in self.probability_bins.values())
        prob_acc = round(total_correct / max(total_prob, 1) * 100, 1)

        # Regime 分布
        regime_lines = []
        for regime, rd in sorted(d.get("regime_trades", {}).items(),
                                 key=lambda x: x[1]["r"], reverse=True):
            rt = rd["wins"] + rd["losses"]
            rwr = rd["wins"] / max(rt, 1) * 100
            regime_lines.append(f"  {regime}: {rt}笔 {rwr:.0f}%WR R={rd['r']:+.2f}")

        regime_text = "\n".join(regime_lines) if regime_lines else "  无"

        # ===== V59.5: Score 区间亏损集中度分析 =====
        # 输出: 哪个 score 区间亏损最集中 → 决定是否继续优化该区间信号
        score_bucket_lines = []
        _buckets = d.get("score_bucket_stats", {})
        # 筛选有足够样本量的区间 (>=2 笔)
        _from_closed_rows = False
        if not _buckets:
            # 从 trade_journal 兜底读取今日已平仓记录，避免日报无数据
            try:
                from state.trade_journal import journal as _tj_local
                _closes = [r for r in _tj_local.load_all() if r.get("status") == "CLOSE" and str(r.get("close_time", ""))[:10] == d.get("date", "")]
                if _closes:
                    _from_closed_rows = True
                    from collections import defaultdict as _dd
                    _buckets = _dd(lambda: {"wins": 0, "losses": 0, "total": 0, "r": 0.0})
                    for _r in _closes:
                        try:
                            _sc = float(_r.get("score", 0) or 0)
                        except (ValueError, TypeError):
                            _sc = 0
                        _pnl_r_local = float(_r.get("pnl_r", 0) or 0)
                        # 同上方区间逻辑
                        _floor = 0
                        if _sc >= 60: _floor = 60
                        if _sc >= 70: _floor = 70
                        if _sc >= 75: _floor = 75
                        if _sc >= 78: _floor = 78
                        if _sc >= 80: _floor = 80
                        if _sc >= 85: _floor = 85
                        if _sc >= 90: _floor = 90
                        _bk = f"{_floor}-{_floor + 5 if _floor < 90 else 100}"
                        if _floor == 60: _bk = "60-70"
                        elif _floor == 70: _bk = "70-75"
                        elif _floor == 75: _bk = "75-78"
                        elif _floor == 78: _bk = "78-80"
                        elif _floor == 80: _bk = "80-85"
                        elif _floor == 85: _bk = "85-90"
                        elif _floor == 90: _bk = "90-100"
                        _b = _buckets[_bk]
                        _b["total"] += 1
                        _b["r"] += _pnl_r_local
                        if _pnl_r_local > 0.2: _b["wins"] += 1
                        elif _pnl_r_local < -0.2: _b["losses"] += 1
            except Exception:
                pass
        if _buckets:
            # 计算各区间亏损率，排序输出
            _bucket_analysis = []
            for _bk_name, _bd in _buckets.items():
                _bt = _bd.get("total", 0)
                if _bt == 0:
                    continue
                _bl = _bd.get("losses", 0)
                _bw = _bd.get("wins", 0)
                _loss_rate = _bl / max(_bt, 1) * 100
                _br = _bd.get("r", 0.0)
                _bucket_analysis.append((_bk_name, _bt, _bw, _bl, _loss_rate, _br))
            if _bucket_analysis:
                # 按亏损率降序排序 → 亏损最集中的区间排最前
                _bucket_analysis.sort(key=lambda x: -x[4])
                score_bucket_lines.append("**V59.3 今日交易质量:**")
                                # 区间明细已足够，直接输出
                for _bk_name, _bt, _bw, _bl, _loss_rate, _br in _bucket_analysis:
                    _marker = " 🎯亏损集中" if _loss_rate >= 50 and _bt >= 2 else ""
                    score_bucket_lines.append(
                        f"  {_bk_name}: {_bt}笔(盈{_bw}/亏{_bl}) 亏损率{_loss_rate:.0f}% R={_br:+.2f}{_marker}"
                    )

        # 构建消息
        msg_lines = [
            f"📊 【日交易面板】{d.get('date', '?')}",
            f"交易: {total} 笔 | 赢: {wins} 亏: {losses}",
            f"胜率: {winrate:.0f}% | PF: {pf} | 平均R: {avg_r:+.2f}",
            f"最佳R: {best_r:+.2f} | 最差R: {worst_r:+.2f}",
            f"总R: {total_r:+.2f}",
            "",
            f"🏆 最佳特征: {best_feat}",
            f"⚠️ 最差特征: {worst_feat}",
        ]
        if best_feat_regime != "N/A":
            msg_lines.append(f"🏆 最佳特征+行情: {best_feat_regime}")
        if worst_feat_regime != "N/A":
            msg_lines.append(f"⚠️ 最差特征+行情: {worst_feat_regime}")
            msg_lines.append("")
        msg_lines.append(f"🎯 概率预测准确率: {prob_acc}% ({total_correct}/{total_prob})")
        # ===== V59.5: Entry Quality Report 亏损集中分析 =====
        if score_bucket_lines:
            msg_lines.extend(score_bucket_lines)
            msg_lines.append("")
        msg_lines.append(f"📈 行情分布:")
        msg_lines.append(regime_text)
        msg_lines.append("")
        msg_lines.append("---")
        msg_lines.append("自动生成 | 数据实时更新")

        return "\n".join(msg_lines)

    def try_send_report(self, send_func, today_report_sent: List[bool]) -> bool:
        """每日定时（跨日后第一条数据）推送报告。

        Args:
            send_func: 推送函数，签名 send_func(msg: str) -> str
            today_report_sent: 外部维护的[bool]标记，防止重复推送

        Returns:
            是否推送了报告
        """
        self._check_new_day()
        today = self._today_str()

        if today_report_sent and today_report_sent[0]:
            return False  # 今天已经推送过了

        msg = self.generate_report()
        if msg:
            try:
                send_func(msg)
                slog.info(f"[DailyPanel] 报告已推送: {today}")
                if today_report_sent:
                    today_report_sent[0] = True
                return True
            except Exception as e:
                slog.error(f"[DailyPanel] 推送失败: {e}")
        return False


# 全局单例
_daily_panel: Optional[DailyPanel] = None


def get_daily_panel() -> DailyPanel:
    global _daily_panel
    if _daily_panel is None:
        _daily_panel = DailyPanel()
    return _daily_panel
