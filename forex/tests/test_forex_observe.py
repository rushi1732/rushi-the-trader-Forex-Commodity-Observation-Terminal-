"""Offline unit tests for Forex Live-Market Observation Mode."""

import unittest
from datetime import datetime, timezone

from forex.observe import (
    check_data_freshness,
    observe_pair,
    format_observation_report,
    BANNER,
)
from forex.tests.test_forex_data import make_sample_yahoo_chart


class TestForexObservation(unittest.TestCase):
    def test_freshness_detection(self):
        now = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)

        # 1. 5 minutes ago -> Fresh
        fresh_ts = int(datetime(2026, 10, 20, 9, 55, tzinfo=timezone.utc).timestamp())
        is_fresh, age, reason = check_data_freshness(fresh_ts, "5m", now_utc=now)
        self.assertTrue(is_fresh)
        self.assertAlmostEqual(age, 5.0, places=1)

        # 2. 45 minutes ago -> Stale (> 20m threshold)
        stale_ts = int(datetime(2026, 10, 20, 9, 15, tzinfo=timezone.utc).timestamp())
        is_fresh2, age2, reason2 = check_data_freshness(stale_ts, "5m", now_utc=now)
        self.assertFalse(is_fresh2)
        self.assertIn("Data is 45.0 minutes old", reason2)

    def test_paper_only_enforcement(self):
        # Attempting to run with paper=False must raise PermissionError
        with self.assertRaises(PermissionError) as cm:
            observe_pair("EUR/USD", config={"paper": False})
        self.assertIn("Live money trading is prohibited", str(cm.exception))

    def test_market_closed_returns_no_signal(self):
        # Simulated run on Saturday
        mock_data = {
            "EURUSD=X": make_sample_yahoo_chart("EURUSD=X", 1.0850, 25),
        }
        sat_noon = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
        obs = observe_pair("EUR/USD", offline_data=mock_data, now_dt=sat_noon)

        self.assertEqual(obs["status"], "NO SIGNAL")
        self.assertIn("Spot Forex market is closed", obs["reason"])
        self.assertTrue(obs["paper_mode"])

        rep = format_observation_report(obs)
        self.assertIn("[OBSERVATION ONLY: PAPER / NOT FOR LIVE EXECUTION]", rep)
        self.assertIn("STATUS: NO SIGNAL", rep)

    def test_stale_data_returns_no_signal(self):
        # Chart candles from 08:00 UTC, current clock is 10:00 UTC (2 hours stale)
        mock_data = {
            "EURUSD=X": make_sample_yahoo_chart("EURUSD=X", 1.0850, 25),
        }
        # The mock chart timestamps start at 1700000000. Set clock 5 hours later.
        now_stale = datetime.fromtimestamp(1700000000 + 300 * 25 + 7200, tz=timezone.utc)
        # Choose a Tuesday to ensure open market
        now_tuesday = datetime(2026, 10, 20, 14, 0, tzinfo=timezone.utc)
        obs = observe_pair("EUR/USD", offline_data=mock_data, now_dt=now_tuesday)

        self.assertEqual(obs["status"], "NO SIGNAL")
        self.assertIn("data is stale", obs["reason"].lower())

    def test_fresh_active_observation_with_paper_simulation(self):
        # Build 10 Asian candles (00:00 - 05:00 UTC) with high 1.0850, low 1.0820
        # + 1 London candle at 08:00 UTC closing at 1.0865 (breakout)
        timestamps = []
        highs = []
        lows = []
        closes = []
        opens = []

        for h in range(7):
            for m in (0, 15, 30, 45):
                dt_bar = datetime(2026, 10, 20, h, m, tzinfo=timezone.utc)
                ts = int(dt_bar.timestamp())
                timestamps.append(ts)
                opens.append(1.0830)
                highs.append(1.0850)
                lows.append(1.0820)
                closes.append(1.0835)

        # Breakout candle at 08:00 UTC
        dt_breakout = datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc)
        timestamps.append(int(dt_breakout.timestamp()))
        opens.append(1.0845)
        highs.append(1.0870)
        lows.append(1.0840)
        closes.append(1.0865)

        mock_chart = {
            "chart": {
                "result": [
                    {
                        "meta": {"currency": "USD", "symbol": "EURUSD=X", "regularMarketPrice": 1.0865},
                        "timestamp": timestamps,
                        "indicators": {
                            "quote": [{
                                "open": opens, "high": highs, "low": lows, "close": closes, "volume": [1000] * len(opens)
                            }]
                        },
                    }
                ],
                "error": None,
            }
        }

        # Observation clock set immediately after breakout (07:10 UTC on Tuesday)
        now_obs = datetime(2026, 10, 20, 7, 10, tzinfo=timezone.utc)
        obs = observe_pair("EUR/USD", offline_data={"EURUSD=X": mock_chart}, now_dt=now_obs)

        self.assertEqual(obs["status"], "SIGNAL CONFIRMED")
        self.assertEqual(obs["direction"], "BULLISH")
        self.assertIsNotNone(obs["paper_simulation"])

        sim = obs["paper_simulation"]
        self.assertEqual(sim["side"], "BUY")
        self.assertGreater(sim["simulated_fill"], 1.0865)  # includes spread & slippage
        self.assertGreater(sim["risk_pips"], 10.0)
        self.assertEqual(sim["reward_risk"], 2.0)
        self.assertGreater(sim["paper_lot_size"], 0.0)
        self.assertEqual(sim["risk_status"], "PASS")

        # Verify formatted report contains required labels
        rep = format_observation_report(obs)
        self.assertIn("[OBSERVATION ONLY: PAPER / NOT FOR LIVE EXECUTION]", rep)
        self.assertIn("SIMULATED PAPER TRADE", rep)
        self.assertIn("INDICATIVE ONLY", rep)

        # Verify auto_signal structure and attributes
        self.assertIn("auto_signal", obs)
        auto_sig = obs["auto_signal"]
        self.assertEqual(auto_sig["action"], "BUY")
        self.assertIn("trader-scanner", auto_sig["strategy"])
        self.assertIn("trader-entry", auto_sig["strategy"])
        self.assertIn("trader-exit", auto_sig["strategy"])
        self.assertEqual(auto_sig["timeframe"], "5m")
        self.assertIsNotNone(auto_sig["entry_reference"])
        self.assertIsNotNone(auto_sig["stop_loss"])
        self.assertIsNotNone(auto_sig["take_profit"])
        self.assertEqual(auto_sig["reward_risk"], 2.0)
        self.assertTrue(auto_sig["candle_close_confirmed"])
        self.assertTrue(auto_sig["paper_mode"])
        self.assertIn("timestamp_et", auto_sig)

    def test_auto_signal_market_closed(self):
        """Verify market-closed state produces NO SIGNAL with detailed explanation."""
        mock_data = {
            "EURUSD=X": make_sample_yahoo_chart("EURUSD=X", 1.0850, 25),
        }
        sat_noon = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
        obs = observe_pair("EUR/USD", offline_data=mock_data, now_dt=sat_noon)

        self.assertIn("auto_signal", obs)
        sig = obs["auto_signal"]
        self.assertEqual(sig["action"], "NO SIGNAL")
        self.assertFalse(sig["is_market_open"])
        self.assertIn("closed", sig["reason"].lower())
        self.assertIsNone(sig["stop_loss"])
        self.assertIsNone(sig["take_profit"])

    def test_auto_signal_stale_data(self):
        """Verify stale feed produces NO SIGNAL and cites feed age."""
        mock_data = {
            "EURUSD=X": make_sample_yahoo_chart("EURUSD=X", 1.0850, 25),
        }
        # Clock 2 hours ahead of chart timestamps on an open market day
        now_tuesday = datetime(2026, 10, 20, 14, 0, tzinfo=timezone.utc)
        obs = observe_pair("EUR/USD", offline_data=mock_data, now_dt=now_tuesday)

        self.assertIn("auto_signal", obs)
        sig = obs["auto_signal"]
        self.assertEqual(sig["action"], "NO SIGNAL")
        self.assertFalse(sig["is_fresh"])
        self.assertIn("stale", sig["reason"].lower())

    def test_auto_signal_insufficient_candles(self):
        """Verify < 15 candles returns NO SIGNAL with insufficient candles reason."""
        short_chart = make_sample_yahoo_chart("EURUSD=X", 1.0850, 8)
        now_tuesday = datetime.fromtimestamp(1700000000 + 8 * 300, tz=timezone.utc)
        obs = observe_pair("EUR/USD", offline_data={"EURUSD=X": short_chart}, now_dt=now_tuesday)

        self.assertIn("auto_signal", obs)
        sig = obs["auto_signal"]
        self.assertEqual(sig["action"], "NO SIGNAL")
        self.assertIn("Insufficient timeframe candles", sig["reason"])


if __name__ == "__main__":
    unittest.main()
