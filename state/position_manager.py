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
from datetime import datetime

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
                print(f"[PositionManager] 加载持久化文件失败: {exc}，使用空字典")

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
        except Exception as exc:
            print(f"[PositionManager] 持久化写入失败: {exc}")
            traceback.print_exc()

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
            print("[PositionManager] 当前无持仓文件，跳过备份")
            return
        today = datetime.now().strftime("%Y-%m-%d")
        os.makedirs(BACKUP_DIR, exist_ok=True)
        backup_path = f"{BACKUP_DIR}/managed_positions_{today}.json"
        if not os.path.exists(backup_path):
            try:
                shutil.copy2(self._persist_path, backup_path)
                print(f"[PositionManager] 每日快照备份完成: {backup_path}")
            except Exception as e:
                print(f"[PositionManager] 备份失败: {e}")

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
                print(f"[PositionManager] 加载已处理信号失败: {exc}，使用空记录")

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
            print(f"[PositionManager] 已处理信号持久化写入失败: {exc}")
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
        with self._lock:
            self._positions[symbol] = pos
            self._mark_dirty()
        self._save()

    def get(self, symbol: str = None):
        with self._lock:
            if symbol:
                pos = self._positions.get(symbol)
                return copy.deepcopy(pos) if pos is not None else None
            return {k: copy.deepcopy(v) for k, v in self._positions.items()}

    def remove(self, symbol: str):
        with self._lock:
            self._positions.pop(symbol, None)
            self._mark_dirty()
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
