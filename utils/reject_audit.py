# -*- coding: utf-8 -*-
"""
Reject Audit — 拦截审计系统

每个拦截点记录：
  - timestamp / symbol / 拦截点名称
  - score / ev / regime 等决策参数快照
  - observer 事件摘要（便于分析拦截是否合理）

不改变交易逻辑，仅用作离线调试与统计分析。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class RejectAudit:
    """拦截审计日志：记录每次被拦截拒绝的原因及上下文快照。

    每个拦截点调用 log() 方法记录一行 JSONL。
    支持按 symbol / 拦截点 / 时间段 查询统计。
    """

    def __init__(self, path: str = "logs/reject_audit.jsonl"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        symbol: str,
        gate_name: str,
        score: float = 0.0,
        ev: float = 0.0,
        regime: str = "UNKNOWN",
        vol_state: str = "unknown",
        direction: str = "",
        setup_type: str = "",
        observer_event_types: Optional[List[str]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ):
        """记录一条拦截审计日志。

        Args:
            symbol: 交易对
            gate_name: 拦截点名称（如 'GATE-2_COOLDOWN', 'EV_TOO_LOW', 'SCORE_LOW'）
            score: 信号评分
            ev: expected value
            regime: 市场状态
            vol_state: 波动率状态
            direction: 信号方向 (Long/Short)
            setup_type: 信号设置类型 (LIQUIDITY_SWEEP/FVG_TOUCH 等)
            observer_event_types: 当前 Observer 事件类型列表
            extra: 额外上下文信息
        """
        entry = {
            "ts": time.time(),
            "symbol": str(symbol),
            "gate": str(gate_name),
            "score": round(float(score), 2) if score else 0.0,
            "ev": round(float(ev), 4) if ev else 0.0,
            "regime": str(regime),
            "vol_state": str(vol_state),
            "direction": str(direction),
            "setup_type": str(setup_type),
            "observer_events": observer_event_types or [],
        }

        if extra:
            # 只保留可序列化的额外字段
            serializable_extra = {}
            for k, v in extra.items():
                try:
                    json.dumps(v)
                    serializable_extra[k] = v
                except (TypeError, ValueError):
                    serializable_extra[k] = str(v)
            entry["extra"] = serializable_extra

        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[RejectAudit] 写入日志失败: {e}")

    # ──────────────── 查询 / 统计 ────────────────

    def load(self) -> List[Dict[str, Any]]:
        """加载所有拦截审计记录。"""
        if not self.path.exists() or self.path.stat().st_size == 0:
            return []
        records = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except Exception:
                    continue
        return records

    def summary(self, top_n: int = 10) -> Dict[str, Any]:
        """生成拦截审计摘要统计。"""
        records = self.load()
        if not records:
            return {"total_rejected": 0, "gates": {}}

        total = len(records)
        gates: Dict[str, int] = {}
        by_symbol: Dict[str, int] = {}
        by_regime: Dict[str, int] = {}

        for rec in records:
            gate = rec.get("gate", "UNKNOWN")
            gates[gate] = gates.get(gate, 0) + 1

            symbol = rec.get("symbol", "?")
            by_symbol[symbol] = by_symbol.get(symbol, 0) + 1

            regime = rec.get("regime", "UNKNOWN")
            by_regime[regime] = by_regime.get(regime, 0) + 1

        sorted_gates = sorted(gates.items(), key=lambda x: x[1], reverse=True)

        return {
            "total_rejected": total,
            "gates": dict(sorted_gates[:top_n]),
            "by_symbol": dict(sorted(by_symbol.items(), key=lambda x: x[1], reverse=True)[:top_n]),
            "by_regime": by_regime,
            "top_gate": sorted_gates[0] if sorted_gates else ("NONE", 0),
        }

    def query_by_gate(self, gate_name: str) -> List[Dict[str, Any]]:
        """按拦截点名称查询。"""
        return [r for r in self.load() if r.get("gate") == gate_name]

    def query_by_symbol(self, symbol: str) -> List[Dict[str, Any]]:
        """按交易对查询。"""
        return [r for r in self.load() if r.get("symbol") == symbol]


# 全局单例
_reject_audit = RejectAudit()


def get_reject_audit() -> RejectAudit:
    return _reject_audit
