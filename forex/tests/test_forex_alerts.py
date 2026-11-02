"""Offline unit tests for Forex alerts and notifications."""

import os
import unittest
from datetime import datetime, timezone

from forex.alerts import (
    add_alert,
    load_alerts,
    save_alerts,
    evaluate_alerts,
    clock_report,
    get_alerts_state_path,
)


class TestForexAlerts(unittest.TestCase):
    def setUp(self):
        # Reset state before each test
        self.state_path = get_alerts_state_path()
        save_alerts({"rules": [], "fired": []})

    def tearDown(self):
        # Clean state after test
        save_alerts({"rules": [], "fired": []})

    def test_add_and_list_alerts(self):
        r1 = add_alert("EUR/USD", "above", price=1.0900, label="EUR/USD 1.0900 Resistance")
        r2 = add_alert("USD/JPY", "below", price=150.00, label="USD/JPY 150 Support")

        st = load_alerts()
        self.assertEqual(len(st["rules"]), 2)
        self.assertEqual(st["rules"][0]["symbol"], "EUR/USD")
        self.assertEqual(st["rules"][1]["symbol"], "USD/JPY")

    def test_stop_and_target_triggers(self):
        # Long Stop at 1.0800, Long Target at 1.0900
        add_alert("EUR/USD", "stop", price=1.0800, side="BUY", label="EUR/USD Long Stop")
        add_alert("EUR/USD", "target", price=1.0900, side="BUY", label="EUR/USD Long Target")

        # 1. Price is at 1.0850 (between stop and target) -> neither should fire
        fired, active = evaluate_alerts(quotes={"EUR/USD": 1.0850})
        self.assertEqual(len(fired), 0)
        self.assertEqual(len(active), 2)

        # 2. Price drops to 1.0795 -> stop should fire
        fired_stop, active2 = evaluate_alerts(quotes={"EUR/USD": 1.0795})
        self.assertEqual(len(fired_stop), 1)
        self.assertIn("STOP LOSS HIT", fired_stop[0])

        # 3. Check again at same low price -> should ring ONCE, no duplicate
        fired_repeat, _ = evaluate_alerts(quotes={"EUR/USD": 1.0795})
        self.assertEqual(len(fired_repeat), 0)

    def test_weekend_close_alert(self):
        add_alert("EUR/USD", "weekend_close", label="Weekend Close")

        # Friday 14:00 ET -> Not fired
        fri_2pm = datetime(2026, 10, 23, 14, 0)
        fired_pre, _ = evaluate_alerts(now_et=fri_2pm)
        self.assertEqual(len(fired_pre), 0)

        # Friday 16:35 ET -> Fired
        fri_post = datetime(2026, 10, 23, 16, 35)
        fired_post, _ = evaluate_alerts(now_et=fri_post)
        self.assertEqual(len(fired_post), 1)
        self.assertIn("WEEKEND FLATTEN", fired_post[0])

    def test_clock_report(self):
        rep = clock_report()
        self.assertIn("FOREX 24/5 MARKET CLOCK", rep)
        self.assertIn("Sydney:", rep)
        self.assertIn("Tokyo:", rep)
        self.assertIn("London:", rep)
        self.assertIn("New York:", rep)
        self.assertIn("INDICATIVE ONLY", rep)


if __name__ == "__main__":
    unittest.main()
