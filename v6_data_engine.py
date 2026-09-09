# -*- coding: utf-8 -*-
"""
V6 数据驱动量化引擎 - Hugging Face 云端安全灾备完全体 (Aisvbo/svb-bot 专属生产版)
"""
import os
import sqlite3
import json
import time
import hashlib
import shutil
import threading
from pathlib import Path

import numpy as np
import pandas as pd
from utils.structured_logger import slog

_ROOT = Path(__file__).parent.absolute()

# ─── 🟢 Hugging Face 路径自适应 ───
IS_HF_SPACE = "SPACE_ID" in os.environ
DB_PATH = _ROOT / "data" / "v6_research.db"

# 【根本修复】缓存标记：拉取成功/确认不存在后写入，避免每次启动重复尝试
_DB_INIT_SENTINEL = _ROOT / "data" / ".db_initialized"

# ── 🟢 节流推送（避免每次开平仓直接 push） ──
_last_push_ts = 0.0
_PUSH_MIN_INTERVAL = 120.0   # 最少 2 分钟推一次
_pending_push = False
_push_lock = threading.Lock()


def request_push_database_to_hub():
    """非阻塞：标记需要备份，由后台线程节流执行。"""
    global _pending_push
    with _push_lock:
        _pending_push = True


def _push_worker_loop():
    global _last_push_ts, _pending_push
    while True:
        time.sleep(15)
        with _push_lock:
            need = _pending_push
            _pending_push = False
        if not need:
            continue
        if time.time() - _last_push_ts < _PUSH_MIN_INTERVAL:
            with _push_lock:
                _pending_push = True   # 稍后再试
            continue
        try:
            push_database_to_hub()
            _last_push_ts = time.time()
        except Exception as e:
            slog.error(f"[V6] throttled push failed: {e}")
            with _push_lock:
                _pending_push = True


if IS_HF_SPACE:
    t = threading.Thread(target=_push_worker_loop, daemon=True)
    t.start()
    slog.info("[V6 DataEngine] 节流推送后台线程已启动 (最小间隔 120s)")


def _get_db_path():
    """Return a normalized pathlib.Path for the configured database path."""
    return Path(DB_PATH) if not isinstance(DB_PATH, Path) else DB_PATH


def make_json_serializable(obj):
    if isinstance(obj, (np.bool_, np.bool)):
        return bool(obj)
    if isinstance(obj, (np.integer, np.int64, np.int32)):
        return int(obj)
    if isinstance(obj, (np.floating, np.float64)):
        return float(obj)
    if isinstance(obj, dict):
        return {k: make_json_serializable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [make_json_serializable(v) for v in obj]
    return obj


def _get_hf_config():
    """安全读取后台锁定的隐私密钥，已为你无缝对齐 Aisvbo 专属仓库配置"""
    # 兜底：如果环境变量中没有 HF_TOKEN，尝试从 .env 文件读取
    if not os.environ.get("HF_TOKEN"):
        _env_path = _ROOT / ".env"
        if _env_path.exists():
            try:
                for _line in _env_path.read_text(encoding="utf-8").splitlines():
                    _line = _line.strip()
                    if _line.startswith("HF_TOKEN="):
                        _val = _line.split("=", 1)[1].strip().strip("'\"")
                        if _val:
                            os.environ["HF_TOKEN"] = _val
                            break
            except Exception:
                pass
    repo_id = os.environ.get("HF_DATASET_REPO", "Aisvbo/svb-bot-v6-snapshots")
    token = os.environ.get("HF_TOKEN", "").strip()                    
    return repo_id, token

def merge_databases(local_db_path: Path, downloaded_db_path: Path) -> int:
    """将云端 DB 增量合并进本地（INSERT OR IGNORE，按 signal_id 主键去重）。"""
    if not Path(downloaded_db_path).exists():
        return 0
    local_db_path = Path(local_db_path)
    local_db_path.parent.mkdir(parents=True, exist_ok=True)
    inserted = 0
    conn = None
    try:
        conn = sqlite3.connect(str(local_db_path))
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='trade_snapshots'"
        )
        if not cursor.fetchone():
            conn.close()
            shutil.copy(str(downloaded_db_path), str(local_db_path))
            slog.info("[V6 DataEngine] 本地无表，直接使用云端库作为初始库")
            return -1

        # ATTACH 路径转义单引号
        remote_path = str(downloaded_db_path).replace("'", "''")
        cursor.execute(f"ATTACH DATABASE '{remote_path}' AS remote_db")
        cursor.execute(
            """
            INSERT OR IGNORE INTO main.trade_snapshots
            SELECT * FROM remote_db.trade_snapshots
            """
        )
        inserted = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
        try:
            cursor.execute(
                """
                UPDATE main.trade_snapshots
                SET
                    exit_reason = (
                        SELECT r.exit_reason FROM remote_db.trade_snapshots r
                        WHERE r.signal_id = main.trade_snapshots.signal_id
                    ),
                    exit_timestamp = (
                        SELECT r.exit_timestamp FROM remote_db.trade_snapshots r
                        WHERE r.signal_id = main.trade_snapshots.signal_id
                    ),
                    exit_price = (
                        SELECT r.exit_price FROM remote_db.trade_snapshots r
                        WHERE r.signal_id = main.trade_snapshots.signal_id
                    ),
                    pnl_r = (
                        SELECT r.pnl_r FROM remote_db.trade_snapshots r
                        WHERE r.signal_id = main.trade_snapshots.signal_id
                    ),
                    max_forward_r = (
                        SELECT r.max_forward_r FROM remote_db.trade_snapshots r
                        WHERE r.signal_id = main.trade_snapshots.signal_id
                    ),
                    max_adverse_r = (
                        SELECT r.max_adverse_r FROM remote_db.trade_snapshots r
                        WHERE r.signal_id = main.trade_snapshots.signal_id
                    )
                WHERE main.trade_snapshots.exit_reason = 'OPEN'
                  AND main.trade_snapshots.signal_id IN (
                      SELECT signal_id FROM remote_db.trade_snapshots
                      WHERE exit_reason IS NOT NULL AND exit_reason != 'OPEN'
                  )
                """
            )
        except Exception as _merge_upd_e:
            slog.warning(f"[V6 DataEngine] 合并补全平仓字段跳过: {_merge_upd_e}")

        conn.commit()
        cursor.execute("DETACH DATABASE remote_db")
        conn.close()
        slog.info(f"[V6 DataEngine] 云端→本地增量合并完成，新增约 {inserted} 行")
    except Exception as e:
        slog.error(f"[V6 DataEngine] merge_databases 失败: {e}")
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
    return inserted


def cleanup_dirty_trade_snapshots() -> dict:
    """清理废弃/空 pnl 脏数据，并钳制异常 MAE/MFE。"""
    stats = {"deleted": 0, "clamped": 0}
    db_path = _get_db_path()
    if not db_path.exists():
        return stats
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute(
            """
            DELETE FROM trade_snapshots
            WHERE exit_reason = 'MANUAL_CLEANUP_DEPRECATED'
               OR (
                    exit_reason IS NOT NULL
                    AND exit_reason != 'OPEN'
                    AND pnl_r IS NULL
               )
            """
        )
        stats["deleted"] = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
        cursor.execute(
            """
            UPDATE trade_snapshots
            SET max_adverse_r = CASE
                    WHEN max_adverse_r < -10.0 THEN -1.0
                    WHEN max_adverse_r > 10.0 THEN -1.0
                    ELSE max_adverse_r
                END,
                max_forward_r = CASE
                    WHEN max_forward_r > 15.0 THEN 15.0
                    WHEN max_forward_r < -5.0 THEN 0.0
                    ELSE max_forward_r
                END
            WHERE max_adverse_r < -10.0 OR max_adverse_r > 10.0
               OR max_forward_r > 15.0 OR max_forward_r < -5.0
            """
        )
        stats["clamped"] = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
        cursor.execute(
            """
            UPDATE trade_snapshots
            SET max_adverse_r = -1.0
            WHERE signal_id = 'V6_BTCUSDT_1786833009'
              AND (max_adverse_r IS NULL OR max_adverse_r < -5.0 OR max_adverse_r > 5.0)
            """
        )
        conn.commit()
        conn.close()
        if stats["deleted"] or stats["clamped"]:
            slog.info(
                f"[V6 DataEngine] 脏数据清理完成 deleted={stats['deleted']} clamped={stats['clamped']}"
            )
    except Exception as e:
        slog.error(f"[V6 DataEngine] cleanup_dirty_trade_snapshots 失败: {e}")
    return stats



def reconcile_stale_open_snapshots(max_age_sec: int = 14400, default_pnl_r: float = 0.0) -> int:
    """将超时仍为 OPEN、且本地无对应持仓的快照强制对账关闭。

    - max_age_sec: 默认 4h（与 position max_hold_seconds 一致）
    - 仅处理 exit_reason='OPEN' 且 timestamp 过旧的行
    - 标记 exit_reason=STALE_OPEN_TIMEOUT，pnl_r 默认 0（未知结局不污染学习）
    返回关闭条数。
    """
    db_path = _get_db_path()
    if not db_path.exists():
        return 0
    closed = 0
    try:
        now = int(time.time())
        cutoff = now - int(max_age_sec)
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT signal_id, timestamp, symbol, entry_price, initial_sl
            FROM trade_snapshots
            WHERE (exit_reason = 'OPEN' OR exit_reason IS NULL OR exit_reason = '')
              AND timestamp IS NOT NULL
              AND timestamp > 0
              AND timestamp < ?
            """,
            (cutoff,),
        )
        rows = cursor.fetchall()
        for signal_id, ts, symbol, entry, sl in rows:
            try:
                cursor.execute(
                    """
                    UPDATE trade_snapshots
                    SET exit_reason = 'STALE_OPEN_TIMEOUT',
                        exit_timestamp = ?,
                        exit_price = COALESCE(exit_price, entry_price, 0),
                        pnl_r = COALESCE(pnl_r, ?)
                    WHERE signal_id = ?
                      AND (exit_reason = 'OPEN' OR exit_reason IS NULL OR exit_reason = '')
                    """,
                    (now, float(default_pnl_r), signal_id),
                )
                if cursor.rowcount and cursor.rowcount > 0:
                    closed += 1
                    slog.warning(
                        f"[V6 DataEngine] 超时 OPEN 对账关闭: {signal_id} symbol={symbol} "
                        f"age={(now - int(ts or now))/3600.0:.1f}h -> STALE_OPEN_TIMEOUT"
                    )
            except Exception as _one_e:
                slog.error(f"[V6 DataEngine] 对账单笔失败 {signal_id}: {_one_e}")
        conn.commit()
        conn.close()
        if closed:
            slog.info(f"[V6 DataEngine] reconcile_stale_open_snapshots 关闭 {closed} 笔超时 OPEN")
            if IS_HF_SPACE:
                request_push_database_to_hub()
    except Exception as e:
        slog.error(f"[V6 DataEngine] reconcile_stale_open_snapshots 失败: {e}")
    return closed


def pull_database_from_hub():
    """【启动恢复】从 HF Dataset 下载历史库；本地已有数据时合并而非覆盖。"""
    if _DB_INIT_SENTINEL.exists():
        slog.warning("[V6 DataEngine] 已确认过云端状态，跳过拉取。")
        return

    repo_id, token = _get_hf_config()
    if not repo_id or not token:
        slog.warning("[V6 DataEngine] 未检测到云端灾备配置，跳过云端数据库拉取。")
        _DB_INIT_SENTINEL.parent.mkdir(parents=True, exist_ok=True)
        _DB_INIT_SENTINEL.write_text("no_cloud_config", encoding="utf-8")
        return
    try:
        from huggingface_hub import hf_hub_download
        slog.info(f"[V6 DataEngine] 正在从云端数据集 [{repo_id}] 拉取最新历史数据库...")
        db_path = _get_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        downloaded = hf_hub_download(
            repo_id=repo_id,
            filename="v6_research.db",
            repo_type="dataset",
            token=token
        )
        if db_path.exists() and db_path.stat().st_size > 0:
            merge_databases(db_path, Path(downloaded))
            slog.info("[V6 DataEngine] 云端库已与本地合并（保留本地新单）")
        else:
            shutil.copy(downloaded, str(db_path))
            slog.info("[V6 DataEngine] 历史交易快照库同步恢复成功！")
        _DB_INIT_SENTINEL.write_text("pulled_ok", encoding="utf-8")
    except Exception as e:
        err_str = str(e)
        if "404" in err_str or "Entry Not Found" in err_str:
            slog.info("[V6 DataEngine] 云端无历史备份 (首次部署)，初始化全新本地库。")
            _DB_INIT_SENTINEL.write_text("no_cloud_backup_404", encoding="utf-8")
        else:
            slog.error(f"[V6 DataEngine] io 云端数据库拉取异常: {e}")

def push_database_to_hub():
    """【实时备份】将本地最新写入的快照瞬间同步至云端私有仓库"""
    repo_id, token = _get_hf_config()
    if not repo_id or not token:
        return
    try:
        from huggingface_hub import HfApi
        api = HfApi(token=token)
        try:
            api.create_repo(repo_id=repo_id, repo_type="dataset", private=True, exist_ok=True)
        except:
            pass
        db_path = _get_db_path()
        api.upload_file(
            path_or_fileobj=str(db_path),
            path_in_repo="v6_research.db",
            repo_id=repo_id,
            repo_type="dataset",
            commit_message=f"🔄 Aisvbo 数据流实时增量备份 - {int(time.time())}"
        )
        slog.info("[V6 DataEngine] 云端备份完成！数据已安全锁入私有 Dataset.")
    except Exception as e:
        slog.error(f"[V6 DataEngine] 实时同步至 Hugging Face Hub 失败: {e}")

# ── 统一特征分类映射器 ──
from state.feature_mapper import (
    FEATURE_GROUP_MAP,
    classify_feature_group,
    classify_all_features,
    GROUP_MAP_V6_BRIDGE,
)


# ============================================================
# PART 1: SQLite 高维交易快照持久化
# ============================================================

def _ensure_column(cursor, table: str, column: str, definition: str):
    cursor.execute(f"PRAGMA table_info({table})")
    existing = [row[1] for row in cursor.fetchall()]
    if column not in existing:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def expand_v6_table_for_smc():
    """在 V6 引擎中初始化 SMC 结构生死账本表。"""
    conn = sqlite3.connect(str(_get_db_path()))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS smc_structure_tracker (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            structure_type TEXT NOT NULL,
            direction TEXT NOT NULL,
            price_level REAL NOT NULL,
            is_mitigated INTEGER DEFAULT 0,
            outcome INTEGER DEFAULT NULL,
            regime TEXT
        )
    """)
    # 常用查询索引：按品种、周期及是否被吸收/缓解快速过滤
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_smc_symbol_tf ON smc_structure_tracker (symbol, timeframe, is_mitigated)")
    conn.commit()
    conn.close()


def get_historical_smc_success_rate(symbol, timeframe, structure_type, current_regime):
    """查询过去 1000 个相似 SMC 结构的真实统计学胜率。"""
    try:
        conn = sqlite3.connect(str(_get_db_path()))
        cursor = conn.cursor()
        query = """
            SELECT outcome FROM smc_structure_tracker
            WHERE symbol = ? AND timeframe = ? AND structure_type = ? AND regime = ? AND outcome IS NOT NULL
            ORDER BY timestamp DESC LIMIT 1000
        """
        cursor.execute(query, (symbol, timeframe, structure_type, current_regime))
        rows = cursor.fetchall()
        conn.close()
    except Exception:
        return 0.48

    if not rows or len(rows) < 30:
        return 0.48

    outcomes = [r[0] for r in rows if r[0] is not None]
    if not outcomes:
        return 0.48

    success_count = sum(1 for o in outcomes if o == 1)
    actual_probability = success_count / len(outcomes)
    return actual_probability


def init_v6_database():
    """初始化数据库流程"""
    db_path = _get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # HF 环境：无论本地是否已有库都尝试拉取合并（pull 内部有 sentinel 防重复）
    if IS_HF_SPACE:
        pull_database_from_hub()
        
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trade_snapshots (
            signal_id TEXT PRIMARY KEY,
            timestamp INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            regime TEXT,
            vol_state TEXT,
            adx_14 REAL,
            atr_14 REAL,
            rsi_50 REAL,
            feature_hash TEXT,
            raw_features_json TEXT,
            p_win_raw REAL,
            p_win_calibrated REAL,
            model_ev REAL,
            blended_ev REAL,
            confidence REAL,
            entry_price REAL,
            initial_sl REAL,
            initial_tp1 REAL,
            estimated_rr REAL,
            kelly_size REAL,
            sqz_released INTEGER DEFAULT 0,
            sqz_duration INTEGER DEFAULT 0,
            sqz_strength REAL DEFAULT 0.0,
            sqz_vol_ratio REAL DEFAULT 1.0,
            sqz_volume_confirmed INTEGER DEFAULT 0,
            mode TEXT DEFAULT 'NORMAL',
            exit_reason TEXT DEFAULT 'OPEN',
            exit_timestamp INTEGER DEFAULT NULL,
            exit_price REAL DEFAULT NULL,
            pnl_r REAL DEFAULT NULL,
            max_forward_r REAL DEFAULT 0.0,
            max_adverse_r REAL DEFAULT 0.0
        )
    """)
    _ensure_column(cursor, "trade_snapshots", "sqz_released", "INTEGER DEFAULT 0")
    _ensure_column(cursor, "trade_snapshots", "sqz_duration", "INTEGER DEFAULT 0")
    _ensure_column(cursor, "trade_snapshots", "sqz_strength", "REAL DEFAULT 0.0")
    _ensure_column(cursor, "trade_snapshots", "sqz_vol_ratio", "REAL DEFAULT 1.0")
    _ensure_column(cursor, "trade_snapshots", "sqz_volume_confirmed", "INTEGER DEFAULT 0")
    _ensure_column(cursor, "trade_snapshots", "mode", "TEXT DEFAULT 'NORMAL'")
    # 常用索引：按品种、开仓时间快速检索
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trade_symbol_time ON trade_snapshots (symbol, timestamp)")
    # 快速查找未平仓的单子 (exit_reason = 'OPEN')
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trade_open_orders ON trade_snapshots (exit_reason) WHERE exit_reason = 'OPEN'")
    conn.commit()
    conn.close()
    expand_v6_table_for_smc()
    try:
        cleanup_dirty_trade_snapshots()
    except Exception as _c_e:
        slog.error(f"[V6 DataEngine] 启动清理失败: {_c_e}")
    try:
        reconcile_stale_open_snapshots(max_age_sec=14400)
    except Exception as _r_e:
        slog.error(f"[V6 DataEngine] 启动 OPEN 对账失败: {_r_e}")
    slog.info(f"[V6 DataEngine] 工作数据库就绪: {DB_PATH}")

def record_open_snapshot(result: dict, kelly_size: float = 0.0):
    """拍摄高维环境特征快照"""
    try:
        conn = sqlite3.connect(str(_get_db_path()))
        cursor = conn.cursor()
        
        signal_id = result.get("signal_id") or f"{result['symbol']}_{int(time.time())}"
        features = result.get("features", {})
        feat_str = ",".join([f"{k}={v}" for k, v in sorted(features.items()) if k != "regime"])
        feat_hash = hashlib.md5(feat_str.encode("utf-8")).hexdigest()[:8]
        
        def _get_val(d, *keys, default=0.0):
            for k in keys:
                if k in d:
                    val = d[k]
                    if val is None:
                        return float(default)
                    if isinstance(val, bool):
                        return float(1.0 if val else 0.0)
                    try:
                        return float(val)
                    except Exception:
                        return float(default)
            return float(default)

        sqz_data = result.get("sqz_data", {}) or {}
                # mode 字段：PROBE / NORMAL；调用方可通过 result["mode"] 指定，缺省 NORMAL
        _mode = str(result.get("mode", "NORMAL")).upper()
        if _mode not in ("NORMAL", "PROBE"):
            _mode = "NORMAL"

        cursor.execute("""
            INSERT OR REPLACE INTO trade_snapshots (
                signal_id, timestamp, symbol, direction,
                regime, vol_state, adx_14, atr_14, rsi_50, feature_hash, raw_features_json,
                p_win_raw, p_win_calibrated, model_ev, blended_ev, confidence,
                entry_price, initial_sl, initial_tp1, estimated_rr, kelly_size,
                sqz_released, sqz_duration, sqz_strength, sqz_vol_ratio, sqz_volume_confirmed,
                mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            signal_id, int(time.time()), result["symbol"], result["direction"],
            str(result.get("regime", "UNKNOWN")).upper(), str(result.get("vol_state", "NORMAL")).upper(),
            _get_val(result, "adx"), _get_val(result, "atr"), _get_val(result, "rsi", default=50.0),
            feat_hash, json.dumps(make_json_serializable(features), ensure_ascii=False),
            float(result.get("p_win_raw", 0.5)), float(result.get("confidence", 0.5)),
            float(result.get("expected_value", 0.0)), float(result.get("blended_ev", 0.0)), float(result.get("confidence", 0.5)),
            float(result["entry"]), float(result["sl"]), float(result["tp1"]), float(result.get("rr", 1.0)), float(kelly_size),
            1.0 if bool(sqz_data.get("released", False)) else 0.0,
            int(sqz_data.get("duration", 0)),
            float(sqz_data.get("strength", 0.0)),
            max(1e-8, max(1e-8, float(sqz_data.get("vol_ratio", 1.0)))),
            1.0 if bool(sqz_data.get("volume_confirmed", False)) else 0.0,
            _mode,
        ))
        conn.commit()
        conn.close()
        slog.info(f"[V6 DataEngine] 开单高维快照已锁定 -> {signal_id}")
        
        if IS_HF_SPACE:
            request_push_database_to_hub()
            # 开仓关键路径：尽量立即推一次，避免重启前节流窗口内云端无 OPEN
            try:
                push_database_to_hub()
            except Exception as _imm_e:
                slog.warning(f"[V6 DataEngine] 开仓立即推送失败(已入队): {_imm_e}")
    except Exception as e:
        slog.error(f"[V6 DataEngine] 记录开单快照失败: {e}")

def record_close_outcome(signal_id: str, pnl_r: float, exit_reason: str, max_fwd: float = 0.0, max_adv: float = 0.0, exit_timestamp: int = None, exit_price: float = None):
    """横向拼接真实结局标签（冻结 MAE/MFE，禁止后续再改）"""
    if not signal_id:
        return
    try:
        # 钳制异常 R，防止价格单位误写入导致 -159R 等溢出
        _mf = float(max_fwd or 0.0)
        _ma = float(max_adv or 0.0)
        if _mf > 15.0 or _mf < -5.0:
            slog.warning(f"[V6 DataEngine] max_forward_r 异常 {_mf:.2f} → 钳制")
            _mf = max(-5.0, min(15.0, _mf))
        if _ma < -10.0 or _ma > 10.0:
            slog.warning(f"[V6 DataEngine] max_adverse_r 异常 {_ma:.2f} → 钳制为 -1.0")
            _ma = -1.0 if _ma < 0 else min(10.0, _ma)
        conn = sqlite3.connect(str(_get_db_path()))
        cursor = conn.cursor()
        # 仅更新仍为 OPEN 的行，已平仓的禁止再改 MAE/MFE
        cursor.execute("""
            UPDATE trade_snapshots 
            SET exit_reason = ?, exit_timestamp = ?, exit_price = ?, pnl_r = ?, max_forward_r = ?, max_adverse_r = ?
            WHERE signal_id = ? AND (exit_reason = 'OPEN' OR exit_reason IS NULL OR exit_reason = '')
        """, (exit_reason, int(exit_timestamp or int(time.time())), exit_price or 0.0, float(pnl_r), _mf, _ma, signal_id))
        # 【修复20260904】先读取 rowcount 再 commit，避免假「已回写」
        _rows = cursor.rowcount
        conn.commit()
        conn.close()
        if _rows == 0:
            slog.warning(
                f"[V6 DataEngine] ⚠️ 平仓回写未命中开仓行（signal_id 错位或无对应 OPEN）"
                f" -> {signal_id} | {pnl_r:+.2f}R | reason={exit_reason}"
            )
        else:
            slog.info(f"[V6 DataEngine] 真实标签拼接成功 -> {signal_id} | {pnl_r:+.2f}R")

        if IS_HF_SPACE:
            request_push_database_to_hub()
    except Exception as e:
        slog.error(f"[V6 DataEngine] 拼接平仓标签失败: {e}")

class DynamicFeatureOptimizer:
    """
    基于历史交易结果动态重算特征分组权重。

    使用统一的 feature_mapper 将 raw_feature 归入 Feature Group，
    避免数据标签污染。
    """

    def __init__(self, min_samples: int = 50, window_size: int = 1000):
        self.min_samples = min_samples
        self.window_size = window_size
        # 初始权重（按 Feature Group 维度）
        self.feature_weights = {
            "MOMENTUM": 15.0,
            "STRUCTURE": 25.0,
            "LIQUIDITY": 20.0,
            "VOLATILITY": 10.0,
            "TREND": 10.0,
            "VOLUME": 5.0,
            "VWAP": 5.0,
            "DIVERGENCE": 10.0,
        }

    def update_feature_importance_from_db(self):
        if not _get_db_path().exists():
            return self.feature_weights
        try:
            conn = sqlite3.connect(str(_get_db_path()))
            query = """
                SELECT raw_features_json, pnl_r FROM trade_snapshots
                WHERE pnl_r IS NOT NULL
                  AND exit_reason IS NOT NULL
                  AND exit_reason NOT IN ('OPEN', 'MANUAL_CLEANUP_DEPRECATED', 'STALE_OPEN_TIMEOUT', 'FORCE_CLOSE_UNKNOWN', 'OPEN_STALE')
                ORDER BY timestamp DESC LIMIT ?
            """
            df = pd.read_sql_query(query, conn, params=(self.window_size,))
            conn.close()

            if len(df) < self.min_samples:
                return self.feature_weights

            # ── 使用 Feature Mapper 统一归因 ──

            parsed_rows = []
            for _, row in df.iterrows():
                try:
                    feat_dict = json.loads(row["raw_features_json"])
                    grouped = classify_all_features(feat_dict)
                    # 构造特征组激活向量（该笔交易中哪些 Feature Group 出现）
                    row_features = {g: 0.0 for g in self.feature_weights}
                    for g in grouped:
                        v6_group = GROUP_MAP_V6_BRIDGE.get(g, g)
                        if v6_group in row_features:
                            row_features[v6_group] = 1.0
                    row_features["pnl_r"] = float(row["pnl_r"])
                    parsed_rows.append(row_features)
                except Exception:
                    continue

            analysis_df = pd.DataFrame(parsed_rows)
            if analysis_df.empty:
                return self.feature_weights

            new_weights = {}
            total_contribution = 0.0
            for feat in self.feature_weights:
                sub_df = analysis_df[analysis_df[feat] == 1.0]
                contribution = (
                    max(0.01, sub_df["pnl_r"].mean() + 1.0)
                    if len(sub_df) >= 5
                    else 1.0
                )
                new_weights[feat] = contribution
                total_contribution += contribution

            if total_contribution > 0:
                for k in new_weights:
                    self.feature_weights[k] = round(
                        (new_weights[k] / total_contribution) * 100, 2
                    )

            print(
                f"[V6 FeatureOptimizer] 🔄 特征权重动态重算成功"
                f" (feature_mapper 驱动): {self.feature_weights}"
            )
            return self.feature_weights
        except Exception as e:
            print(f"自动更新特征权重异常: {e}")
            return self.feature_weights


get_v6_optimizer = DynamicFeatureOptimizer()
