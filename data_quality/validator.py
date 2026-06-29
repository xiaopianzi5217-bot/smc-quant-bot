# -*- coding: utf-8 -*-
from __future__ import annotations

import pandas as pd

REQUIRED_OHLCV = ["datetime", "open", "high", "low", "close", "volume"]


class DataQualityValidator:
    def __init__(self, max_gap_mult=3, max_return_abs=0.15):
        self.max_gap_mult = max_gap_mult
        self.max_return_abs = max_return_abs

    def _normalize(self, df):
        x = df.copy()
        x.columns = [str(c).lower().strip() for c in x.columns]
        for col in ["ts", "date", "timestamp", "time", "open_time"]:
            if col in x.columns and "datetime" not in x.columns:
                x = x.rename(columns={col: "datetime"})
        return x

    def validate_ohlcv(self, df, timeframe_minutes=None, return_report=False):
        issues = []
        report = {"rows": 0, "issues": issues, "gap_count": 0, "max_gap_minutes": 0.0, "timeframe_minutes": timeframe_minutes}
        if df is None or df.empty:
            issues.append("数据为空")
            return (False, report) if return_report else (False, issues)
        x = self._normalize(df)
        report["rows"] = int(len(x))
        missing = [c for c in REQUIRED_OHLCV if c not in x.columns]
        if missing:
            issues.append(f"缺少字段: {missing}")
            return (False, report) if return_report else (False, issues)
        x["datetime"] = pd.to_datetime(x["datetime"], errors="coerce")
        if x["datetime"].isna().any():
            issues.append("datetime 存在无法解析的时间")
        if x.duplicated("datetime").any():
            issues.append("存在重复K线时间")
        x = x.sort_values("datetime")
        for c in ["open", "high", "low", "close", "volume"]:
            x[c] = pd.to_numeric(x[c], errors="coerce")
            if x[c].isna().any():
                issues.append(f"{c} 存在非数字或空值")
        bad_price = (x[["open", "high", "low", "close"]] <= 0).any(axis=1)
        if bad_price.any():
            issues.append("存在小于等于0的价格")
        bad_ohlc = (x["high"] < x[["open", "close", "low"]].max(axis=1)) | (x["low"] > x[["open", "close", "high"]].min(axis=1))
        if bad_ohlc.any():
            issues.append("存在 high/low 结构错误")
        ret = x["close"].pct_change().abs()
        if (ret > self.max_return_abs).any():
            issues.append(f"存在异常涨跌幅 > {self.max_return_abs:.0%}")
        if timeframe_minutes:
            gap = x["datetime"].diff().dt.total_seconds().div(60)
            max_gap = float(gap.max()) if len(gap.dropna()) else 0.0
            threshold = timeframe_minutes * self.max_gap_mult
            bad_gaps = gap[gap > threshold]
            report["max_gap_minutes"] = max_gap
            report["gap_count"] = int(len(bad_gaps))
            if len(bad_gaps):
                issues.append(f"存在K线缺口: {len(bad_gaps)}处, 最大缺口 {round(max_gap, 2)} 分钟")
        report["issues"] = issues
        return (len(issues) == 0, report) if return_report else (len(issues) == 0, issues)

    def align_exec_macro(self, exec_df, macro_df):
        ok1, report1 = self.validate_ohlcv(exec_df, return_report=True)
        ok2, report2 = self.validate_ohlcv(macro_df, return_report=True)
        issues = [f"执行周期: {i}" for i in report1.get("issues", [])] + [f"宏观周期: {i}" for i in report2.get("issues", [])]
        if not ok1 or not ok2:
            return False, issues
        if pd.to_datetime(exec_df["datetime"]).min() < pd.to_datetime(macro_df["datetime"]).min():
            issues.append("执行周期开始时间早于宏观周期，前段可能无法生成宏观上下文")
        return len(issues) == 0, issues