# -*- coding: utf-8 -*-
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from indicators.basic import calculate_advanced_sqzmom
import importlib
import v6_data_engine


class TestSqzmomV6Engine(unittest.TestCase):

    def test_calculate_advanced_sqzmom_returns_expected_keys(self):
        # 构造平稳振荡后放量的一组 OHLCV 数据
        np.random.seed(0)
        N = 40
        base = 100.0
        closes = base + np.concatenate([np.random.normal(0.0, 0.2, 30), np.random.normal(1.5, 0.3, 10)])
        highs = closes + np.random.uniform(0.1, 0.4, N)
        lows = closes - np.random.uniform(0.1, 0.4, N)
        opens = closes + np.random.uniform(-0.15, 0.15, N)
        volumes = np.concatenate([np.random.uniform(100.0, 120.0, 30), np.random.uniform(180.0, 260.0, 10)])

        df = pd.DataFrame({
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        })

        sqz_data = calculate_advanced_sqzmom(df)

        self.assertIsInstance(sqz_data, dict)
        self.assertEqual(set(sqz_data.keys()), {
            "released",
            "duration",
            "strength",
            "vol_ratio",
            "volume_confirmed",
        })
        self.assertIsInstance(sqz_data["released"], bool)
        self.assertIsInstance(sqz_data["duration"], int)
        self.assertIsInstance(sqz_data["strength"], float)
        self.assertIsInstance(sqz_data["vol_ratio"], float)
        self.assertIsInstance(sqz_data["volume_confirmed"], bool)
        self.assertGreaterEqual(sqz_data["duration"], 0)
        self.assertGreaterEqual(sqz_data["vol_ratio"], 0)

    def test_record_open_snapshot_writes_sqz_fields_to_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_db = Path(tmpdir) / "v6_research_test.db"
            original_db_path = v6_data_engine.DB_PATH
            try:
                v6_data_engine.DB_PATH = tmp_db
                v6_data_engine.init_v6_database()

                result = {
                    "signal_id": "test_signal_1",
                    "symbol": "BTC/USDT",
                    "direction": "Long",
                    "regime": "TREND",
                    "vol_state": "HIGH_VOL",
                    "adx": 22.0,
                    "atr": 0.9,
                    "rsi": 55.0,
                    "features": {"squeeze_release": True, "bb_width_expand": False},
                    "p_win_raw": 0.65,
                    "confidence": 0.72,
                    "expected_value": 0.035,
                    "blended_ev": 0.040,
                    "entry": 100.0,
                    "sl": 97.5,
                    "tp1": 102.5,
                    "rr": 1.0,
                    "sqz_data": {
                        "released": True,
                        "duration": 16,
                        "strength": 2.45,
                        "vol_ratio": 1.42,
                        "volume_confirmed": True,
                    },
                }

                v6_data_engine.record_open_snapshot(result, kelly_size=0.05)

                conn = sqlite3.connect(str(tmp_db))
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT signal_id, sqz_released, sqz_duration, sqz_strength, sqz_vol_ratio, sqz_volume_confirmed FROM trade_snapshots WHERE signal_id = ?",
                    ("test_signal_1",)
                )
                row = cursor.fetchone()
                conn.close()

                self.assertIsNotNone(row)
                self.assertEqual(row[0], "test_signal_1")
                self.assertIn(row[1], (1.0, 1))
                self.assertEqual(row[2], 16)
                self.assertAlmostEqual(row[3], 2.45, places=6)
                self.assertAlmostEqual(row[4], 1.42, places=6)
                self.assertIn(row[5], (1.0, 1))
            finally:
                importlib.reload(v6_data_engine)
                v6_data_engine.DB_PATH = original_db_path

    def test_get_historical_smc_success_rate_defaults_when_table_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_db = Path(tmpdir) / "v6_research_test.db"
            original_db_path = v6_data_engine.DB_PATH
            try:
                v6_data_engine.DB_PATH = tmp_db
                v6_data_engine.init_v6_database()

                rate = v6_data_engine.get_historical_smc_success_rate(
                    symbol="BTC/USDT",
                    timeframe="15m",
                    structure_type="bullish_ob",
                    current_regime="TREND",
                )

                self.assertIsInstance(rate, float)
                self.assertEqual(rate, 0.48)
            finally:
                importlib.reload(v6_data_engine)
                v6_data_engine.DB_PATH = original_db_path


if __name__ == "__main__":
    unittest.main()
