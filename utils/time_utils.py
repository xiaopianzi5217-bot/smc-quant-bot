# -*- coding: utf-8 -*-
from datetime import datetime, timezone
import pandas as pd
import pytz

BJ_TZ = pytz.timezone("Asia/Shanghai")


def now_bj() -> datetime:
    return datetime.now(BJ_TZ).replace(tzinfo=None)


def now_bj_str(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    return now_bj().strftime(fmt)


def now_bj_iso() -> str:
    return now_bj().isoformat(timespec="seconds")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ts_to_bj(ts_ms: int) -> datetime:
    return datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc).astimezone(BJ_TZ).replace(tzinfo=None)


def ts_to_bj_str(ts_ms: int, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    return ts_to_bj(ts_ms).strftime(fmt)


def series_ms_to_bj(series):
    return pd.to_datetime(series, unit="ms", utc=True).dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)


def to_bj_datetime(value) -> datetime:
    ts = pd.to_datetime(value, utc=True)
    return ts.tz_convert("Asia/Shanghai").tz_localize(None).to_pydatetime()