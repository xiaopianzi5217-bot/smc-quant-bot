# utils/smart_position_sizer.py
"""智能仓位管理器：Kelly 公式 + 多因子修正。

统一整合：
  - ScoreGrade 的 size_mult（信号质量分级）
  - V37 Gate 的 size_mult（环境风险）
  - FeedbackLoop 的 confidence + avg_win_r + avg_loss_r（历史校准）
  - 日风控缩减（DailyRiskGuard）
  - 连续亏损缩减
  - 波动率修正

核心公式：
  Kelly% = (P_win * avg_win - P_loss * avg_loss) / avg_win
  最终仓位 = Kelly% * 基础杠杆 * size_mult_env * size_mult_grade * size_mult_drawdown
"""

from __future__ import annotations
import json
import math
import os
import time
from pathlib import Path
from typing import Optional, Dict, Any
from collections import deque


class SmartPositionSizer:
    """智能仓位管理器。

    用法：
        sizer = SmartPositionSizer()
        size = sizer.calculate(
            score=72.0,
            confidence=0.55,
            avg_win_r=0.85,
            avg_loss_r=0.65,
            base_leverage=0.05,        # 基础仓位 5%
            grade_size_mult=0.85,       # ScoreGrade 的 size_mult
            env_size_mult=0.90,         # V37 Gate / intelligence_engine 的 size_mult
            regime="TREND",
            volatility="normal",
        )
    """

    # ── 约束参数 ──
    MAX_POSITION = 0.15        # 最大仓位 15%
    MIN_POSITION = 0.005       # 最小仓位 0.5%（原 1%，允许 probe 更小）
    DEFAULT_BASE = 0.05        # 基础仓位 5%

    # Kelly 比例缩放（保守度）：实际使用 Kelly% * FRACTION
    # 1.0 = 全 Kelly（激进），0.25 = 1/4 Kelly（保守）
    KELLY_FRACTION = 0.25      # 约 1/4 Kelly（从 0.35 降低，更加保守）

    # 【新增20260729】单笔最大风险（账户比例）
    # 基于 ATR 自适应：当 ATR 比例较大时降低仓位
    MAX_RISK_PER_TRADE = 0.01  # 单笔最大风险 1% 账户
    TARGET_RISK_PER_TRADE = 0.005  # 目标风险 0.5% 账户

    # 连续亏损缩减参数
    CONS_LOSS_CUT = 0.80       # 每连续亏损一笔仓位乘以 0.8
    CONS_LOSS_MAX = 5          # 最多追踪 5 笔

    # 波动率修正
    VOL_MULTIPLIERS = {
        "HIGH_VOL": 0.65,
        "MID_VOL": 0.85,
        "LOW_VOL": 1.0,
        "high_vol": 0.65,
        "mid_vol": 0.85,
        "low_vol": 1.0,
        "normal": 1.0,
    }

    # 市况修正（与 intelligence_engine 保持一致）
    REGIME_MULTIPLIERS = {
        "TREND": 1.0,
        "BULL": 1.0,
        "BEAR": 1.0,
        "CHOP": 0.75,
        "RANGE": 0.75,
        "TRANSITION": 0.80,
        "CRISIS_RISK_OFF": 0.40,
    }

    def __init__(self, save_path: str = "data/sizer_state.json"):
        self.save_path = Path(save_path)
        self.save_path.parent.mkdir(parents=True, exist_ok=True)
        # 追踪最近 20 笔盈亏（用于连续亏损缩减 + Kelly 统计）
        self.recent_pnls: deque = deque(maxlen=20)
        self._load()

    # ──────────────── 核心计算 ────────────────

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
        atr_pct: float = 0.0,       # 【新增】ATR 百分比（当前 ATR / 价格）
        account_balance: float = 1000.0,  # 【新增】账户余额
        entry_price: float = 0.0,   # 【新增】入场价，用于计算风险金额
        sl_price: float = 0.0,      # 【新增】止损价，用于计算风险比例
    ) -> dict:
        """计算最终仓位。

        Args:
            score: 信号评分 (0~100)
            confidence: P(win) 校准概率
            avg_win_r: 历史盈利单的平均 R 倍数
            avg_loss_r: 历史亏损单的平均 R 倍数
            base_leverage: 基础杠杆（默认 5% = 0.05）
            grade_size_mult: ScoreGrade 等级乘数（A_PLUS=1.0, A=0.85, B=0.65）
            env_size_mult: 环境风险乘数（V37 Gate / intelligence_engine）
            regime: 市况（TREND / CHOP / CRISIS_RISK_OFF...）
            volatility: 波动率（HIGH_VOL / MID_VOL / LOW_VOL）

        Returns:
            包含各项明细的字典
        """
        base = base_leverage if base_leverage is not None else self.DEFAULT_BASE

        # 1️⃣ Kelly 公式
        kelly_pct = self._kelly(confidence, avg_win_r, avg_loss_r)

        # 2️⃣ 多因子缩减链
        grade_mult = max(0.0, min(1.0, grade_size_mult))
        env_mult = max(0.0, min(1.0, env_size_mult))
        regime_mult = self.REGIME_MULTIPLIERS.get(regime.upper(), 1.0)
        vol_mult = self.VOL_MULTIPLIERS.get(volatility, 1.0)

        # 3️⃣ 连续亏损缩减
        cons_loss_mult = self._consecutive_loss_mult()

        # 4️⃣ 评分软缩减（与 score_grade 互补）
        score_mult = self._score_penalty(score)

        # 5️⃣ 【新增20260729】ATR 自适应风险限制
        #   当 ATR 百分比高于正常水平时，自动降低仓位
        atr_mult = 1.0
        if atr_pct > 0:
            # 基准 ATR 比例 0.8% (BTC 15m 典型值)
            _baseline_atr = 0.008
            if atr_pct > _baseline_atr * 2:
                atr_mult = max(0.3, _baseline_atr * 2 / atr_pct)
            elif atr_pct > _baseline_atr * 1.5:
                atr_mult = max(0.5, _baseline_atr * 1.5 / atr_pct)
            elif atr_pct > _baseline_atr:
                atr_mult = max(0.7, _baseline_atr / atr_pct)
            # atr_pct <= 基线时 atr_mult = 1.0

        # 6️⃣ 【新增20260729】单笔最大风险金额限制
        risk_mult = 1.0
        if entry_price > 0 and sl_price > 0 and account_balance > 0:
            _risk_per_unit = abs(entry_price - sl_price) / entry_price  # 每元仓位的风险比例
            if _risk_per_unit > 0:
                # 根据目标风险反推仓位上限
                _max_pos_for_risk = self.TARGET_RISK_PER_TRADE / _risk_per_unit
                _abs_max_pos = self.MAX_RISK_PER_TRADE / _risk_per_unit
                risk_mult = min(_max_pos_for_risk / max(base, 0.001), 1.0)
                # 当风险比例很高时，强制缩减
                if risk_mult < 0.3:
                    # 风险过大，强烈降仓
                    risk_mult = max(0.1, risk_mult)
                    print(f"[SmartSizer] 风险比例 {_risk_per_unit*100:.2f}% 过高, 强制缩减至 {risk_mult*100:.0f}%")
                elif risk_mult < 0.6:
                    print(f"[SmartSizer] 风险比例 {_risk_per_unit*100:.2f}% 偏高, 缩减至 {risk_mult*100:.0f}%")

        # ── 最终计算 ──
        raw_size = base * kelly_pct * grade_mult * env_mult * regime_mult * vol_mult * cons_loss_mult * score_mult * atr_mult * risk_mult
        final_size = max(self.MIN_POSITION, min(self.MAX_POSITION, raw_size))

        # 保存状态
        self._save()

        return {
            "final_size": round(final_size, 4),
            "base_leverage": round(base, 4),
            "kelly_pct": round(kelly_pct, 4),
            "kelly_info": self._last_kelly_info,
            "grade_mult": round(grade_mult, 4),
            "env_mult": round(env_mult, 4),
            "regime_mult": round(regime_mult, 4),
            "vol_mult": round(vol_mult, 4),
            "cons_loss_mult": round(cons_loss_mult, 4),
            "score_mult": round(score_mult, 4),
            "atr_mult": round(atr_mult, 4),        # 【新增】ATR 自适应系数
            "risk_mult": round(risk_mult, 4),      # 【新增】单笔风险限制系数
            "raw_size": round(raw_size, 4),
            "confidence": round(confidence, 4),
            "score": round(score, 2),
        }

    # ──────────────── 子模块 ────────────────

    def _kelly(self, p_win: float, avg_win: float, avg_loss: float) -> float:
        """计算 Kelly 百分比。

        Kelly% = (p * b - q) / b
        其中 b = avg_win / avg_loss（赔率），q = 1 - p

        Args:
            p_win: 胜率 P(win)
            avg_win: 盈利单平均 R
            avg_loss: 亏损单平均 R（正数）

        Returns:
            Kelly 百分比（0~1），经半 Kelly 和上下限约束
        """
        p = max(0.01, min(0.99, p_win))
        q = 1.0 - p
        b = max(0.1, avg_win / max(avg_loss, 0.01))

        if b <= 0:
            kelly = 0.0
        else:
            kelly = (p * b - q) / b

        # 半 Kelly + 上下限
        kelly = max(0.0, min(0.5, kelly * self.KELLY_FRACTION))

        self._last_kelly_info = {
            "p_win": round(p, 4),
            "q_loss": round(q, 4),
            "odds_b": round(b, 4),
            "full_kelly": round(kelly / max(self.KELLY_FRACTION, 0.01), 4),
            "fraction": self.KELLY_FRACTION,
        }
        return kelly

    def _consecutive_loss_mult(self) -> float:
        """连续亏损缩减：每连续亏损一笔乘 0.8，最小 0.2。"""
        cons = self._count_consecutive_losses()
        if cons <= 0:
            return 1.0
        mult = self.CONS_LOSS_CUT ** min(cons, self.CONS_LOSS_MAX)
        return max(0.2, mult)

    def _score_penalty(self, score: float) -> float:
        """评分软缩减：与 score_grade 互补，但在 low end 更敏感。"""
        if score >= 85:
            return 1.0
        elif score >= 75:
            return 0.90
        elif score >= 65:
            return 0.75
        elif score >= 50:
            return 0.55
        else:
            return 0.30

    # ──────────────── 外部接口 ────────────────

    def record_outcome(self, pnl_r: float):
        """记录一笔交易的结果，用于连续亏损统计。"""
        self.recent_pnls.append(pnl_r)
        self._save()

    def _count_consecutive_losses(self) -> int:
        """计算最近连续亏损笔数。"""
        count = 0
        for r in reversed(self.recent_pnls):
            if r <= 0:
                count += 1
            else:
                break
        return count

    def get_recent_win_rate(self, n: int = 20) -> float:
        """最近 N 笔的胜率。"""
        recent = list(self.recent_pnls)[-n:]
        if not recent:
            return 0.5
        return sum(1 for r in recent if r > 0) / len(recent)

    def get_recent_avg_r(self, n: int = 20) -> float:
        """最近 N 笔的平均 R。"""
        recent = list(self.recent_pnls)[-n:]
        if not recent:
            return 0.0
        return sum(recent) / len(recent)

    # ──────────────── 持久化 ────────────────

    def _load(self):
        if self.save_path.exists():
            try:
                data = json.loads(self.save_path.read_text(encoding="utf-8"))
                self.recent_pnls = deque(data.get("recent_pnls", []), maxlen=20)
            except Exception:
                pass

    def _save(self):
        try:
            self.save_path.write_text(
                json.dumps({"recent_pnls": list(self.recent_pnls)}, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[SmartPositionSizer] save failed: {e}")


# 全局单例
_sizer: Optional[SmartPositionSizer] = None


def get_smart_sizer() -> SmartPositionSizer:
    global _sizer
    if _sizer is None:
        _sizer = SmartPositionSizer()
    return _sizer
