"""Offline unit tests for Forex entry confirmation timer."""

import unittest
from datetime import datetime, timezone

from forex.entry import evaluate_entry_from_candles, format_entry_report


class TestForexEntry(unittest.TestCase):
    def test_wick_does_not_trigger_entry(self):
        # Asian range high = 1.0850.
        candles = []
        for h in range(0, 6):
            ts = int(datetime(2026, 10, 20, h, 0, tzinfo=timezone.utc).timestamp())
            candles.append({
                "timestamp": ts,
                "high": 1.0850,
                "low": 1.0820,
                "close": 1.0835,
            })

        # London candle with wick at 1.0860 (above 1.0850) but CLOSE at 1.0845 (below 1.0850)
        ts_london = int(datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc).timestamp())
        candles.append({
            "timestamp": ts_london,
            "high": 1.0860,
            "low": 1.0830,
            "close": 1.0845,
        })

        res = evaluate_entry_from_candles("EUR/USD", side="BUY", candles=candles, setup="asian")
        # Must be WAITING, NOT TRIGGERED
        self.assertEqual(res["status"], "WAITING")
        self.assertAlmostEqual(res["distance_pips"], 5.0, places=1)

    def test_candle_close_triggers_long(self):
        candles = []
        for h in range(0, 6):
            ts = int(datetime(2026, 10, 20, h, 0, tzinfo=timezone.utc).timestamp())
            candles.append({
                "timestamp": ts,
                "high": 1.0850,
                "low": 1.0820,
                "close": 1.0835,
            })

        # London candle with CLOSE at 1.0865 (15 pips above Asian high)
        ts_london = int(datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc).timestamp())
        candles.append({
            "timestamp": ts_london,
            "high": 1.0870,
            "low": 1.0840,
            "close": 1.0865,
        })

        res = evaluate_entry_from_candles("EUR/USD", side="BUY", candles=candles, setup="asian", spread_pips=1.0)
        self.assertEqual(res["status"], "TRIGGERED")
        self.assertEqual(res["side"], "BUY")
        self.assertEqual(res["trigger_close"], 1.0865)
        # Ask fill price includes half spread (1.0865 + 0.00005 = 1.08655)
        self.assertEqual(res["indicative_fill_ask"], 1.08655)
        self.assertAlmostEqual(res["pips_beyond"], 15.0, places=1)

        rep = format_entry_report(res)
        self.assertIn("FOREX ENTRY TIMER", rep)
        self.assertIn("TRIGGERED", rep)
        self.assertIn("Level Beat:  1.085 by 15.0 pips", rep)

    def test_candle_close_triggers_short(self):
        candles = []
        for h in range(0, 6):
            ts = int(datetime(2026, 10, 20, h, 0, tzinfo=timezone.utc).timestamp())
            candles.append({
                "timestamp": ts,
                "high": 1.0850,
                "low": 1.0820,
                "close": 1.0835,
            })

        # London candle with CLOSE at 1.0805 (15 pips below Asian low)
        ts_london = int(datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc).timestamp())
        candles.append({
            "timestamp": ts_london,
            "high": 1.0825,
            "low": 1.0800,
            "close": 1.0805,
        })

        res = evaluate_entry_from_candles("EUR/USD", side="SELL", candles=candles, setup="asian", spread_pips=1.0)
        self.assertEqual(res["status"], "TRIGGERED")
        self.assertEqual(res["side"], "SELL")
        self.assertEqual(res["trigger_close"], 1.0805)
        # Bid fill price includes half spread (1.0805 - 0.00005 = 1.08045)
        self.assertEqual(res["indicative_fill_bid"], 1.08045)
        self.assertAlmostEqual(res["pips_beyond"], 15.0, places=1)

    def test_custom_level_with_min_pips(self):
        candles = [
            {"timestamp": 1700000000, "high": 150.20, "low": 149.80, "close": 150.05},
            {"timestamp": 1700000300, "high": 150.35, "low": 149.90, "close": 150.25},
        ]
        # Breakout level 150.20 with min-pips 2.0 (requires close > 150.22)
        # Close is 150.25 -> triggered
        res = evaluate_entry_from_candles(
            "USD/JPY", side="BUY", candles=candles, setup="level", level=150.20, min_breakout_pips=2.0
        )
        self.assertEqual(res["status"], "TRIGGERED")
        self.assertEqual(res["side"], "BUY")


if __name__ == "__main__":
    unittest.main()
