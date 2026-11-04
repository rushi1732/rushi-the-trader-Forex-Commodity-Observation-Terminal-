"""Unit tests for Automatic Signal Engine stability, persistence, hysteresis, and invalidation."""

import unittest
from datetime import datetime, timezone

from forex.observe import SignalStateManager, reset_signal_state


class TestSignalStability(unittest.TestCase):

    def setUp(self):
        reset_signal_state()
        self.manager = SignalStateManager()
        self.symbol = "EUR/USD"
        self.now_utc = datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc)
        self.now_et = self.now_utc

        # 20 synthetic 5m candles
        self.candles = []
        base_ts = 1700000000
        for i in range(20):
            self.candles.append({
                "timestamp": base_ts + i * 300,
                "open": 1.0800 + i * 0.0002,
                "high": 1.0810 + i * 0.0002,
                "low": 1.0795 + i * 0.0002,
                "close": 1.0805 + i * 0.0002,
                "volume": 1000,
            })

    def tearDown(self):
        reset_signal_state()

    def test_signal_persistence_across_price_updates(self):
        """Once confirmed, signal and trade details remain visible and unchanged across price updates."""
        # 1. Confirm BUY signal
        conf_sig = {
            "action": "BUY",
            "confluence_score": 75,
            "grade": "Grade A",
            "is_in_entry_zone": True,
            "confluence_factors": ["Bullish Order Block in Discount", "BSL Liquidity Target"],
            "execution_plan": {
                "entry_price": 1.0850,
                "stop_loss": 1.0825,
                "take_profit_1": 1.0900,
                "take_profit_2": 1.09375,
                "risk_pips": 25.0,
                "reward_pips_tp1": 50.0,
                "rr_ratio_tp1": 2.0,
                "rr_ratio_tp2": 3.5,
            },
        }

        sig1 = self.manager.process(
            symbol=self.symbol,
            timeframe="5m",
            is_open=True,
            is_fresh=True,
            now_et=self.now_et,
            now_utc=self.now_utc,
            current_price=1.0850,
            candles=self.candles,
            confluence_signal=conf_sig,
            is_offline_test=False,
        )

        self.assertEqual(sig1["action"], "BUY")
        self.assertEqual(sig1["entry_reference"], 1.0850)
        self.assertEqual(sig1["stop_loss"], 1.0825)
        self.assertEqual(sig1["take_profit"], 1.0900)
        self.assertEqual(sig1["take_profit_2"], 1.09375)

        # 2. Subsequent price updates: price moves into profit (1.0870) and moves outside entry zone
        # Without persistence, the old code would have immediately deleted the signal!
        neutral_conf = {
            "action": "NEUTRAL",
            "confluence_score": 40,
            "grade": "Grade C",
            "is_in_entry_zone": False,  # Price has moved outside entry zone
        }

        sig2 = self.manager.process(
            symbol=self.symbol,
            timeframe="5m",
            is_open=True,
            is_fresh=True,
            now_et=self.now_et,
            now_utc=self.now_utc,
            current_price=1.0870,
            candles=self.candles,
            confluence_signal=neutral_conf,
            is_offline_test=False,
        )

        # Signal MUST PERSIST and details MUST NOT CHANGE!
        self.assertEqual(sig2["action"], "BUY")
        self.assertEqual(sig2["entry_reference"], 1.0850)
        self.assertEqual(sig2["stop_loss"], 1.0825)
        self.assertEqual(sig2["take_profit"], 1.0900)
        self.assertEqual(sig2["take_profit_2"], 1.09375)

    def test_signal_invalidation_on_stop_loss(self):
        """Signal is invalidated when price hits Stop Loss, transitioning to WAITING FOR ENTRY ZONE."""
        # 1. Establish BUY signal with SL at 1.0825
        conf_sig = {
            "action": "BUY",
            "confluence_score": 75,
            "grade": "Grade A",
            "is_in_entry_zone": True,
            "confluence_factors": ["Bullish Order Block"],
            "execution_plan": {
                "entry_price": 1.0850,
                "stop_loss": 1.0825,
                "take_profit_1": 1.0900,
                "take_profit_2": 1.0935,
                "risk_pips": 25.0,
                "reward_pips_tp1": 50.0,
            },
        }

        self.manager.process(
            symbol=self.symbol,
            timeframe="5m",
            is_open=True,
            is_fresh=True,
            now_et=self.now_et,
            now_utc=self.now_utc,
            current_price=1.0850,
            candles=self.candles,
            confluence_signal=conf_sig,
        )

        # 2. Price drops to 1.0820 (Stop Loss hit)
        sig_invalidated = self.manager.process(
            symbol=self.symbol,
            timeframe="5m",
            is_open=True,
            is_fresh=True,
            now_et=self.now_et,
            now_utc=self.now_utc,
            current_price=1.0820,
            candles=self.candles,
        )

        self.assertEqual(sig_invalidated["action"], "NO SIGNAL")
        self.assertEqual(sig_invalidated["status_label"], "WAITING FOR ENTRY ZONE")
        self.assertIsNone(sig_invalidated["entry_reference"])
        self.assertIsNone(sig_invalidated["stop_loss"])
        self.assertIn("Stop Loss hit", sig_invalidated["reason"])

    def test_signal_invalidation_on_take_profit_2(self):
        """Signal completes when price reaches Take Profit 2 target."""
        conf_sig = {
            "action": "SELL",
            "confluence_score": 70,
            "grade": "Grade A",
            "is_in_entry_zone": True,
            "confluence_factors": ["Bearish Supply Zone"],
            "execution_plan": {
                "entry_price": 1.0850,
                "stop_loss": 1.0875,
                "take_profit_1": 1.0800,
                "take_profit_2": 1.07625,
                "risk_pips": 25.0,
                "reward_pips_tp1": 50.0,
            },
        }

        self.manager.process(
            symbol=self.symbol,
            timeframe="5m",
            is_open=True,
            is_fresh=True,
            now_et=self.now_et,
            now_utc=self.now_utc,
            current_price=1.0850,
            candles=self.candles,
            confluence_signal=conf_sig,
        )

        # Price drops to 1.0760 (TP2 achieved for SELL)
        sig_target_hit = self.manager.process(
            symbol=self.symbol,
            timeframe="5m",
            is_open=True,
            is_fresh=True,
            now_et=self.now_et,
            now_utc=self.now_utc,
            current_price=1.0760,
            candles=self.candles,
        )

        self.assertEqual(sig_target_hit["action"], "NO SIGNAL")
        self.assertEqual(sig_target_hit["status_label"], "WAITING FOR ENTRY ZONE")
        self.assertIsNone(sig_target_hit["entry_reference"])
        self.assertIn("Take Profit 2 target completed", sig_target_hit["reason"])

    def test_no_instant_reversal(self):
        """Never switch directly from BUY to SELL. Must invalidate first and wait."""
        # 1. Establish active BUY
        buy_conf = {
            "action": "BUY",
            "confluence_score": 75,
            "grade": "Grade A",
            "is_in_entry_zone": True,
            "confluence_factors": ["Bullish Order Block"],
            "execution_plan": {
                "entry_price": 1.0850,
                "stop_loss": 1.0825,
                "take_profit_1": 1.0900,
                "take_profit_2": 1.0935,
            },
        }

        sig1 = self.manager.process(
            symbol=self.symbol,
            timeframe="5m",
            is_open=True,
            is_fresh=True,
            now_et=self.now_et,
            now_utc=self.now_utc,
            current_price=1.0850,
            candles=self.candles,
            confluence_signal=buy_conf,
        )
        self.assertEqual(sig1["action"], "BUY")

        # 2. Feeding a SELL candidate while BUY is active must NOT flip to SELL!
        sell_conf = {
            "action": "SELL",
            "confluence_score": 80,
            "grade": "Grade A+",
            "is_in_entry_zone": True,
            "confluence_factors": ["Bearish Supply Zone"],
            "execution_plan": {
                "entry_price": 1.0850,
                "stop_loss": 1.0875,
                "take_profit_1": 1.0800,
            },
        }

        sig2 = self.manager.process(
            symbol=self.symbol,
            timeframe="5m",
            is_open=True,
            is_fresh=True,
            now_et=self.now_et,
            now_utc=self.now_utc,
            current_price=1.0850,  # Price still above SL (1.0825)
            candles=self.candles,
            confluence_signal=sell_conf,
        )

        # Must NOT instantly reverse to SELL!
        self.assertEqual(sig2["action"], "BUY")

    def test_status_transitions_waiting_vs_no_signal(self):
        """Status is WAITING FOR ENTRY ZONE before entry, and NO SIGNAL when closed or stale."""
        # Market Closed
        sig_closed = self.manager.process(
            symbol=self.symbol,
            timeframe="5m",
            is_open=False,
            is_fresh=True,
            now_et=self.now_et,
            now_utc=self.now_utc,
            current_price=1.0850,
            candles=self.candles,
        )
        self.assertEqual(sig_closed["status_label"], "NO SIGNAL")
        self.assertEqual(sig_closed["strategy"], "--")

        # Market Open, institutional bias detected but not in entry zone
        potential_conf = {
            "action": "NEUTRAL",
            "bullish_score": 60,
            "bearish_score": 10,
            "is_in_entry_zone": False,
        }
        sig_waiting = self.manager.process(
            symbol=self.symbol,
            timeframe="5m",
            is_open=True,
            is_fresh=True,
            now_et=self.now_et,
            now_utc=self.now_utc,
            current_price=1.0850,
            candles=self.candles,
            confluence_signal=potential_conf,
        )
        self.assertEqual(sig_waiting["status_label"], "WAITING FOR ENTRY ZONE")
        self.assertEqual(sig_waiting["action"], "NO SIGNAL")
        self.assertEqual(sig_waiting["strategy"], "--")
        self.assertIsNone(sig_waiting["entry_reference"])

    def test_active_signal_guarantees_all_metadata_fields_populated(self):
        """Verify that confirmed signals strictly populate all 9 metadata fields without '--' or None."""
        conf_sig = {
            "action": "BUY",
            "confluence_score": 75,
            "grade": "Grade A",
            "is_in_entry_zone": True,
            "confluence_factors": ["Bullish Order Block in Discount", "BSL Liquidity Target"],
            "execution_plan": {
                "entry_price": 1.0850,
                "stop_loss": 1.0825,
                "take_profit_1": 1.0900,
                "take_profit_2": 1.0938,
                "risk_pips": 25.0,
                "reward_pips_tp1": 50.0,
                "rr_ratio_tp1": 2.0,
            }
        }
        sig = self.manager.process(
            symbol=self.symbol,
            timeframe="5m",
            is_open=True,
            is_fresh=True,
            now_et=self.now_et,
            now_utc=self.now_utc,
            current_price=1.0850,
            candles=self.candles,
            confluence_signal=conf_sig,
            is_offline_test=True,
        )
        self.assertIn(sig["action"], ("BUY", "SELL"))
        self.assertIsNotNone(sig.get("strategy"))
        self.assertNotEqual(sig.get("strategy"), "--")
        self.assertIsNotNone(sig.get("timeframe"))
        self.assertNotEqual(sig.get("timeframe"), "--")
        self.assertIsNotNone(sig.get("entry_reference"))
        self.assertIsInstance(sig.get("entry_reference"), (int, float))
        self.assertIsNotNone(sig.get("stop_loss"))
        self.assertIsInstance(sig.get("stop_loss"), (int, float))
        self.assertIsNotNone(sig.get("take_profit"))
        self.assertIsInstance(sig.get("take_profit"), (int, float))
        self.assertIsNotNone(sig.get("take_profit_2"))
        self.assertIsInstance(sig.get("take_profit_2"), (int, float))
        self.assertIsNotNone(sig.get("reward_risk"))
        self.assertIsNotNone(sig.get("confluence_score"))
        self.assertIsNotNone(sig.get("grade"))
        self.assertIsNotNone(sig.get("timestamp_et"))
        self.assertNotEqual(sig.get("timestamp_et"), "--")

        # Directional assertions
        if sig["action"] == "BUY":
            self.assertLess(sig["stop_loss"], sig["entry_reference"])
            self.assertGreater(sig["take_profit"], sig["entry_reference"])
            self.assertGreater(sig["take_profit_2"], sig["take_profit"])
        else:
            self.assertGreater(sig["stop_loss"], sig["entry_reference"])
            self.assertLess(sig["take_profit"], sig["entry_reference"])
            self.assertLess(sig["take_profit_2"], sig["take_profit"])

    def test_observe_pair_locks_to_twelve_data_spot_and_reconciles_entry_reference(self):
        """Verify that when Twelve Data live tick is provided, observe_pair strictly uses Twelve Data spot price."""
        from forex.observe import observe_pair
        import time
        mock_candles = {
            "GC=F": {
                "chart": {
                    "result": [{
                        "meta": {"currency": "USD", "symbol": "GC=F"},
                        "timestamp": [1700000000 + i * 300 for i in range(20)],
                        "indicators": {"quote": [{
                            "open": [3900.0] * 20,
                            "high": [3905.0] * 20,
                            "low": [3898.0] * 20,
                            "close": [3902.0] * 20,
                            "volume": [100] * 20,
                        }]}
                    }],
                    "error": None
                }
            }
        }
        live_spot = {
            "symbol": "XAU/USD",
            "mid": 4055.20,
            "bid": 4055.05,
            "ask": 4055.35,
            "last": 4055.20,
            "received_at": time.time(),
        }
        obs = observe_pair("XAU/USD", offline_data=mock_candles, live_tick=live_spot)
        self.assertEqual(obs["indicative_mid"], 4055.20)
        self.assertEqual(obs["latest_price"], 4055.20)
        self.assertEqual(obs["source"], "Twelve Data Real-Time (Retail Spot Feed)")
        if obs.get("auto_signal") and obs["auto_signal"].get("entry_reference"):
            self.assertEqual(obs["auto_signal"]["entry_reference"], 4055.20)


if __name__ == "__main__":
    unittest.main()

