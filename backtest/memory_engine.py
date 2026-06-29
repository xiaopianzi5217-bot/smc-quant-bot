import pandas as pd

def liquidity_memory_zones(df: pd.DataFrame):
    # historical liquidity accumulation zones
    high_zone = df["high"].rolling(20, min_periods=5).max()
    low_zone = df["low"].rolling(20, min_periods=5).min()
    return high_zone, low_zone


def market_memory_score(df: pd.DataFrame) -> pd.Series:
    # repeated interaction with same zones = higher score
    touch = ((df["close"] - df["close"].rolling(20).mean()).abs())
    return 1 / (1 + touch)
