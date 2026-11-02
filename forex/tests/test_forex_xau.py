"""Unit tests for XAU/USD (Gold) paper observation engine.

Validates price precision, pip/point conventions, 100 oz contract size, USD risk calculations,
spread/slippage simulation, and paper-only observation formatting.
"""

import unittest
from datetime import datetime, timezone
from forex.pairs import get_pair_info, normalize_symbol, to_pips, from_pips, pip_value_usd
from forex.pricing import calc_spread_prices, execution_price
from forex.sizer import size_position
from forex.risk import validate_forex_trade
from forex.ticket import format_forex_ticket
from forex.data import pair_to_yahoo_ticker, yahoo_ticker_to_pair, INDICATIVE_DISCLAIMER_GOLD
from forex.sessions import is_gold_futures_market_open, is_forex_market_open, is_market_open_for_symbol
from forex.observe import observe_pair, format_observation_report


class TestForexXAUUSD(unittest.TestCase):
    def test_xau_metadata_and_normalization(self):
        """Verify Gold metadata reflects 2 decimals, 0.10 pip, 100 oz contract size."""
        self.assertEqual(normalize_symbol("XAU/USD"), "XAU/USD")
        self.assertEqual(normalize_symbol("XAUUSD"), "XAU/USD")
        self.assertEqual(normalize_symbol("GOLD"), "XAU/USD")
        self.assertEqual(normalize_symbol("GC=F"), "XAU/USD")
        self.assertEqual(normalize_symbol("xau-usd"), "XAU/USD")

        info = get_pair_info("XAU/USD")
        self.assertEqual(info["base"], "XAU")
        self.assertEqual(info["quote"], "USD")
        self.assertEqual(info["pip_size"], 0.10)
        self.assertEqual(info["decimals"], 2)
        self.assertEqual(info["contract_size"], 100)
        self.assertEqual(info["default_spread"], 2.5)
        self.assertEqual(info["asset_type"], "metal")

    def test_xau_pip_and_point_conversions(self):
        """Verify 1 pip = $0.10, $1.00 move = 10 pips in gold."""
        # $1.00 price move = 10 pips
        self.assertEqual(to_pips("XAU/USD", 1.00), 10.0)
        self.assertEqual(to_pips("XAU/USD", 0.50), 5.0)
        self.assertEqual(to_pips("XAU/USD", 0.10), 1.0)
        self.assertEqual(to_pips("XAU/USD", 2.35), 23.5)

        # 10 pips = $1.00 offset
        self.assertAlmostEqual(from_pips("XAU/USD", 10.0), 1.0, places=2)
        self.assertAlmostEqual(from_pips("XAU/USD", 25.0), 2.5, places=2)
        self.assertAlmostEqual(from_pips("XAU/USD", 1.0), 0.10, places=2)

    def test_xau_pip_value_usd(self):
        """
        Verify USD pip value for standard (100 oz), mini (10 oz), and micro (1 oz) lots.
        1 pip ($0.10) on 100 oz = $10.00 USD.
        """
        # 1.0 Standard lot (100 oz): 100 * 0.10 = $10.00
        val_std = pip_value_usd("XAU/USD", lot_size=1.0)
        self.assertEqual(val_std, 10.0)

        # 0.10 Mini lot (10 oz): 10 * 0.10 = $1.00
        val_mini = pip_value_usd("XAU/USD", lot_size=0.10)
        self.assertEqual(val_mini, 1.0)

        # 0.01 Micro lot (1 oz): 1 * 0.10 = $0.10
        val_micro = pip_value_usd("XAU/USD", lot_size=0.01)
        self.assertEqual(val_micro, 0.10)

    def test_xau_position_sizing_within_risk_cap(self):
        """
        Verify sizing is bounded by $50 max risk and 100 oz contract size.
        Entry: $2740.00, Stop: $2735.00 ($5.00 risk = 50 pips).
        Max Loss: $50 -> Lot size = $50 / (50 * $10) = 0.10 Lot (10 oz).
        """
        sizing = size_position(
            symbol="XAU/USD",
            side="BUY",
            entry_price=2740.00,
            stop_price=2735.00,
            budget_usd=5000.0,
            max_loss_usd=50.0,
            leverage=30.0,
        )
        self.assertTrue(sizing["allowed"])
        self.assertEqual(sizing["lot_size"], 0.10)
        self.assertEqual(sizing["units"], 10)  # 10 oz
        self.assertEqual(sizing["pips_at_risk"], 50.0)
        self.assertEqual(sizing["max_loss_usd"], 50.0)
        self.assertLessEqual(sizing["max_loss_usd"], 50.0)
        # Margin: (10 oz * 2740) / 30 = $913.33 USD
        self.assertAlmostEqual(sizing["margin_required_usd"], 913.33, places=1)
        self.assertLess(sizing["margin_required_usd"], 4500.0)

    def test_xau_spread_and_execution_pricing(self):
        """Verify Bid/Ask calculation with default 2.5 pips ($0.25) and simulated fills."""
        mid = 2740.00
        bid, ask = calc_spread_prices("XAU/USD", mid_price=mid)
        # 2.5 pips spread = $0.25 -> half = $0.125
        self.assertAlmostEqual(ask - bid, 0.25, places=3)
        self.assertAlmostEqual(bid, 2739.875, places=3)
        self.assertAlmostEqual(ask, 2740.125, places=3)

        # Long fill fills at Ask + 0.5 pip ($0.05) slippage
        fill_long = execution_price("XAU/USD", "BUY", mid_price=mid, slippage_pips=0.5)
        self.assertAlmostEqual(fill_long, 2740.175, places=3)

        # Short fill fills at Bid - 0.5 pip ($0.05) slippage
        fill_short = execution_price("XAU/USD", "SELL", mid_price=mid, slippage_pips=0.5)
        self.assertAlmostEqual(fill_short, 2739.825, places=3)

    def test_xau_ticket_formatting(self):
        """Verify ticket correctly displays oz instead of generic units."""
        ticket = format_forex_ticket(
            symbol="XAU/USD",
            side="BUY",
            lot_size=0.10,
            entry_price=2740.18,
            stop_price=2735.00,
            target_price=2750.54,
            pips_at_risk=51.8,
            target_pips=103.6,
            max_loss_usd=50.0,
            margin_required_usd=913.33,
            effective_leverage=5.48,
        )
        self.assertIn("FOREX ORDER TICKET (PAPER TRADING ONLY)", ticket)
        self.assertIn("Pair:             XAU/USD", ticket)
        self.assertIn("0.10 Lots (10 oz)", ticket)

    def test_xau_observation_paper_mode(self):
        """Verify XAU/USD observation output includes paper notice, oz display, and indicative disclaimer."""
        base_dt = datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)
        timestamps = [int(base_dt.timestamp()) + i * 900 for i in range(29)]
        # Asian range 2735.00 to 2745.00
        opens = [2740.00] * 28 + [2740.00]
        highs = [2745.00] * 28 + [2752.00]
        lows = [2735.00] * 28 + [2739.00]
        closes = [2742.00] * 28 + [2750.00]  # Breakout above 2745.00 by 50 pips ($5.00)

        mock_chart = {
            "chart": {
                "result": [
                    {
                        "meta": {"currency": "USD", "symbol": "GC=F"},
                        "timestamp": timestamps,
                        "indicators": {
                            "quote": [{"open": opens, "high": highs, "low": lows, "close": closes, "volume": [1000] * 29}]
                        },
                    }
                ],
                "error": None,
            }
        }

        now_obs = datetime(2026, 10, 20, 7, 10, tzinfo=timezone.utc)
        obs = observe_pair("XAU/USD", offline_data={"GC=F": mock_chart}, now_dt=now_obs)

        self.assertEqual(obs["symbol"], "XAU/USD")
        self.assertEqual(obs["status"], "SIGNAL CONFIRMED")
        self.assertEqual(obs["direction"], "BULLISH")
        self.assertIsNotNone(obs["paper_simulation"])

        sim = obs["paper_simulation"]
        self.assertEqual(sim["side"], "BUY")
        self.assertGreater(sim["simulated_fill"], 2745.00)
        self.assertGreater(sim["paper_lot_size"], 0.0)

        report = format_observation_report(obs)
        self.assertIn("[OBSERVATION ONLY: PAPER / NOT FOR LIVE EXECUTION]", report)
        self.assertIn("PAIR: XAU/USD (COMEX Gold Futures GC=F Benchmark)", report)
        self.assertIn("oz", report)
        self.assertIn("COMEX Gold Futures", report)

    def test_xau_market_hours_calendar(self):
        """Verify COMEX Gold futures calendar respects 18:00 ET Sunday open and 17:00-18:00 ET daily halts."""
        # 1. Sunday 17:30 ET: Spot forex is open, but COMEX Gold futures is still CLOSED
        sun_1730 = datetime(2026, 10, 18, 17, 30)
        fx_open, _ = is_forex_market_open(sun_1730)
        gold_open, gold_reason = is_gold_futures_market_open(sun_1730)
        self.assertTrue(fx_open, "Spot forex should be open at Sun 17:30 ET")
        self.assertFalse(gold_open, "COMEX Gold should be closed at Sun 17:30 ET (opens 18:00 ET)")
        self.assertIn("opens at 18:00 ET", gold_reason)

        # 2. Sunday 18:05 ET: COMEX Gold futures is now OPEN
        sun_1805 = datetime(2026, 10, 18, 18, 5)
        gold_open, _ = is_gold_futures_market_open(sun_1805)
        self.assertTrue(gold_open, "COMEX Gold should be open at Sun 18:05 ET")

        # 3. Tuesday 17:30 ET: Daily maintenance halt (17:00 - 18:00 ET)
        tue_1730 = datetime(2026, 10, 20, 17, 30)
        gold_open, halt_reason = is_gold_futures_market_open(tue_1730)
        self.assertFalse(gold_open, "COMEX Gold should be closed during 17:00-18:00 ET daily maintenance halt")
        self.assertIn("Daily maintenance trading halt", halt_reason)

        # 4. Tuesday 18:30 ET: Regular trading resumed
        tue_1830 = datetime(2026, 10, 20, 18, 30)
        gold_open, _ = is_gold_futures_market_open(tue_1830)
        self.assertTrue(gold_open, "COMEX Gold should be open at Tue 18:30 ET")

        # 5. Friday 17:15 ET: Weekend close
        fri_1715 = datetime(2026, 10, 23, 17, 15)
        gold_open, _ = is_gold_futures_market_open(fri_1715)
        self.assertFalse(gold_open, "COMEX Gold should be closed after Fri 17:00 ET")

        # 6. Symbol dispatcher test
        self.assertFalse(is_market_open_for_symbol("XAU/USD", sun_1730)[0])
        self.assertTrue(is_market_open_for_symbol("EUR/USD", sun_1730)[0])

    def test_xau_source_and_disclaimer_labeling(self):
        """Verify GC=F is explicitly labeled as COMEX Gold futures benchmark, not spot interbank."""
        mock_chart = {
            "chart": {
                "result": [{
                    "meta": {"currency": "USD", "symbol": "GC=F"},
                    "timestamp": [1792483200],
                    "indicators": {"quote": [{"open": [2740.0], "high": [2745.0], "low": [2738.0], "close": [2742.0], "volume": [100]}]}
                }],
                "error": None
            }
        }
        # Run observation
        now_dt = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
        obs = observe_pair("XAU/USD", offline_data={"GC=F": mock_chart}, now_dt=now_dt)

        self.assertEqual(obs["source"], "Yahoo Finance (GC=F: COMEX Gold Futures indicative benchmark)")
        self.assertIn("COMEX Gold Futures", obs["disclaimer"])
        self.assertIn("not OTC spot interbank feeds", obs["disclaimer"])

    def test_xau_report_strictly_forbids_bank_feed_wording(self):
        """Verify that Gold observation reports never contain 'aggregate bank feeds' wording in banner or disclaimer."""
        mock_chart = {
            "chart": {
                "result": [{
                    "meta": {"currency": "USD", "symbol": "GC=F"},
                    "timestamp": [1792483200],
                    "indicators": {"quote": [{"open": [2740.0], "high": [2745.0], "low": [2738.0], "close": [2742.0], "volume": [100]}]}
                }],
                "error": None
            }
        }
        # 1. Active market / setup report
        now_dt = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
        obs_active = observe_pair("XAU/USD", offline_data={"GC=F": mock_chart}, now_dt=now_dt)
        report_active = format_observation_report(obs_active)

        self.assertNotIn("aggregate bank feeds", report_active.lower(), "Gold report must not mention aggregate bank feeds")
        self.assertNotIn("bank indicators", report_active.lower(), "Gold report must not mention bank indicators")
        self.assertIn("comex gold futures", report_active.lower())

        # 2. Closed market / NO SIGNAL report
        sun_closed = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
        obs_closed = observe_pair("XAU/USD", offline_data={"GC=F": mock_chart}, now_dt=sun_closed)
        report_closed = format_observation_report(obs_closed)

        self.assertNotIn("aggregate bank feeds", report_closed.lower(), "Closed Gold report must not mention aggregate bank feeds")
        self.assertNotIn("bank indicators", report_closed.lower(), "Closed Gold report must not mention bank indicators")
        self.assertIn("comex gold futures", report_closed.lower())


if __name__ == "__main__":
    unittest.main()
