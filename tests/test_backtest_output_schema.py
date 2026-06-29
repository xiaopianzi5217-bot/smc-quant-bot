import tempfile
import unittest
from pathlib import Path

import pandas as pd

from backtest.main import _ensure_trade_output_schema


class EnsureTradeOutputSchemaTests(unittest.TestCase):
    def test_empty_trades_get_standard_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "trades.csv"
            trades = pd.DataFrame()

            saved = _ensure_trade_output_schema(trades, out_path)

            self.assertTrue(out_path.exists())
            self.assertTrue(out_path.stat().st_size > 0)
            self.assertIn("pnl_r", saved.columns)
            self.assertIn("expected_value", saved.columns)
            self.assertIn("estimated_rr", saved.columns)
            self.assertIn("regime", saved.columns)
            self.assertIn("setup_type", saved.columns)


if __name__ == "__main__":
    unittest.main()
