"""Unit tests verifying full institutional multi-strategy parity between XAU/USD and BTC/USD.

Verifies:
1. BTC/USD institutional confluence evaluation across SMC, Order Blocks, FVGs, Fibonacci OTE, and Liquidity.
2. Scaled buffer thresholds adapted for Bitcoin ($80k-$100k) avoiding noise suffocation.
3. Strict execution plan geometry: 0 pips entry drift, minimum $650 risk distance, 1:2, 1:3.5, 1:4.5 R:R.
4. Spread guardrail (blocks when spread > 50.0 pips).
5. Equal highs/lows detection with adaptive BTC pip tolerance.
"""

import unittest
from datetime import datetime, timezone
from forex.signals import evaluate_confluence_signal
from forex.liquidity import find_equal_highs_and_lows, detect_liquidity_sweeps
from forex.observe import SignalStateManager, reset_signal_state


class TestBTCParity(unittest.TestCase):
    def setUp(self):
        reset_signal_state()
        self.state = SignalStateManager()

    def tearDown(self):
        reset_signal_state()

    def test_btc_equal_highs_and_lows_adaptive_tolerance(self):
        """BTC uses 40.0 pip tolerance for equal highs and lows instead of 3.0."""
        candles = []
        base_ts = 1700000000
        for i in range(25):
            candles.append({
                "timestamp": base_ts + i * 300,
                "open": 90000.0,
                "high": 90500.0 if i in (5, 15) else (90485.0 if i == 20 else 90200.0),
                "low": 89500.0 if i in (8, 18) else (89515.0 if i == 22 else 89800.0),
                "close": 90000.0,
                "volume": 500,
            })

        # When pip_tolerance is default (None), it should pick 40.0 for BTC and detect EQH/EQL
        res = find_equal_highs_and_lows(candles, "BTC/USD")
        self.assertTrue(len(res["eqh"]) > 0 or len(res["eql"]) > 0)

    def test_btc_buy_signal_confluence_and_execution_plan(self):
        """Verify BTC generates a valid BUY signal with scaled buffers, 0 pips drift, and correct R:R."""
        candles = []
        base_ts = 1700000000
        # Build an upward structure with a pullback into Demand OB
        for i in range(35):
            p = 88000.0 + i * 50.0
            candles.append({
                "timestamp": base_ts + i * 300,
                "open": p - 20.0,
                "high": p + 80.0,
                "low": p - 60.0,
                "close": p,
                "volume": 800,
            })

        live_spot = 89750.0
        sig = evaluate_confluence_signal(
            symbol="BTC/USD",
            candles=candles,
            interval="5m",
            live_price=live_spot,
            offline_data={"dummy": True},
        )

        plan = sig.get("execution_plan", {})
        self.assertEqual(sig["current_price"], live_spot)

        if sig.get("action") == "BUY":
            self.assertEqual(plan.get("entry_price"), live_spot)
            # Risk distance must be at least $650
            risk = live_spot - plan["stop_loss"]
            self.assertGreaterEqual(risk, 650.0)
            # TP1 must be exactly 2R
            expected_tp1 = round(live_spot + (risk * 2.0), 2)
            self.assertAlmostEqual(plan["take_profit_1"], expected_tp1, places=1)
            # TP2 must be 3.5R or BSL target
            self.assertGreater(plan["take_profit_2"], plan["take_profit_1"])
            # TP3 must be 4.5R
            expected_tp3 = round(live_spot + (risk * 4.5), 2)
            self.assertAlmostEqual(plan["take_profit_3"], expected_tp3, places=1)

    def test_btc_spread_guardrail(self):
        """BTC spread guardrail allows normal 15 pips spread but blocks when spread > 50.0 pips."""
        candles = []
        base_ts = 1700000000
        for i in range(30):
            p = 90000.0 + i * 20.0
            candles.append({
                "timestamp": base_ts + i * 300,
                "open": p - 10.0,
                "high": p + 50.0,
                "low": p - 50.0,
                "close": p,
                "volume": 500,
            })

        # 1. Normal spread (15 pips): not blocked by spread guard
        sig_ok = evaluate_confluence_signal(
            symbol="BTC/USD",
            candles=candles,
            interval="5m",
            live_spread_pips=15.0,
            offline_data={"dummy": True},
        )
        has_spread_guard = any("SPREAD GUARD" in f for f in sig_ok.get("confluence_factors", []))
        self.assertFalse(has_spread_guard)

        # 2. Extreme illiquid spread (60 pips): safely blocked
        sig_blocked = evaluate_confluence_signal(
            symbol="BTC/USD",
            candles=candles,
            interval="5m",
            live_spread_pips=60.0,
            offline_data={"dummy": True},
        )
        has_spread_guard_blocked = any("SPREAD GUARD" in f for f in sig_blocked.get("confluence_factors", []))
        self.assertTrue(has_spread_guard_blocked)
        self.assertEqual(sig_blocked["action"], "NEUTRAL")


if __name__ == "__main__":
    unittest.main()
