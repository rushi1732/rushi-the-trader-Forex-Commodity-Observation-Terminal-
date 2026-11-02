"""Offline unit tests for Forex session scanner."""

import unittest
from datetime import datetime, timezone

from forex.scanner import (
    calculate_sma,
    calculate_rsi,
    calculate_atr_pips,
    detect_asian_range_breakout,
    analyze_pair,
    scan_market,
    format_scan_output,
)
from forex.tests.test_forex_data import make_sample_yahoo_chart


class TestForexScannerIndicators(unittest.TestCase):
    def test_sma(self):
        prices = [float(i) for i in range(1, 26)]  # 1 to 25
        sma = calculate_sma(prices, period=5)
        self.assertIsNone(sma[0])
        self.assertIsNone(sma[3])
        # average of 1,2,3,4,5 = 3.0
        self.assertEqual(sma[4], 3.0)
        # average of 21,22,23,24,25 = 23.0
        self.assertEqual(sma[24], 23.0)

    def test_rsi(self):
        # Monotonically increasing prices -> RSI should be high
        up_prices = [10.0 + i * 0.5 for i in range(25)]
        rsi_up = calculate_rsi(up_prices, period=14)
        self.assertIsNotNone(rsi_up[-1])
        self.assertGreater(rsi_up[-1], 80.0)

        # Monotonically decreasing prices -> RSI should be low
        down_prices = [30.0 - i * 0.5 for i in range(25)]
        rsi_down = calculate_rsi(down_prices, period=14)
        self.assertIsNotNone(rsi_down[-1])
        self.assertLess(rsi_down[-1], 20.0)

    def test_atr_pips(self):
        # Synthetic candles with 10 pip range each
        candles = []
        for i in range(20):
            candles.append({
                "high": 1.0860,
                "low": 1.0850,
                "close": 1.0855,
            })
        atr = calculate_atr_pips(candles, "EUR/USD", period=14)
        self.assertIsNotNone(atr)
        self.assertAlmostEqual(atr, 10.0, places=1)


class TestAsianRangeBreakout(unittest.TestCase):
    def test_asian_breakout_long(self):
        # Build 10 Asian session candles (00:00 to 05:00 UTC) with high 1.0850
        candles = []
        for h in range(0, 6):
            ts = int(datetime(2026, 10, 20, h, 0, tzinfo=timezone.utc).timestamp())
            candles.append({
                "timestamp": ts,
                "high": 1.0850,
                "low": 1.0820,
                "close": 1.0835,
            })

        # Add London session candle at 08:00 UTC closing at 1.0865 (above 1.0850)
        ts_london = int(datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc).timestamp())
        candles.append({
            "timestamp": ts_london,
            "high": 1.0870,
            "low": 1.0840,
            "close": 1.0865,
        })

        res = detect_asian_range_breakout(candles, "EUR/USD")
        self.assertEqual(res["setup"], "ASIAN_BREAKOUT_BULLISH")
        self.assertEqual(res["direction"], "LONG")
        self.assertEqual(res["reference_level"], 1.0850)
        self.assertAlmostEqual(res["pips_beyond"], 15.0, places=1)

    def test_asian_breakdown_short(self):
        # Asian session high 1.0850, low 1.0820
        candles = []
        for h in range(0, 6):
            ts = int(datetime(2026, 10, 20, h, 0, tzinfo=timezone.utc).timestamp())
            candles.append({
                "timestamp": ts,
                "high": 1.0850,
                "low": 1.0820,
                "close": 1.0835,
            })

        # London session candle at 08:00 UTC closing at 1.0810 (below 1.0820)
        ts_london = int(datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc).timestamp())
        candles.append({
            "timestamp": ts_london,
            "high": 1.0825,
            "low": 1.0805,
            "close": 1.0810,
        })

        res = detect_asian_range_breakout(candles, "EUR/USD")
        self.assertEqual(res["setup"], "ASIAN_BREAKOUT_BEARISH")
        self.assertEqual(res["direction"], "SHORT")
        self.assertEqual(res["reference_level"], 1.0820)
        self.assertAlmostEqual(res["pips_beyond"], 10.0, places=1)


class TestForexScannerOfflineIntegration(unittest.TestCase):
    def test_scan_market_offline(self):
        mock_bundle = {
            "EURUSD=X": make_sample_yahoo_chart("EURUSD=X", 1.0850, 30),
            "GBPUSD=X": make_sample_yahoo_chart("GBPUSD=X", 1.2850, 30),
            "USDJPY=X": make_sample_yahoo_chart("USDJPY=X", 152.00, 30),
        }

        results = scan_market(
            pairs=["EUR/USD", "GBP/USD", "USD/JPY"],
            offline_data=mock_bundle,
        )

        self.assertEqual(len(results), 3)
        for r in results:
            self.assertEqual(r["status"], "OK")
            self.assertIn(r["direction"], ("BULLISH", "BEARISH", "NEUTRAL"))
            self.assertIn("atr_pips", r)
            self.assertIn("disclaimer", r)

        dashboard = format_scan_output(results)
        self.assertIn("FOREX MARKET SCANNER", dashboard)
        self.assertIn("INDICATIVE ONLY", dashboard)
        self.assertIn("EUR/USD", dashboard)
        self.assertIn("GBP/USD", dashboard)
        self.assertIn("USD/JPY", dashboard)


if __name__ == "__main__":
    unittest.main()
