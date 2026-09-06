# -*- coding: utf-8 -*-
"""回归测试：防止 _backfill_from_cloud_v6_db 的按日期 key 去重机制被改回全局 bool。"""
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from analytics.daily_report import _backfill_from_cloud_v6_db


def _db_has_real_exit_rows(db_path="data/v6_research.db"):
    """判断 DB 是否有可用于体验真实数据的已清仓行。"""
    if not Path(db_path).exists() or Path(db_path).stat().st_size == 0:
        return False
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT 1 FROM trade_snapshots
            WHERE exit_reason IS NOT NULL
              AND exit_reason != ''
              AND exit_reason != 'OPEN'
              AND exit_reason NOT IN ('MANUAL_CLEANUP_DEPRECATED', 'FORCE_CLOSE_UNKNOWN', 'OPEN_STALE')
              AND pnl_r IS NOT NULL
              AND exit_timestamp IS NOT NULL
              AND exit_timestamp > 0
            LIMIT 1
            """
        )
        has = cur.fetchone() is not None
        conn.close()
        return has
    except Exception:
        return False


def _get_real_db_dates(db_path="data/v6_research.db", limit=2):
    """返回有真实已清仓行的最早两个日期（UTC iso date/秒级窗口）。"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT exit_timestamp
        FROM trade_snapshots
        WHERE exit_reason IS NOT NULL
          AND exit_reason != ''
          AND exit_reason != 'OPEN'
          AND exit_reason NOT IN ('MANUAL_CLEANUP_DEPRECATED', 'FORCE_CLOSE_UNKNOWN', 'OPEN_STALE')
          AND pnl_r IS NOT NULL
          AND exit_timestamp IS NOT NULL
          AND exit_timestamp > 0
        ORDER BY exit_timestamp ASC
        """
    )
    ep_vals = [r[0] for r in cur.fetchall()]
    conn.close()
    # 用 python 将 epoch 秒归一为日期字符串，避免 unixepoch 函数对异常 epoch 的宽容差异
    dates = []
    for ep in ep_vals:
        try:
            d = datetime.fromtimestamp(float(ep), timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            continue
        if d not in dates:
            dates.append(d)
    return dates[:limit]


def test_backfill_date_isolation_with_two_distinct_dates():
    """
    核心回归：调用 _backfill_from_cloud_v6_db 处理两个日期时,
    后一个日期不应因前一个日期的 backfill 成功而返回 []。
    (旧实现: 全局 _backfilled=True 导致第二日期永久返回 []。)
    """
    if not _db_has_real_exit_rows():
        print("SKIP: DB 无真实已清仓数据（可能未 pull），跳过真实日期隔离检查")
        return

    dates = _get_real_db_dates(limit=2)
    assert len(dates) >= 2, f"需要至少 2 个不同日期做隔离验证, 只有 {dates}"

    results = []
    for d in dates:
        y, m, dd = map(int, d.split("-"))
        target = datetime(y, m, dd, tzinfo=timezone.utc)
        start = datetime(y, m, dd, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        evts = _backfill_from_cloud_v6_db(target, start, end)
        results.append(len(evts))
        assert evts, f"日期 {d} 应至少有 1 笔真实 DB 兜底记录, 得到 0 笔"

    print(f"回归通过: 两个日期的 DB 兜底分别返回 {results} 笔 → 无跨日期误清空")


def test_backfill_same_date_deduplicated():
    """同一天第二次调用应返回 []（一次性执行语义保留）。"""
    # 若无真实数据则跳过
    if not _db_has_real_exit_rows():
        print("SKIP: DB 无真实数据，跳过同日去重测试")
        return

    # 清理函数级去重状态，确保此前其它测试（如跨日期隔离测试）已执行的日期
    # 不会让本测试的"首次调用"被误判为二次调用
    if hasattr(_backfill_from_cloud_v6_db, "_backfilled_date_keys"):
        _backfill_from_cloud_v6_db._backfilled_date_keys = set()

    d = _get_real_db_dates(limit=1)[0]
    y, m, dd = map(int, d.split("-"))
    start = datetime(y, m, dd, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    evts1 = _backfill_from_cloud_v6_db(start, start, end)
    assert evts1, f"首次调用日期 {d} 应返回 >=1 笔"
    evts2 = _backfill_from_cloud_v6_db(start, start, end)
    assert evts2 == [], "同日期第二次调用应返回 []（防重复 DB 查询）"
    print(f"同日去重通过: 首次 {len(evts1)} 笔, 二次为空")


if __name__ == "__main__":
    test_backfill_date_isolation_with_two_distinct_dates()
    test_backfill_same_date_deduplicated()
    print("\n=== 回归测试全部完成 ===")