# -*- coding: utf-8 -*-
"""线程安全的全局持仓管理器，带文件持久化"""
import threading
import json
import os
import atexit
import traceback

POSITIONS_FILE = "state/managed_positions.json"


class PositionManager:
    def __init__(self):
        self._positions = {}
        self._lock = threading.Lock()
        self._persist_path = POSITIONS_FILE
        self._dirty = False
        self._load()
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

    def _mark_dirty(self):
        self._dirty = True

    # ── 核心接口 ────────────────────────────────────────────

    def update(self, symbol: str, pos: dict):
        with self._lock:
            self._positions[symbol] = pos
            self._mark_dirty()
        self._save()

    def get(self, symbol: str = None):
        with self._lock:
            if symbol:
                return self._positions.get(symbol)
            return dict(self._positions)

    def remove(self, symbol: str):
        with self._lock:
            self._positions.pop(symbol, None)
            self._mark_dirty()
        self._save()

    def exists(self, symbol: str) -> bool:
        with self._lock:
            return symbol in self._positions

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
