# -*- coding: utf-8 -*-
"""
V59.7 Trade Funnel Analytics — 信号交易漏斗统计

统计信号从扫描 → 候选 → 质量门通过 → 实际开仓 → 平仓的每一步数量，
帮助定位信号在哪一步丢失。

用法:
    from analytics.trade_funnel import trade_funnel

    # 在关键节点调用
    trade_funnel.add("scan")       # 扫描开始
    trade_funnel.add("candidate")  # 候选信号生成
    trade_funnel.add("gate_pass")  # 质量门通过
    trade_funnel.add("opened")     # 实际开仓成功
    trade_funnel.add("closed")     # 平仓完成

    # 查看漏斗报告
    print(trade_funnel.report())   # 返回 dict
    print(trade_funnel.text())     # 返回可读文本

数据持久化到 data/trade_funnel.json，按日自动重置。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict


FUNNEL_FILE = "data/trade_funnel.json"


class TradeFunnel:
    """交易漏斗计数器。按日持久化，跨进程共享同一 JSON。"""

    def __init__(self, path: str = FUNNEL_FILE):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.data: Dict[str, Any] = self._default_data()
        self.load()

    def _default_data(self) -> Dict[str, Any]:
        return {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "scan": 0,          # 扫描次数（每 symbol 每轮）
            "candidate": 0,     # 候选信号数量（排重后）
            "gate_pass": 0,     # 通过质量门的信号数
            "opened": 0,        # 实际开仓成功数
            "closed": 0,        # 平仓完成数
        }

    def load(self) -> None:
        """加载当日漏斗数据；若日期过期则重置。"""
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                today = datetime.now().strftime("%Y-%m-%d")
                if saved.get("date") == today:
                    self.data = saved
                else:
                    # 新的一天，重置并保留日期
                    self.data = self._default_data()
        except Exception:
            self.data = self._default_data()

    def save(self) -> None:
        """持久化漏斗数据。"""
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def add(self, key: str) -> None:
        """增加某个漏斗节点的计数。"""
        if key in self.data:
            self.data[key] += 1
            self.save()

    def report(self) -> Dict[str, Any]:
        """返回当前漏斗数据 dict。"""
        return dict(self.data)

    def text(self) -> str:
        """返回可读的漏斗报告文本。"""
        d = self.data
        lines = [
            "📊 Trade Funnel 交易漏斗",
            f"  日期: {d.get('date', '?')}",
            f"  扫描: {d.get('scan', 0)}",
            f"  候选: {d.get('candidate', 0)}",
            f"  质量门通过: {d.get('gate_pass', 0)}",
            f"  实际开仓: {d.get('opened', 0)}",
            f"  平仓完成: {d.get('closed', 0)}",
        ]
        # 计算各阶段流失率
        scan = max(1, int(d.get("scan", 0)))
        cand = int(d.get("candidate", 0))
        gate = int(d.get("gate_pass", 0))
        opened = int(d.get("opened", 0))
        if cand > 0:
            lines.append(f"  候选/扫描: {cand/scan*100:.2f}%")
            if cand > gate:
                lines.append(f"  质量门拦截: {cand - gate} ({ (cand-gate)/cand*100:.1f}%)")
        if gate > opened:
            lines.append(f"  执行层丢失: {gate - opened} ({ (gate-opened)/max(1,gate)*100:.1f}%)")
        return "\n".join(lines)


# 全局单例，供直接导入使用
trade_funnel = TradeFunnel()