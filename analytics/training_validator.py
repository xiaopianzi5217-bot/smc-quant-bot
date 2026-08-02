import logging

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = [
    "trade_id",
    "symbol",
    "profit_r",
    "features",
    "regime",
    "score",
    "ev",
    "confidence",
]


def validate_trade_sample(trade: dict) -> bool:
    missing = []
    for field in REQUIRED_FIELDS:
        if trade.get(field) is None:
            missing.append(field)

    if missing:
        logger.warning(
            f"[TrainingValidator] reject sample {trade.get('trade_id')} missing {missing}"
        )
        return False

    # 防止异常利润污染
    try:
        profit_r = float(trade.get("profit_r") or 0.0)
    except Exception:
        logger.warning(f"[TrainingValidator] invalid profit_r for {trade.get('trade_id')}")
        return False

    if abs(profit_r) > 20:
        logger.warning(
            f"[TrainingValidator] abnormal profit_r {profit_r} for {trade.get('trade_id')}"
        )
        return False

    return True
