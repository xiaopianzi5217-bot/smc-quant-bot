# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from typing import Any, Dict

from risk.position_sizing import calc_position_size
try:
    from notifier.manager import dispatch_execution_event
except Exception:
    dispatch_execution_event = None

# ===== 微观执行卫士（可选导入，不影响无 ZMQ 环境的运行）=====
try:
    from execution.micro.guard import MicroExecutionGuard
except ImportError:
    MicroExecutionGuard = None  # type: ignore


def _num(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        x = float(v)
        return x if x == x else default
    except Exception:
        return default


class LiveExecutionEngine:
    def __init__(self, cfg, exchange_adapter, portfolio, logger, notifier=None):
        self.cfg = cfg
        self.exchange = exchange_adapter
        self.portfolio = portfolio
        self.logger = logger
        self.notifier = notifier

        # ===== 微观执行卫士（惰性初始化）=====
        self.micro_guard = None  # type: ignore[assignment]
        self._init_micro_guard()

    def _init_micro_guard(self):
        """根据配置初始化微观卫士"""
        micro_cfg = self.cfg.get("micro_guard", {})
        if not micro_cfg.get("enabled", False):
            self.logger and self.logger.log("MICRO_GUARD", message="微观卫士未启用 (micro_guard.enabled=false)")
            return
        if MicroExecutionGuard is None:
            self.logger and self.logger.log("MICRO_GUARD", message="微观卫士模块未安装 (execution.micro.guard 不可用)")
            return
        try:
            # CCXT 格式 (BTC/USDT) → ZMQ 格式 (BTCUSDT)
            raw_symbol = str(micro_cfg.get("symbol", "BTCUSDT"))
            zmq_symbol = raw_symbol.replace("/", "").replace(":", "").upper()
            self.micro_guard = MicroExecutionGuard(
                symbol=zmq_symbol,
                connect_addr=micro_cfg.get("zmq_addr", "tcp://127.0.0.1:5555"),
                obi_long=float(micro_cfg.get("obi_long", 0.20)),
                obi_short=float(micro_cfg.get("obi_short", -0.20)),
                cvd_long=float(micro_cfg.get("cvd_long", 5.0)),
                cvd_short=float(micro_cfg.get("cvd_short", -5.0)),
            )
            self.logger and self.logger.log(
                "MICRO_GUARD",
                message=f"微观卫士已启动: {zmq_symbol} @ {micro_cfg.get('zmq_addr')}",
                raw={"thresholds": {
                    "obi_long": micro_cfg.get("obi_long"),
                    "obi_short": micro_cfg.get("obi_short"),
                }},
            )
        except Exception as exc:
            self.logger and self.logger.log("MICRO_GUARD", message=f"微观卫士初始化失败: {exc}")
            self.micro_guard = None

    def _validate_risk_plan(self, risk_plan: Dict[str, Any]):
        required = ["entry", "sl", "tp1", "tp2", "tp3"]
        missing = [k for k in required if risk_plan.get(k) is None]
        if missing:
            return False, f"risk_plan缺少字段: {missing}"
        entry = _num(risk_plan.get("entry"))
        sl = _num(risk_plan.get("sl"))
        if entry <= 0 or sl <= 0 or abs(entry - sl) <= 0:
            return False, "risk_plan入场价/止损价无效"
        return True, "OK"

    def _normalize_order(self, order: Dict[str, Any] | None, requested_size: float, fallback_price: float) -> Dict[str, Any]:
        order = dict(order or {})
        status = str(order.get("status") or "").lower()
        if status in {"open", "new", "partially_filled", "partial"} and order.get("id"):
            refreshed = None
            if hasattr(self.exchange, "fetch_order_safe"):
                refreshed = self.exchange.fetch_order_safe(order.get("id"), order.get("symbol"))
            if refreshed:
                order.update(refreshed)
                status = str(order.get("status") or status).lower()

        filled = _num(order.get("filled"), 0.0)
        amount = _num(order.get("amount"), requested_size)
        remaining = _num(order.get("remaining"), max(amount - filled, 0.0))
        average = _num(order.get("average") or order.get("avgPrice") or order.get("price"), fallback_price)

        if filled <= 0 and getattr(self.exchange, "dry_run", False):
            filled = requested_size
            remaining = 0.0
            status = "closed"
        if average <= 0:
            average = fallback_price

        ok_status = status in {"closed", "filled", "ok"} or (filled > 0 and remaining <= max(amount * 0.001, 1e-12))
        order.update({
            "filled": filled,
            "remaining": remaining,
            "average": average,
            "status": status or ("closed" if ok_status else "unknown"),
            "fill_ok": bool(ok_status and filled > 0),
        })
        return order

    def execute_decision(self, symbol, decision):
        if not decision or not decision.get("approved"):
            self.logger.log("REJECTED", symbol=symbol, message="decision not approved", raw=decision or {})
            return {"ok": False, "reason": "decision not approved"}

        live_enabled = os.getenv("ENABLE_LIVE_TRADING", "false").lower() == "true"
        if not self.exchange.dry_run and not live_enabled:
            return {"ok": False, "reason": "live trading blocked: set ENABLE_LIVE_TRADING=true"}

        risk_plan = decision.get("risk_plan") or {}
        primary = decision.get("primary") or {}
        direction = risk_plan.get("direction") or primary.get("direction") or decision.get("direction")
        if direction not in {"Long", "Short"}:
            return {"ok": False, "reason": f"方向无效: {direction}"}

        ok_plan, plan_reason = self._validate_risk_plan(risk_plan)
        if not ok_plan:
            self.logger.log("RISK_PLAN_BLOCK", symbol=symbol, direction=direction, message=plan_reason, raw=decision)
            return {"ok": False, "reason": plan_reason}

        can_open, reason = self.portfolio.can_open(symbol, direction)
        if not can_open:
            self.logger.log("PORTFOLIO_BLOCK", symbol=symbol, direction=direction, message=reason, raw=decision)
            return {"ok": False, "reason": reason}

        in_cd, cd_reason = self.portfolio.is_in_loss_cooldown(symbol)
        if in_cd:
            self.logger.log("COOLDOWN_BLOCK", symbol=symbol, direction=direction, message=cd_reason, raw=decision)
            return {"ok": False, "reason": cd_reason}

        balance = self.exchange.fetch_balance_usdt()
        sizing = calc_position_size(
            balance=balance,
            risk_pct=self.cfg["risk"].get("account_risk_pct", 0.005),
            entry=risk_plan["entry"],
            stop_loss=risk_plan["sl"],
            min_notional=self.cfg["risk"].get("min_notional_usdt", 5),
        )
        if not sizing["ok"]:
            self.logger.log("SIZING_BLOCK", symbol=symbol, direction=direction, message=sizing["reason"], raw={"decision": decision, "sizing": sizing})
            return {"ok": False, "reason": sizing["reason"]}

        # ===== 微观执行卫士：发单前的盘口验证 =====
        micro_cfg = self.cfg.get("micro_guard", {})
        if self.micro_guard is not None and micro_cfg.get("enabled", False):
            micro_timeout = float(micro_cfg.get("timeout_seconds", 60))
            quick = self.micro_guard.check_entry_immediate(direction)
            if quick is True:
                self.logger.log("MICRO_PASS", symbol=symbol, direction=direction,
                                message="微观瞬时检查通过（OBI达标）")
            elif quick is False:
                self.logger.log("MICRO_BLOCK", symbol=symbol, direction=direction,
                                message="微观瞬时检查拦截（OBI方向错误）",
                                raw=self.micro_guard.latest_state)
                return {"ok": False, "reason": "MICRO_BLOCK: OBI direction mismatch"}
            elif quick is None and micro_timeout > 0 and not self.exchange.dry_run:
                # 数据不新鲜 + 实盘模式：阻塞等待确认
                is_approved = self.micro_guard.verify_entry(
                    direction=direction,
                    timeout_seconds=micro_timeout,
                )
                if not is_approved:
                    self.logger.log("MICRO_BLOCK", symbol=symbol, direction=direction,
                                    message=f"微观确认超时/失败 ({micro_timeout}s)",
                                    raw=self.micro_guard.latest_state)
                    return {"ok": False, "reason": f"MICRO_BLOCK: 微观确认超时 ({micro_timeout}s)"}
            else:
                # 干运行/模拟模式：数据不新鲜但放行（日志记录）
                self.logger.log("MICRO_SKIP", symbol=symbol, direction=direction,
                                message="模拟模式：微观数据不新鲜但放行（仅记录）")
        # ===== 微观验证结束 =====

        try:
            raw_order = self.exchange.create_market_order(symbol, direction, sizing["size"])
            order = self._normalize_order(raw_order, sizing["size"], float(risk_plan["entry"]))
        except Exception as exc:
            self.logger.log("ORDER_SUBMIT_FAILED", symbol=symbol, direction=direction, message=str(exc), raw={"decision": decision, "sizing": sizing})
            return {"ok": False, "reason": f"ORDER_SUBMIT_FAILED: {exc}"}

        if not order.get("fill_ok"):
            self.logger.log("ORDER_NOT_FILLED", symbol=symbol, direction=direction, message="order submitted but fill not confirmed", raw={"decision": decision, "sizing": sizing, "order": order})
            return {"ok": False, "reason": "order submitted but fill not confirmed", "order": order, "sizing": sizing}

        fill_qty = float(order.get("filled") or sizing["size"])
        fill_price = float(order.get("average") or risk_plan["entry"])
        filled_plan = dict(risk_plan)
        filled_plan["entry"] = fill_price
        pos = self.portfolio.add_position(symbol, direction, fill_qty, filled_plan, order=order)

        self.logger.log(
            "OPEN",
            symbol=symbol,
            direction=direction,
            state="OPEN",
            size=fill_qty,
            entry=fill_price,
            sl=filled_plan["sl"],
            tp1=filled_plan["tp1"],
            tp2=filled_plan["tp2"],
            tp3=filled_plan["tp3"],
            score=primary.get("score", ""),
            priority=primary.get("priority", ""),
            risk_amount=sizing["risk_amount"],
            notional=sizing["notional"],
            message="open position after fill confirmed",
            raw={"decision": decision, "sizing": sizing, "order": order, "position": pos.to_dict()},
        )

        event = {
            "type": "POSITION_OPENED",
            "symbol": symbol,
            "message": "Execution 层确认成交后记录开仓事件",
            "detail": self._format_open_message(symbol, direction, filled_plan, primary, sizing, order),
            "raw": {"direction": direction, "sizing": sizing, "order": order},
        }
        if dispatch_execution_event:
            dispatch_execution_event(event)
        elif self.notifier:
            self.notifier(self._format_open_message(symbol, direction, filled_plan, primary, sizing, order))

        return {"ok": True, "order": order, "position": pos.to_dict(), "sizing": sizing}

    def _format_open_message(self, symbol, direction, plan, primary, sizing, order):
        cn_dir = "做多" if direction == "Long" else "做空"
        return (
            f"【实盘开单】\n"
            f"币种：{symbol}\n"
            f"方向：{cn_dir}\n"
            f"评分：{primary.get('score', '')}\n"
            f"优先级：{primary.get('priority', '')}\n"
            f"开仓价：{plan.get('entry')}\n"
            f"止损价：{plan.get('sl')}\n"
            f"止盈1：{plan.get('tp1')}\n"
            f"止盈2：{plan.get('tp2')}\n"
            f"止盈3：{plan.get('tp3')}\n"
            f"仓位数量：{order.get('filled', sizing.get('size'))}\n"
            f"名义价值：{sizing.get('notional')} USDT\n"
            f"本单风险：{sizing.get('risk_amount')} USDT\n"
            f"订单状态：{order.get('status')}\n"
            f"执行模式：{'模拟' if order.get('dry_run') else '实盘'}"
        )