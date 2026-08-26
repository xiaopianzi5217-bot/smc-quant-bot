# -*- coding: utf-8 -*-
"""
科研观察信号虚拟持仓追踪器
===========================
为 RESEARCH_SILENT 分支的信号提供"虚拟持仓"跟踪：

- 当某个信号被路由到 RESEARCH_SILENT 时，注册一个虚拟持仓
- 在 main_loop 的每轮循环中，用当前价格检查虚拟持仓是否触及 SL/TP1
- 触发后立即调用 record_close_outcome 回写 pnl_r / exit_reason
- 超过 VIRTUAL_TIMEOUT_HOURS 后强制平仓（按最近扫描到的价格）

这样 "RES_" 信号就有了真实可用的标签，从而成为
DynamicFeatureOptimizer 可以学习的"进化燃料"。
"""

import time
import threading
from typing import Dict, Optional

from utils.structured_logger import slog

# 虚拟持仓记录结构：
# {
#   "signal_id": str,        # RES_...
#   "symbol": str,           # BTCUSDT
#   "direction": str,        # Long / Short
#   "entry_price": float,
#   "sl_price": float,
#   "tp1_price": float,
#   "created_at": float,     # timestamp
#   "last_price": float,     # 最近一次扫描到的价格
#   "max_fwd": float,        # 最大有利偏移(R)
#   "max_adv": float,        # 最大不利偏移(R)
#   "risk": float,           # 风险单位
# }

class ResearchSignalTracker:
    """虚拟持仓追踪器（线程安全）"""

    VIRTUAL_TIMEOUT_HOURS = 24.0

    def __init__(self):
        self._virtual_positions: Dict[str, dict] = {}
        self._lock = threading.RLock()

    # ── 注册虚拟持仓 ──
    def register(
        self,
        signal_id: str,
        symbol: str,
        direction: str,
        entry_price: float,
        sl_price: float,
        tp1_price: float,
    ) -> bool:
        """注册一条虚拟持仓"""
        if not signal_id or not signal_id.startswith("RES_"):
            return False
        if not entry_price or entry_price <= 0:
            return False

        risk = abs(entry_price - sl_price) if sl_price and sl_price > 0 else entry_price * 0.01
        if risk <= 0:
            risk = entry_price * 0.01

        with self._lock:
            self._virtual_positions[signal_id] = {
                "signal_id": signal_id,
                "symbol": symbol,
                "direction": direction,
                "entry_price": float(entry_price),
                "sl_price": float(sl_price) if sl_price else 0.0,
                "tp1_price": float(tp1_price) if tp1_price else 0.0,
                "created_at": time.time(),
                "last_price": float(entry_price),
                "max_fwd": 0.0,
                "max_adv": 0.0,
                "risk": float(risk),
            }
        slog.info(f"[ResearchTracker] 注册虚拟持仓: {signal_id} {symbol} {direction} entry={entry_price:.4f} sl={sl_price:.4f} tp1={tp1_price:.4f}")
        return True

    # ── 更新价格并检查触发 ──
    def update_price(self, symbol: str, current_price: float) -> list:
        """
        对指定 symbol 的所有虚拟持仓更新最新价格。
        如果触发 SL/TP1/超时，返回需要回写的平仓结果列表。
        """
        triggers = []
        if current_price is None or current_price <= 0:
            return triggers

        with self._lock:
            for sig_id in list(self._virtual_positions.keys()):
                pos = self._virtual_positions.get(sig_id)
                if not pos or pos["symbol"] != symbol:
                    continue

                entry = pos["entry_price"]
                risk = pos["risk"]
                if risk <= 0:
                    continue

                direction = pos["direction"].lower()
                sl = pos["sl_price"]
                tp1 = pos["tp1_price"]

                # 更新 max_fwd / max_adv
                if direction in ("long", "buy"):
                    fwd_r = (current_price - entry) / risk
                    adv_r = (entry - current_price) / risk
                else:
                    fwd_r = (entry - current_price) / risk
                    adv_r = (current_price - entry) / risk

                if fwd_r > pos["max_fwd"]:
                    pos["max_fwd"] = fwd_r
                if adv_r > pos["max_adv"]:
                    pos["max_adv"] = adv_r

                pos["last_price"] = current_price

                # ── 检查触发条件 ──
                exit_reason = None
                exit_price = None

                if direction in ("long", "buy"):
                    # 检查 TP1（先检查 TP 再检查 SL，更乐观）
                    if tp1 and tp1 > 0 and current_price >= tp1:
                        exit_reason = "RESEARCH_TP1"
                        exit_price = tp1
                    # 检查 SL
                    elif sl and sl > 0 and current_price <= sl:
                        exit_reason = "RESEARCH_SL"
                        exit_price = sl
                else:  # short
                    if tp1 and tp1 > 0 and current_price <= tp1:
                        exit_reason = "RESEARCH_TP1"
                        exit_price = tp1
                    elif sl and sl > 0 and current_price >= sl:
                        exit_reason = "RESEARCH_SL"
                        exit_price = sl

                # 检查超时
                if exit_reason is None:
                    elapsed_h = (time.time() - pos["created_at"]) / 3600.0
                    if elapsed_h > self.VIRTUAL_TIMEOUT_HOURS:
                        exit_reason = "RESEARCH_TIMEOUT"
                        exit_price = current_price

                # 触发平仓
                if exit_reason and exit_price:
                    # 计算 pnl_r
                    if direction in ("long", "buy"):
                        pnl_r = (exit_price - entry) / risk
                    else:
                        pnl_r = (entry - exit_price) / risk

                    triggers.append({
                        "signal_id": sig_id,
                        "pnl_r": round(pnl_r, 4),
                        "exit_reason": exit_reason,
                        "max_fwd": round(pos["max_fwd"], 4),
                        "max_adv": round(pos["max_adv"], 4),
                        "exit_price": round(exit_price, 4),
                    })

                    # 移除虚拟持仓
                    del self._virtual_positions[sig_id]
                    slog.info(f"[ResearchTracker] [触发] 触发平仓: {sig_id} {symbol} {direction} "
                              f"pnl_r={pnl_r:+.2f}R reason={exit_reason}")

        return triggers

    # ── 获取活跃虚拟持仓数量 ──
    def active_count(self) -> int:
        with self._lock:
            return len(self._virtual_positions)

    # ── 列出所有活跃虚拟持仓 ──
    def list_active(self) -> list:
        with self._lock:
            return list(self._virtual_positions.values())


# ===== 全局单例 =====
_research_tracker: Optional[ResearchSignalTracker] = None


def get_research_tracker() -> ResearchSignalTracker:
    global _research_tracker
    if _research_tracker is None:
        _research_tracker = ResearchSignalTracker()
    return _research_tracker
