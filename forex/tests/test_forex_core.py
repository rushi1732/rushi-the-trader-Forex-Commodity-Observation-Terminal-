"""Unit tests for the Forex core currency calculation and pricing engine."""

import unittest
from datetime import datetime, timezone, timedelta

from forex.pairs import (
    normalize_symbol,
    get_pair_info,
    to_pips,
    from_pips,
    pip_value_usd,
    STANDARD_LOT_UNITS,
)
from forex.pricing import (
    calc_spread_prices,
    execution_price,
    is_stop_triggered,
    is_target_triggered,
)
from forex.sizer import size_position
from forex.sessions import (
    is_forex_market_open,
    get_active_sessions,
    session_status,
)
from forex.risk import validate_forex_trade
from forex.ticket import format_forex_ticket


class TestForexPairsAndPips(unittest.TestCase):
    def test_symbol_normalization(self):
        self.assertEqual(normalize_symbol("EURUSD"), "EUR/USD")
        self.assertEqual(normalize_symbol("eur/usd"), "EUR/USD")
        self.assertEqual(normalize_symbol("USDJPY=X"), "USD/JPY")
        self.assertEqual(normalize_symbol("gbp_jpy"), "GBP/JPY")

    def test_pair_metadata(self):
        eurusd = get_pair_info("EUR/USD")
        self.assertEqual(eurusd["pip_size"], 0.0001)
        self.assertEqual(eurusd["decimals"], 4)
        self.assertEqual(eurusd["base"], "EUR")
        self.assertEqual(eurusd["quote"], "USD")

        usdjpy = get_pair_info("USD/JPY")
        self.assertEqual(usdjpy["pip_size"], 0.01)
        self.assertEqual(usdjpy["decimals"], 2)
        self.assertEqual(usdjpy["base"], "USD")
        self.assertEqual(usdjpy["quote"], "JPY")

    def test_pip_conversions(self):
        # 4-decimal pair
        self.assertEqual(to_pips("EUR/USD", 0.0025), 25.0)
        self.assertEqual(from_pips("EUR/USD", 25.0), 0.0025)

        # 2-decimal JPY pair
        self.assertEqual(to_pips("USD/JPY", 0.85), 85.0)
        self.assertEqual(from_pips("USD/JPY", 85.0), 0.85)

    def test_pip_values_usd(self):
        # 1. USD is Quote: 1 standard lot (100,000 units) * 0.0001 = $10.00
        val_eurusd_1 = pip_value_usd("EUR/USD", lot_size=1.0)
        self.assertAlmostEqual(val_eurusd_1, 10.0, places=2)

        # Micro lot: 0.01 lot = $0.10
        val_eurusd_micro = pip_value_usd("EUR/USD", lot_size=0.01)
        self.assertAlmostEqual(val_eurusd_micro, 0.10, places=2)

        # 2. USD is Base: USD/JPY at 150.00 -> 100,000 * 0.01 / 150 = 1000 / 150 = $6.67
        val_usdjpy = pip_value_usd("USD/JPY", lot_size=1.0, current_rates={"USD/JPY": 150.0})
        self.assertAlmostEqual(val_usdjpy, 6.6667, places=3)

        # 3. Cross pair: EUR/GBP with GBP/USD at 1.2800 -> 10 GBP * 1.28 = $12.80
        val_eurgbp = pip_value_usd("EUR/GBP", lot_size=1.0, current_rates={"GBP/USD": 1.2800})
        self.assertAlmostEqual(val_eurgbp, 12.80, places=2)


class TestForexPricingAndSpreads(unittest.TestCase):
    def test_spread_calculation(self):
        # EUR/USD mid 1.08500 with 1.0 pip spread (0.00010)
        bid, ask = calc_spread_prices("EUR/USD", mid_price=1.08500, spread_pips=1.0)
        self.assertEqual(bid, 1.08495)
        self.assertEqual(ask, 1.08505)

    def test_execution_prices(self):
        # Long enters at Ask
        fill_long = execution_price("EUR/USD", side="buy", mid_price=1.08500, spread_pips=1.0)
        self.assertEqual(fill_long, 1.08505)

        # Short enters at Bid
        fill_short = execution_price("EUR/USD", side="sell", mid_price=1.08500, spread_pips=1.0)
        self.assertEqual(fill_short, 1.08495)

    def test_trigger_evaluations(self):
        # Long trade with Stop at 1.0800, Target at 1.0900
        # Long stop triggers if Bid <= 1.0800
        self.assertTrue(is_stop_triggered("buy", current_bid=1.0799, current_ask=1.0801, stop_price=1.0800))
        self.assertFalse(is_stop_triggered("buy", current_bid=1.0805, current_ask=1.0807, stop_price=1.0800))

        # Long target triggers if Bid >= 1.0900
        self.assertTrue(is_target_triggered("buy", current_bid=1.0905, current_ask=1.0907, target_price=1.0900))
        self.assertFalse(is_target_triggered("buy", current_bid=1.0895, current_ask=1.0897, target_price=1.0900))

        # Short trade with Stop at 1.0900, Target at 1.0800
        # Short stop triggers if Ask >= 1.0900
        self.assertTrue(is_stop_triggered("sell", current_bid=1.0898, current_ask=1.0902, stop_price=1.0900))
        self.assertFalse(is_stop_triggered("sell", current_bid=1.0890, current_ask=1.0895, stop_price=1.0900))

        # Short target triggers if Ask <= 1.0800
        self.assertTrue(is_target_triggered("sell", current_bid=1.0798, current_ask=1.0800, target_price=1.0800))
        self.assertFalse(is_target_triggered("sell", current_bid=1.0805, current_ask=1.0807, target_price=1.0800))


class TestForexSizer(unittest.TestCase):
    def test_long_sizing_bound_by_loss(self):
        # Budget $5000, Max loss $50
        # EUR/USD: Entry 1.0850, Stop 1.0825 (25 pips risk)
        # 1 pip per lot = $10
        # Sizing: $50 / (25 * $10) = 0.20 lots (20,000 units)
        res = size_position(
            symbol="EUR/USD",
            side="buy",
            entry_price=1.0850,
            stop_price=1.0825,
            budget_usd=5000.0,
            max_loss_usd=50.0,
            leverage=30.0,
        )
        self.assertTrue(res["allowed"])
        self.assertEqual(res["lot_size"], 0.20)
        self.assertEqual(res["units"], 20000)
        self.assertEqual(res["pips_at_risk"], 25.0)
        self.assertAlmostEqual(res["max_loss_usd"], 50.0, places=2)
        # Margin: (20,000 * 1.0850) / 30 = $723.33
        self.assertAlmostEqual(res["margin_required_usd"], 723.33, places=1)

    def test_short_sizing_bound_by_loss(self):
        # EUR/USD Short: Entry 1.0850, Stop 1.0890 (40 pips risk)
        # $50 / (40 * 10) = 0.125 lots -> floor to 0.12 lots (12,000 units)
        res = size_position(
            symbol="EUR/USD",
            side="sell",
            entry_price=1.0850,
            stop_price=1.0890,
            budget_usd=5000.0,
            max_loss_usd=50.0,
        )
        self.assertTrue(res["allowed"])
        self.assertEqual(res["lot_size"], 0.12)
        self.assertEqual(res["pips_at_risk"], 40.0)
        # 0.12 * 40 * $10 = $48.00 <= $50 max loss
        self.assertEqual(res["max_loss_usd"], 48.0)

    def test_invalid_stop_orientation(self):
        with self.assertRaises(ValueError):
            # Long with stop higher than entry
            size_position("EUR/USD", "buy", entry_price=1.0850, stop_price=1.0900)

        with self.assertRaises(ValueError):
            # Short with stop lower than entry
            size_position("EUR/USD", "sell", entry_price=1.0850, stop_price=1.0800)


class TestForexSessions(unittest.TestCase):
    def test_market_open_schedule(self):
        # Tuesday 10:00 ET -> Open
        tue_10am = datetime(2026, 10, 20, 10, 0)
        is_open, _ = is_forex_market_open(tue_10am)
        self.assertTrue(is_open)

        # Saturday 14:00 ET -> Closed
        sat_2pm = datetime(2026, 10, 24, 14, 0)
        is_open, _ = is_forex_market_open(sat_2pm)
        self.assertFalse(is_open)

        # Sunday 12:00 ET -> Closed (before 17:00)
        sun_noon = datetime(2026, 10, 25, 12, 0)
        is_open, _ = is_forex_market_open(sun_noon)
        self.assertFalse(is_open)

        # Sunday 18:00 ET -> Open (Sydney/Asian session open)
        sun_6pm = datetime(2026, 10, 25, 18, 0)
        is_open, _ = is_forex_market_open(sun_6pm)
        self.assertTrue(is_open)

        # Friday 18:00 ET -> Closed (after 17:00 close)
        fri_6pm = datetime(2026, 10, 23, 18, 0)
        is_open, _ = is_forex_market_open(fri_6pm)
        self.assertFalse(is_open)

    def test_active_session_detection(self):
        # 10:00 ET -> London & New York overlap
        dt_10am = datetime(2026, 10, 20, 10, 0)
        sessions = get_active_sessions(dt_10am)
        self.assertIn("London", sessions)
        self.assertIn("New York", sessions)
        self.assertIn("London/NY Overlap (Peak Volume)", sessions)

        # 21:00 ET (9 PM) -> Tokyo & Sydney
        dt_9pm = datetime(2026, 10, 20, 21, 0)
        sessions = get_active_sessions(dt_9pm)
        self.assertIn("Tokyo", sessions)
        self.assertIn("Sydney", sessions)
        self.assertNotIn("London", sessions)


class TestForexRiskValidation(unittest.TestCase):
    def setUp(self):
        self.config = {
            "budget": 5000.0,
            "leverage": 30.0,
            "max_loss_per_trade_usd": 50.0,
            "min_reward_risk": 1.0,
            "max_currency_exposure_pct": 50.0,
        }

    def test_valid_trade_passes(self):
        # EUR/USD Buy: Entry 1.0850, Stop 1.0830 (20 pips), Target 1.0890 (40 pips -> 2R)
        # Lot: 0.20 lots -> max loss = 0.20 * 20 * $10 = $40 <= $50
        res = validate_forex_trade(
            symbol="EUR/USD",
            side="BUY",
            entry_price=1.0850,
            stop_price=1.0830,
            target_price=1.0890,
            lot_size=0.20,
            config=self.config,
            now_et=datetime(2026, 10, 20, 10, 0)  # Tuesday
        )
        self.assertTrue(res["passed"])
        self.assertEqual(res["status"], "PASS")
        self.assertEqual(res["reward_risk"], 2.0)
        self.assertEqual(res["max_loss_usd"], 40.0)

    def test_trade_blocked_on_weekend(self):
        # Same trade submitted on Saturday
        res = validate_forex_trade(
            symbol="EUR/USD",
            side="BUY",
            entry_price=1.0850,
            stop_price=1.0830,
            target_price=1.0890,
            lot_size=0.20,
            config=self.config,
            now_et=datetime(2026, 10, 24, 12, 0)  # Saturday
        )
        self.assertFalse(res["passed"])
        self.assertEqual(res["status"], "BLOCK")
        self.assertTrue(any("market_closed" in r for r in res["reasons"]))

    def test_trade_blocked_on_excessive_risk(self):
        # Lot: 0.50 lots -> max loss = 0.50 * 20 * $10 = $100 > $50
        res = validate_forex_trade(
            symbol="EUR/USD",
            side="BUY",
            entry_price=1.0850,
            stop_price=1.0830,
            target_price=1.0890,
            lot_size=0.50,
            config=self.config,
            now_et=datetime(2026, 10, 20, 10, 0)
        )
        self.assertFalse(res["passed"])
        self.assertEqual(res["status"], "BLOCK")
        self.assertTrue(any("max_loss" in r for r in res["reasons"]))

    def test_trade_blocked_on_low_reward_risk(self):
        # Target 1.0860 (10 pips) vs Stop 1.0830 (20 pips) -> RR = 0.5 < 1.0
        res = validate_forex_trade(
            symbol="EUR/USD",
            side="BUY",
            entry_price=1.0850,
            stop_price=1.0830,
            target_price=1.0860,
            lot_size=0.10,
            config=self.config,
            now_et=datetime(2026, 10, 20, 10, 0)
        )
        self.assertFalse(res["passed"])
        self.assertEqual(res["status"], "BLOCK")
        self.assertTrue(any("reward_risk" in r for r in res["reasons"]))


class TestForexTicket(unittest.TestCase):
    def test_ticket_formatting(self):
        ticket = format_forex_ticket(
            symbol="EUR/USD",
            side="BUY",
            lot_size=0.20,
            entry_price=1.0850,
            stop_price=1.0830,
            target_price=1.0890,
            pips_at_risk=20.0,
            target_pips=40.0,
            max_loss_usd=40.0,
            margin_required_usd=723.33,
            effective_leverage=4.34,
        )
        self.assertIn("FOREX ORDER TICKET (PAPER TRADING ONLY)", ticket)
        self.assertIn("0.20 Lots (20,000 units)", ticket)
        self.assertIn("Stop Loss:        1.083 (20.0 pips)", ticket)
        self.assertIn("Take Profit:      1.089 (+40.0 pips)", ticket)
        self.assertIn("Max Loss at Stop: $40.00 USD", ticket)


if __name__ == "__main__":
    unittest.main()
