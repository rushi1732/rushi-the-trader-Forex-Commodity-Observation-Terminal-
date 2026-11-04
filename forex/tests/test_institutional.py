"""Unit tests for SMC, ICT, Fibonacci, Session & Liquidity Hunt engines.
"""

import unittest
from datetime import datetime, timezone
import time

from forex.fibonacci import calculate_fibonacci_levels, analyze_fibonacci, find_recent_swing_points
from forex.smc import analyze_smc, find_swings, detect_market_structure, detect_order_blocks, detect_fair_value_gaps
from forex.liquidity import find_equal_highs_and_lows, detect_liquidity_sweeps, analyze_liquidity
from forex.ict import get_ict_killzone_status, detect_judas_swing, analyze_ict
from forex.signals import evaluate_confluence_signal


class TestInstitutionalEngines(unittest.TestCase):

    def setUp(self):
        # Create synthetic candlesticks simulating:
        # 1. Asian range accumulation around 1.0800 - 1.0820
        # 2. London open liquidity sweep down to 1.0790 (SSL sweep / Turtle soup)
        # 3. Aggressive displacement up to 1.0850 breaking structure (BOS + Bullish FVG + Bullish OB)
        # 4. Pullback into OTE zone (0.618 - 0.707)
        self.candles = []
        base_time = int(time.time()) - 3600 * 5

        # Base Asian consolidation
        prices = [
            (1.0800, 1.0810, 1.0798, 1.0805),
            (1.0805, 1.0812, 1.0801, 1.0807),
            (1.0807, 1.0815, 1.0802, 1.0810),
            (1.0810, 1.0820, 1.0808, 1.0818),
            (1.0818, 1.0822, 1.0812, 1.0815),
            (1.0815, 1.0819, 1.0805, 1.0808),
            (1.0808, 1.0812, 1.0800, 1.0803),
            # Down candle before displacement (Bullish Order Block)
            (1.0803, 1.0805, 1.0795, 1.0798),
            # Liquidity sweep candle wicking to 1.0788 (sweeping 1.0800 low) and closing at 1.0804
            (1.0798, 1.0806, 1.0788, 1.0804),
            # Strong green displacement candles creating FVG and breaking 1.0822 high (BOS)
            (1.0804, 1.0830, 1.0804, 1.0828),
            (1.0828, 1.0850, 1.0825, 1.0848),
            (1.0848, 1.0855, 1.0840, 1.0850),
            # Pullback towards OTE / FVG
            (1.0850, 1.0852, 1.0835, 1.0838),
            (1.0838, 1.0840, 1.0820, 1.0822),
            (1.0822, 1.0826, 1.0812, 1.0815),
            (1.0815, 1.0825, 1.0814, 1.0820),
            (1.0820, 1.0830, 1.0818, 1.0825),
        ]

        for i, (o, h, l, c) in enumerate(prices):
            self.candles.append({
                "timestamp": base_time + i * 300,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": 1000 + i * 50,
            })

    def test_fibonacci_calculations(self):
        fib = calculate_fibonacci_levels(1.1000, 1.0000, trend="BULLISH")
        self.assertIn("levels", fib)
        levels = fib["levels"]
        self.assertEqual(levels["0.500"], 1.0500)
        self.assertEqual(levels["0.618"], 1.0382)
        self.assertEqual(levels["0.707"], 1.0293)
        self.assertEqual(levels["0.786"], 1.0214)

        analysis = analyze_fibonacci(self.candles)
        self.assertEqual(analysis["status"], "OK")
        self.assertIn("equilibrium", analysis)
        self.assertIn("zone", analysis)

    def test_smc_engine(self):
        analysis = analyze_smc(self.candles)
        self.assertEqual(analysis["status"], "OK")
        self.assertIn("structure", analysis)
        self.assertIn("order_blocks", analysis)
        self.assertIn("fvgs", analysis)

        obs = analysis["order_blocks"]
        self.assertIsInstance(obs.get("all_order_blocks"), list)

        fvgs = analysis["fvgs"]
        self.assertIsInstance(fvgs.get("all_fvgs"), list)

    def test_liquidity_engine(self):
        analysis = analyze_liquidity(self.candles, symbol="EUR/USD")
        self.assertEqual(analysis["status"], "OK")
        self.assertIn("bsl_target", analysis)
        self.assertIn("ssl_target", analysis)
        self.assertIn("sweeps", analysis)

    def test_ict_killzones(self):
        # 03:30 ET -> London Open Killzone + London Silver Bullet
        dt_et = datetime(2026, 11, 3, 3, 30)
        status = get_ict_killzone_status(dt_et)
        self.assertTrue(status["is_killzone_active"])
        self.assertTrue(status["is_silver_bullet_active"])
        names = [k["name"] for k in status["active_killzones"]]
        self.assertIn("London Open (LOKZ)", names)

    def test_confluence_signal_generator(self):
        # Test signal evaluation
        dt_et = datetime(2026, 11, 3, 3, 30)
        sig = evaluate_confluence_signal(
            symbol="EUR/USD",
            candles=self.candles,
            interval="5m",
            now_dt=dt_et,
        )
        self.assertEqual(sig["symbol"], "EUR/USD")
        self.assertIn("confluence_score", sig)
        self.assertIn("action", sig)
        self.assertIn("execution_plan", sig)
        plan = sig["execution_plan"]
        self.assertIn("entry_price", plan)

    def test_signal_strictly_requires_valid_entry_zone(self):
        """Verify that BUY or SELL is NEVER generated outside a valid entry zone."""
        # Create candles where trend/session points are high, but current price is floating outside any entry zone
        dt_et = datetime(2026, 11, 3, 3, 30)
        outside_candles = [dict(c) for c in self.candles]
        # Set the current price to an extreme arbitrary price far away from any OB, FVG, or OTE
        outside_candles[-1] = {
            "timestamp": outside_candles[-1]["timestamp"],
            "open": 1.0950,
            "high": 1.0952,
            "low": 1.0948,
            "close": 1.0950,
        }
        sig = evaluate_confluence_signal(
            symbol="EUR/USD",
            candles=outside_candles,
            interval="5m",
            now_dt=dt_et,
        )
        # Must be strictly NEUTRAL (NO SIGNAL outside entry zone)
        self.assertEqual(sig["action"], "NEUTRAL")
        self.assertFalse(sig["is_in_entry_zone"])


if __name__ == "__main__":
    unittest.main()
