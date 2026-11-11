"""Unit tests for Multi-Timeframe (MTF) Confluence Engine."""

import unittest
from forex.multitimeframe import analyze_single_timeframe, analyze_multi_timeframe


class TestMultiTimeframeEngine(unittest.TestCase):
    def test_single_timeframe_analysis(self):
        # 30 candles rising
        candles = []
        for i in range(30):
            candles.append({
                "open": 2000.0 + i,
                "high": 2005.0 + i,
                "low": 1995.0 + i,
                "close": 2000.0 + i,
                "volume": 1000,
            })
        res = analyze_single_timeframe(candles, interval="5m")
        self.assertEqual(res["interval"], "5m")
        self.assertEqual(res["status"], "OK")
        self.assertIn(res["trend"], ("BULLISH", "NEUTRAL", "BEARISH"))
        self.assertIsNotNone(res["rsi"])
        self.assertIn("divergence", res)

    def test_mtf_all_bullish_aligned(self):
        bullish_candles = [
            {"open": 2000.0 + i, "high": 2005.0 + i, "low": 1995.0 + i, "close": 2002.0 + i, "volume": 1000}
            for i in range(30)
        ]
        candles_by_tf = {
            "4h": list(bullish_candles),
            "1h": list(bullish_candles),
            "15m": list(bullish_candles),
            "5m": list(bullish_candles),
        }
        res = analyze_multi_timeframe("XAU/USD", candles_by_tf=candles_by_tf)
        self.assertEqual(res["overall_bias"], "BULLISH")
        self.assertEqual(res["alignment_score"], 100)
        self.assertEqual(res["bullish_count"], 4)
        self.assertIn("FULL QUADRUPLE ALIGNMENT", res["verdict"])
        self.assertEqual(res["bonus_score"], 30)
        self.assertIn("४ तास", res["summary_marathi"])

    def test_mtf_all_bearish_aligned(self):
        bearish_candles = [
            {"open": 2050.0 - i, "high": 2055.0 - i, "low": 2045.0 - i, "close": 2048.0 - i, "volume": 1000}
            for i in range(30)
        ]
        candles_by_tf = {
            "4h": list(bearish_candles),
            "1h": list(bearish_candles),
            "15m": list(bearish_candles),
            "5m": list(bearish_candles),
        }
        res = analyze_multi_timeframe("XAU/USD", candles_by_tf=candles_by_tf)
        self.assertEqual(res["overall_bias"], "BEARISH")
        self.assertEqual(res["alignment_score"], 100)
        self.assertEqual(res["bearish_count"], 4)
        self.assertIn("FULL QUADRUPLE ALIGNMENT", res["verdict"])
        self.assertEqual(res["bonus_score"], 30)

    def test_mtf_htf_aligned_with_pullback(self):
        bullish_htf = [
            {"open": 2000.0 + i * 2, "high": 2005.0 + i * 2, "low": 1995.0 + i * 2, "close": 2002.0 + i * 2, "volume": 1000}
            for i in range(30)
        ]
        bearish_ltf = [
            {"open": 2050.0 - i, "high": 2055.0 - i, "low": 2045.0 - i, "close": 2048.0 - i, "volume": 1000}
            for i in range(30)
        ]
        candles_by_tf = {
            "4h": list(bullish_htf),
            "1h": list(bullish_htf),
            "15m": list(bullish_htf),
            "5m": list(bearish_ltf),
        }
        res = analyze_multi_timeframe("XAU/USD", candles_by_tf=candles_by_tf)
        self.assertEqual(res["overall_bias"], "BULLISH")
        self.assertGreaterEqual(res["alignment_score"], 75)
        self.assertEqual(res["bullish_count"], 3)


if __name__ == "__main__":
    unittest.main()
