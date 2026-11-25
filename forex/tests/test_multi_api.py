"""
Unit tests for Centralized Multi-API Market Data Engine.
Tests all 4 providers: Twelve Data, Finnhub, Alpha Vantage, EODHD.
Verifies normalized quotes, candles, failover, caching, rate-limit handling,
and dashboard API endpoints.
Zero em dashes are allowed in this test suite.
"""

import json
import time
import unittest
from unittest.mock import MagicMock, patch

from forex.market_api import (
    AlphaVantageProvider,
    EODHDProvider,
    FinnhubProvider,
    MarketDataManager,
    NormalizedCandle,
    NormalizedQuote,
    TwelveDataProvider,
)


class TestNormalizedDataStructures(unittest.TestCase):
    """Verify data structures normalize correctly across all providers."""

    def test_normalized_quote_to_dict(self):
        q = NormalizedQuote(
            symbol="XAU/USD",
            price=2750.50,
            bid=2750.35,
            ask=2750.65,
            open=2740.00,
            high=2760.00,
            low=2735.00,
            close=2750.50,
            change=10.50,
            change_percent=0.38,
            spread_pips=3.0,
            volume=15000,
            timestamp=1700000000,
            datetime_utc="2026-11-25 10:00:00 UTC",
            provider="Twelve Data",
            is_failover=False,
            status="OK",
        )
        d = q.to_dict()
        self.assertEqual(d["symbol"], "XAU/USD")
        self.assertEqual(d["asset_name"], "Gold")
        self.assertEqual(d["asset_type"], "Precious Metal")
        self.assertEqual(d["price"], 2750.50)
        self.assertEqual(d["provider"], "Twelve Data")
        self.assertFalse(d["is_failover"])
        self.assertEqual(d["data_status"], "OK")

    def test_normalized_candle_to_dict(self):
        c = NormalizedCandle(
            timestamp=1700000000,
            datetime_utc="2026-11-25 10:00:00 UTC",
            open=2745.0,
            high=2755.0,
            low=2742.0,
            close=2750.0,
            volume=500,
        )
        cd = c.to_dict()
        self.assertEqual(cd["open"], 2745.0)
        self.assertEqual(cd["close"], 2750.0)
        self.assertEqual(cd["time"], 1700000000)


class TestSupportedAssetsConstraint(unittest.TestCase):
    """Verify system strictly supports ONLY Gold (XAU/USD) and Bitcoin (BTC/USD)."""

    def setUp(self):
        self.mgr = MarketDataManager()

    def test_gold_and_btc_are_supported(self):
        self.assertIn("XAU/USD", self.mgr.SUPPORTED_SYMBOLS)
        self.assertIn("BTC/USD", self.mgr.SUPPORTED_SYMBOLS)

    def test_unsupported_assets_rejected(self):
        unsupported = ["EUR/USD", "GBP/USD", "USD/JPY", "XAG/USD", "ETH/USD", "SOL/USD"]
        for sym in unsupported:
            q = self.mgr.get_quote(sym)
            self.assertIsNone(q, f"Symbol {sym} must be rejected by MarketDataManager")
            chart = self.mgr.get_chart(sym, interval="5m")
            self.assertIsNone(chart, f"Symbol {sym} chart must be rejected by MarketDataManager")


class TestProviderMasking(unittest.TestCase):
    """Verify API keys are never exposed in plaintext."""

    def test_masking_format(self):
        td = TwelveDataProvider("73345dbfbdc94360af524b86732676d2")
        self.assertEqual(td.get_masked_key(), "7334...76d2")

        fh = FinnhubProvider("dc36sopr01quhc9m986gdc36sopr01quhc9m9870")
        self.assertEqual(fh.get_masked_key(), "dc36...9870")

        av = AlphaVantageProvider("XA2YB4NCNWP7XWX5")
        self.assertEqual(av.get_masked_key(), "XA2Y...XWX5")

        eod = EODHDProvider("6b066f56f31863.99311154")
        self.assertEqual(eod.get_masked_key(), "6b06...1154")

    def test_empty_key_masking(self):
        p = TwelveDataProvider(api_key="")
        self.assertEqual(p.get_masked_key(), "")


class TestMultiProviderFailover(unittest.TestCase):
    """Verify automatic failover when primary provider fails."""

    def setUp(self):
        self.mgr = MarketDataManager()

    def tearDown(self):
        self.mgr._init_providers()
        self.mgr._quote_cache.clear()
        self.mgr._chart_cache.clear()

    def test_quote_failover_to_backup_when_primary_fails(self):
        primary_mock = MagicMock()
        primary_mock.id = "twelvedata"
        primary_mock.name = "Twelve Data"
        primary_mock.enabled = True
        primary_mock.supported_assets = ["XAU/USD", "BTC/USD"]
        primary_mock.fetch_quote.side_effect = Exception("Twelve Data 503 Outage")

        backup_mock = MagicMock()
        backup_mock.id = "finnhub"
        backup_mock.name = "Finnhub"
        backup_mock.enabled = True
        backup_mock.supported_assets = ["BTC/USD"]
        backup_quote = NormalizedQuote(
            symbol="BTC/USD",
            price=92500.0,
            bid=92495.0,
            ask=92505.0,
            open=91000.0,
            high=93000.0,
            low=90500.0,
            close=92500.0,
            change=1500.0,
            change_percent=1.65,
            spread_pips=10.0,
            volume=5000,
            timestamp=int(time.time()),
            datetime_utc="2026-11-25 10:00:00 UTC",
            provider="Finnhub",
            is_failover=True,
            status="OK",
        )
        backup_mock.fetch_quote.return_value = backup_quote

        self.mgr.providers = {
            "twelvedata": primary_mock,
            "finnhub": backup_mock,
        }
        self.mgr.config["preferences"]["BTC/USD"] = {
            "primary": "twelvedata",
            "backups": ["finnhub"],
        }

        self.mgr._quote_cache.clear()

        res = self.mgr.get_quote("BTC/USD", force_refresh=True)

        self.assertIsNotNone(res)
        self.assertEqual(res.provider, "Finnhub")
        self.assertTrue(res.is_failover)
        self.assertEqual(res.price, 92500.0)

    def test_chart_failover_to_backup_when_primary_returns_empty(self):
        primary_mock = MagicMock()
        primary_mock.id = "twelvedata"
        primary_mock.name = "Twelve Data"
        primary_mock.enabled = True
        primary_mock.supported_assets = ["XAU/USD", "BTC/USD"]
        primary_mock.supported_timeframes = ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"]
        primary_mock.fetch_candles.return_value = []

        backup_mock = MagicMock()
        backup_mock.id = "finnhub"
        backup_mock.name = "Finnhub"
        backup_mock.enabled = True
        backup_mock.supported_assets = ["BTC/USD"]
        backup_mock.supported_timeframes = ["1m", "5m", "15m", "1h", "1d"]
        backup_candles = [
            {"time": 1700000000 + i * 300, "open": 92000.0, "high": 92100.0, "low": 91900.0, "close": 92050.0, "volume": 100}
            for i in range(25)
        ]
        backup_mock.fetch_candles.return_value = backup_candles

        self.mgr.providers = {
            "twelvedata": primary_mock,
            "finnhub": backup_mock,
        }
        self.mgr.config["preferences"]["BTC/USD"] = {
            "primary": "twelvedata",
            "backups": ["finnhub"],
        }

        self.mgr._chart_cache.clear()

        chart = self.mgr.get_chart("BTC/USD", interval="5m", limit=30)
        self.assertIsNotNone(chart)
        self.assertEqual(chart["provider"], "Finnhub")
        self.assertTrue(chart["is_failover"])
        self.assertEqual(len(chart["candles"]), 25)


class TestCachingAndRateLimiting(unittest.TestCase):
    """Verify smart caching prevents excessive API calls."""

    def setUp(self):
        self.mgr = MarketDataManager()

    def tearDown(self):
        self.mgr._init_providers()
        self.mgr._quote_cache.clear()
        self.mgr._chart_cache.clear()

    def test_quote_cache_within_ttl(self):
        p_mock = MagicMock()
        p_mock.id = "twelvedata"
        p_mock.name = "Twelve Data"
        p_mock.enabled = True
        p_mock.supported_assets = ["XAU/USD", "BTC/USD"]
        q1 = NormalizedQuote(
            symbol="XAU/USD",
            price=2750.0,
            bid=2749.9,
            ask=2750.1,
            open=2740.0,
            high=2760.0,
            low=2735.0,
            close=2750.0,
            change=10.0,
            change_percent=0.36,
            spread_pips=2.0,
            volume=1000,
            timestamp=int(time.time()),
            datetime_utc="2026-11-25 10:00:00 UTC",
            provider="Twelve Data",
            is_failover=False,
            status="OK",
        )
        p_mock.fetch_quote.return_value = q1

        self.mgr.providers = {"twelvedata": p_mock}
        self.mgr.config["preferences"]["XAU/USD"] = {"primary": "twelvedata", "backups": ["finnhub"]}
        self.mgr._quote_cache.clear()

        # Call 1: hits provider
        r1 = self.mgr.get_quote("XAU/USD")
        self.assertEqual(p_mock.fetch_quote.call_count, 1)

        # Call 2: hits cache immediately
        r2 = self.mgr.get_quote("XAU/USD")
        self.assertEqual(p_mock.fetch_quote.call_count, 1)
        self.assertEqual(r1.price, r2.price)


class TestDashboardAPIEndpoints(unittest.TestCase):
    """Verify backend endpoints for market cards and provider management."""

    def setUp(self):
        from forex.market_api import market_manager
        market_manager._init_providers()
        market_manager._quote_cache.clear()
        market_manager._chart_cache.clear()

    def test_market_cards_endpoint_structure(self):
        from forex.market_api import market_manager
        cards = market_manager.get_market_cards()
        self.assertIn("XAU/USD", cards)
        self.assertIn("BTC/USD", cards)

        gold = cards["XAU/USD"]
        self.assertEqual(gold["symbol"], "XAU/USD")
        self.assertIn("price", gold)
        self.assertIn("change", gold)
        self.assertIn("change_percent", gold)
        self.assertIn("provider", gold)

        btc = cards["BTC/USD"]
        self.assertEqual(btc["symbol"], "BTC/USD")
        self.assertIn("price", btc)
        self.assertIn("provider", btc)

    def test_providers_status_list(self):
        from forex.market_api import market_manager
        status_list = market_manager.get_providers_status()
        self.assertEqual(len(status_list), 4)
        ids = [p["id"] for p in status_list]
        self.assertIn("twelvedata", ids)
        self.assertIn("finnhub", ids)
        self.assertIn("alphavantage", ids)
        self.assertIn("eodhd", ids)

        for p in status_list:
            self.assertIn("name", p)
            self.assertIn("status", p)
            self.assertIn("enabled", p)
            self.assertIn("api_key_masked", p)
            self.assertIn("supported_assets", p)

    def test_test_provider_method(self):
        from forex.market_api import market_manager
        # Test nonexistent provider returns failure
        res = market_manager.test_provider("unknown_provider")
        self.assertFalse(res["success"])

        # Test valid registered provider returns structured dict
        res_td = market_manager.test_provider("twelvedata")
        self.assertIn("success", res_td)
        self.assertIn("provider", res_td)
        self.assertIn("latency_ms", res_td)


if __name__ == "__main__":
    unittest.main()
