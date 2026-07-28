"""资金曲线级熔断：日损 / 滚动回撤 / 连续亏损 → 降仓或停机。"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import List, Optional
import json
from pathlib import Path
from utils.structured_logger import slog


@dataclass
class BreakerState:
    day: str = ""
    day_pnl_r: float = 0.0
    consecutive_losses: int = 0
    halted: bool = False
    halt_reason: str = ""
    size_mult: float = 1.0
    recent_r: List[float] = field(default_factory=list)


class EquityCircuitBreaker:
    def __init__(
        self,
        max_daily_loss_r: float = 3.0,
        max_consec_losses: int = 3,
        max_rolling_dd_r: float = 5.0,     # 最近 20 笔累计最大回撤
        rolling_window: int = 20,
        soft_dd_r: float = 2.5,             # 达到后 size_mult=0.5
        state_path: str = "data/circuit_breaker_state.json",
    ):
        self.max_daily_loss_r = max_daily_loss_r
        self.max_consec_losses = max_consec_losses
        self.max_rolling_dd_r = max_rolling_dd_r
        self.rolling_window = rolling_window
        self.soft_dd_r = soft_dd_r
        self.state_path = Path(state_path)
        self.state = BreakerState()
        self._load()

    def on_new_day_if_needed(self):
        today = date.today().isoformat()
        if self.state.day != today:
            self.state.day = today
            self.state.day_pnl_r = 0.0
            # 新的一天不自动解除 halt（需人工或显式 reset）
            self._save()

    def record_trade(self, pnl_r: float):
        self.on_new_day_if_needed()
        self.state.day_pnl_r += float(pnl_r)
        self.state.recent_r.append(float(pnl_r))
        self.state.recent_r = self.state.recent_r[-self.rolling_window :]
        if pnl_r <= 0:
            self.state.consecutive_losses += 1
        else:
            self.state.consecutive_losses = 0
        self._recompute()
        self._save()

    def _rolling_drawdown(self) -> float:
        if not self.state.recent_r:
            return 0.0
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for r in self.state.recent_r:
            equity += r
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)
        return max_dd

    def _recompute(self):
        dd = self._rolling_drawdown()
        reasons = []
        if self.state.day_pnl_r <= -abs(self.max_daily_loss_r):
            self.state.halted = True
            reasons.append(f"日损 {self.state.day_pnl_r:.2f}R >= 上限")
        if self.state.consecutive_losses >= self.max_consec_losses:
            self.state.halted = True
            reasons.append(f"连续亏损 {self.state.consecutive_losses} 笔")
        if dd >= self.max_rolling_dd_r:
            self.state.halted = True
            reasons.append(f"滚动回撤 {dd:.2f}R")
        if self.state.halted:
            self.state.size_mult = 0.0
            self.state.halt_reason = "; ".join(reasons)
            return
        # 软降仓
        if dd >= self.soft_dd_r:
            self.state.size_mult = 0.5
            self.state.halt_reason = f"软熔断：滚动回撤 {dd:.2f}R"
        elif self.state.consecutive_losses >= max(1, self.max_consec_losses - 1):
            self.state.size_mult = 0.6
            self.state.halt_reason = "接近连续亏损上限"
        else:
            self.state.size_mult = 1.0
            self.state.halt_reason = ""

    def can_open(self) -> bool:
        self.on_new_day_if_needed()
        return not self.state.halted

    def size_multiplier(self) -> float:
        self.on_new_day_if_needed()
        return 0.0 if self.state.halted else float(self.state.size_mult)

    def reset_halt(self, reason: str = "manual"):
        self.state.halted = False
        self.state.halt_reason = f"reset:{reason}"
        self.state.size_mult = 1.0
        self._save()

    def snapshot(self) -> dict:
        return {
            "halted": self.state.halted,
            "halt_reason": self.state.halt_reason,
            "size_mult": self.state.size_mult,
            "day_pnl_r": round(self.state.day_pnl_r, 3),
            "consecutive_losses": self.state.consecutive_losses,
            "rolling_dd_r": round(self._rolling_drawdown(), 3),
        }

    def _load(self):
        if not self.state_path.exists():
            return
        try:
            d = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.state = BreakerState(
                **{k: d.get(k, getattr(BreakerState(), k))
                   for k in BreakerState.__dataclass_fields__}
            )
        except Exception:
            pass

    def _save(self):
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps(self.state.__dict__, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass