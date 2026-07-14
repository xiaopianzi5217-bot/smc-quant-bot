# -*- coding: utf-8 -*-
"""
每日监控面板 (Daily Panel)
每天 UTC+8 0点输出交易统计数据到 Telegram
"""
import json
import os
import time
import math
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Any


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
        }

    def _save(self):
        try:
            self.panel_path.write_text(
                json.dumps(dict(self.data), indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
        except Exception as e:
            print(f"[DailyPanel] save failed: {e}")

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
            print(f"[DailyPanel] save_history failed: {e}")

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
    # 报告生成
    # ------------------------------------------------------------------
    def generate_report(self) -> Optional[str]:
        """生成当日统计摘要（用于推送）"""
        self._check_new_day()
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
                print(f"[DailyPanel] 报告已推送: {today}")
                if today_report_sent:
                    today_report_sent[0] = True
                return True
            except Exception as e:
                print(f"[DailyPanel] 推送失败: {e}")
        return False


# 全局单例
_daily_panel: Optional[DailyPanel] = None


def get_daily_panel() -> DailyPanel:
    global _daily_panel
    if _daily_panel is None:
        _daily_panel = DailyPanel()
    return _daily_panel
