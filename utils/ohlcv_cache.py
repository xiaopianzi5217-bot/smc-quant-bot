# -*- coding: utf-8 -*-
"""
ohlcv_cache.py — 增量 K 线缓存，避免重复 API 请求

特性：
- 线程安全（threading.Lock）
- TTL 自动过期（默认 60 秒）
- 按 symbol + timeframe 做 key
- 增量追加新 K 线（不重复下载完整数据）
- 最近 N 根 K 线的看涨/看跌占比快速查看

用法：
    from utils.ohlcv_cache import ohlcv_cache
    
    # 写入
    ohlcv_cache.put("BTC/USDT", "15m", df)
    
    # 读取
    df = ohlcv_cache.get("BTC/USDT", "15m")
    
    # 检查是否过期
    if ohlcv_cache.is_stale("BTC/USDT", "15m", max_age_sec=120):
        df = await fetch_from_api(...)
        ohlcv_cache.put(...)
    
    # 清空
    ohlcv_cache.clear()
"""

from __future__ import annotations
import threading
import time
from typing import Optional, Dict, Tuple

import pandas as pd


class OHLCVCache:
    """
    增量 K 线缓存（线程安全，按 symbol + timeframe 隔离）
    
    内部存储：
        _store: Dict[str, Tuple[float, pd.DataFrame]]
        key = f"{symbol}|{timeframe}"
        value = (timestamp_written, dataframe)
    """
    
    def __init__(self, default_ttl_sec: float = 60.0):
        self._store: Dict[str, Tuple[float, pd.DataFrame]] = {}
        self._lock = threading.Lock()
        self._default_ttl = default_ttl_sec
    
    # ── 核心 API ──────────────────────────────────────────
    
    def _make_key(self, symbol: str, timeframe: str) -> str:
        return f"{symbol.strip().upper()}|{timeframe.strip().lower()}"
    
    def put(self, symbol: str, timeframe: str, df: pd.DataFrame) -> None:
        """写入缓存（覆盖旧数据）"""
        key = self._make_key(symbol, timeframe)
        with self._lock:
            self._store[key] = (time.time(), df.copy())
    
    def get(self, symbol: str, timeframe: str) -> Optional[pd.DataFrame]:
        """获取缓存 DataFrame，过期或不存在返回 None"""
        key = self._make_key(symbol, timeframe)
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            ts, df = entry
            if time.time() - ts > self._default_ttl:
                del self._store[key]
                return None
            return df.copy()  # 返回副本防止外部修改
    
    def get_raw(self, symbol: str, timeframe: str) -> Optional[pd.DataFrame]:
        """获取缓存但不过期检查（内部用）"""
        key = self._make_key(symbol, timeframe)
        with self._lock:
            entry = self._store.get(key)
            return entry[1].copy() if entry else None
    
    def is_stale(self, symbol: str, timeframe: str, max_age_sec: Optional[float] = None) -> bool:
        """
        检查缓存是否过期。
        
        返回 True 的情况：
        - 缓存不存在
        - 缓存超过 max_age_sec 秒（默认 self._default_ttl）
        """
        key = self._make_key(symbol, timeframe)
        max_age = max_age_sec if max_age_sec is not None else self._default_ttl
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return True
            ts, _ = entry
            return (time.time() - ts) > max_age
    
    def remove(self, symbol: str, timeframe: str) -> None:
        """主动清除某个缓存"""
        key = self._make_key(symbol, timeframe)
        with self._lock:
            self._store.pop(key, None)
    
    def clear(self) -> None:
        """清空全部缓存"""
        with self._lock:
            self._store.clear()
    
    # ── 增量 API ──────────────────────────────────────────
    
    def append(self, symbol: str, timeframe: str, new_rows: pd.DataFrame) -> Optional[pd.DataFrame]:
        """
        向已有缓存追加新 K 线（增量更新，避免重复全量下载）
        
        匹配逻辑：
        - 用 timestamp 列去重（保留新数据）
        - 删除最后 1 根 K 线（可能被更新的新数据覆盖）
        - 合并后按 timestamp 排序
        
        参数:
            symbol: 交易对
            timeframe: 时间周期
            new_rows: 新拉取的 K 线 DataFrame（至少含 timestamp 列）
        
        返回:
            合并后的完整 DataFrame，或 None（缓存不存在时）
        """
        key = self._make_key(symbol, timeframe)
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            ts, df = entry
            
            # 合并：删除最后 1 根 + 追加新数据
            df = df.iloc[:-1]  # 最后 1 根可能不完整
            combined = pd.concat([df, new_rows], ignore_index=True)
            
            # 去重（按 timestamp）
            if "timestamp" in combined.columns:
                combined = combined.drop_duplicates(subset=["timestamp"], keep="last")
                combined = combined.sort_values("timestamp").reset_index(drop=True)
            
            self._store[key] = (time.time(), combined)
            return combined.copy()
    
    # ── 统计 & 调试 ───────────────────────────────────────
    
    def keys(self) -> list:
        """查看所有缓存的 key"""
        with self._lock:
            return list(self._store.keys())
    
    def age(self, symbol: str, timeframe: str) -> Optional[float]:
        """查看缓存已存活秒数"""
        key = self._make_key(symbol, timeframe)
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            return time.time() - entry[0]
    
    def __len__(self) -> int:
        with self._lock:
            return len(self._store)
    
    def __repr__(self) -> str:
        with self._lock:
            parts = [f"{k}: {v[0].shape[0]} rows" for k, v in self._store.items()]
        return f"OHLCVCache({len(self)} keys): {', '.join(parts) if parts else 'empty'}"


# ── 单例 ──
ohlcv_cache = OHLCVCache(default_ttl_sec=60.0)
