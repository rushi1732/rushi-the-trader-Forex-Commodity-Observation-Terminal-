"""Offline unit tests for Forex market data fetcher."""

import unittest
from forex.data import (
    pair_to_yahoo_ticker,
    yahoo_ticker_to_pair,
    parse_yahoo_chart,
    fetch_chart,
    INDICATIVE_DISCLAIMER,
)


def make_sample_yahoo_chart(symbol="EURUSD=X", base_price=1.0850, count=30):
    """Generates synthetic Yahoo chart JSON structure for offline testing."""
    timestamps = [1700000000 + i * 300 for i in range(count)]
    opens = [round(base_price + i * 0.0001, 5) for i in range(count)]
    highs = [round(o + 0.0005, 5) for o in opens]
    lows = [round(o - 0.0005, 5) for o in opens]
    closes = [round(o + 0.0002, 5) for o in opens]
    volumes = [1000 + i * 10 for i in range(count)]

    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "currency": "USD",
                        "symbol": symbol,
                        "dataGranularity": "5m",
                        "regularMarketPrice": closes[-1],
                    },
                    "timestamp": timestamps,
                    "indicators": {
                        "quote": [
                            {
                                "open": opens,
                                "high": highs,
                                "low": lows,
                                "close": closes,
                                "volume": volumes,
                            }
                        ]
                    },
                }
            ],
            "error": None,
        }
    }


class TestForexData(unittest.TestCase):
    def test_ticker_conversions(self):
        self.assertEqual(pair_to_yahoo_ticker("EUR/USD"), "EURUSD=X")
        self.assertEqual(pair_to_yahoo_ticker("USD/JPY"), "USDJPY=X")
        self.assertEqual(pair_to_yahoo_ticker("eurusd"), "EURUSD=X")

        self.assertEqual(yahoo_ticker_to_pair("EURUSD=X"), "EUR/USD")
        self.assertEqual(yahoo_ticker_to_pair("GBPJPY=X"), "GBP/JPY")

    def test_parse_valid_chart(self):
        raw = make_sample_yahoo_chart("EURUSD=X", base_price=1.0800, count=25)
        parsed = parse_yahoo_chart(raw, "EUR/USD")

        self.assertEqual(parsed["symbol"], "EUR/USD")
        self.assertEqual(parsed["candles_count"], 25)
        self.assertEqual(len(parsed["candles"]), 25)
        self.assertAlmostEqual(parsed["latest_price"], 1.0826, places=4)
        self.assertEqual(parsed["price_type"], "INDICATIVE_MID")
        self.assertEqual(parsed["disclaimer"], INDICATIVE_DISCLAIMER)

    def test_parse_chart_with_null_ticks(self):
        raw = make_sample_yahoo_chart("GBPUSD=X", base_price=1.2800, count=10)
        # inject null
        raw["chart"]["result"][0]["indicators"]["quote"][0]["close"][3] = None

        parsed = parse_yahoo_chart(raw, "GBP/USD")
        # should drop the null candle
        self.assertEqual(parsed["candles_count"], 9)

    def test_parse_error_payload(self):
        raw = {"chart": {"result": None, "error": {"code": "Not Found", "description": "Invalid ticker"}}}
        with self.assertRaises(ValueError):
            parse_yahoo_chart(raw, "INVALID/PAIR")

    def test_offline_fetch_chart(self):
        mock_bundle = {
            "EURUSD=X": make_sample_yahoo_chart("EURUSD=X", 1.0850, 20),
            "USDJPY=X": make_sample_yahoo_chart("USDJPY=X", 152.00, 20),
        }
        res_eur = fetch_chart("EUR/USD", offline_data=mock_bundle)
        self.assertEqual(res_eur["symbol"], "EUR/USD")
        self.assertEqual(res_eur["candles_count"], 20)

        res_jpy = fetch_chart("USD/JPY", offline_data=mock_bundle)
        self.assertEqual(res_jpy["symbol"], "USD/JPY")
        self.assertEqual(res_jpy["candles_count"], 20)


if __name__ == "__main__":
    unittest.main()
