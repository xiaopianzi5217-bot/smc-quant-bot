# -*- coding: utf-8 -*-
"""
一次性补丁：DailyPanel 云端兜底修复（日报无数据）
根因: 本地无平仓事件流 (trade_journal CLOSE=0 / events.jsonl 空)
     但云端 v6_research.db 有真实已平仓记录（实测6条，exit_reason非OPEN）。
修复: generate_report() 本地数据为空时，自动从云端
      v6_research.db (trade_snapshots) 兜底读取最近7天已平仓记录。
防重: 进程内 _cloud_backfilled 标记，仅首次兜底执行一次。
     只改报表侧，不动交易引擎；失败静默回退，可安全重复执行。
"""
from pathlib import Path

p = Path("utils/daily_panel.py")
src = p.read_text(encoding="utf-8")
src = src.replace("\r\n", "\n")

# 1) import 补充 sqlite3 / time
old_imp = "import json\nimport os\nimport time\nimport math\n"
new_imp = "import json\nimport os\nimport time\nimport math\nimport sqlite3\n"
assert old_imp in src, "[1] import anchor missing"
src = src.replace(old_imp, new_imp, 1)

# 2) 在报告生成分区前插入云端兜底方法
anchor = "    # ------------------------------------------------------------------\n    # 报告生成\n    # ------------------------------------------------------------------\n    def generate_report"
assert anchor in src, "[2] report anchor missing"

method = '''    # ------------------------------------------------------------------
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

'''

src = src.replace(anchor, method + anchor, 1)

# 3) generate_report 开头增加兜底触发
old_gen = '''    def generate_report(self) -> Optional[str]:
        """生成当日统计摘要（用于推送）"""
        self._check_new_day()
        d = self.data
        total = d.get("total_trades", 0)
        if total == 0:
            return None
'''
new_gen = '''    def generate_report(self) -> Optional[str]:
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
'''
assert old_gen in src, "[3] generate_report anchor missing"
src = src.replace(old_gen, new_gen, 1)

p.write_text(src, encoding="utf-8")
print("PATCH_OK")