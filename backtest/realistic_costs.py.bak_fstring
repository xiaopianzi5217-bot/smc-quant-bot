# -*- coding: utf-8 -*-
from dataclasses import dataclass
from typing import Any

import math
import pandas as pd


@dataclass
class CostConfig:
    # 单边手续费，单位 bps。6 bps = 0.06%
    fee_bps: float = 6.0

    # 单边滑点，单位 bps。10 bps = 0.10% (实盘高频必备摩擦还原)
    slippage_bps: float = 10.0

    # 资金费率，百分比单位。0.01 = 0.01%
    funding_rate_pct: float = 0.0

    # 永续常见 8 小时结算一次
    funding_interval_hours: float = 8.0

    # 是否把资金费率计入净收益
    include_funding: bool = True


def bps_to_pct(bps: float) -> float:
    return float(bps) / 100.0


def calc_holding_hours(entry_time: Any, exit_time: Any) -> float:
    try:
        a = pd.to_datetime(entry_time)
        b = pd.to_datetime(exit_time)
        return max(0.0, (b - a).total_seconds() / 3600.0)
    except Exception:
        return 0.0


def calc_gross_pnl_pct(direction: str, entry_price: float, exit_price: float) -> float:
    direction = str(direction)

    entry_price = float(entry_price)
    exit_price = float(exit_price)

    if entry_price <= 0:
        return 0.0

    if direction == "Short":
        return (entry_price - exit_price) / entry_price * 100.0

    return (exit_price - entry_price) / entry_price * 100.0


def calc_cost_pct(config: CostConfig) -> float:
    # 开仓 + 平仓，两边手续费 + 两边滑点
    return bps_to_pct(config.fee_bps) * 2.0 + bps_to_pct(config.slippage_bps) * 2.0


def calc_funding_pnl_pct( direction: str, holding_hours: float, config: CostConfig, ) -> float:
    if not config.include_funding:
        return 0.0

    if config.funding_interval_hours <= 0:
        return 0.0

    periods = math.ceil(max(0.0, holding_hours) / config.funding_interval_hours)

    if periods <= 0:
        return 0.0

    fr = float(config.funding_rate_pct)

    # 正资金费率：多头付费，空头收钱
    if str(direction) == "Long":
        return -fr * periods

    if str(direction) == "Short":
        return fr * periods

    return 0.0


def calc_net_pnl_pct( direction: str, entry_price: float, exit_price: float, entry_time: Any, exit_time: Any, config: CostConfig | None = None, ):
    config = config or CostConfig()

    gross = calc_gross_pnl_pct(direction, entry_price, exit_price)
    holding_hours = calc_holding_hours(entry_time, exit_time)
    cost = calc_cost_pct(config)
    funding = calc_funding_pnl_pct(direction, holding_hours, config)

    net = gross + funding - cost

    return {
        "gross_pnl_pct": gross,
        "fee_slippage_cost_pct": cost,
        "funding_pnl_pct": funding,
        "net_pnl_pct": net,
        "holding_hours": holding_hours,
    }


def apply_realistic_costs( trades: pd.DataFrame, config: CostConfig | None = None, ) -> pd.DataFrame:
    config = config or CostConfig()

    if trades is None or len(trades) == 0:
        return pd.DataFrame()

    out = trades.copy()

    rows = []

    for _, row in out.iterrows():
        direction = row.get("direction", row.get("side", "Long"))
        entry = float(row.get("entry", row.get("entry_price", 0)))
        exit_ = float(row.get("exit", row.get("exit_price", entry)))
        entry_time = row.get("entry_time", row.get("open_time", None))
        exit_time = row.get("exit_time", row.get("close_time", entry_time))

        calc = calc_net_pnl_pct(
            direction=direction,
            entry_price=entry,
            exit_price=exit_,
            entry_time=entry_time,
            exit_time=exit_time,
            config=config,
        )
        rows.append(calc)

    cost_df = pd.DataFrame(rows)

    for col in cost_df.columns:
        out[col] = cost_df[col].values

    return out