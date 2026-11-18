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

    def test_anti_whiplash_reversal_blocks_flip_flop(self):
        """After a BUY signal is invalidated or hits SL, reversing to SELL is strictly blocked for 3 bars."""
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
        self.manager.process(
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

        # 2. Hit Stop Loss (1.0820 < 1.0825)
        sig_sl = self.manager.process(
            symbol=self.symbol,
            timeframe="5m",
            is_open=True,
            is_fresh=True,
            now_et=self.now_et,
            now_utc=self.now_utc,
            current_price=1.0820,
            candles=self.candles,
        )
        self.assertEqual(sig_sl["action"], "NO SIGNAL")

        # 3. Next immediate candle (bar 1): Feed SELL candidate. Must be BLOCKED!
        sell_conf = {
            "action": "SELL",
            "confluence_score": 80,
            "grade": "Grade A+",
            "is_in_entry_zone": True,
            "confluence_factors": ["Bearish Supply Zone"],
            "execution_plan": {
                "entry_price": 1.0820,
                "stop_loss": 1.0845,
                "take_profit_1": 1.0770,
            },
        }
        bar1_candles = list(self.candles) + [{
            "timestamp": self.candles[-1]["timestamp"] + 300,
            "open": 1.0820,
            "high": 1.0825,
            "low": 1.0815,
            "close": 1.0820,
            "volume": 1000,
        }]
        sig_flip1 = self.manager.process(
            symbol=self.symbol,
            timeframe="5m",
            is_open=True,
            is_fresh=True,
            now_et=self.now_et,
            now_utc=self.now_utc,
            current_price=1.0820,
            candles=bar1_candles,
            confluence_signal=sell_conf,
        )
        # Flip to SELL must be strictly BLOCKED on bar 1!
        self.assertEqual(sig_flip1["action"], "NO SIGNAL")
        self.assertEqual(sig_flip1["status_label"], "WAITING FOR ENTRY ZONE")

        # 4. Bar 2: Feed SELL candidate. Must still be BLOCKED!
        bar2_candles = list(bar1_candles) + [{
            "timestamp": bar1_candles[-1]["timestamp"] + 300,
            "open": 1.0820,
            "high": 1.0822,
            "low": 1.0812,
            "close": 1.0818,
            "volume": 1000,
        }]
        sig_flip2 = self.manager.process(
            symbol=self.symbol,
            timeframe="5m",
            is_open=True,
            is_fresh=True,
            now_et=self.now_et,
            now_utc=self.now_utc,
            current_price=1.0818,
            candles=bar2_candles,
            confluence_signal=sell_conf,
        )
        self.assertEqual(sig_flip2["action"], "NO SIGNAL")

        # 5. Bar 3 (3 bars passed): Feed SELL candidate. Cooldown elapsed, SELL is now permitted!
        bar3_candles = list(bar2_candles) + [{
            "timestamp": bar2_candles[-1]["timestamp"] + 300,
            "open": 1.0818,
            "high": 1.0820,
            "low": 1.0810,
            "close": 1.0815,
            "volume": 1000,
        }]
        sig_flip3 = self.manager.process(
            symbol=self.symbol,
            timeframe="5m",
            is_open=True,
            is_fresh=True,
            now_et=self.now_et,
            now_utc=self.now_utc,
            current_price=1.0815,
            candles=bar3_candles,
            confluence_signal=sell_conf,
        )
        self.assertEqual(sig_flip3["action"], "SELL")

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

    def test_hard_trend_filter_prohibits_counter_trend_buy_in_selloff(self):
        """Simulate Gold selloff from 4056 to 4045: evaluate_confluence_signal MUST NOT trigger BUY."""
        from forex.signals import evaluate_confluence_signal
        # Create cascading downward candles (4056 -> 4045)
        falling_candles = []
        base_ts = 1700000000
        for i in range(25):
            price = 4056.0 - (i * 0.45)
            falling_candles.append({
                "timestamp": base_ts + i * 300,
                "open": price + 0.20,
                "high": price + 0.30,
                "low": price - 0.20,
                "close": price,
                "volume": 500,
            })
        sig = evaluate_confluence_signal("XAU/USD", falling_candles, interval="5m")
        # In a cascade selloff, BUY is strictly prohibited
        self.assertNotEqual(sig["action"], "BUY")
        self.assertIn(sig["action"], ("SELL", "NEUTRAL"))

    def test_technical_bias_vetoes_opposite_candidate_action(self):
        """Card 2 Technical Bias: BEARISH strictly vetoes any BUY candidate in Card 3."""
        conf_buy = {
            "action": "BUY",
            "confluence_score": 75,
            "grade": "Grade A",
            "is_in_entry_zone": True,
            "execution_plan": {
                "entry_price": 4048.0,
                "stop_loss": 4044.0,
                "take_profit_1": 4056.0,
                "take_profit_2": 4062.0,
            },
        }
        scan_bearish = {"direction": "BEARISH", "status": "OK"}
        sig = self.manager.process(
            symbol="XAU/USD",
            timeframe="5m",
            is_open=True,
            is_fresh=True,
            now_et=self.now_et,
            now_utc=self.now_utc,
            current_price=4048.0,
            candles=self.candles,
            scan_info=scan_bearish,
            confluence_signal=conf_buy,
        )
        self.assertEqual(sig["action"], "NO SIGNAL")
        self.assertEqual(sig["status_label"], "WAITING FOR ENTRY ZONE")

    def test_circuit_breaker_prevents_consecutive_stop_outs(self):
        """After hitting Stop Loss, circuit breaker blocks consecutive entries in same direction for 3 bars."""
        conf_buy = {
            "action": "BUY",
            "confluence_score": 75,
            "grade": "Grade A",
            "is_in_entry_zone": True,
            "execution_plan": {
                "entry_price": 4050.0,
                "stop_loss": 4046.0,
                "take_profit_1": 4058.0,
                "take_profit_2": 4064.0,
            },
        }
        # 1. Establish BUY
        self.manager.process(
            symbol="XAU/USD",
            timeframe="5m",
            is_open=True,
            is_fresh=True,
            now_et=self.now_et,
            now_utc=self.now_utc,
            current_price=4050.0,
            candles=self.candles,
            confluence_signal=conf_buy,
        )
        # 2. Price hits Stop Loss (4045.0 < 4046.0)
        sig_sl = self.manager.process(
            symbol="XAU/USD",
            timeframe="5m",
            is_open=True,
            is_fresh=True,
            now_et=self.now_et,
            now_utc=self.now_utc,
            current_price=4045.0,
            candles=self.candles,
        )
        self.assertEqual(sig_sl["action"], "NO SIGNAL")
        self.assertIn("Stop Loss hit", sig_sl["reason"])

        # 3. Next immediate candle: attempt to BUY again must be BLOCKED by circuit breaker
        sig_retry = self.manager.process(
            symbol="XAU/USD",
            timeframe="5m",
            is_open=True,
            is_fresh=True,
            now_et=self.now_et,
            now_utc=self.now_utc,
            current_price=4046.0,
            candles=self.candles,
            confluence_signal=conf_buy,
        )
        self.assertEqual(sig_retry["action"], "NO SIGNAL")
        self.assertEqual(sig_retry["status_label"], "WAITING FOR ENTRY ZONE")

    def test_active_signal_invalidated_when_market_trend_turns_bearish(self):
        """Active BUY signal is immediately invalidated when live technical bias turns BEARISH."""
        conf_buy = {
            "action": "BUY",
            "confluence_score": 75,
            "grade": "Grade A",
            "is_in_entry_zone": True,
            "execution_plan": {
                "entry_price": 4050.0,
                "stop_loss": 4040.0,
                "take_profit_1": 4060.0,
                "take_profit_2": 4070.0,
            },
        }
        # 1. Establish BUY
        self.manager.process(
            symbol="XAU/USD",
            timeframe="5m",
            is_open=True,
            is_fresh=True,
            now_et=self.now_et,
            now_utc=self.now_utc,
            current_price=4050.0,
            candles=self.candles,
            confluence_signal=conf_buy,
        )
        # 2. Market turns BEARISH (price at 4048, not hit SL yet)
        scan_bearish = {"direction": "BEARISH", "status": "OK"}
        sig_inv = self.manager.process(
            symbol="XAU/USD",
            timeframe="5m",
            is_open=True,
            is_fresh=True,
            now_et=self.now_et,
            now_utc=self.now_utc,
            current_price=4048.0,
            candles=self.candles,
            scan_info=scan_bearish,
        )
        self.assertEqual(sig_inv["action"], "NO SIGNAL")
        self.assertIn("Technical bias turned BEARISH", sig_inv["reason"])

    def test_dynamic_breakeven_trailing_protection(self):
        """When price advances 1R into profit, stop loss trails to Breakeven (+1 pip buffer)."""
        conf_buy = {
            "action": "BUY",
            "confluence_score": 75,
            "grade": "Grade A",
            "is_in_entry_zone": True,
            "execution_plan": {
                "entry_price": 4050.0,
                "stop_loss": 4045.0,  # Risk = 5.0
                "take_profit_1": 4060.0,  # 2R = 10.0
                "take_profit_2": 4067.5,
            },
        }
        # 1. Establish BUY at 4050.0
        sig1 = self.manager.process(
            symbol="XAU/USD",
            timeframe="5m",
            is_open=True,
            is_fresh=True,
            now_et=self.now_et,
            now_utc=self.now_utc,
            current_price=4050.0,
            candles=self.candles,
            confluence_signal=conf_buy,
        )
        self.assertEqual(sig1["action"], "BUY")
        self.assertEqual(sig1["stop_loss"], 4045.0)

        # 2. Price advances to 4055.20 (+5.20 >= 1R of 5.0)
        sig2 = self.manager.process(
            symbol="XAU/USD",
            timeframe="5m",
            is_open=True,
            is_fresh=True,
            now_et=self.now_et,
            now_utc=self.now_utc,
            current_price=4055.20,
            candles=self.candles,
        )
        # SL MUST be trailed to Breakeven >= 4050.0!
        self.assertEqual(sig2["action"], "BUY")
        self.assertGreaterEqual(sig2["stop_loss"], 4050.0)
        self.assertIn("BREAKEVEN", sig2["status_label"])

        # 3. If price subsequently retraces back below Breakeven (e.g. 4049.50)
        sig3 = self.manager.process(
            symbol="XAU/USD",
            timeframe="5m",
            is_open=True,
            is_fresh=True,
            now_et=self.now_et,
            now_utc=self.now_utc,
            current_price=4049.50,
            candles=self.candles,
        )
        # Trade exits at Breakeven, capital protected!
        self.assertEqual(sig3["action"], "NO SIGNAL")
        self.assertIn("Breakeven", sig3["reason"])

    def test_gold_minimum_safe_stop_loss_distance(self):
        """XAU/USD execution plan enforces minimum $4.00 (40 pips) risk distance to avoid noise suffocation."""
        from forex.signals import evaluate_confluence_signal
        # Create candles where tight swings exist
        tight_candles = []
        base_ts = 1700000000
        for i in range(25):
            tight_candles.append({
                "timestamp": base_ts + i * 300,
                "open": 4050.0 + i * 0.05,
                "high": 4050.2 + i * 0.05,
                "low": 4049.8 + i * 0.05,
                "close": 4050.1 + i * 0.05,
                "volume": 500,
            })
        sig = evaluate_confluence_signal("XAU/USD", tight_candles, interval="5m")
        plan = sig.get("execution_plan", {})
        if plan.get("entry_price") and plan.get("stop_loss"):
            risk_dist = abs(plan["entry_price"] - plan["stop_loss"])
            self.assertGreaterEqual(risk_dist, 4.00)

    def test_gold_stop_loss_strictly_between_100_and_150_pips(self):
        """XAU/USD execution plan strictly clamps Stop Loss between 100 and 150 pips (never exceeds 150 pips)."""
        from forex.signals import evaluate_confluence_signal
        from forex.data import fetch_chart

        chart = fetch_chart("XAU/USD", "5m", "2d")
        candles = chart.get("candles", [])
        if candles:
            candles_test = [dict(c) for c in candles]
            last_close = candles_test[-1]["close"]
            candles_test.append({
                "timestamp": candles_test[-1]["timestamp"] + 300,
                "open": last_close,
                "high": last_close + 0.50,
                "low": last_close - 4.0,
                "close": last_close - 3.8,
                "volume": 1500,
            })
            sig = evaluate_confluence_signal("XAU/USD", candles_test, "5m")
            plan = sig.get("execution_plan", {})
            if plan.get("entry_price") and plan.get("stop_loss"):
                risk_pips = plan.get("risk_pips", 0.0)
                self.assertGreaterEqual(risk_pips, 100.0)
                self.assertLessEqual(risk_pips, 150.0)
                self.assertEqual(plan.get("rr_ratio_tp1"), 2.0)
                self.assertGreaterEqual(plan.get("reward_pips_tp1"), 200.0)
                self.assertLessEqual(plan.get("reward_pips_tp1"), 300.0)


if __name__ == "__main__":
    unittest.main()

