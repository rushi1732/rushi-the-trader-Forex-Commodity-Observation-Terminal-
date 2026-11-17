"""Unit tests for HTF Macro Trend Veto, Judas Swing Trap Protection, and Counter-Trend Prevention.
"""

import unittest
from datetime import datetime, timezone

from forex.signals import evaluate_confluence_signal
from forex.scanner import detect_asian_range_breakout
from forex.data import aggregate_5m_to_15m


class TestHTFMacroVeto(unittest.TestCase):
    def test_htf_bearish_veto_blocks_counter_trend_buy(self):
        """When 15m HTF trend is Bearish, local 5m minor CHoCH cannot generate BUY."""
        candles_5m = []
        base_time = 1794800000
        # Build 60 bars of persistent downtrend on 15m (price falling from 3920 to 3870)
        curr = 3920.0
        for i in range(60):
            curr -= 0.8
            candles_5m.append({
                "time": base_time + i * 300,
                "timestamp": base_time + i * 300,
                "open": curr + 0.4,
                "high": curr + 0.9,
                "low": curr - 0.4,
                "close": curr,
                "volume": 100,
            })

        # Add 3 green bounce bars at the end poking above previous swing (local 5m CHoCH attempt)
        for i in range(3):
            curr += 1.5
            candles_5m.append({
                "time": base_time + (60 + i) * 300,
                "timestamp": base_time + (60 + i) * 300,
                "open": curr - 1.2,
                "high": curr + 0.5,
                "low": curr - 1.2,
                "close": curr,
                "volume": 200,
            })

        sig = evaluate_confluence_signal(
            symbol="XAU/USD",
            candles=candles_5m,
            interval="5m",
            offline_data={"dummy": True},
        )
        # Macro trend is heavily bearish - BUY must be strictly vetoed!
        self.assertNotEqual(sig["action"], "BUY")
        self.assertEqual(sig["htf_trend"], "BEARISH")
        self.assertTrue(any("15m HTF Macro Trend is BEARISH" in f for f in sig["confluence_factors"]))

    def test_asian_high_sweep_rejection_detected(self):
        """When price sweeps above Asian High but leaves a 50%+ upper rejection wick, detect Judas Trap."""
        candles = []
        base_time = 1794800000
        # Asian session candles between 3865 and 3875 (hour 0 to 6)
        for i in range(12):
            candles.append({
                "datetime": f"2026-11-16 0{i//2}:{(i%2)*30}:00",
                "time": base_time + i * 1800,
                "timestamp": base_time + i * 1800,
                "open": 3870.0,
                "high": 3875.0,
                "low": 3865.0,
                "close": 3872.0,
            })

        # Post-Asian London open candle sweeps above 3875 to 3881 but closes at 3875.5 with 80% upper wick
        candles.append({
            "datetime": "2026-11-16 08:00:00",
            "time": base_time + 12 * 1800,
            "timestamp": base_time + 12 * 1800,
            "open": 3874.0,
            "high": 3881.0,
            "low": 3873.5,
            "close": 3875.5,
        })

        res = detect_asian_range_breakout(candles, "XAU/USD")
        self.assertEqual(res["setup"], "ASIAN_HIGH_SWEEP_REJECTION")
        self.assertEqual(res["direction"], "SHORT")
        self.assertIn("Judas Swing", res["reason"])


if __name__ == "__main__":
    unittest.main()
