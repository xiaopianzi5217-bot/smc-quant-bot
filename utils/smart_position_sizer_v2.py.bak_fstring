"""SmartPositionSizer V2：滚动收缩 Kelly + 权益回撤缩放 + 双约束仓位。"""
from __future__ import annotations
import json
import math
from collections import deque
from pathlib import Path
from typing import Optional, Deque, Dict, Any
import numpy as np
from utils.structured_logger import slog


class SmartPositionSizerV2:
    MAX_POSITION = 0.12
    MIN_POSITION = 0.003
    DEFAULT_BASE = 0.05
    KELLY_FRACTION = 0.20            # 更保守：1/5 Kelly
    MAX_RISK_PER_TRADE = 0.008
    TARGET_RISK_PER_TRADE = 0.005
    CONS_LOSS_CUT = 0.75
    CONS_LOSS_MAX = 5
    # 滚动窗口与收缩
    ROLLING_N = 40
    PRIOR_WIN_RATE = 0.48
    PRIOR_STRENGTH = 12               # 伪计数，防止小样本爆炸
    VOL_MULTIPLIERS = {
        "HIGH_VOL": 0.60, "MID_VOL": 0.85, "LOW_VOL": 1.0,
        "high_vol": 0.60, "mid_vol": 0.85, "low_vol": 1.0, "normal": 1.0,
    }
    REGIME_MULTIPLIERS = {
        "TREND": 1.0, "BULL": 1.0, "BEAR": 0.95,
        "CHOP": 0.70, "RANGE": 0.70, "TRANSITION": 0.75,
        "CRISIS_RISK_OFF": 0.30,
    }

    def __init__(self, save_path: str = "data/sizer_state_v2.json"):
        self.save_path = Path(save_path)
        self.save_path.parent.mkdir(parents=True, exist_ok=True)
        self.recent_pnls: Deque[float] = deque(maxlen=self.ROLLING_N)
        self.equity_curve: Deque[float] = deque(maxlen=200)   # 累计 R
        self.peak_equity: float = 0.0
        self._load()

    # ── 核心 ────────────────────────────────────────────
    def calculate(
        self,
        score: float = 0.0,
        confidence: float = 0.5,
        avg_win_r: float = 0.50,
        avg_loss_r: float = 0.50,
        base_leverage: Optional[float] = None,
        grade_size_mult: float = 1.0,
        env_size_mult: float = 1.0,
        regime: str = "UNKNOWN",
        volatility: str = "normal",
        atr_pct: float = 0.0,
        account_balance: float = 1000.0,
        entry_price: float = 0.0,
        sl_price: float = 0.0,
    ) -> Dict[str, Any]:
        base = base_leverage if base_leverage is not None else self.DEFAULT_BASE

        # 1) 用滚动结果收缩 p_win / avg_win / avg_loss
        p_win, aw, al = self._shrunk_stats(confidence, avg_win_r, avg_loss_r)
        kelly_pct = self._kelly(p_win, aw, al)

        grade_mult = float(np.clip(grade_size_mult, 0.0, 1.0))
        env_mult = float(np.clip(env_size_mult, 0.0, 1.0))
        regime_mult = self.REGIME_MULTIPLIERS.get(str(regime).upper(), 0.85)
        vol_mult = self.VOL_MULTIPLIERS.get(volatility, 1.0)
        cons_loss_mult = self._consecutive_loss_mult()
        score_mult = self._score_penalty(score)
        atr_mult = self._atr_mult(atr_pct)
        risk_mult = self._risk_amount_mult(entry_price, sl_price, account_balance, base)
        dd_mult = self._drawdown_mult()                     # 新增：权益回撤缩放

        raw = (
            base * kelly_pct * grade_mult * env_mult * regime_mult
            * vol_mult * cons_loss_mult * score_mult * atr_mult
            * risk_mult * dd_mult
        )
        final = float(np.clip(raw, self.MIN_POSITION, self.MAX_POSITION))

        # 双约束：若按目标风险算出的仓位更小，取 min
        risk_cap = self._position_from_risk(entry_price, sl_price, account_balance)
        if risk_cap is not None:
            final = min(final, risk_cap)

        self._save()

        return {
            "final_size": round(final, 4),
            "base_leverage": round(base, 4),
            "kelly_pct": round(kelly_pct, 4),
            "p_win_shrunk": round(p_win, 4),
            "avg_win_r": round(aw, 4),
            "avg_loss_r": round(al, 4),
            "grade_mult": round(grade_mult, 4),
            "env_mult": round(env_mult, 4),
            "regime_mult": round(regime_mult, 4),
            "vol_mult": round(vol_mult, 4),
            "cons_loss_mult": round(cons_loss_mult, 4),
            "score_mult": round(score_mult, 4),
            "atr_mult": round(atr_mult, 4),
            "risk_mult": round(risk_mult, 4),
            "dd_mult": round(dd_mult, 4),
            "risk_cap": None if risk_cap is None else round(risk_cap, 4),
            "raw_size": round(raw, 4),
            "score": round(score, 2),
        }

    # ── 子模块 ──────────────────────────────────────────
    def _shrunk_stats(self, p_prior: float, aw_prior: float, al_prior: float):
        recent = list(self.recent_pnls)
        n = len(recent)
        if n < 5:
            return (
                float(np.clip(p_prior, 0.25, 0.65)),
                max(0.15, aw_prior),
                max(0.15, al_prior),
            )
        wins = [r for r in recent if r > 0]
        losses = [abs(r) for r in recent if r <= 0]
        emp_p = len(wins) / n
        emp_aw = float(np.mean(wins)) if wins else aw_prior
        emp_al = float(np.mean(losses)) if losses else al_prior
        # 贝叶斯收缩
        w = n / (n + self.PRIOR_STRENGTH)
        p = w * emp_p + (1 - w) * self.PRIOR_WIN_RATE
        aw = w * emp_aw + (1 - w) * max(0.2, aw_prior)
        al = w * emp_al + (1 - w) * max(0.2, al_prior)
        return float(np.clip(p, 0.30, 0.62)), max(0.15, aw), max(0.15, al)

    def _kelly(self, p_win: float, avg_win: float, avg_loss: float) -> float:
        p = float(np.clip(p_win, 0.01, 0.99))
        q = 1.0 - p
        b = max(0.15, avg_win / max(avg_loss, 0.05))
        full = (p * b - q) / b if b > 0 else 0.0
        return float(np.clip(full * self.KELLY_FRACTION, 0.0, 0.40))

    def _consecutive_loss_mult(self) -> float:
        cons = 0
        for r in reversed(self.recent_pnls):
            if r <= 0:
                cons += 1
            else:
                break
        if cons <= 0:
            return 1.0
        return max(0.15, self.CONS_LOSS_CUT ** min(cons, self.CONS_LOSS_MAX))

    def _score_penalty(self, score: float) -> float:
        nodes = [0.0, 35.0, 40.0, 50.0, 70.0, 100.0]
        scales = [0.0, 0.00, 0.45, 1.00, 1.35, 1.35]
        return float(np.interp(score, nodes, scales))

    def _atr_mult(self, atr_pct: float) -> float:
        if atr_pct <= 0:
            return 1.0
        baseline = 0.008
        if atr_pct > baseline * 2.5:
            return max(0.25, baseline * 2.0 / atr_pct)
        if atr_pct > baseline * 1.5:
            return max(0.45, baseline * 1.5 / atr_pct)
        if atr_pct > baseline:
            return max(0.70, baseline / atr_pct)
        return 1.0

    def _risk_amount_mult(self, entry, sl, balance, base) -> float:
        if entry <= 0 or sl <= 0 or balance <= 0:
            return 1.0
        risk_per_unit = abs(entry - sl) / entry
        if risk_per_unit <= 0:
            return 1.0
        max_pos = self.TARGET_RISK_PER_TRADE / risk_per_unit
        return float(np.clip(max_pos / max(base, 0.001), 0.10, 1.0))

    def _position_from_risk(self, entry, sl, balance) -> Optional[float]:
        if entry <= 0 or sl <= 0 or balance <= 0:
            return None
        risk_per_unit = abs(entry - sl) / entry
        if risk_per_unit <= 0:
            return None
        return float(np.clip(self.MAX_RISK_PER_TRADE / risk_per_unit, self.MIN_POSITION, self.MAX_POSITION))

    def _drawdown_mult(self) -> float:
        """滚动权益从峰值回撤越大，仓位越小。"""
        if not self.equity_curve:
            return 1.0
        eq = self.equity_curve[-1]
        peak = max(self.peak_equity, eq)
        if peak <= 0:
            return 1.0
        dd = (peak - eq) / max(abs(peak), 1e-6)
        if dd >= 0.25:
            return 0.25
        if dd >= 0.15:
            return 0.50
        if dd >= 0.08:
            return 0.75
        return 1.0

    # ── 外部接口 ────────────────────────────────────────
    def record_outcome(self, pnl_r: float):
        self.recent_pnls.append(float(pnl_r))
        prev = self.equity_curve[-1] if self.equity_curve else 0.0
        new_eq = prev + float(pnl_r)
        self.equity_curve.append(new_eq)
        self.peak_equity = max(self.peak_equity, new_eq)
        self._save()

    def _load(self):
        if not self.save_path.exists():
            return
        try:
            data = json.loads(self.save_path.read_text(encoding="utf-8"))
            self.recent_pnls = deque(data.get("recent_pnls", []), maxlen=self.ROLLING_N)
            self.equity_curve = deque(data.get("equity_curve", []), maxlen=200)
            self.peak_equity = float(data.get("peak_equity", 0.0))
        except Exception:
            pass

    def _save(self):
        try:
            self.save_path.write_text(
                json.dumps(
                    {
                        "recent_pnls": list(self.recent_pnls),
                        "equity_curve": list(self.equity_curve),
                        "peak_equity": self.peak_equity,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as e:
            slog.error("[SmartPositionSizerV2] save failed: {e}")


_sizer_v2: Optional[SmartPositionSizerV2] = None


def get_smart_sizer_v2() -> SmartPositionSizerV2:
    global _sizer_v2
    if _sizer_v2 is None:
        _sizer_v2 = SmartPositionSizerV2()
    return _sizer_v2