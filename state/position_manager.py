# -*- coding: utf-8 -*-
"""线程安全的全局持仓管理器，带文件持久化"""
import threading
import json
import os
import shutil
import atexit
import traceback
import copy
import time
from pathlib import Path
from datetime import datetime
from utils.structured_logger import slog


# V59.8: 延迟导入 trade_journal，避免循环依赖
def _get_trade_journal():
    from state.trade_journal import journal as _tj
    return _tj


POSITIONS_FILE = "state/managed_positions.json"
BACKUP_DIR = "storage/position_backups"
PROCESSED_SIGNALS_FILE = "state/processed_signals.json"
PROCESSED_SIGNAL_TTL_SEC = 86400 * 7


class PositionManager:
    def __init__(self):
        self._positions = {}
        self._processed_signals = {}
        self._lock = threading.Lock()
        self._persist_path = POSITIONS_FILE
        self._processed_signals_path = PROCESSED_SIGNALS_FILE
        self._dirty = False
        self._processed_dirty = False
        self._load()
        self._load_processed_signals()
        atexit.register(self._save_at_exit)

    # ── 持久化 ──────────────────────────────────────────────

    def _load(self):
        """从文件加载持仓状态"""
        if os.path.exists(self._persist_path):
            try:
                with open(self._persist_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._positions = data
            except (json.JSONDecodeError, OSError) as exc:
                slog.error(f"[PositionManager] 加载持久化文件失败: {exc}，使用空字典")

    def _save(self):
        """写入文件持久化（全面异常防护）"""
        if not self._dirty:
            return
        try:
            os.makedirs(os.path.dirname(self._persist_path) or ".", exist_ok=True)
            # 先序列化到字符串再写文件，捕获所有序列化异常
            serialized = json.dumps(
                self._positions, ensure_ascii=False, indent=2, default=str
            )
            with open(self._persist_path + ".tmp", "w", encoding="utf-8") as f:
                f.write(serialized)
            # 原子替换
            os.replace(self._persist_path + ".tmp", self._persist_path)
            self._dirty = False
            # 双写到 data/，便于与 v6_research 同目录备份/排查
            try:
                import json as _json
                _alt = Path("data/positions_state.json")
                if not _alt.is_absolute():
                    _alt = Path(__file__).resolve().parents[1] / "data" / "positions_state.json"
                _alt.parent.mkdir(parents=True, exist_ok=True)
                _alt.write_text(
                    _json.dumps({"timestamp": time.time(), "positions": self._positions}, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )
            except Exception as _alt_e:
                slog.error(f"[PositionManager] data/positions_state 双写失败: {_alt_e}")
        except Exception as exc:
            slog.error(f"[PositionManager] 持久化写入失败: {exc}")
            traceback.print_exc()

    # ── 新增：启动时恢复持仓 ──────────────────────────────

    def recover_from_disk(self) -> list:
        """
        从持久化文件恢复未结算持仓。
        返回已恢复的持仓 symbol 列表（可用于日志/通知计数）。
        """
        self._load()
        with self._lock:
            symbols = list(self._positions.keys())
            if symbols:
                slog.info(f"[PositionManager] 从磁盘恢复持仓: {symbols}")
            return symbols

    def recover_open_from_research_db(self) -> list:
        """从 v6_research.db 中仍为 OPEN 的快照恢复持仓（HF 重启后磁盘 state 丢失时的兜底）。"""
        import sqlite3
        import time as _time
        from pathlib import Path as _Path
        candidates = [
            _Path("data/v6_research.db"),
            _Path("/app/data/v6_research.db"),
            _Path(__file__).resolve().parents[1] / "data" / "v6_research.db",
        ]
        db_path = next((p for p in candidates if p.exists()), None)
        if db_path is None:
            return []
        recovered = []
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                SELECT signal_id, timestamp, symbol, direction, regime,
                       entry_price, initial_sl, initial_tp1, atr_14,
                       model_ev, confidence, kelly_size, raw_features_json
                FROM trade_snapshots
                WHERE (exit_reason = 'OPEN' OR exit_reason IS NULL OR exit_reason = '')
                ORDER BY timestamp DESC
                """
            )
            rows = cur.fetchall()
            conn.close()
        except Exception as e:
            print(f"[PositionManager] recover_open_from_research_db query failed: {e}")
            return []

        # 每个 symbol 只恢复最新一笔 OPEN
        seen_sym = set()
        for row in rows:
            try:
                symbol = str(row["symbol"] or "")
                if not symbol or symbol in seen_sym:
                    continue
                # 已有内存持仓则不覆盖
                with self._lock:
                    if symbol in self._positions:
                        seen_sym.add(symbol)
                        continue
                entry = float(row["entry_price"] or 0)
                sl = float(row["initial_sl"] or 0)
                if entry <= 0 or sl <= 0:
                    continue
                direction = str(row["direction"] or "Long")
                risk = abs(entry - sl)
                tp1 = float(row["initial_tp1"] or 0)
                # 粗略补 TP2/TP3（若库中无）
                if tp1 <= 0 and risk > 0:
                    if direction.lower().startswith("long"):
                        tp1 = entry + risk
                    else:
                        tp1 = entry - risk
                if direction.lower().startswith("long"):
                    tp2 = entry + risk * 1.8
                    tp3 = entry + risk * 2.8
                else:
                    tp2 = entry - risk * 1.8
                    tp3 = entry - risk * 2.8
                sig = str(row["signal_id"] or "")
                pos = {
                    "direction": direction,
                    "signal_id": sig,
                    "short_id": (sig[-8:] if sig else "REC"),
                    "entry": entry,
                    "current_sl": sl,
                    "initial_risk": risk,
                    "tp1": tp1,
                    "tp2": tp2,
                    "tp3": tp3,
                    "stage": 0,
                    "sl_hit": False,
                    "score": 0.0,
                    "confidence": float(row["confidence"] or 0.5),
                    "regime": str(row["regime"] or "UNKNOWN"),
                    "features": [],
                    "ev": float(row["model_ev"] or 0),
                    "atr": float(row["atr_14"] or 0),
                    "trade_id": sig,
                    "open_time": float(row["timestamp"] or _time.time()),
                    "max_hold_seconds": 14400.0,
                    "size": float(row["kelly_size"] or 0.025) or 0.025,
                    "recovered_from_db": True,
                }
                self.update(symbol, pos)
                seen_sym.add(symbol)
                recovered.append(symbol)
                print(f"[PositionManager] 从 research.db 恢复 OPEN: {symbol} {direction} entry={entry} sid={sig}")
            except Exception as _e:
                print(f"[PositionManager] restore row failed: {_e}")
                continue
        return recovered

    def _save_at_exit(self):
        """程序退出时强制保存"""
        if self._dirty:
            self._save()
        if self._processed_dirty:
            self._save_processed_signals()

    # ── 每日快照 ──────────────────────────────────────────────

    def _daily_snapshot(self):
        """每日一次持仓快照备份，首次启动时文件不存在则跳过"""
        if not os.path.exists(self._persist_path):
            slog.warning("[PositionManager] 当前无持仓文件，跳过备份")
            return
        today = datetime.now().strftime("%Y-%m-%d")
        os.makedirs(BACKUP_DIR, exist_ok=True)
        backup_path = f"{BACKUP_DIR}/managed_positions_{today}.json"
        if not os.path.exists(backup_path):
            try:
                shutil.copy2(self._persist_path, backup_path)
                slog.info(f"[PositionManager] 每日快照备份完成: {backup_path}")
            except Exception as e:
                slog.error(f"[PositionManager] 备份失败: {e}")

    def _mark_dirty(self):
        self._dirty = True

    def _load_processed_signals(self):
        """从文件加载已处理信号指纹。"""
        if os.path.exists(self._processed_signals_path):
            try:
                with open(self._processed_signals_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    now = time.time()
                    cutoff = now - PROCESSED_SIGNAL_TTL_SEC
                    self._processed_signals = {
                        k: float(v)
                        for k, v in data.items()
                        if isinstance(k, str) and isinstance(v, (int, float)) and float(v) >= cutoff
                    }
            except (json.JSONDecodeError, OSError, ValueError) as exc:
                slog.error(f"[PositionManager] 加载已处理信号失败: {exc}，使用空记录")

    def _save_processed_signals(self):
        """写入已处理信号指纹持久化文件。"""
        if not self._processed_dirty:
            return
        try:
            os.makedirs(os.path.dirname(self._processed_signals_path) or ".", exist_ok=True)
            serialized = json.dumps(
                self._processed_signals, ensure_ascii=False, indent=2, default=str
            )
            with open(self._processed_signals_path + ".tmp", "w", encoding="utf-8") as f:
                f.write(serialized)
            os.replace(self._processed_signals_path + ".tmp", self._processed_signals_path)
            self._processed_dirty = False
        except Exception as exc:
            slog.error(f"[PositionManager] 已处理信号持久化写入失败: {exc}")
            traceback.print_exc()

    def _mark_processed_dirty(self):
        self._processed_dirty = True

    def _cleanup_processed_signals(self):
        cutoff = time.time() - PROCESSED_SIGNAL_TTL_SEC
        stale = [k for k, v in self._processed_signals.items() if v < cutoff]
        for k in stale:
            self._processed_signals.pop(k, None)

    # ── 核心接口 ────────────────────────────────────────────

    def update(self, symbol: str, pos: dict):
        """全量覆盖（兼容旧调用方）

        注意：此方法会在锁内合并新旧字典，保留所有旧字段，
        避免 EXIT_MANAGER 用旧快照覆盖主策略线程新增的字段（如 order_id）。
        """
        with self._lock:
            existing = self._positions.get(symbol, {})
            # 增量合并：existing 为基础，新 pos 字段覆盖同名旧值
            merged = {**existing, **pos}
            self._positions[symbol] = merged
            self._mark_dirty()
        self._save()

    def update_fields(self, symbol: str, **kwargs):
        """【原子】仅更新指定字段，不覆盖其他字段。

        适用于 EXIT_MANAGER 等后台线程，避免全量 update 导致幽灵覆盖。
        """
        if not kwargs:
            return
        with self._lock:
            existing = self._positions.get(symbol)
            if existing is None:
                # 持仓不存在时直接创建（部分场景如持久化恢复）
                self._positions[symbol] = dict(kwargs)
            else:
                self._positions[symbol] = {**existing, **kwargs}
            self._mark_dirty()
        self._save()

    def get(self, symbol: str = None):
        with self._lock:
            if symbol:
                pos = self._positions.get(symbol)
                return copy.deepcopy(pos) if pos is not None else None
            return {k: copy.deepcopy(v) for k, v in self._positions.items()}


    def pop(self, symbol: str) -> dict | None:
        """【原子】抢出持仓并立即从管理器移除。

        仅调用方拿到非 None 结果才被授权执行平仓动作。
        防止 Monitor 线程与主策略线程并发平仓同一持仓造成重复推送/回写。
        """

        with self._lock:
            pos = self._positions.pop(symbol, None)
            if pos is not None:
                self._mark_dirty()
        return pos

    def remove(self, symbol: str):
        with self._lock:
            self._positions.pop(symbol, None)
            self._mark_dirty()
        self._daily_snapshot()
        self._save()

    def close(self, symbol: str, pnl_r=0.0, exit_reason='SL', exit_price=None):
        """V59.8: 平仓 - 移除持仓并向 trade_journal 写入 CLOSE 记录"""
        with self._lock:
            pos = self._positions.pop(symbol, None)
            if pos is None:
                return
            self._mark_dirty()

        # 写入 trade_journal 平仓日志（延迟导入避免循环依赖）
        try:
            order_id = pos.get('order_id') or ''
            close_px = float(exit_price) if exit_price else 0.0
            if order_id:
                tj = _get_trade_journal()
                tj.close_trade(
                    order_id=order_id,
                    close_price=close_px,
                    pnl_r=float(pnl_r or 0.0),
                    exit_reason=str(exit_reason or 'SL'),
                )
                slog.info(f'[PositionManager.close] trade_journal CLOSE 已写入: {order_id} reason={exit_reason} pnl_r={pnl_r:.2f}')
            else:
                slog.warning(f'[PositionManager.close] {symbol} 无 order_id，跳过 trade_journal')
        except Exception as e:
            slog.error(f'[PositionManager.close] trade_journal 写入失败: {e}')

        self._daily_snapshot()
        self._save()

    def exists(self, symbol: str) -> bool:
        with self._lock:
            return symbol in self._positions

    def is_signal_already_processed(self, signal_id: str) -> bool:
        with self._lock:
            return signal_id in self._processed_signals

    def mark_signal_processed(self, signal_id: str) -> None:
        with self._lock:
            self._processed_signals[signal_id] = time.time()
            self._cleanup_processed_signals()
            self._mark_processed_dirty()
        self._save_processed_signals()

    def all_symbols(self) -> list:
        with self._lock:
            return list(self._positions.keys())

    def __len__(self):
        with self._lock:
            return len(self._positions)

    def __repr__(self):
        with self._lock:
            return repr(self._positions)


# 单例
position_manager = PositionManager()
