# utils/daily_risk_guard.py
import json
import os
from datetime import date


class DailyRiskGuard:
    """日风险守卫：防止单日过度亏损/连续亏损/过度交易。

    硬限制（任一达标即拦截）：
      - 单日亏损 >= -3R
      - 单日交易 >= 6 次
      - 连续亏损 >= 3 笔
      - 最大回撤 >= 5%（从当日峰值计算）

    用法：
        guard = DailyRiskGuard(save_path="daily_risk_state.json")
        if guard.can_trade():
            # 执行交易
            guard.on_trade_closed(r=1.5, equity_change=0.01)
    """

    def __init__(self, save_path="daily_risk_state.json"):
        self.save_path = save_path
        self._load_or_reset()

    def _load_or_reset(self):
        """从磁盘加载当日风控状态。若文件不存在或日期不符则重置。"""
        today = str(date.today())
        loaded = False
        if os.path.exists(self.save_path):
            try:
                with open(self.save_path, 'r') as f:
                    data = json.load(f)
                if data.get("date") == today:
                    self.date = today
                    self.daily_loss_r = data.get("daily_loss_r", 0.0)
                    self.trade_count = data.get("trade_count", 0)
                    self.consecutive_loss = data.get("consecutive_loss", 0)
                    self.max_drawdown = data.get("max_drawdown", 0.0)
                    loaded = True
            except Exception:
                pass
        if not loaded:
            self.reset()

    def reset(self):
        """重置当日风控状态（新的一天或首次加载）。"""
        self.date = str(date.today())
        self.daily_loss_r = 0.0
        self.trade_count = 0
        self.consecutive_loss = 0
        self.max_drawdown = 0.0
        self._save()

    def can_trade(self) -> bool:
        """检查当前是否允许开新单。

        Returns:
            True = 可以开单，False = 任一限制达标，禁止开单
        """
        # 当日是否跨天（跨天自动重置）
        if str(date.today()) != self.date:
            self.reset()
            return True

        if self.daily_loss_r <= -3.0:
            print(f"[DailyRiskGuard] 拦截：单日亏损 {self.daily_loss_r:.2f}R <= -3R")
            return False
        if self.trade_count >= 6:
            print(f"[DailyRiskGuard] 拦截：当日已交易 {self.trade_count} 次 >= 6")
            return False
        if self.consecutive_loss >= 3:
            print(f"[DailyRiskGuard] 拦截：连续亏损 {self.consecutive_loss} 笔 >= 3")
            return False
        if self.max_drawdown >= 0.05:
            print(f"[DailyRiskGuard] 拦截：当日最大回撤 {self.max_drawdown:.2%} >= 5%")
            return False
        return True

    def on_trade_closed(self, r: float, equity_change: float = 0.0):
        """交易结束后更新风控状态。

        Args:
            r: 该笔交易的盈亏 R 倍数（正=盈利，负=亏损）
            equity_change: 账户权益变化比例（如 0.01 = +1%）
        """
        # 检查是否跨天（跨天自动重置再计数）
        if str(date.today()) != self.date:
            self.reset()

        self.trade_count += 1
        # daily_loss_r 只累计亏损（保护余额而非利润）
        if r < 0:
            self.daily_loss_r += r
        if r < 0:
            self.consecutive_loss += 1
        else:
            self.consecutive_loss = 0
        # 最大回撤只记录亏损方向
        if equity_change < 0:
            self.max_drawdown = max(self.max_drawdown, -equity_change)
        self._save()

    def _save(self):
        """持久化当前风控状态到磁盘。"""
        try:
            data = {
                "date": self.date,
                "daily_loss_r": round(self.daily_loss_r, 4),
                "trade_count": self.trade_count,
                "consecutive_loss": self.consecutive_loss,
                "max_drawdown": round(self.max_drawdown, 4),
            }
            with open(self.save_path, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[DailyRiskGuard] 保存失败: {e}")
