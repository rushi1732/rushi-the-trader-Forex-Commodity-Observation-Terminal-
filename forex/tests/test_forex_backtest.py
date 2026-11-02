"""Unit tests for Forex & Gold Historical Backtest Engine."""

import unittest
from datetime import datetime, timezone
from forex.backtest import calculate_ema, run_backtest
from forex.tests.test_forex_data import make_sample_yahoo_chart


class TestForexBacktest(unittest.TestCase):
    def test_calculate_ema(self):
        prices = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0]
        ema3 = calculate_ema(prices, 3)
        self.assertEqual(len(ema3), len(prices))
        self.assertIsNone(ema3[0])
        self.assertIsNone(ema3[1])
        self.assertAlmostEqual(ema3[2], 11.0)  # (10 + 11 + 12) / 3 = 11.0
        self.assertGreater(ema3[3], 11.0)

    def test_backtest_insufficient_data(self):
        short_chart = make_sample_yahoo_chart("EURUSD=X", 1.0850, 10)
        res = run_backtest("EUR/USD", offline_data={"EURUSD=X": short_chart})
        self.assertEqual(res["status"], "INSUFFICIENT_DATA")
        self.assertIn("Insufficient data", res["error"])
        self.assertEqual(len(res["trades"]), 0)

    def test_backtest_with_synthetic_candles(self):
        # Build 60 synthetic candles with an Asian consolidation and a subsequent breakout
        timestamps = []
        highs = []
        lows = []
        closes = []
        opens = []

        base_time = 1792483200
        # 25 consolidation candles between 2738 and 2742
        for i in range(25):
            timestamps.append(base_time + i * 300)
            opens.append(2740.0)
            highs.append(2742.0)
            lows.append(2738.0)
            closes.append(2740.5)

        # Breakout candle: closes at 2744.0 (above 2742.0)
        timestamps.append(base_time + 25 * 300)
        opens.append(2741.0)
        highs.append(2744.5)
        lows.append(2740.8)
        closes.append(2744.0)

        # 10 continuation candles hitting target at 2752.0
        for i in range(26, 36):
            timestamps.append(base_time + i * 300)
            opens.append(2744.0 + (i - 25) * 1.0)
            highs.append(2745.0 + (i - 25) * 1.0)
            lows.append(2743.0 + (i - 25) * 1.0)
            closes.append(2744.5 + (i - 25) * 1.0)

        mock_chart = {
            "chart": {
                "result": [{
                    "meta": {"currency": "USD", "symbol": "GC=F"},
                    "timestamp": timestamps,
                    "indicators": {"quote": [{
                        "open": opens, "high": highs, "low": lows, "close": closes, "volume": [100] * len(opens)
                    }]}
                }],
                "error": None,
            }
        }

        res = run_backtest("XAU/USD", offline_data={"GC=F": mock_chart})
        self.assertEqual(res["status"], "COMPLETED")
        self.assertTrue(res["paper_only"])
        self.assertIn("HISTORICAL BACKTEST NOTICE", res["disclaimer"])
        metrics = res["metrics"]
        self.assertGreaterEqual(metrics["total_trades"], 1)
        self.assertGreaterEqual(metrics["wins"], 1)
        self.assertEqual(metrics["losses"], 0)
        self.assertEqual(metrics["win_rate_pct"], 100.0)
        self.assertGreater(metrics["net_pnl_usd"], 0.0)


if __name__ == "__main__":
    unittest.main()
