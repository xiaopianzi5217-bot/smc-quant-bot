# -*- coding: utf-8 -*-
"""信号后验验证日志。

每次开单时记录：
  - signal_id（完整指纹：symbol_direction_setup_type_idx_price_score_ev_slot）
  - 入口快照（score, ev, entry, sl, tp1~tp3, rr, regime, vol_state, book）
  - future_bars: 开单后的 N 根 15m K线（收盘价）
  - max_forward_r: 最大顺向 R
  - max_adverse_r: 最大逆向 R
  - final_outcome: 最终盈亏 R
  - 数据源可用于统计学习（哪些 signal_id 特征能预测正 EV）

使用方式：
  from utils.signal_audit_log import signal_audit_log
  signal_audit_log.record_open(signal_id, snapshot, future_close_prices)
  signal_audit_log.record_close(signal_id, final_pnl_r, max_forward_r, max_adverse_r, exit_reason)
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import List, Optional, Any, Dict


class SignalAuditLog:
    """后验验证日志：记录每个 signal_id 的完整生命周期。"""

    def __init__(self, path: str = "data/signal_audit"):
        self.dir = Path(path)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.open_file = self.dir / "open_signals.jsonl"
        self.closed_file = self.dir / "closed_signals.jsonl"
        # 内存缓存：signal_id -> line index in open_file (approximate)
        self._open_cache: Dict[str, dict] = {}

    # ------------------------------------------------------------------
    #  开单记录
    # ------------------------------------------------------------------
    def record_open(self, signal_id: str, snapshot: dict, future_close_prices: List[float]) -> None:
        """记录一条新开单信号。

        Args:
            signal_id: 信号指纹（含 setup_type, idx, score, ev, slot 等）
            snapshot: 开单快照 {symbol, direction, entry, sl, tp1~tp3, rr, score, ev, regime, ...}
            future_close_prices: 开单后 N 根 K线的收盘价（用于计算 max_forward/adverse R）
        """
        entry = {
            "signal_id": signal_id,
            "ts": time.time(),
            "snapshot": snapshot,
            "future_bars_ex0": list(future_close_prices),  # future_close_prices[0] = entry_bar
            "close_ts": None,
            "final_pnl_r": None,
            "max_forward_r": None,
            "max_adverse_r": None,
            "exit_reason": None,
        }
        self._open_cache[signal_id] = entry
        # 追加写入 open 文件
        try:
            with open(self.open_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except Exception as e:
            print(f"[SignalAuditLog] 写入开单记录失败: {e}")

        # 同时写入 closed 的 placeholder（便于统一分析）
        try:
            closed_entry = {
                "signal_id": signal_id,
                "ts_open": time.time(),
                "ts_close": None,
                "snapshot": snapshot,
                "future_bars_ex0": list(future_close_prices),
                "max_forward_r": None,
                "max_adverse_r": None,
                "final_pnl_r": None,
                "exit_reason": None,
            }
            with open(self.closed_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(closed_entry, ensure_ascii=False, default=str) + "\n")
        except Exception as e:
            print(f"[SignalAuditLog] 写入 closed placeholder 失败: {e}")

    # ------------------------------------------------------------------
    #  平仓记录（更新 closed 文件中的对应行）
    # ------------------------------------------------------------------
    def record_close(self, signal_id: str, final_pnl_r: float,
                     max_forward_r: float, max_adverse_r: float,
                     exit_reason: str) -> bool:
        """更新平仓结果。

        Args:
            signal_id: 信号指纹
            final_pnl_r: 最终盈亏（R倍数）
            max_forward_r: 持仓期间最大顺向 R
            max_adverse_r: 持仓期间最大逆向 R
            exit_reason: 平仓原因 (SL / TP1 / TP2 / TP3 / BE_AFTER_TP1 / TIME_EXIT)

        Returns:
            是否更新成功
        """
        if signal_id in self._open_cache:
            cached = self._open_cache[signal_id]
            cached["final_pnl_r"] = final_pnl_r
            cached["max_forward_r"] = max_forward_r
            cached["max_adverse_r"] = max_adverse_r
            cached["exit_reason"] = exit_reason
            cached["close_ts"] = time.time()

        # 更新 closed 文件：逐行读取，找到匹配的 signal_id，更新后回写
        if not self.closed_file.exists() or self.closed_file.stat().st_size == 0:
            return False

        try:
            lines = self.closed_file.read_text(encoding="utf-8").strip().split("\n")
            updated = False
            new_lines = []
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("signal_id") == signal_id and entry.get("exit_reason") is None:
                        # 只更新第一个未关闭的匹配项
                        entry["ts_close"] = time.time()
                        entry["final_pnl_r"] = final_pnl_r
                        entry["max_forward_r"] = max_forward_r
                        entry["max_adverse_r"] = max_adverse_r
                        entry["exit_reason"] = exit_reason
                        updated = True
                    new_lines.append(json.dumps(entry, ensure_ascii=False, default=str))
                except Exception:
                    new_lines.append(line)
            if updated:
                self.closed_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                return True
        except Exception as e:
            print(f"[SignalAuditLog] 更新平仓记录失败: {e}")
        return False

    # ------------------------------------------------------------------
    #  查询与统计
    # ------------------------------------------------------------------
    def load_closed(self) -> list:
        """加载所有已平仓记录（含后验结果）。"""
        if not self.closed_file.exists():
            return []
        lines = self.closed_file.read_text(encoding="utf-8").strip().split("\n")
        result = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                result.append(json.loads(line))
            except Exception:
                continue
        return result

    def summary(self) -> dict:
        """生成后验验证统计摘要。"""
        closed = self.load_closed()
        finished = [c for c in closed if c.get("final_pnl_r") is not None]
        if not finished:
            return {
                "total_signals": len(closed),
                "finished": 0,
                "win_rate": 0.0,
                "avg_pnl_r": 0.0,
                "avg_max_forward_r": 0.0,
                "avg_max_adverse_r": 0.0,
            }
        pnl_list = [c["final_pnl_r"] for c in finished if c["final_pnl_r"] is not None]
        fwd_list = [c["max_forward_r"] for c in finished if c["max_forward_r"] is not None]
        adv_list = [c["max_adverse_r"] for c in finished if c["max_adverse_r"] is not None]
        return {
            "total_signals": len(closed),
            "finished": len(finished),
            "win_rate": round(sum(1 for p in pnl_list if p > 0) / max(1, len(pnl_list)), 4),
            "avg_pnl_r": round(sum(pnl_list) / max(1, len(pnl_list)), 4),
            "avg_max_forward_r": round(sum(fwd_list) / max(1, len(fwd_list)), 4),
            "avg_max_adverse_r": round(sum(adv_list) / max(1, len(adv_list)), 4),
        }


# 全局单例
signal_audit_log = SignalAuditLog()
