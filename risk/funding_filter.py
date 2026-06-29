# -*- coding: utf-8 -*-
from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class FundingFilterConfig:
    enabled: bool = True

    # 百分比单位。0.01 = 0.01%
    extreme_pct: float = 0.01

    # 资金费率过高时，是否直接拦截顺拥挤方向
    block_adverse: bool = True

    # 轻微惩罚分，用于只降低分数不直接拦截的场景
    score_penalty: float = 0.8


def parse_funding_rate_pct(v: Any) -> float:
    """
    返回百分比单位：
    - 0.01 表示 0.01%
    - 如果交易所返回 0.0001，代表 0.01%，这里会自动乘 100
    """
    if v in [None, "", "N/A"]:
        return 0.0

    x = float(v)

    # 大多数交易所 API 原始值是 0.0001 = 0.01%
    # 但系统展示里经常已经乘过 100。
    if abs(x) < 0.001:
        x *= 100.0

    return x


def evaluate_funding_filter(
    direction: str,
    funding_rate: Any,
    config: FundingFilterConfig | None = None,
) -> Dict[str, Any]:
    config = config or FundingFilterConfig()

    if not config.enabled:
        return {
            "allowed": True,
            "status": "DISABLED",
            "penalty": 1.0,
            "reason": "资金费率过滤关闭",
            "funding_rate_pct": funding_rate,
        }

    try:
        fr = parse_funding_rate_pct(funding_rate)
    except Exception as exc:
        return {
            "allowed": True,
            "status": "UNKNOWN",
            "penalty": 1.0,
            "reason": f"资金费率解析失败，默认放行：{exc}",
            "funding_rate_pct": funding_rate,
        }

    direction = str(direction)

    if abs(fr) < config.extreme_pct:
        return {
            "allowed": True,
            "status": "PASS",
            "penalty": 1.0,
            "reason": f"资金费率 {fr:.4f}%，未达到极端阈值 {config.extreme_pct:.4f}%",
            "funding_rate_pct": fr,
        }

    # 正资金费率：多头付费，说明多头较拥挤
    if direction == "Long" and fr > 0:
        return {
            "allowed": not config.block_adverse,
            "status": "BLOCK_LONG" if config.block_adverse else "WARN_LONG",
            "penalty": config.score_penalty,
            "reason": f"资金费率 {fr:.4f}% 偏高，多头付费，多头拥挤，不建议追多",
            "funding_rate_pct": fr,
        }

    # 负资金费率：空头付费，说明空头较拥挤
    if direction == "Short" and fr < 0:
        return {
            "allowed": not config.block_adverse,
            "status": "BLOCK_SHORT" if config.block_adverse else "WARN_SHORT",
            "penalty": config.score_penalty,
            "reason": f"资金费率 {fr:.4f}% 偏低，空头付费，空头拥挤，不建议追空",
            "funding_rate_pct": fr,
        }

    return {
        "allowed": True,
        "status": "PASS_COUNTER",
        "penalty": 1.0,
        "reason": f"资金费率 {fr:.4f}%，但不是当前方向的拥挤成本，放行",
        "funding_rate_pct": fr,
    }