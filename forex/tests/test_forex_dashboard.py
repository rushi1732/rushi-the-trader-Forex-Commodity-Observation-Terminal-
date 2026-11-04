"""Unit tests for the browser-based Paper Observation Dashboard.

Verifies:
- Dashboard HTML contains required safety banners and Gold futures disclaimers.
- Strictly forbids 'aggregate bank feeds' wording for Gold.
- API endpoints enforce paper_mode and return valid observation state.
- Stale/closed market states return 'NO SIGNAL' / closed market indicators.
- Paper simulation calculates realistic fills without live broker execution.
"""

import json
import threading
import time
import unittest
import urllib.request
import urllib.parse
from http.server import HTTPServer
from forex.dashboard import DashboardRequestHandler, HTML_TEMPLATE, PAPER_JOURNAL, GOLD_CALIBRATION, TWELVE_DATA_CONFIG
from forex.data import parse_twelve_data_chart, fetch_twelve_data_chart


class TestForexDashboard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Configure offline mock chart data so tests do not rely on external networks
        mock_fx = {
            "chart": {
                "result": [{
                    "meta": {"currency": "USD", "symbol": "EURUSD=X"},
                    "timestamp": [1792483200 + i * 300 for i in range(25)],
                    "indicators": {"quote": [{
                        "open": [1.0850] * 25,
                        "high": [1.0860] * 25,
                        "low": [1.0840] * 25,
                        "close": [1.0855] * 25,
                        "volume": [1000] * 25,
                    }]}
                }],
                "error": None,
            }
        }
        mock_gold = {
            "chart": {
                "result": [{
                    "meta": {"currency": "USD", "symbol": "GC=F"},
                    "timestamp": [1792483200 + i * 300 for i in range(25)],
                    "indicators": {"quote": [{
                        "open": [2740.0] * 25,
                        "high": [2745.0] * 25,
                        "low": [2738.0] * 25,
                        "close": [2742.0] * 25,
                        "volume": [100] * 25,
                    }]}
                }],
                "error": None,
            }
        }
        DashboardRequestHandler.OFFLINE_DATA = {
            "EURUSD=X": mock_fx,
            "EUR/USD": mock_fx,
            "GC=F": mock_gold,
            "XAU/USD": mock_gold,
        }

        # Start test HTTP server on an ephemeral loopback port
        cls.server = HTTPServer(("127.0.0.1", 0), DashboardRequestHandler)
        cls.port = cls.server.server_address[1]
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        DashboardRequestHandler.OFFLINE_DATA = None
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        from forex.dashboard import RISK_CONFIG, PAPER_JOURNAL
        RISK_CONFIG["daily_loss_limit_usd"] = 150.0
        RISK_CONFIG["max_risk_per_trade_usd"] = 50.0
        RISK_CONFIG["max_open_positions"] = 3
        PAPER_JOURNAL.clear()
        PAPER_JOURNAL.extend([
            {
                "id": "SIM-101",
                "symbol": "XAU/USD",
                "side": "BUY",
                "simulated_fill": 2740.18,
                "stop_price": 2735.00,
                "target_price": 2750.54,
                "lot_size": 0.10,
                "units": 10,
                "unit_label": "oz",
                "risk_usd": 50.00,
                "pnl_usd": 0.0,
                "timestamp": "2026-10-30 09:15 ET",
                "status": "OPEN",
                "note": "London session simulated breakout setup",
            }
        ])

    def test_dashboard_html_safety_banners(self):
        """Verify dashboard HTML contains mandatory paper-only safety ribbons and notices."""
        url = f"http://127.0.0.1:{self.port}/"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("X-Paper-Trading-Only"), "true")
            html = resp.read().decode("utf-8")

        # Required safety notices
        self.assertIn("SIMULATED PAPER OBSERVATION ONLY", html)
        self.assertIn("ZERO BROKER CONNECTIVITY", html)
        self.assertIn("Futures Benchmark", html)
        self.assertIn("not OTC spot/interbank aggregate feeds", html)
        self.assertIn("COMEX Gold Futures (GC=F)", html)

        # Strictly forbid old 'aggregate bank feeds' wording for Gold
        self.assertNotIn("aggregate bank feeds", html.lower())

    def test_api_pairs(self):
        """Verify /api/pairs returns list of supported currency and commodity pairs."""
        url = f"http://127.0.0.1:{self.port}/api/pairs"
        with urllib.request.urlopen(url, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            pairs = json.loads(resp.read().decode("utf-8"))
            self.assertIn("XAU/USD", pairs)
            self.assertIn("EUR/USD", pairs)

    def test_api_observe_xau_gold(self):
        """Verify /api/observe for XAU/USD identifies GC=F futures benchmark and paper mode."""
        url = f"http://127.0.0.1:{self.port}/api/observe?symbol=XAU/USD"
        with urllib.request.urlopen(url, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))

        self.assertEqual(data["symbol"], "XAU/USD")
        self.assertTrue(data.get("paper_mode"))
        self.assertIn("COMEX Gold Futures", data.get("source", ""))
        self.assertIn("not OTC spot interbank feeds", data.get("disclaimer", ""))
        self.assertNotIn("aggregate bank feeds", data.get("source", "").lower())
        self.assertNotIn("aggregate bank feeds", data.get("disclaimer", "").lower())

    def test_api_observe_closed_or_stale_shows_no_signal(self):
        """Verify that when market is closed, observe endpoint returns status NO SIGNAL with reason."""
        url = f"http://127.0.0.1:{self.port}/api/observe?symbol=EUR/USD"
        with urllib.request.urlopen(url, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))

        # On weekend or outside session, status must be NO SIGNAL with explicit reason
        if data.get("status") == "NO SIGNAL":
            self.assertTrue("reason" in data)
            self.assertTrue("closed" in data["reason"].lower() or "stale" in data["reason"].lower())

    def test_api_paper_simulate(self):
        """Verify /api/paper/simulate calculates paper fill, lot size, and USD risk."""
        url = f"http://127.0.0.1:{self.port}/api/paper/simulate"
        payload = {
            "symbol": "XAU/USD",
            "side": "BUY",
            "entry_price": 2740.00,
            "stop_price": 2735.00,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            sim = json.loads(resp.read().decode("utf-8"))

        self.assertTrue(sim["allowed"])
        self.assertEqual(sim["lot_size"], 0.10)
        self.assertEqual(sim["units"], 10)  # 10 oz
        self.assertAlmostEqual(sim["max_loss_usd"], 50.0, places=1)
        self.assertEqual(sim["warning"], "SIMULATED — NOT A REAL TRADE")

    def test_api_paper_journal_crud(self):
        """Verify journal listing and adding new simulated entries."""
        # 1. Fetch current journal
        url = f"http://127.0.0.1:{self.port}/api/paper/journal"
        with urllib.request.urlopen(url, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            initial = json.loads(resp.read().decode("utf-8"))
            self.assertIsInstance(initial, list)

        # 2. Add simulated paper trade
        new_trade = {
            "symbol": "EUR/USD",
            "side": "BUY",
            "simulated_fill": 1.0865,
            "stop_price": 1.0845,
            "target_price": 1.0905,
            "note": "Unit test journal injection"
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(new_trade).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            res = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(res["success"])
            self.assertEqual(res["entry"]["symbol"], "EUR/USD")
            self.assertEqual(res["entry"]["status"], "OPEN")

    def test_api_skills_returns_all_11_skills(self):
        """Verify /api/skills returns all 11 modular skills with roles, inputs, and outputs."""
        url = f"http://127.0.0.1:{self.port}/api/skills"
        with urllib.request.urlopen(url, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("X-Paper-Trading-Only"), "true")
            skills = json.loads(resp.read().decode("utf-8"))

        self.assertEqual(len(skills), 11)
        expected_ids = {
            "trader-scanner", "trader-news", "trader-analyst",
            "trader-entry", "trader-exit", "trader-risk",
            "trader-budget", "trader-broker", "trader-alerts",
            "trader-pelosi", "trader-timemachine"
        }
        actual_ids = {s["id"] for s in skills}
        self.assertEqual(actual_ids, expected_ids)

        for s in skills:
            self.assertIn("name", s)
            self.assertIn("folder", s)
            self.assertIn("role", s)
            self.assertIn("inputs", s)
            self.assertIn("outputs", s)
            self.assertIn("status", s)
            self.assertTrue(len(s["inputs"]) > 0)
            self.assertTrue(len(s["outputs"]) > 0)

    def test_broker_skill_strictly_disabled(self):
        """Verify trader-broker is strictly labeled 'Disabled — no execution' and rejects run requests."""
        url_skills = f"http://127.0.0.1:{self.port}/api/skills"
        with urllib.request.urlopen(url_skills, timeout=5) as resp:
            skills = json.loads(resp.read().decode("utf-8"))
        broker_skill = next(s for s in skills if s["id"] == "trader-broker")
        self.assertEqual(broker_skill["status"], "Disabled — no execution")
        self.assertFalse(broker_skill["is_executable"])

        # Attempt to trigger trader-broker
        url_run = f"http://127.0.0.1:{self.port}/api/skills/run"
        payload = {"skill_id": "trader-broker", "input": "place_stock_order"}
        req = urllib.request.Request(
            url_run,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            res = json.loads(resp.read().decode("utf-8"))
            self.assertFalse(res["allowed"])
            self.assertEqual(res["status"], "Disabled — no execution")
            self.assertIn("Disabled — no execution", res["error"])
            self.assertIn("trader-broker IS DISABLED", res["output"])

    def test_api_skills_run_paper_simulation(self):
        """Verify analytical skills return valid paper simulation outputs without broker access."""
        url = f"http://127.0.0.1:{self.port}/api/skills/run"
        test_skills = ["trader-scanner", "trader-entry", "trader-exit", "trader-risk", "trader-budget", "trader-timemachine"]

        for sk in test_skills:
            payload = {"skill_id": sk, "input": "test-paper"}
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                res = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(res["allowed"], f"Skill {sk} should allow paper simulation")
                self.assertIn("output", res)
                self.assertTrue(len(res["output"]) > 20)

    def test_dashboard_html_renders_11_skills(self):
        """Verify dashboard HTML includes the 11 skills container and dynamic loader."""
        url = f"http://127.0.0.1:{self.port}/"
        with urllib.request.urlopen(url, timeout=5) as resp:
            html = resp.read().decode("utf-8")

        self.assertIn("11 Modular Trading Skills", html)
        self.assertIn("skillsContainer", html)
        self.assertIn("fetchSkills", html)
        self.assertIn("runSkill", html)
        self.assertIn("PACK: 10 PAPER ACTIVE / 1 DISABLED", html)

    def test_dashboard_html_auto_signal_engine(self):
        """Verify dashboard HTML contains Automatic Signal Engine hero card and elements."""
        url = f"http://127.0.0.1:{self.port}/"
        with urllib.request.urlopen(url, timeout=5) as resp:
            html = resp.read().decode("utf-8")

        self.assertIn("Automatic Signal Engine", html)
        self.assertIn("autoSignalHero", html)
        self.assertIn("autoSignalBadge", html)
        self.assertIn("autoSignalAction", html)
        self.assertIn("autoSignalReason", html)
        self.assertIn("btnLogAutoSignal", html)
        self.assertIn("autoSignalStrategy", html)
        self.assertIn("autoSignalTimeframe", html)
        self.assertIn("autoSignalEntry", html)
        self.assertIn("autoSignalStop", html)
        self.assertIn("autoSignalTarget", html)
        self.assertIn("autoSignalTarget2", html)
        self.assertIn("autoSignalConfluence", html)
        self.assertIn("autoSignalRR", html)
        self.assertIn("autoSignalTime", html)
        self.assertIn("const elStrategy = document.getElementById('autoSignalStrategy')", html)
        self.assertIn("const elEntry = document.getElementById('autoSignalEntry')", html)
        self.assertIn("const elStop = document.getElementById('autoSignalStop')", html)

    def test_api_paper_log_signal_success(self):
        """Verify confirmed automatic signal is logged into paper journal with AUTO- prefix."""
        url = f"http://127.0.0.1:{self.port}/api/paper/log-signal"
        payload = {
            "symbol": "XAU/USD",
            "action": "BUY",
            "entry_reference": 2742.0,
            "stop_loss": 2735.0,
            "take_profit": 2756.0,
            "strategy": "trader-scanner (Asian Breakout) + trader-entry (Candle Close) + trader-exit (2R Plan)",
            "timeframe": "5m",
            "reason": "Confirmed BUY breakout beyond 2740.00",
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            res = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(res["success"])
            entry = res["entry"]
            self.assertTrue(entry["id"].startswith("AUTO-"))
            self.assertEqual(entry["side"], "BUY")
            self.assertEqual(entry["symbol"], "XAU/USD")
            self.assertEqual(entry["simulated_fill"], 2742.0)
            self.assertEqual(entry["unit_label"], "oz")
            self.assertIn("Asian Breakout", entry["note"])

    def test_api_paper_log_signal_rejects_no_signal(self):
        """Verify /api/paper/log-signal refuses to log unconfirmed NO SIGNAL states."""
        url = f"http://127.0.0.1:{self.port}/api/paper/log-signal"
        payload = {
            "symbol": "EUR/USD",
            "action": "NO SIGNAL",
            "reason": "Market closed",
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                self.fail("Expected HTTP 400 Bad Request")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
            err_data = json.loads(e.read().decode("utf-8"))
            self.assertIn("Cannot log journal entry when signal is NO SIGNAL", err_data["error"])

    def test_dashboard_html_new_components(self):
        """Verify dashboard HTML contains chart canvas, backtest UI, data health bar, and CSV export."""
        url = f"http://127.0.0.1:{self.port}/"
        with urllib.request.urlopen(url, timeout=5) as resp:
            html = resp.read().decode("utf-8")

        # Chart components
        self.assertIn('id="chartCanvas"', html)
        self.assertIn('id="chartStatsBar"', html)
        self.assertIn('id="chartTitle"', html)
        self.assertIn("EMA 9", html)
        self.assertIn("EMA 21", html)

        # Backtest panel
        self.assertIn('id="backtestMetrics"', html)
        self.assertIn('id="btWinRate"', html)
        self.assertIn('id="btProfitFactor"', html)
        self.assertIn('id="btNetPnl"', html)
        self.assertIn("Historical Strategy Backtest", html)

        # Data health status bar
        self.assertIn('class="health-bar"', html)
        self.assertIn('id="healthBenchmark"', html)

        # Journal export & risk
        self.assertIn('id="btnExportCsv"', html)
        self.assertIn('id="riskModal"', html)

    def test_api_chart_endpoint(self):
        """Verify /api/chart returns candles, EMA indicators, and benchmark notice."""
        url = f"http://127.0.0.1:{self.port}/api/chart?symbol=XAU/USD&interval=5m"
        with urllib.request.urlopen(url, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["symbol"], "XAU/USD")
            self.assertEqual(data["interval"], "5m")
            self.assertTrue(len(data["candles"]) > 0)
            self.assertIn("ema9", data)
            self.assertIn("ema21", data)
            self.assertIn("signal_markers", data)
            self.assertIn("COMEX Gold Futures", data["benchmark_notice"])

    def test_api_backtest_endpoint(self):
        """Verify /api/backtest executes historical replay and returns performance metrics."""
        url = f"http://127.0.0.1:{self.port}/api/backtest?symbol=XAU/USD&interval=5m&range=5d"
        with urllib.request.urlopen(url, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["status"], "COMPLETED")
            self.assertEqual(data["symbol"], "XAU/USD")
            self.assertIn("summary", data)
            summary = data["summary"]
            self.assertIn("win_rate_pct", summary)
            self.assertIn("total_trades", summary)
            self.assertIn("profit_factor", summary)
            self.assertIn("net_pnl_usd", summary)
            self.assertIn("disclaimer", data)

    def test_api_health_endpoint(self):
        """Verify /api/health returns feed status, GC=F benchmark verification, and broker disconnect."""
        url = f"http://127.0.0.1:{self.port}/api/health?symbol=XAU/USD"
        with urllib.request.urlopen(url, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["status"], "HEALTHY")
            self.assertTrue(data["benchmark_verified"])
            self.assertTrue(data["broker_disconnected"])
            self.assertIn("COMEX Gold Futures", data["benchmark_notice"])

    def test_api_config_get_and_post(self):
        """Verify /api/config allows reading and updating risk limits."""
        url = f"http://127.0.0.1:{self.port}/api/config"
        # GET config
        with urllib.request.urlopen(url, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            cfg = json.loads(resp.read().decode("utf-8"))
            self.assertIn("daily_loss_limit_usd", cfg)
            self.assertIn("max_risk_per_trade_usd", cfg)
            self.assertIn("max_open_positions", cfg)
            self.assertTrue(cfg.get("paper"))

        # POST config
        update_payload = {
            "daily_loss_limit_usd": 200.0,
            "max_risk_per_trade_usd": 75.0,
            "max_open_positions": 4,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(update_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            res = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(res["success"])
            self.assertEqual(res["config"]["daily_loss_limit_usd"], 200.0)
            self.assertEqual(res["config"]["max_risk_per_trade_usd"], 75.0)
            self.assertEqual(res["config"]["max_open_positions"], 4)

    def test_api_paper_journal_export_csv(self):
        """Verify /api/paper/journal/export returns valid text/csv format."""
        url = f"http://127.0.0.1:{self.port}/api/paper/journal/export"
        with urllib.request.urlopen(url, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("text/csv", resp.headers.get("Content-Type", ""))
            csv_content = resp.read().decode("utf-8")
            self.assertIn("ID,Symbol,Side,SimulatedFill,StopLoss,TakeProfit,LotSize,Units,RiskUSD,PnLUSD,Timestamp,Status,Strategy", csv_content)

    def test_twelve_data_websocket_client_present_in_html(self):
        """Verify HTML includes Twelve Data Real-Time WebSocket connection, reconnect logic, and BTC/USD support."""
        url = f"http://127.0.0.1:{self.port}/"
        with urllib.request.urlopen(url, timeout=5) as resp:
            html = resp.read().decode("utf-8")

        self.assertIn("wss://ws.twelvedata.com/v1/quotes/price", html)
        self.assertIn("initTwelveDataWebSocket", html)
        self.assertIn("scheduleTwelveDataWsReconnect", html)
        self.assertIn("renderLiveRealtimeTick", html)
        self.assertIn("BTC/USD", html)
        self.assertIn("Twelve Data Real-Time", html)
        self.assertNotIn("wss://stream.binance.com", html)
        self.assertNotIn("PAXG/USDT", html)

    def test_api_tick_ingestion_and_journal_sync(self):
        """Verify /api/tick ingests live ticks and auto-resolves paper journal positions on hit TP."""
        from forex.dashboard import LIVE_TICKS, PAPER_JOURNAL
        # Ensure SIM-101 is OPEN with target 2750.54
        trade = next((t for t in PAPER_JOURNAL if t.get("id") == "SIM-101"), None)
        self.assertIsNotNone(trade)
        self.assertEqual(trade["status"], "OPEN")

        # Send tick higher than take-profit
        url = f"http://127.0.0.1:{self.port}/api/tick"
        payload = {
            "symbol": "XAU/USD",
            "mid": 2755.00,
            "bid": 2754.90,
            "ask": 2755.10,
            "last": 2755.00,
            "timestamp": int(time.time() * 1000)
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            res = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(res["success"])

        # Verify tick cached
        self.assertIn("XAU/USD", LIVE_TICKS)
        self.assertEqual(LIVE_TICKS["XAU/USD"]["mid"], 2755.00)

        # Verify SIM-101 position resolved to HIT TP
        self.assertEqual(trade["status"], "CLOSED (HIT TP)")
        self.assertEqual(trade["exit_price"], 2750.54)
        self.assertAlmostEqual(trade["pnl_usd"], 100.0, places=1)

    def test_gold_spot_calibration_api_and_html(self):
        """Verify calibration modal, buttons, GET/POST /api/calibrate endpoints, and chart offset."""
        from forex.dashboard import GOLD_CALIBRATION
        # 1. HTML check
        url = f"http://127.0.0.1:{self.port}/"
        with urllib.request.urlopen(url, timeout=5) as resp:
            html = resp.read().decode("utf-8")
        self.assertIn("calibrateModal", html)
        self.assertIn("openCalibrateModal", html)
        self.assertIn("calcOffsetFromTarget", html)
        self.assertIn("saveCalibration", html)

        # 2. GET /api/calibrate
        cal_url = f"http://127.0.0.1:{self.port}/api/calibrate"
        with urllib.request.urlopen(cal_url, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertIn("offset", data)

        # 3. POST /api/calibrate with -12.95 offset
        post_payload = {
            "offset": -12.95,
            "target_broker": "TradingView OANDA",
            "target_price": 3896.00
        }
        req = urllib.request.Request(
            cal_url,
            data=json.dumps(post_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            res = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(res["success"])
            self.assertEqual(res["calibration"]["offset"], -12.95)

        self.assertEqual(GOLD_CALIBRATION["offset"], -12.95)

        # 4. Check /api/chart applies offset
        chart_url = f"http://127.0.0.1:{self.port}/api/chart?symbol=XAU/USD"
        with urllib.request.urlopen(chart_url, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            chart = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(chart.get("calibrated_offset"), -12.95)

        # Reset calibration to 0
        reset_req = urllib.request.Request(
            cal_url,
            data=json.dumps({"offset": 0.0, "target_broker": "Spot XAU/USD"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(reset_req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
        self.assertEqual(GOLD_CALIBRATION["offset"], 0.0)

    def test_strategy_decision_matrix_and_trade_rationale(self):
        """Verify Strategy Decision Matrix, Why BUY/SELL logic, and 4-rule checklist in HTML."""
        url = f"http://127.0.0.1:{self.port}/"
        with urllib.request.urlopen(url, timeout=5) as resp:
            html = resp.read().decode("utf-8")
        self.assertIn("matrix-card", html)
        self.assertIn("matrixDecisionBox", html)
        self.assertIn("matrixActionLabel", html)
        self.assertIn("matrixDecisionSummary", html)
        self.assertIn("chkSessionStatus", html)
        self.assertIn("chkTrendStatus", html)
        self.assertIn("chkBreakoutStatus", html)
        self.assertIn("chkRiskStatus", html)
        self.assertIn("matrixLevelHigh", html)
        self.assertIn("matrixLevelLow", html)
        self.assertIn("updateStrategyMatrix", html)

    def test_twelve_data_api_config_get_and_post(self):
        """Verify GET and POST /api/twelvedata endpoints."""
        url = f"http://127.0.0.1:{self.port}/api/twelvedata"
        # 1. GET current config
        with urllib.request.urlopen(url, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("X-Paper-Trading-Only"), "true")
            cfg = json.loads(resp.read().decode("utf-8"))
            self.assertIn("api_key", cfg)
            self.assertIn("enabled", cfg)
            self.assertEqual(cfg["api_key"], "73345dbfbdc94360af524b86732676d2")

        # 2. POST update config
        update_payload = {
            "api_key": "test_api_key_12345",
            "enabled": False
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(update_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            res = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(res["success"])
            self.assertEqual(res["config"]["api_key"], "test_api_key_12345")
            self.assertFalse(res["config"]["enabled"])

        # 3. Restore user's API key
        restore_payload = {
            "api_key": "73345dbfbdc94360af524b86732676d2",
            "enabled": True
        }
        restore_req = urllib.request.Request(
            url,
            data=json.dumps(restore_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(restore_req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
        self.assertEqual(TWELVE_DATA_CONFIG["api_key"], "73345dbfbdc94360af524b86732676d2")
        self.assertTrue(TWELVE_DATA_CONFIG["enabled"])

    def test_twelve_data_html_elements(self):
        """Verify Twelve Data UI controls, modal, and WebSocket script in HTML."""
        url = f"http://127.0.0.1:{self.port}/"
        with urllib.request.urlopen(url, timeout=5) as resp:
            html = resp.read().decode("utf-8")
        self.assertIn("btnTwelveData", html)
        self.assertIn("tdStatusLabel", html)
        self.assertIn("tdBannerBadge", html)
        self.assertIn("twelveDataModal", html)
        self.assertIn("tdApiKey", html)
        self.assertIn("tdEnabled", html)
        self.assertIn("openTwelveDataModal", html)
        self.assertIn("saveTwelveDataConfig", html)
        self.assertIn("initTwelveDataWebSocket", html)

    def test_twelve_data_chart_parsing(self):
        """Verify parse_twelve_data_chart parses Twelve Data JSON into chronological candles."""
        sample_td_json = {
            "meta": {
                "symbol": "XAU/USD",
                "interval": "5min",
                "currency_base": "Gold",
                "currency_quote": "USD",
                "exchange": "Retail Spot"
            },
            "values": [
                {
                    "datetime": "2026-11-02 11:35:00",
                    "open": "3896.10",
                    "high": "3897.50",
                    "low": "3895.80",
                    "close": "3896.80",
                    "volume": "450"
                },
                {
                    "datetime": "2026-11-02 11:30:00",
                    "open": "3894.50",
                    "high": "3896.40",
                    "low": "3894.20",
                    "close": "3896.00",
                    "volume": "520"
                }
            ],
            "status": "ok"
        }
        res = parse_twelve_data_chart(sample_td_json, symbol="XAU/USD", interval="5m")
        self.assertEqual(res["symbol"], "XAU/USD")
        self.assertEqual(res["candles_count"], 2)
        self.assertEqual(res["latest_price"], 3896.80)
        self.assertEqual(res["price_type"], "SPOT_RETAIL_MID")
        candles = res["candles"]
        self.assertEqual(candles[0]["time"], "11:30")
        self.assertEqual(candles[0]["close"], 3896.00)
        self.assertEqual(candles[1]["time"], "11:35")
        self.assertEqual(candles[1]["close"], 3896.80)

        # Also verify fetch_twelve_data_chart with offline mock
        chart = fetch_twelve_data_chart(
            symbol="XAU/USD",
            interval="5m",
            offline_data=sample_td_json
        )
        self.assertEqual(chart["candles_count"], 2)
        self.assertEqual(chart["latest_price"], 3896.80)

    def test_institutional_api_and_chart(self):
        """Verify GET /api/institutional and /api/chart institutional components."""
        # 1. /api/institutional endpoint
        url = f"http://127.0.0.1:{self.port}/api/institutional?symbol=EUR/USD"
        with urllib.request.urlopen(url, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("X-Paper-Trading-Only"), "true")
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["symbol"], "EUR/USD")
            self.assertIn("confluence_signal", data)
            self.assertIn("smc", data)
            self.assertIn("fibonacci", data)
            self.assertIn("liquidity", data)
            self.assertIn("ict", data)

        # 2. /api/chart endpoint with institutional overlays
        url_chart = f"http://127.0.0.1:{self.port}/api/chart?symbol=EUR/USD&interval=5m"
        with urllib.request.urlopen(url_chart, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            chart_data = json.loads(resp.read().decode("utf-8"))
            self.assertIn("smc", chart_data)
            self.assertIn("fibonacci", chart_data)
            self.assertIn("liquidity", chart_data)
            self.assertIn("ict", chart_data)
            self.assertIn("confluence_signal", chart_data)

    def test_signals_scan_api_and_scanner_elements(self):
        """Verify GET /api/signals/scan endpoint and scanner HTML UI components."""
        # 1. /api/signals/scan endpoint
        url = f"http://127.0.0.1:{self.port}/api/signals/scan"
        with urllib.request.urlopen(url, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("X-Paper-Trading-Only"), "true")
            scan = json.loads(resp.read().decode("utf-8"))
            self.assertIn("results", scan)
            self.assertIn("count", scan)
            self.assertGreater(scan["count"], 0)
            first = scan["results"][0]
            self.assertIn("symbol", first)
            self.assertIn("action", first)
            self.assertIn("confluence_score", first)
            self.assertIn("trend", first)
            self.assertIn("zone", first)
            self.assertIn("liquidity_state", first)
            self.assertIn("killzone", first)

        # 2. Scanner HTML elements
        url_html = f"http://127.0.0.1:{self.port}/"
        with urllib.request.urlopen(url_html, timeout=5) as resp:
            html = resp.read().decode("utf-8")
        self.assertIn("scannerTableBody", html)
        self.assertIn("fetchScannerSignals", html)
        self.assertIn("selectScannerPair", html)
        self.assertIn("toggleSMC", html)
        self.assertIn("toggleFVG", html)
        self.assertIn("toggleFib", html)
        self.assertIn("toggleLiq", html)


if __name__ == "__main__":
    unittest.main()



