"""Offline unit tests for Forex exit planner."""

import unittest
from forex.exit import plan_exit, format_plan_report


class TestForexExit(unittest.TestCase):
    def test_long_exit_plan_range_mid(self):
        # Entry at 1.0860, Asian range: 1.0820 - 1.0850. Midpoint = 1.0835
        # Risk = 1.0860 - 1.0835 = 25 pips
        # Target 2R = 25 * 2 = 50 pips -> 1.0910
        plan = plan_exit(
            symbol="EUR/USD",
            side="BUY",
            entry_price=1.0860,
            stop_rule="range-mid",
            target_rule="2R",
            asian_range={"asian_high": 1.0850, "asian_low": 1.0820},
        )
        self.assertEqual(plan["symbol"], "EUR/USD")
        self.assertEqual(plan["side"], "BUY")
        self.assertEqual(plan["stop_price"], 1.0835)
        self.assertEqual(plan["target_price"], 1.0910)
        self.assertAlmostEqual(plan["risk_pips"], 25.0, places=1)
        self.assertAlmostEqual(plan["reward_pips"], 50.0, places=1)
        self.assertEqual(plan["reward_risk_ratio"], 2.0)

        rep = format_plan_report(plan)
        self.assertIn("FOREX EXIT PLAN", rep)
        self.assertIn("Stop Loss:        1.0835  (-25.0 pips)", rep)
        self.assertIn("Take Profit:      1.0910  (+50.0 pips)", rep)

    def test_short_exit_plan_range_mid(self):
        # Entry at 1.0810, Asian range: 1.0820 - 1.0850. Midpoint = 1.0835
        # Risk = 1.0835 - 1.0810 = 25 pips
        # Target 1.5R = 25 * 1.5 = 37.5 pips -> 1.07725
        plan = plan_exit(
            symbol="EUR/USD",
            side="SELL",
            entry_price=1.0810,
            stop_rule="range-mid",
            target_rule="1.5R",
            asian_range={"asian_high": 1.0850, "asian_low": 1.0820},
        )
        self.assertEqual(plan["symbol"], "EUR/USD")
        self.assertEqual(plan["side"], "SELL")
        self.assertEqual(plan["stop_price"], 1.0835)
        self.assertEqual(plan["target_price"], 1.07725)
        self.assertAlmostEqual(plan["risk_pips"], 25.0, places=1)
        self.assertAlmostEqual(plan["reward_pips"], 37.5, places=1)
        self.assertEqual(plan["reward_risk_ratio"], 1.5)

    def test_fixed_pips_stop_and_target(self):
        # USD/JPY Short: Entry 152.00, stop pips:30 -> 152.30, target pips:60 -> 151.40
        plan = plan_exit(
            symbol="USD/JPY",
            side="SELL",
            entry_price=152.00,
            stop_rule="pips:30",
            target_rule="pips:60",
        )
        self.assertEqual(plan["stop_price"], 152.30)
        self.assertEqual(plan["target_price"], 151.40)
        self.assertEqual(plan["risk_pips"], 30.0)
        self.assertEqual(plan["reward_pips"], 60.0)
        self.assertEqual(plan["reward_risk_ratio"], 2.0)

    def test_atr_multiplier_stop(self):
        # EUR/USD: Entry 1.0850, atr:2.0 with ATR 15 pips -> Stop = 1.0850 - 30 pips = 1.0820
        plan = plan_exit(
            symbol="EUR/USD",
            side="BUY",
            entry_price=1.0850,
            stop_rule="atr:2.0",
            target_rule="1R",
            atr_pips=15.0,
        )
        self.assertEqual(plan["stop_price"], 1.0820)
        self.assertEqual(plan["risk_pips"], 30.0)
        self.assertEqual(plan["target_price"], 1.0880)

    def test_refuse_stop_too_tight(self):
        # Spread is 0.8 pips; 1 pip stop is refused as too tight (< 2x spread)
        with self.assertRaises(ValueError) as cm:
            plan_exit(
                symbol="EUR/USD",
                side="BUY",
                entry_price=1.08500,
                stop_rule="pips:1.0",
                target_rule="2R",
            )
        self.assertIn("too tight", str(cm.exception))

    def test_refuse_wrong_orientation(self):
        # Long stop above entry
        with self.assertRaises(ValueError):
            plan_exit(
                symbol="EUR/USD",
                side="BUY",
                entry_price=1.0850,
                stop_rule="price:1.0860",
                target_rule="2R",
            )


if __name__ == "__main__":
    unittest.main()
