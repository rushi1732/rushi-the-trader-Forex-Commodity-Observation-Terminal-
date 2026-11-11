"""Unit tests for RSI Hidden & Regular Divergence Engine."""

import unittest
from forex.divergence import calculate_rsi, detect_divergence, find_divergence_swings


class TestDivergenceEngine(unittest.TestCase):
    def test_calculate_rsi_length_and_range(self):
        # 30 close prices
        closes = [100.0 + i * 0.5 for i in range(30)]
        rsis = calculate_rsi(closes, period=14)
        self.assertEqual(len(rsis), 30)
        self.assertIsNone(rsis[0])
        self.assertIsNone(rsis[13])
        # In a steady uptrend, RSI should be high
        self.assertIsNotNone(rsis[14])
        self.assertGreater(rsis[14], 70.0)

    def test_detect_hidden_bullish_divergence(self):
        """
        Hidden Bullish Divergence:
        Price makes a Higher Low (e.g. low1=2000.0 at bar 10, low2=2015.0 at bar 22).
        RSI makes a Lower Low (e.g. rsi1=45.0 at bar 10, rsi2=38.0 at bar 22).
        """
        candles = []
        # Build 28 candles
        for i in range(28):
            candles.append({
                "open": 2000.0 + i,
                "high": 2005.0 + i,
                "low": 1995.0 + i,
                "close": 2000.0 + i,
                "volume": 1000,
            })

        # Inject Low 1 at bar 10
        candles[10]["low"] = 1980.0
        candles[10]["close"] = 1985.0
        # Inject Low 2 at bar 22 (Higher Low: 1992.0 > 1980.0)
        candles[22]["low"] = 1992.0
        candles[22]["close"] = 1995.0

        # Construct custom RSIs
        rsis = [50.0] * 28
        rsis[10] = 42.0   # RSI 1
        rsis[22] = 34.0   # RSI 2 (Lower Low: 34.0 < 42.0)

        div = detect_divergence(candles, rsis, lookback=2)
        self.assertTrue(div["detected"])
        self.assertEqual(div["type"], "HIDDEN_BULLISH")
        self.assertEqual(div["bias"], "BUY")
        self.assertTrue(div["is_continuation"])
        self.assertEqual(div["grade_boost"], 25)

    def test_detect_hidden_bearish_divergence(self):
        """
        Hidden Bearish Divergence:
        Price makes a Lower High (e.g. high1=2050.0 at bar 10, high2=2035.0 at bar 22).
        RSI makes a Higher High (e.g. rsi1=58.0 at bar 10, rsi2=66.0 at bar 22).
        """
        candles = []
        for i in range(28):
            candles.append({
                "open": 2050.0 - i,
                "high": 2055.0 - i,
                "low": 2045.0 - i,
                "close": 2050.0 - i,
                "volume": 1000,
            })

        # Inject High 1 at bar 10
        candles[10]["high"] = 2060.0
        candles[10]["close"] = 2055.0
        # Inject High 2 at bar 22 (Lower High: 2045.0 < 2060.0)
        candles[22]["high"] = 2045.0
        candles[22]["close"] = 2040.0

        rsis = [50.0] * 28
        rsis[10] = 58.0   # RSI 1
        rsis[22] = 68.0   # RSI 2 (Higher High: 68.0 > 58.0)

        div = detect_divergence(candles, rsis, lookback=2)
        self.assertTrue(div["detected"])
        self.assertEqual(div["type"], "HIDDEN_BEARISH")
        self.assertEqual(div["bias"], "SELL")
        self.assertTrue(div["is_continuation"])
        self.assertEqual(div["grade_boost"], 25)

    def test_no_divergence_when_flat(self):
        candles = []
        for i in range(30):
            candles.append({
                "open": 2000.0,
                "high": 2005.0,
                "low": 1995.0,
                "close": 2000.0,
                "volume": 1000,
            })
        rsis = [50.0] * 30
        div = detect_divergence(candles, rsis, lookback=2)
        self.assertFalse(div["detected"])
        self.assertEqual(div["type"], "NONE")

    def test_detect_regular_bullish_divergence(self):
        """
        Regular Bullish:
        Price makes Lower Low (e.g. low1=2000 at bar 10, low2=1980 at bar 22).
        RSI makes Higher Low (e.g. rsi1=25 at bar 10, rsi2=35 at bar 22).
        """
        candles = []
        for i in range(28):
            candles.append({
                "open": 2000.0,
                "high": 2005.0,
                "low": 1995.0,
                "close": 2000.0,
                "volume": 1000,
            })
        candles[10]["low"] = 1990.0
        candles[22]["low"] = 1975.0  # Lower Low
        rsis = [50.0] * 28
        rsis[10] = 25.0
        rsis[22] = 35.0  # Higher Low
        div = detect_divergence(candles, rsis, lookback=2)
        self.assertTrue(div["detected"])
        self.assertEqual(div["type"], "REGULAR_BULLISH")
        self.assertEqual(div["bias"], "BUY")
        self.assertFalse(div["is_continuation"])
        self.assertEqual(div["grade_boost"], 15)

    def test_detect_regular_bearish_divergence(self):
        """
        Regular Bearish:
        Price makes Higher High (e.g. high1=2010 at bar 10, high2=2025 at bar 22).
        RSI makes Lower High (e.g. rsi1=75 at bar 10, rsi2=65 at bar 22).
        """
        candles = []
        for i in range(28):
            candles.append({
                "open": 2000.0,
                "high": 2005.0,
                "low": 1995.0,
                "close": 2000.0,
                "volume": 1000,
            })
        candles[10]["high"] = 2010.0
        candles[22]["high"] = 2025.0  # Higher High
        rsis = [50.0] * 28
        rsis[10] = 75.0
        rsis[22] = 65.0  # Lower High
        div = detect_divergence(candles, rsis, lookback=2)
        self.assertTrue(div["detected"])
        self.assertEqual(div["type"], "REGULAR_BEARISH")
        self.assertEqual(div["bias"], "SELL")
        self.assertFalse(div["is_continuation"])
        self.assertEqual(div["grade_boost"], 15)

    def test_confluence_signal_components_has_divergence(self):
        from forex.signals import evaluate_confluence_signal
        candles = []
        for i in range(30):
            candles.append({
                "timestamp": 1700000000 + i * 300,
                "open": 2000.0 + i,
                "high": 2005.0 + i,
                "low": 1995.0 + i,
                "close": 2000.0 + i,
                "volume": 1000,
            })
        res = evaluate_confluence_signal(
            symbol="XAU/USD",
            candles=candles,
        )
        self.assertIn("components", res)
        self.assertIn("divergence", res["components"])
        self.assertIn("detected", res["components"]["divergence"])


if __name__ == "__main__":
    unittest.main()
