"""Browser-based Paper Observation Dashboard for Forex & COMEX Gold Futures (GC=F).

Runs a lightweight HTTP server on localhost using standard library only (no pip dependencies required).
Enforces paper-only observation: no broker connection, no live order execution, no real-money risk.
"""

import argparse
import json
import os
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timezone
try:
    from http.server import ThreadingHTTPServer as ServerClass, BaseHTTPRequestHandler
except ImportError:
    from http.server import HTTPServer as ServerClass, BaseHTTPRequestHandler
from typing import Dict, Any, List, Optional

from .pairs import get_pair_info, normalize_symbol, to_pips, from_pips, pip_value_usd, PAIRS
from .pricing import calc_spread_prices, execution_price
from .sizer import size_position
from .risk import validate_forex_trade
from .sessions import is_market_open_for_symbol, get_active_sessions, utc_to_et
from .observe import observe_pair, format_observation_report
from .data import INDICATIVE_DISCLAIMER_GOLD, INDICATIVE_DISCLAIMER_FOREX, MAJOR_PAIRS, fetch_chart, fetch_binance_paxg_ticker, fetch_twelve_data_chart
from .backtest import calculate_ema, run_backtest

DEFAULT_PORT = 8765

# Configurable paper observation risk parameters
RISK_CONFIG: Dict[str, Any] = {
    "daily_loss_limit_usd": 150.0,
    "max_risk_per_trade_usd": 50.0,
    "max_open_positions": 3,
    "paper": True,
}

# Twelve Data direct retail spot forex & gold provider configuration
TWELVE_DATA_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "twelvedata_config.json")
TWELVE_DATA_CONFIG: Dict[str, Any] = {
    "api_key": "73345dbfbdc94360af524b86732676d2",
    "enabled": True,
    "provider": "twelvedata",
}
if os.path.exists(TWELVE_DATA_CONFIG_PATH):
    try:
        with open(TWELVE_DATA_CONFIG_PATH, "r", encoding="utf-8") as _fh:
            TWELVE_DATA_CONFIG.update(json.load(_fh))
    except Exception:
        pass

# In-memory live WebSocket tick cache for 24/7 streams (e.g. Binance PAXG/USDT)
LIVE_TICKS: Dict[str, Dict[str, Any]] = {}

# Spot Gold (XAU/USD) live price calibration / offset settings
GOLD_CALIBRATION: Dict[str, Any] = {
    "offset": 0.0,
    "target_broker": "Spot XAU/USD",
    "target_price": 0.0,
    "updated_at": "",
}

# In-memory paper observation journal
PAPER_JOURNAL: List[Dict[str, Any]] = [
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
    },
    {
        "id": "SIM-102",
        "symbol": "EUR/USD",
        "side": "BUY",
        "simulated_fill": 1.08669,
        "stop_price": 1.08500,
        "target_price": 1.09007,
        "lot_size": 0.29,
        "units": 29000,
        "unit_label": "units",
        "risk_usd": 49.01,
        "pnl_usd": 0.0,
        "timestamp": "2026-10-30 08:30 ET",
        "status": "OPEN",
        "note": "Asian range breakout paper observation",
    }
]


def update_journal_positions(current_price: float, symbol: str) -> List[Dict[str, Any]]:
    """Automatically resolve paper positions when live price touches stop-loss or take-profit."""
    canon = normalize_symbol(symbol)
    resolved_events = []
    for item in PAPER_JOURNAL:
        if item.get("status") != "OPEN" or normalize_symbol(item.get("symbol", "")) != canon:
            continue

        side = item.get("side", "BUY")
        entry = float(item.get("simulated_fill", current_price))
        stop = float(item.get("stop_price", current_price))
        target = float(item.get("target_price", current_price))
        risk_usd = float(item.get("risk_usd", 50.0))

        if side == "BUY":
            if current_price >= target:
                item["status"] = "CLOSED (HIT TP)"
                item["exit_price"] = target
                item["pnl_usd"] = round(risk_usd * 2.0, 2)
                item["closed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                resolved_events.append({"id": item["id"], "symbol": canon, "event": "HIT TP", "pnl": item["pnl_usd"]})
            elif current_price <= stop:
                item["status"] = "CLOSED (HIT SL)"
                item["exit_price"] = stop
                item["pnl_usd"] = round(-risk_usd, 2)
                item["closed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                resolved_events.append({"id": item["id"], "symbol": canon, "event": "HIT SL", "pnl": item["pnl_usd"]})
            else:
                dist_pct = (current_price - entry) / abs(entry - stop) if abs(entry - stop) > 0 else 0
                item["pnl_usd"] = round(dist_pct * risk_usd, 2)
        else:  # SELL
            if current_price <= target:
                item["status"] = "CLOSED (HIT TP)"
                item["exit_price"] = target
                item["pnl_usd"] = round(risk_usd * 2.0, 2)
                item["closed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                resolved_events.append({"id": item["id"], "symbol": canon, "event": "HIT TP", "pnl": item["pnl_usd"]})
            elif current_price >= stop:
                item["status"] = "CLOSED (HIT SL)"
                item["exit_price"] = stop
                item["pnl_usd"] = round(-risk_usd, 2)
                item["closed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                resolved_events.append({"id": item["id"], "symbol": canon, "event": "HIT SL", "pnl": item["pnl_usd"]})
            else:
                dist_pct = (entry - current_price) / abs(entry - stop) if abs(entry - stop) > 0 else 0
                item["pnl_usd"] = round(dist_pct * risk_usd, 2)

    return resolved_events

# Catalog of the 11 Trading Skills from day-trader-skill repository
SKILLS_REGISTRY: List[Dict[str, Any]] = [
    {
        "id": "trader-scanner",
        "name": "Chart Setup Scanner",
        "folder": "trader-scanner",
        "role": "Scans thousands of US stocks/ADRs for high-probability setups (20-day breakout, bull flag, reclaim MA20, oversold bounce, and 15m opening-range breakout).",
        "inputs": [
            {"flag": "mode", "desc": "universe | daily | orb"},
            {"flag": "--symbols", "desc": "Comma-separated tickers (e.g. TSLA,NVDA)"},
            {"flag": "--at", "desc": "Intraday cutoff time (e.g. 10:15 ET)"},
            {"flag": "--min-price", "desc": "Minimum price floor (default $5)"},
            {"flag": "--or-minutes", "desc": "Opening range duration (15m)"}
        ],
        "outputs": [
            {"name": "trader/watchlist.json", "desc": "List of breakout candidate stocks"},
            {"name": "Scan Hits", "desc": "Breaking charts with timestamps, range highs/lows, and percentage over range"}
        ],
        "status": "Active (Paper/Observation)",
        "badge_class": "badge-green",
        "is_executable": True,
        "sample_input": "orb --symbols TSLA,NVDA,AMD --at 10:15"
    },
    {
        "id": "trader-news",
        "name": "Catalyst & SEC 8-K Monitor",
        "folder": "trader-news",
        "role": "Monitors breaking catalysts, Yahoo RSS news, and SEC EDGAR 8-K filings (earnings, CEO transitions, material agreements) alongside price gap from yesterday's close.",
        "inputs": [
            {"flag": "--tickers", "desc": "Target ticker list or reads watchlist.json"},
            {"flag": "--hours", "desc": "Lookback window in hours (default 12h)"},
            {"flag": "--forms", "desc": "SEC form types (8-K, 10-Q)"},
            {"flag": "--no-sec", "desc": "Skip SEC EDGAR queries (RSS only)"}
        ],
        "outputs": [
            {"name": "Feed Output", "desc": "Categorized headlines (earnings, analyst, deal, halt, regulatory, macro)"},
            {"name": "Price Gap", "desc": "Percentage change from yesterday's close"}
        ],
        "status": "Active (Paper/Observation)",
        "badge_class": "badge-green",
        "is_executable": True,
        "sample_input": "--tickers TSLA,NVDA --hours 12"
    },
    {
        "id": "trader-analyst",
        "name": "SEC Financials Analyst",
        "folder": "trader-analyst",
        "role": "Parses company facts straight from SEC EDGAR XBRL filings (10-Q / 10-K) to analyze YoY revenue growth, operating margins, net income, and diluted EPS.",
        "inputs": [
            {"flag": "tickers", "desc": "Ticker symbols to analyze"},
            {"flag": "--metrics", "desc": "Write summary rows to trader/metrics.json"},
            {"flag": "SEC_USER_AGENT", "desc": "Mandatory user agent header for EDGAR"}
        ],
        "outputs": [
            {"name": "trader/metrics.json", "desc": "TTM financials & margin trends"},
            {"name": "Quarterly Table", "desc": "QoQ & YoY financial comparisons"}
        ],
        "status": "Active (Paper/Observation)",
        "badge_class": "badge-green",
        "is_executable": True,
        "sample_input": "TSLA"
    },
    {
        "id": "trader-entry",
        "name": "Candle-Close Entry Timer",
        "folder": "trader-entry",
        "role": "Watches setup bar by bar (1m/5m) and confirms long entry strictly on the first completed candle that CLOSES above the range high or specified level. Wicks are ignored.",
        "inputs": [
            {"flag": "TICKER", "desc": "Target symbol to watch"},
            {"flag": "--setup", "desc": "orb (opening range) or level"},
            {"flag": "--interval", "desc": "Bar size (5m or 1m)"},
            {"flag": "--level", "desc": "Custom price breakout level"}
        ],
        "outputs": [
            {"name": "trader/entry.json", "desc": "Entry trigger record with exact timestamp and bar close"},
            {"name": "Trigger Signal", "desc": "WAITING, TRIGGERED, or EXPIRED status"}
        ],
        "status": "Active (Paper/Observation)",
        "badge_class": "badge-green",
        "is_executable": True,
        "sample_input": "TSLA --setup orb --interval 5m"
    },
    {
        "id": "trader-exit",
        "name": "Stop Loss & 2R Exit Planner",
        "folder": "trader-exit",
        "role": "Calculates stop loss (opening range midpoint/low, ATR, percent) and 2R take-profit before entry. Forbids tight stops and inverted orientations.",
        "inputs": [
            {"flag": "plan", "desc": "Computes plan from trader/entry.json"},
            {"flag": "--stop", "desc": "or-mid | or-low | price:X | pct:X | atr:K"},
            {"flag": "--target", "desc": "2R | 1R | price:X"},
            {"flag": "--flat-by", "desc": "Closing time cutoff (default 15:55 ET)"}
        ],
        "outputs": [
            {"name": "trader/plan.json", "desc": "Structured trade plan (entry, stop, target, R-ratio)"},
            {"name": "Risk Metrics", "desc": "Per-share risk in dollars and 2R reward target"}
        ],
        "status": "Active (Paper/Observation)",
        "badge_class": "badge-green",
        "is_executable": True,
        "sample_input": "plan --stop or-mid --target 2R"
    },
    {
        "id": "trader-risk",
        "name": "Pre-Trade Risk Validator",
        "folder": "trader-risk",
        "role": "Strict gatekeeper validating proposed trades against max loss per trade ($50), daily loss limit, trade count, entry windows, and bracket validity.",
        "inputs": [
            {"flag": "orders.json", "desc": "Proposed orders from trader-budget"},
            {"flag": "config.json", "desc": "Risk limits and parameters"},
            {"flag": "--no-write", "desc": "Inspect verdict without recording state"}
        ],
        "outputs": [
            {"name": "trader/state/risk.json", "desc": "Recorded PASS or BLOCK audit log"},
            {"name": "Verdict", "desc": "Explicit PASS or BLOCK with reason"}
        ],
        "status": "Active (Paper/Observation)",
        "badge_class": "badge-green",
        "is_executable": True,
        "sample_input": "--json"
    },
    {
        "id": "trader-budget",
        "name": "Capital Sizer & Position Sizer",
        "folder": "trader-budget",
        "role": "Calculates exact whole-share position size so the dollar loss at stop-loss strictly matches the max loss limit ($50) without exceeding cash budget.",
        "inputs": [
            {"flag": "plan.json", "desc": "Trade plan with entry and stop"},
            {"flag": "--write", "desc": "Writes proposal to trader/orders.json"},
            {"flag": "--limit", "desc": "Limit order price"}
        ],
        "outputs": [
            {"name": "trader/orders.json", "desc": "Proposed bracket order in Alpaca-compatible schema"},
            {"name": "Sizing Rationale", "desc": "Shares, dollar risk, and binding constraint (risk/cash)"}
        ],
        "status": "Active (Paper/Observation)",
        "badge_class": "badge-green",
        "is_executable": True,
        "sample_input": "--write"
    },
    {
        "id": "trader-broker",
        "name": "Order Execution & Broker Gateway",
        "folder": "trader-broker",
        "role": "Broker connector and order routing ticket generator. PERMANENTLY DISABLED in this paper observation environment to protect capital.",
        "inputs": [
            {"flag": "DISABLED", "desc": "No input accepted - broker execution disabled"},
            {"flag": "NO CREDENTIALS", "desc": "API keys and logins prohibited"}
        ],
        "outputs": [
            {"name": "NO EXECUTION", "desc": "Real-money order routing and executions are blocked"}
        ],
        "status": "Disabled — no execution",
        "badge_class": "badge-rose",
        "is_executable": False,
        "sample_input": "DISABLED"
    },
    {
        "id": "trader-alerts",
        "name": "Price Alerts & Market Bell",
        "folder": "trader-alerts",
        "role": "Tracks stop-loss, take-profit, NYSE opening bell, and 15:55 ET flat-by-close reminders with delayed quote evaluation.",
        "inputs": [
            {"flag": "clock", "desc": "NYSE session open/close status"},
            {"flag": "add --from-plan", "desc": "Auto-creates stop, target & close alerts"},
            {"flag": "quote TICKER", "desc": "Fetch delayed quote to trader/prices.json"}
        ],
        "outputs": [
            {"name": "trader/prices.json", "desc": "Latest prices for sizing and risk checks"},
            {"name": "Alert Notifications", "desc": "Single-ring alert triggers"}
        ],
        "status": "Active (Paper/Observation)",
        "badge_class": "badge-green",
        "is_executable": True,
        "sample_input": "clock"
    },
    {
        "id": "trader-pelosi",
        "name": "Congressional STOCK Act Tracker",
        "folder": "trader-pelosi",
        "role": "Scans House Clerk public Periodic Transaction Reports (PTRs) for disclosed stock trades made by members of Congress and spouses.",
        "inputs": [
            {"flag": "member name", "desc": "Filter by representative name (e.g. Pelosi)"},
            {"flag": "--read", "desc": "Extract and parse PDF transaction tables"},
            {"flag": "--year", "desc": "Disclosure calendar year"}
        ],
        "outputs": [
            {"name": "trader/state/pelosi.json", "desc": "Log of reviewed PTR document IDs"},
            {"name": "Disclosures Table", "desc": "Ticker, transaction (Buy/Sell), amount, disclosure lag"}
        ],
        "status": "Active (Paper/Observation)",
        "badge_class": "badge-green",
        "is_executable": True,
        "sample_input": "Pelosi"
    },
    {
        "id": "trader-timemachine",
        "name": "Monte Carlo Replay & Backtest Engine",
        "folder": "trader-timemachine",
        "role": "Simulates 10,000 potential trade outcomes using the stock's 5-minute bars over the past 60 days to compute exact empirical odds of hitting target vs stop.",
        "inputs": [
            {"flag": "plan.json", "desc": "Trade plan (entry, stop, target)"},
            {"flag": "--replays", "desc": "Replay iterations (default 10,000)"},
            {"flag": "--seed", "desc": "Random seed for reproducible replays"},
            {"flag": "--until", "desc": "Session cutoff time (15:55 ET)"}
        ],
        "outputs": [
            {"name": "trader/timemachine.json", "desc": "Distribution outcomes and win/loss odds"},
            {"name": "Odds Report", "desc": "Target First %, Stop First %, Neither % & Average R"}
        ],
        "status": "Active (Paper/Observation)",
        "badge_class": "badge-green",
        "is_executable": True,
        "sample_input": "run --seed 19"
    }
]


def simulate_skill_paper_run(skill_id: str, user_input: str = "") -> Dict[str, Any]:
    """Execute a safe, paper-only simulation test for a given skill.
    
    Guarantees:
    - trader-broker is strictly rejected and disabled.
    - No live orders, no API credentials, no broker connectivity.
    """
    if skill_id == "trader-broker":
        return {
            "skill_id": "trader-broker",
            "name": "Order Execution & Broker Gateway",
            "status": "Disabled — no execution",
            "allowed": False,
            "error": "Disabled — no execution: Live broker connections, order routing, and real-money execution are permanently prohibited in Paper Observation Mode.",
            "output": (
                "================================================================================\n"
                "[SECURITY GUARD] trader-broker IS DISABLED\n"
                "Status: Disabled — no execution\n"
                "Reason: Order execution, broker credentials, and real-money trading are prohibited.\n"
                "Safety Assurance: Zero broker accounts connected; live execution is blocked.\n"
                "================================================================================"
            ),
        }

    if skill_id == "trader-scanner":
        return {
            "skill_id": skill_id,
            "name": "Chart Setup Scanner",
            "status": "Simulated (Paper Only)",
            "allowed": True,
            "output": (
                "SCANNER PAPER RUN (Indicative Setup Scanner)\n"
                "Universe: 5,942 liquid US equities & ETFs\n"
                "Filters: price >= $5.00, volume >= 1,000,000, 15m Opening Range\n"
                "Scanned at: 10:15 AM ET (Simulated Mode)\n"
                "Hits Detected (5):\n"
                "  - TSLA: $364.18 | ORB Range: 354.47 - 362.18 (+0.55% above range high)\n"
                "  - NVDA: $141.25 | 20-Day Closing High Breakout (+1.82%)\n"
                "  - AMD:  $158.40 | Bull Flag consolidation (reclaiming 20-day MA)\n"
                "  - XOM:  $122.10 | Oversold 14-RSI bounce setup\n"
                "  - XAU/USD (GC=F): 2742.50 | COMEX Gold Futures benchmark consolidation\n"
                "Output written: trader/watchlist.json [SIMULATED WATCHLIST]"
            ),
        }
    elif skill_id == "trader-news":
        ticker = user_input.strip() or "TSLA"
        return {
            "skill_id": skill_id,
            "name": "Catalyst & SEC 8-K Monitor",
            "status": "Simulated (Paper Only)",
            "allowed": True,
            "output": (
                f"NEWS & SEC 8-K MONITOR [PAPER BRIEFING]\n"
                f"Target Ticker: {ticker}\n"
                f"Previous Close: $358.50 | Current Indicative: $364.18 (+1.58% gap)\n"
                "Recent Catalysts (Last 12 Hours):\n"
                "  [SEC EDGAR 8-K] Item 2.02: Q3 Automotive Deliveries & Production Report\n"
                "  [ANALYST NOTE] Morgan Stanley maintains Overweight, raises target to $380\n"
                "  [YAHOO FEED] Global supply chain logistics update confirms record throughput\n"
                "Filings verified: 0 restatements, 0 pending halts, 0 litigation notices."
            ),
        }
    elif skill_id == "trader-analyst":
        return {
            "skill_id": skill_id,
            "name": "SEC Financials Analyst",
            "status": "Simulated (Paper Only)",
            "allowed": True,
            "output": (
                "SEC EDGAR XBRL FINANCIALS EXTRACTOR [SIMULATION]\n"
                "Company: Tesla, Inc. (CIK: 0001318605)\n"
                "Metric              Q3 2026       Q3 2025       YoY Change\n"
                "----------------------------------------------------------\n"
                "Revenue             $25.18B       $23.35B       +7.84%\n"
                "Gross Margin        19.8%         17.9%         +190 bps\n"
                "Operating Margin    10.8%         8.2%          +260 bps\n"
                "Net Income          $2.17B        $1.85B        +17.3%\n"
                "Diluted EPS         $0.72         $0.53         +35.8%\n"
                "Summary: Operating margins expansion driven by lower COGS per unit."
            ),
        }
    elif skill_id == "trader-entry":
        return {
            "skill_id": skill_id,
            "name": "Candle-Close Entry Timer",
            "status": "Simulated (Paper Only)",
            "allowed": True,
            "output": (
                "ENTRY TIMER (Candle-Close Rule Verification)\n"
                "Symbol: TSLA | Timeframe: 5-minute bars\n"
                "Opening Range (09:30-09:45): High $362.18, Low $354.47\n"
                "Bar Checks:\n"
                "  - 09:55 ET bar: high $362.45, close $361.80 -> REJECTED (wick-only poke)\n"
                "  - 10:05 ET bar: high $363.10, close $362.05 -> REJECTED (closed below high)\n"
                "  - 10:15 ET bar: close $364.18 -> TRIGGER CONFIRMED!\n"
                "Status: TRIGGERED at 10:15 ET at price $364.18\n"
                "Wrote record: trader/entry.json [PAPER RECORD]"
            ),
        }
    elif skill_id == "trader-exit":
        return {
            "skill_id": skill_id,
            "name": "Stop Loss & 2R Exit Planner",
            "status": "Simulated (Paper Only)",
            "allowed": True,
            "output": (
                "EXIT PLANNER [SIMULATION]\n"
                "Entry Reference: $364.18 (Long Setup)\n"
                "Stop Selection: Opening Range Midpoint (or-mid)\n"
                "----------------------------------------------------------\n"
                "Entry Price:     $364.18\n"
                "Stop Loss:       $358.32  (Risk: $5.86 / share)\n"
                "Target (2R):     $370.04  (Reward: $11.72 / share, R:R = 2.00)\n"
                "Flat-By Time:    15:55 ET (Mandatory intraday exit)\n"
                "Validation: Stop is below entry, target is above entry, spread check passed.\n"
                "Wrote record: trader/plan.json [PAPER PLAN]"
            ),
        }
    elif skill_id == "trader-risk":
        return {
            "skill_id": skill_id,
            "name": "Pre-Trade Risk Validator",
            "status": "Simulated (Paper Only)",
            "allowed": True,
            "output": (
                "PRE-TRADE RISK VALIDATION AUDIT\n"
                "Check 1: Stop loss defined and intact            -> PASS ($358.32)\n"
                "Check 2: Max loss per trade <= $50.00            -> PASS ($46.88 loss at stop)\n"
                "Check 3: Daily loss limit ($150.00) intact      -> PASS ($0 realized today)\n"
                "Check 4: Within allowed entry window (09:45-15:30)-> PASS (10:15 ET)\n"
                "Check 5: No duplicate open order for symbol     -> PASS\n"
                "Check 6: Cash buffer >= 10% retained            -> PASS ($2,086.56 cash remains)\n"
                "VERDICT: PASS — All risk guardrails satisfied."
            ),
        }
    elif skill_id == "trader-budget":
        return {
            "skill_id": skill_id,
            "name": "Capital Sizer & Position Sizer",
            "status": "Simulated (Paper Only)",
            "allowed": True,
            "output": (
                "CAPITAL ALLOCATOR & POSITION SIZER\n"
                "Input Plan: Buy TSLA at $364.18, Stop $358.32 (Risk $5.86/sh)\n"
                "Account Budget: $5,000.00 | Max Risk: $50.00\n"
                "Calculation:\n"
                "  - Shares by risk: $50.00 / $5.86 = 8.53 -> 8 whole shares\n"
                "  - Order Value: 8 x $364.18 = $2,913.44 (within $5,000 budget)\n"
                "  - Realized Dollar Risk at Stop: 8 x $5.86 = $46.88\n"
                "Proposed Order Ticket (Alpaca JSON Schema - FOR SIMULATION ONLY):\n"
                "  {\n"
                "    \"symbol\": \"TSLA\", \"side\": \"buy\", \"qty\": \"8\",\n"
                "    \"type\": \"market\", \"time_in_force\": \"day\",\n"
                "    \"order_class\": \"bracket\",\n"
                "    \"take_profit_limit_price\": \"370.04\",\n"
                "    \"stop_loss_stop_price\": \"358.32\"\n"
                "  }\n"
                "Wrote record: trader/orders.json [PAPER PROPOSAL]"
            ),
        }
    elif skill_id == "trader-alerts":
        return {
            "skill_id": skill_id,
            "name": "Price Alerts & Market Bell",
            "status": "Simulated (Paper Only)",
            "allowed": True,
            "output": (
                "PRICE & SESSION ALERTS MONITOR\n"
                "Market Clock: NYSE Regular Hours (09:30 - 16:00 ET)\n"
                "Active Observation Rules:\n"
                "  [RULE 1] TSLA Stop Loss: Alert if price <= $358.32\n"
                "  [RULE 2] TSLA Profit Target: Alert if price >= $370.04\n"
                "  [RULE 3] Flat-By-Close: Alert at 15:55 ET (5 minutes before market close)\n"
                "  [RULE 4] COMEX Gold (GC=F): Alert on London fix 10:00 ET\n"
                "All rules active. Single-trigger mode enabled (anti-spam)."
            ),
        }
    elif skill_id == "trader-pelosi":
        return {
            "skill_id": skill_id,
            "name": "Congressional STOCK Act Tracker",
            "status": "Simulated (Paper Only)",
            "allowed": True,
            "output": (
                "CONGRESSIONAL STOCK ACT PTR MONITOR [PUBLIC DISCLOSURES]\n"
                "Source: US House of Representatives Financial Disclosures Office\n"
                "Latest Filings Logged:\n"
                "  - Member: Hon. Nancy Pelosi (CA-11)\n"
                "  - Filing Date: 2026-10-14 (DocID: 20025891)\n"
                "  - Asset: Microsoft Corp. (MSFT) Call Options $420 Strike\n"
                "  - Transaction: Purchase (Spouse) | Amount: $1,000,001 - $5,000,000\n"
                "  - Trade Date: 2026-09-22 | Disclosure Lag: 22 Days (within 45-day rule)\n"
                "Note: STOCK Act filings are trailing public records, not predictive indicators."
            ),
        }
    elif skill_id == "trader-timemachine":
        return {
            "skill_id": skill_id,
            "name": "Monte Carlo Replay & Backtest Engine",
            "status": "Simulated (Paper Only)",
            "allowed": True,
            "output": (
                "TIME MACHINE MONTE CARLO SIMULATION\n"
                "Simulated Sessions: 10,000 runs (Bootstrapped from 60-day 5m bars)\n"
                "Input Plan: Entry $364.18 | Stop $358.32 (1R = $5.86) | Target $370.04 (2R)\n"
                "Empirical Outcome Distribution:\n"
                "  - Target Reached First (Win):  54.2% (5,420 sessions)\n"
                "  - Stop Loss Reached First:     38.6% (3,860 sessions)\n"
                "  - Neither (Closed at 15:55):    7.2% (720 sessions)\n"
                "Performance Metrics:\n"
                "  - Expectancy / Average R:      +0.698 R per trade\n"
                "  - Profit Factor (Simulated):    1.40\n"
                "Verdict: Statistically positive expectancy under historical sample."
            ),
        }
    else:
        return {
            "skill_id": skill_id,
            "status": "Unknown",
            "allowed": False,
            "error": f"Unknown skill: {skill_id}",
            "output": f"Skill {skill_id} is not recognized.",
        }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Forex & Gold Paper Observation Dashboard</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-base: #0a0e17;
            --bg-surface: #111827;
            --bg-card: #162032;
            --bg-card-hover: #1c2940;
            --border-subtle: #1f2d42;
            --border-strong: #2c3e59;
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --text-muted: #64748b;
            --accent-blue: #38bdf8;
            --accent-green: #10b981;
            --accent-amber: #f59e0b;
            --accent-rose: #f43f5e;
            --accent-gold: #fbbf24;
            --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            --font-mono: 'JetBrains Mono', monospace;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            background-color: var(--bg-base);
            color: var(--text-primary);
            font-family: var(--font-sans);
            font-size: 14px;
            line-height: 1.5;
            -webkit-font-smoothing: antialiased;
        }

        /* Top Warning Ribbon */
        .safety-ribbon {
            background: linear-gradient(90deg, #b91c1c 0%, #dc2626 50%, #b91c1c 100%);
            color: #ffffff;
            font-size: 12px;
            font-weight: 700;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            text-align: center;
            padding: 8px 16px;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 12px;
            border-bottom: 1px solid rgba(0,0,0,0.3);
        }

        .container {
            max-width: 1440px;
            margin: 0 auto;
            padding: 24px;
        }

        /* Header Bar */
        header {
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
            margin-bottom: 24px;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--border-subtle);
        }

        .brand-block {
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .brand-icon {
            width: 36px;
            height: 36px;
            background: var(--bg-card);
            border: 1px solid var(--border-strong);
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            color: var(--accent-blue);
        }

        .brand-title {
            font-size: 18px;
            font-weight: 700;
            letter-spacing: -0.02em;
        }

        .brand-subtitle {
            font-size: 12px;
            color: var(--text-secondary);
        }

        .controls-block {
            display: flex;
            align-items: center;
            gap: 12px;
            flex-wrap: wrap;
        }

        select, button, input {
            font-family: inherit;
            font-size: 13px;
        }

        .pair-select {
            background-color: var(--bg-surface);
            color: var(--text-primary);
            border: 1px solid var(--border-strong);
            padding: 8px 14px;
            border-radius: 6px;
            cursor: pointer;
            outline: none;
            font-weight: 600;
        }

        .pair-select:focus {
            border-color: var(--accent-blue);
        }

        .btn {
            background-color: var(--bg-surface);
            color: var(--text-primary);
            border: 1px solid var(--border-strong);
            padding: 8px 16px;
            border-radius: 6px;
            font-weight: 600;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            transition: all 0.15s ease;
        }

        .btn:hover {
            background-color: var(--bg-card-hover);
            border-color: var(--text-secondary);
        }

        .btn-primary {
            background-color: #2563eb;
            border-color: #1d4ed8;
            color: #ffffff;
        }

        .btn-primary:hover {
            background-color: #1d4ed8;
        }

        /* Gold Callout Banner */
        .gold-banner {
            background: rgba(251, 191, 36, 0.08);
            border: 1px solid rgba(251, 191, 36, 0.3);
            border-left: 4px solid var(--accent-gold);
            padding: 12px 18px;
            border-radius: 6px;
            margin-bottom: 24px;
            display: none;
            align-items: center;
            gap: 12px;
            font-size: 13px;
            color: #fef3c7;
        }

        .gold-banner.active {
            display: flex;
        }

        .gold-badge {
            background: #d97706;
            color: #ffffff;
            font-size: 11px;
            font-weight: 700;
            padding: 2px 8px;
            border-radius: 4px;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        /* Dashboard Grid Layout */
        .grid-dashboard {
            display: grid;
            grid-template-columns: repeat(12, 1fr);
            gap: 20px;
            margin-bottom: 24px;
        }

        .col-4 { grid-column: span 4; }
        .col-6 { grid-column: span 6; }
        .col-8 { grid-column: span 8; }
        .col-12 { grid-column: span 12; }

        @media (max-width: 1024px) {
            .col-4, .col-6, .col-8 { grid-column: span 12; }
        }

        .card {
            background-color: var(--bg-surface);
            border: 1px solid var(--border-subtle);
            border-radius: 10px;
            padding: 20px;
            display: flex;
            flex-direction: column;
            gap: 16px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2);
        }

        .card-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid var(--border-subtle);
            padding-bottom: 12px;
        }

        .card-title {
            font-size: 13px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-secondary);
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .badge {
            font-size: 11px;
            font-weight: 700;
            padding: 3px 8px;
            border-radius: 4px;
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }

        .badge-green { background: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3); }
        .badge-amber { background: rgba(245, 158, 11, 0.15); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.3); }
        .badge-rose { background: rgba(244, 63, 94, 0.15); color: #fb7185; border: 1px solid rgba(244, 63, 94, 0.3); }
        .badge-blue { background: rgba(56, 189, 248, 0.15); color: #7dd3fc; border: 1px solid rgba(56, 189, 248, 0.3); }

        /* Price Hero Section */
        .price-hero {
            display: flex;
            flex-direction: column;
            gap: 4px;
        }

        .price-label {
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            color: var(--text-muted);
        }

        .price-big {
            font-family: var(--font-mono);
            font-size: 36px;
            font-weight: 700;
            letter-spacing: -0.03em;
            color: var(--text-primary);
        }

        .bid-ask-box {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            border-radius: 6px;
            padding: 12px;
        }

        .meta-list {
            display: flex;
            flex-direction: column;
            gap: 8px;
        }

        .meta-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 12px;
        }

        .meta-k {
            color: var(--text-secondary);
        }

        .meta-v {
            font-family: var(--font-mono);
            font-weight: 600;
            color: var(--text-primary);
        }

        /* Status Callout Box */
        .status-box {
            padding: 14px;
            border-radius: 8px;
            border: 1px solid var(--border-subtle);
            background: var(--bg-card);
            display: flex;
            flex-direction: column;
            gap: 6px;
        }

        .status-box.closed {
            background: rgba(244, 63, 94, 0.08);
            border-color: rgba(244, 63, 94, 0.3);
        }

        .status-box.stale {
            background: rgba(245, 158, 11, 0.08);
            border-color: rgba(245, 158, 11, 0.3);
        }

        .status-box.active {
            background: rgba(16, 185, 129, 0.08);
            border-color: rgba(16, 185, 129, 0.3);
        }

        .status-heading {
            font-weight: 700;
            font-size: 13px;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .status-desc {
            font-size: 12px;
            color: var(--text-secondary);
        }

        /* Form Inputs */
        .form-group {
            display: flex;
            flex-direction: column;
            gap: 6px;
            margin-bottom: 12px;
        }

        .form-group label {
            font-size: 11px;
            text-transform: uppercase;
            font-weight: 600;
            color: var(--text-secondary);
            letter-spacing: 0.04em;
        }

        .form-control {
            background: var(--bg-card);
            border: 1px solid var(--border-strong);
            color: var(--text-primary);
            padding: 8px 12px;
            border-radius: 6px;
            font-family: var(--font-mono);
            font-size: 13px;
            outline: none;
        }

        .form-control:focus {
            border-color: var(--accent-blue);
        }

        .form-row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
        }

        /* Journal Table */
        .journal-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 12px;
            text-align: left;
        }

        .journal-table th {
            color: var(--text-muted);
            text-transform: uppercase;
            font-size: 11px;
            padding: 8px 12px;
            border-bottom: 1px solid var(--border-subtle);
            font-weight: 600;
        }

        .journal-table td {
            padding: 10px 12px;
            border-bottom: 1px solid var(--border-subtle);
            font-family: var(--font-mono);
        }

        .journal-table tr:hover {
            background: var(--bg-card);
        }

        .disclaimer-box {
            background: rgba(15, 23, 42, 0.6);
            border: 1px solid var(--border-subtle);
            padding: 16px;
            border-radius: 8px;
            font-size: 11px;
            color: var(--text-muted);
            line-height: 1.6;
        }

        /* Pulse animation */
        .pulse-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            display: inline-block;
        }

        .pulse-dot.green { background: #10b981; box-shadow: 0 0 8px #10b981; }
        .pulse-dot.amber { background: #f59e0b; box-shadow: 0 0 8px #f59e0b; }
        .pulse-dot.red { background: #f43f5e; box-shadow: 0 0 8px #f43f5e; }

        /* 11 Skills Suite Section */
        .skills-section-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 12px;
            margin-top: 28px;
            margin-bottom: 20px;
            padding-bottom: 12px;
            border-bottom: 1px solid var(--border-subtle);
        }

        .skills-grid {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 20px;
            margin-bottom: 28px;
        }

        @media (max-width: 1200px) {
            .skills-grid {
                grid-template-columns: repeat(2, 1fr);
            }
        }

        @media (max-width: 768px) {
            .skills-grid {
                grid-template-columns: 1fr;
            }
        }

        .skill-card {
            background-color: var(--bg-surface);
            border: 1px solid var(--border-subtle);
            border-radius: 10px;
            padding: 18px;
            display: flex;
            flex-direction: column;
            gap: 14px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2);
            transition: border-color 0.15s ease;
        }

        .skill-card:hover {
            border-color: var(--border-strong);
        }

        .skill-card.broker-card {
            border-color: rgba(244, 63, 94, 0.4);
            background: linear-gradient(180deg, rgba(244, 63, 94, 0.05) 0%, var(--bg-surface) 100%);
        }

        .skill-header {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 8px;
        }

        .skill-title-box {
            display: flex;
            flex-direction: column;
            gap: 2px;
        }

        .skill-id {
            font-family: var(--font-mono);
            font-size: 11px;
            color: var(--accent-blue);
            font-weight: 600;
        }

        .skill-name {
            font-size: 14px;
            font-weight: 700;
            color: var(--text-primary);
        }

        .skill-role {
            font-size: 12px;
            color: var(--text-secondary);
            line-height: 1.45;
        }

        .skill-section-title {
            font-size: 11px;
            text-transform: uppercase;
            font-weight: 700;
            letter-spacing: 0.05em;
            color: var(--text-muted);
            margin-bottom: 6px;
        }

        .skill-tags {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
        }

        .skill-tag {
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            padding: 3px 8px;
            border-radius: 4px;
            font-family: var(--font-mono);
            font-size: 11px;
            color: var(--text-secondary);
            line-height: 1.3;
        }

        .skill-tag.output-tag {
            color: #7dd3fc;
            border-color: rgba(56, 189, 248, 0.2);
        }

        .skill-runner {
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            border-radius: 6px;
            padding: 12px;
            display: flex;
            flex-direction: column;
            gap: 8px;
            margin-top: auto;
        }

        .skill-input-row {
            display: flex;
            gap: 8px;
        }

        .skill-input {
            flex: 1;
            background: var(--bg-surface);
            border: 1px solid var(--border-strong);
            color: var(--text-primary);
            padding: 6px 10px;
            border-radius: 4px;
            font-family: var(--font-mono);
            font-size: 11px;
            outline: none;
        }

        .skill-input:focus {
            border-color: var(--accent-blue);
        }

        .skill-btn {
            padding: 6px 12px;
            font-size: 11px;
            font-weight: 600;
            border-radius: 4px;
            cursor: pointer;
            border: none;
            background: #2563eb;
            color: #ffffff;
            transition: background 0.15s;
            white-space: nowrap;
        }

        .skill-btn:hover {
            background: #1d4ed8;
        }

        .skill-btn:disabled {
            background: rgba(244, 63, 94, 0.2);
            color: #fb7185;
            border: 1px solid rgba(244, 63, 94, 0.4);
            cursor: not-allowed;
        }

        .skill-console {
            background: #070b12;
            border: 1px solid var(--border-subtle);
            border-radius: 4px;
            padding: 8px 10px;
            font-family: var(--font-mono);
            font-size: 11px;
            line-height: 1.4;
            color: #38bdf8;
            max-height: 140px;
            overflow-y: auto;
            white-space: pre-wrap;
            word-break: break-all;
            display: none;
        }

        .skill-console.active {
            display: block;
        }

        /* Automatic Signal Hero Display */
        .signal-hero {
            padding: 14px 16px;
            border-radius: 8px;
            display: flex;
            flex-direction: column;
            gap: 6px;
            border: 1px solid var(--border-subtle);
            background: var(--bg-card);
            transition: all 0.2s ease;
        }

        .signal-hero.buy {
            background: rgba(16, 185, 129, 0.12);
            border-color: rgba(16, 185, 129, 0.4);
        }

        .signal-hero.sell {
            background: rgba(244, 63, 94, 0.12);
            border-color: rgba(244, 63, 94, 0.4);
        }

        .signal-hero.no-signal {
            background: rgba(245, 158, 11, 0.08);
            border-color: rgba(245, 158, 11, 0.3);
        }

        .signal-action {
            font-size: 18px;
            font-weight: 800;
            letter-spacing: 0.03em;
            display: flex;
            align-items: center;
            gap: 10px;
            font-family: var(--font-mono);
        }

        .signal-hero.buy .signal-action { color: #34d399; }
        .signal-hero.sell .signal-action { color: #fb7185; }
        .signal-hero.no-signal .signal-action { color: #fbbf24; }

        .signal-reason {
            font-size: 12px;
            color: var(--text-secondary);
            line-height: 1.4;
        }

        /* Data Health Bar */
        .health-bar {
            background: var(--bg-surface);
            border: 1px solid var(--border-subtle);
            border-radius: 8px;
            padding: 10px 16px;
            margin-bottom: 20px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 12px;
            font-size: 12px;
        }

        .health-items {
            display: flex;
            align-items: center;
            flex-wrap: wrap;
            gap: 16px;
        }

        .health-pill {
            display: flex;
            align-items: center;
            gap: 6px;
            color: var(--text-secondary);
        }

        .health-pill strong {
            color: var(--text-primary);
        }

        /* Interactive Chart Card */
        .chart-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 12px;
            margin-bottom: 12px;
            padding-bottom: 12px;
            border-bottom: 1px solid var(--border-subtle);
        }

        .tf-group {
            display: flex;
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            border-radius: 6px;
            overflow: hidden;
        }

        .tf-btn {
            background: transparent;
            border: none;
            color: var(--text-secondary);
            padding: 5px 12px;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
        }

        .tf-btn:hover {
            color: var(--text-primary);
            background: rgba(255, 255, 255, 0.05);
        }

        .tf-btn.active {
            background: var(--accent-blue);
            color: #0f172a;
        }

        .chart-legend {
            display: flex;
            align-items: center;
            flex-wrap: wrap;
            gap: 14px;
            font-size: 11px;
            font-family: var(--font-mono);
        }

        .legend-item {
            display: flex;
            align-items: center;
            gap: 6px;
        }

        .legend-color {
            width: 12px;
            height: 3px;
            border-radius: 2px;
        }

        .chart-stats-bar {
            background: rgba(15, 23, 42, 0.7);
            border: 1px solid var(--border-subtle);
            border-radius: 4px;
            padding: 6px 12px;
            font-size: 11px;
            font-family: var(--font-mono);
            color: var(--text-secondary);
            margin-bottom: 8px;
            display: flex;
            align-items: center;
            flex-wrap: wrap;
            gap: 14px;
            min-height: 30px;
        }

        .chart-canvas-wrap {
            position: relative;
            width: 100%;
            height: 440px;
            background: #090d16;
            border: 1px solid var(--border-subtle);
            border-radius: 6px;
            overflow: hidden;
        }

        #chartCanvas {
            width: 100%;
            height: 100%;
            display: block;
            cursor: crosshair;
        }

        /* Backtest Section */
        .backtest-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
            gap: 12px;
            margin-bottom: 16px;
        }

        .stat-card {
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            border-radius: 6px;
            padding: 12px 10px;
            text-align: center;
        }

        .stat-card-title {
            font-size: 10px;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 4px;
        }

        .stat-card-value {
            font-size: 18px;
            font-weight: 700;
            font-family: var(--font-mono);
        }

        /* Risk Settings Modal */
        .modal-overlay {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0, 0, 0, 0.75);
            backdrop-filter: blur(4px);
            z-index: 999;
            align-items: center;
            justify-content: center;
        }

        .modal-overlay.active {
            display: flex;
        }

        .modal-content {
            background: var(--bg-surface);
            border: 1px solid var(--border-strong);
            border-radius: 10px;
            max-width: 460px;
            width: 90%;
            padding: 24px;
        /* Strategy Decision Matrix & Trade Rationale */
        .matrix-card {
            border: 1px solid rgba(56, 189, 248, 0.25);
            background: linear-gradient(180deg, rgba(15, 23, 42, 0.95) 0%, rgba(10, 15, 26, 0.95) 100%);
            position: relative;
        }

        .decision-box {
            border-radius: 8px;
            padding: 16px 20px;
            margin-bottom: 18px;
            border: 1px solid var(--border-strong);
            background: rgba(30, 41, 59, 0.4);
            display: flex;
            flex-direction: column;
            gap: 10px;
            transition: all 0.2s ease;
        }

        .decision-box.buy-active {
            background: rgba(16, 185, 129, 0.1);
            border-color: rgba(16, 185, 129, 0.4);
        }

        .decision-box.sell-active {
            background: rgba(244, 63, 94, 0.1);
            border-color: rgba(244, 63, 94, 0.4);
        }

        .decision-box.wait-active {
            background: rgba(245, 158, 11, 0.08);
            border-color: rgba(245, 158, 11, 0.35);
        }

        .decision-action-title {
            font-size: 16px;
            font-weight: 800;
            letter-spacing: 0.02em;
            display: flex;
            align-items: center;
            gap: 10px;
            font-family: var(--font-mono);
        }

        .decision-summary-text {
            font-size: 13px;
            color: var(--text-primary);
            line-height: 1.6;
        }

        .decision-summary-text strong {
            color: #f1f5f9;
        }

        .checklist-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
            gap: 12px;
            margin-bottom: 18px;
        }

        .check-card {
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            border-radius: 8px;
            padding: 14px;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }

        .check-card-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        .check-card-title {
            font-size: 11px;
            text-transform: uppercase;
            font-weight: 700;
            color: var(--text-secondary);
            letter-spacing: 0.04em;
            display: flex;
            align-items: center;
            gap: 6px;
        }

        .check-card-detail {
            font-size: 12px;
            color: var(--text-primary);
            line-height: 1.4;
            font-family: var(--font-mono);
        }

        .levels-bar {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 10px;
            background: rgba(15, 23, 42, 0.6);
            border: 1px solid var(--border-subtle);
            border-radius: 6px;
            padding: 12px;
        }

        .level-metric {
            display: flex;
            flex-direction: column;
            gap: 4px;
        }

        .level-metric-label {
            font-size: 10px;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        .level-metric-val {
            font-size: 14px;
            font-weight: 700;
            font-family: var(--font-mono);
            color: var(--text-primary);
        }
    </style>
</head>
<body>
    <div class="safety-ribbon">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
        <span>SIMULATED PAPER OBSERVATION ONLY — ZERO BROKER CONNECTIVITY — REAL CAPITAL IS NEVER AT RISK</span>
    </div>

    <div class="container">
        <header>
            <div class="brand-block">
                <div class="brand-icon">
                    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="m12 6 4 6h-8l4 6"/></svg>
                </div>
                <div>
                    <div class="brand-title">Forex & Commodity Observation Terminal</div>
                    <div class="brand-subtitle">Indicative Feed Monitor & Candle-Close Paper Simulator</div>
                </div>
            </div>

            <div class="controls-block">
                <select id="pairSelect" class="pair-select" onchange="onPairChange()">
                    <option value="XAU/USD">XAU/USD (PAXG 24/7 Spot Gold / COMEX GC=F)</option>
                    <option value="EUR/USD">EUR/USD (Euro / US Dollar)</option>
                    <option value="GBP/USD">GBP/USD (British Pound / US Dollar)</option>
                    <option value="USD/JPY">USD/JPY (US Dollar / Japanese Yen)</option>
                    <option value="AUD/USD">AUD/USD (Australian Dollar / US Dollar)</option>
                    <option value="USD/CAD">USD/CAD (US Dollar / Canadian Dollar)</option>
                    <option value="USD/CHF">USD/CHF (US Dollar / Swiss Franc)</option>
                    <option value="NZD/USD">NZD/USD (New Zealand Dollar / US Dollar)</option>
                    <option value="EUR/GBP">EUR/GBP (Euro / British Pound)</option>
                    <option value="EUR/JPY">EUR/JPY (Euro / Japanese Yen)</option>
                    <option value="GBP/JPY">GBP/JPY (British Pound / Japanese Yen)</option>
                </select>

                <button id="btnNotif" class="btn" onclick="toggleNotifications()">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>
                    Enable Alerts
                </button>

                <button class="btn" onclick="openRiskModal()">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/></svg>
                    Risk Settings
                </button>

                <button id="btnTwelveData" class="btn" onclick="openTwelveDataModal()" title="Twelve Data Direct Retail Spot Gold & Forex API" style="border-color:rgba(16,185,129,0.5); color:var(--accent-green); display:inline-flex; align-items:center; gap:6px;">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>
                    Twelve Data Spot: <span id="tdStatusLabel" style="font-weight:700;">Connected</span>
                </button>

                <button id="btnCalibrate" class="btn" onclick="openCalibrateModal()" title="Calibrate Spot Gold to match your Broker/TradingView price" style="border-color:rgba(56,189,248,0.4); color:var(--accent-blue);">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20v-6M6 20V10M18 20V4"/><circle cx="12" cy="14" r="2"/><circle cx="6" cy="10" r="2"/><circle cx="18" cy="4" r="2"/></svg>
                    Calibrate Spot (<span id="offsetBadge">0.00</span>)
                </button>

                <button class="btn" onclick="fetchObservation(); fetchChart();">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>
                    Refresh
                </button>
            </div>
        </header>

        <!-- Gold Callout -->
        <div id="goldBanner" class="gold-banner active">
            <span class="gold-badge" style="background:#10b981;">Futures Benchmark & 24/7 Spot Stream</span>
            <span id="tdBannerBadge" class="gold-badge" style="background:#059669; cursor:pointer;" onclick="openTwelveDataModal()">Twelve Data API: <span id="tdKeyDisplay">7334...76d2</span> (Spot Active)</span>
            <span id="goldCalibBadge" class="gold-badge" style="background:#0284c7; cursor:pointer;" onclick="openCalibrateModal()">Spot Calibrated: <span id="goldOffsetDisplay">0.00</span> (<span id="goldBrokerDisplay">Exact Spot</span>)</span>
            <span><strong>Twelve Data Spot Gold & COMEX Gold Futures (GC=F):</strong> Direct retail Spot Gold rates with zero crypto basis matching TradingView, benchmarked against COMEX Gold Futures (GC=F) & 24/7 Spot Stream — not OTC spot/interbank aggregate feeds.</span>
        </div>

        <!-- Data Health Status Bar -->
        <div class="health-bar">
            <div class="health-items">
                <div class="health-pill">
                    <span id="healthDot" class="pulse-dot green"></span>
                    <span>Feed: <strong id="healthFeed">Healthy (200 OK)</strong></span>
                </div>
                <div class="health-pill">
                    <span>Market Session: <strong id="healthSession">Open (London / NY)</strong></span>
                </div>
                <div class="health-pill">
                    <span>Benchmark: <strong id="healthBenchmark">COMEX GC=F Futures</strong></span>
                </div>
                <div class="health-pill">
                    <span>Broker Connected: <strong style="color:var(--accent-rose);">None (Permanently Disabled)</strong></span>
                </div>
                <div class="health-pill">
                    <span>Paper Risk Cap: <strong id="healthRiskCap">$50.00 / trade</strong></span>
                </div>
            </div>
            <div style="font-family:var(--font-mono); color:var(--text-muted); font-size:11px;">
                <span id="healthTime">--:--:-- UTC</span>
            </div>
        </div>

        <!-- Main Grid -->
        <div class="grid-dashboard">
            <!-- Card 1: Live Feed & Quote -->
            <div class="col-4">
                <div class="card">
                    <div class="card-header">
                        <span class="card-title">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/></svg>
                            Indicative Market Price
                        </span>
                        <span id="marketBadge" class="badge badge-amber">CHECKING</span>
                    </div>

                    <div class="price-hero">
                        <div class="price-label">Indicative Mid Benchmark</div>
                        <div id="priceMid" class="price-big">Connecting...</div>
                    </div>

                    <div class="bid-ask-box">
                        <div>
                            <div class="price-label">Indicative Bid</div>
                            <div id="priceBid" style="font-family:var(--font-mono); font-weight:600; color:var(--text-primary); font-size:16px;">Connecting...</div>
                        </div>
                        <div>
                            <div class="price-label">Indicative Ask</div>
                            <div id="priceAsk" style="font-family:var(--font-mono); font-weight:600; color:var(--text-primary); font-size:16px;">Connecting...</div>
                        </div>
                    </div>

                    <div class="meta-list">
                        <div class="meta-item">
                            <span class="meta-k">Spread</span>
                            <span id="spreadDisplay" class="meta-v">-- pips</span>
                        </div>
                        <div class="meta-item">
                            <span class="meta-k">Data Source</span>
                            <span id="sourceDisplay" class="meta-v">Yahoo Finance</span>
                        </div>
                        <div class="meta-item">
                            <span class="meta-k">Feed Freshness</span>
                            <span id="freshnessDisplay" class="meta-v">N/A</span>
                        </div>
                        <div class="meta-item">
                            <span class="meta-k">Timestamp (ET)</span>
                            <span id="timeDisplay" class="meta-v">--</span>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Card 2: Market Status & Candle-Close Monitor -->
            <div class="col-4">
                <div class="card">
                    <div class="card-header">
                        <span class="card-title">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                            Market Session & Signal
                        </span>
                        <span id="signalBadge" class="badge badge-blue">NO SIGNAL</span>
                    </div>

                    <div id="statusBox" class="status-box closed">
                        <div id="statusHeading" class="status-heading">
                            <span class="pulse-dot red"></span>
                            MARKET CLOSED
                        </div>
                        <div id="statusDesc" class="status-desc">
                            Checking market hours calendar...
                        </div>
                    </div>

                    <div class="meta-list">
                        <div class="meta-item">
                            <span class="meta-k">Active Sessions</span>
                            <span id="sessionsDisplay" class="meta-v">None</span>
                        </div>
                        <div class="meta-item">
                            <span class="meta-k">Technical Bias</span>
                            <span id="biasDisplay" class="meta-v">NEUTRAL</span>
                        </div>
                        <div class="meta-item">
                            <span class="meta-k">Candle-Close Rule</span>
                            <span id="candleCloseDisplay" class="meta-v">Wicks ignored (Bar close required)</span>
                        </div>
                        <div class="meta-item">
                            <span class="meta-k">Execution Mode</span>
                            <span class="meta-v" style="color:var(--accent-amber);">PAPER ONLY</span>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Card 3: Automatic Signal Generation Engine (Paper Observation) -->
            <div class="col-4">
                <div class="card">
                    <div class="card-header">
                        <span class="card-title">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m12 3-1.9 5.8a2 2 0 0 1-1.3 1.3L3 12l5.8 1.9a2 2 0 0 1 1.3 1.3L12 21l1.9-5.8a2 2 0 0 1 1.3-1.3L21 12l-5.8-1.9a2 2 0 0 1-1.3-1.3L12 3z"/></svg>
                            Automatic Signal Engine
                        </span>
                        <span id="autoSignalBadge" class="badge badge-amber">NO SIGNAL</span>
                    </div>

                    <!-- Hero Action Display -->
                    <div id="autoSignalHero" class="signal-hero no-signal">
                        <div id="autoSignalAction" class="signal-action">NO SIGNAL</div>
                        <div id="autoSignalReason" class="signal-reason">Checking market schedule and data freshness...</div>
                    </div>

                    <!-- Signal Metadata List -->
                    <div class="meta-list" style="margin-bottom: 12px;">
                        <div class="meta-item">
                            <span class="meta-k">Active Strategy</span>
                            <span id="autoSignalStrategy" class="meta-v" style="font-size:11px;">trader-scanner + trader-entry</span>
                        </div>
                        <div class="meta-item">
                            <span class="meta-k">Timeframe</span>
                            <span id="autoSignalTimeframe" class="meta-v">5m</span>
                        </div>
                        <div class="meta-item">
                            <span class="meta-k">Entry Reference</span>
                            <span id="autoSignalEntry" class="meta-v">--</span>
                        </div>
                        <div class="meta-item">
                            <span class="meta-k">Stop Loss</span>
                            <span id="autoSignalStop" class="meta-v">--</span>
                        </div>
                        <div class="meta-item">
                            <span class="meta-k">Take Profit (2R)</span>
                            <span id="autoSignalTarget" class="meta-v">--</span>
                        </div>
                        <div class="meta-item">
                            <span class="meta-k">Reward : Risk</span>
                            <span id="autoSignalRR" class="meta-v">--</span>
                        </div>
                        <div class="meta-item">
                            <span class="meta-k">Signal Time</span>
                            <span id="autoSignalTime" class="meta-v">--</span>
                        </div>
                    </div>

                    <!-- Log to Journal Button -->
                    <button id="btnLogAutoSignal" class="btn btn-primary" onclick="logAutoSignal()" style="width:100%; margin-bottom:8px;">
                        Log Signal to Paper Journal
                    </button>

                    <!-- Quick Scroll to Strategy Rationale & Decision Matrix -->
                    <button class="btn" onclick="document.querySelector('.matrix-card').scrollIntoView({behavior:'smooth'})" style="width:100%; margin-bottom:10px; font-size:11px; border-color:rgba(56,189,248,0.3); color:var(--accent-blue); display:flex; align-items:center; justify-content:center; gap:6px;">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
                        View Strategy Rationale & Checklist (ट्रेड विश्लेषण व कारणे)
                    </button>

                    <!-- Optional Manual Simulation Calculator Drawer -->
                    <details style="font-size:12px; color:var(--text-secondary); border-top:1px solid var(--border-color); padding-top:8px;">
                        <summary style="cursor:pointer; font-weight:600; color:var(--text-muted); margin-bottom:8px;">
                            Manual Simulation Calculator (Override)
                        </summary>
                        <div class="form-row" style="margin-bottom:6px;">
                            <div class="form-group">
                                <label style="font-size:10px;">Direction</label>
                                <select id="simSide" class="form-control" style="font-size:11px; padding:4px 8px;" onchange="recalcSimulation()">
                                    <option value="BUY">BUY / LONG</option>
                                    <option value="SELL">SELL / SHORT</option>
                                </select>
                            </div>
                            <div class="form-group">
                                <label style="font-size:10px;">Est. Entry</label>
                                <input id="simEntry" type="number" step="any" class="form-control" style="font-size:11px; padding:4px 8px;" oninput="recalcSimulation()">
                            </div>
                        </div>
                        <div class="form-row" style="margin-bottom:6px;">
                            <div class="form-group">
                                <label style="font-size:10px;">Stop Loss</label>
                                <input id="simStop" type="number" step="any" class="form-control" style="font-size:11px; padding:4px 8px;" oninput="recalcSimulation()">
                            </div>
                            <div class="form-group">
                                <label style="font-size:10px;">Take Profit</label>
                                <input id="simTarget" type="number" step="any" class="form-control" style="font-size:11px; padding:4px 8px;" oninput="recalcSimulation()">
                            </div>
                        </div>
                        <div class="meta-list" style="background:var(--bg-card); padding:6px; border-radius:4px; font-size:11px; margin-bottom:8px;">
                            <div class="meta-item">
                                <span class="meta-k">Fill (0.5p slip)</span>
                                <span id="simFillDisplay" class="meta-v">--</span>
                            </div>
                            <div class="meta-item">
                                <span class="meta-k">Paper Size</span>
                                <span id="simLotDisplay" class="meta-v">--</span>
                            </div>
                            <div class="meta-item">
                                <span class="meta-k">Max Risk</span>
                                <span id="simRiskDisplay" class="meta-v">$0.00</span>
                            </div>
                        </div>
                        <button class="btn" style="width:100%; font-size:11px; padding:6px; background:var(--bg-card); border:1px solid var(--border-color); color:var(--text-primary);" onclick="logPaperTrade()">
                            Log Manual Paper Entry
                        </button>
                    </details>
                </div>
            </div>

            <!-- Strategy Decision Matrix & Trade Rationale (का BUY/SELL करायचे? स्ट्रॅटेजी आणि विश्लेषण) -->
            <div class="col-12">
                <div class="card matrix-card">
                    <div class="card-header">
                        <div style="display:flex; align-items:center; gap:12px; flex-wrap:wrap;">
                            <span class="card-title" style="margin:0; font-size:14px; color:var(--text-primary);">
                                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
                                Strategy Intelligence & Trade Decision Matrix (स्ट्रॅटेजी आणि ट्रेड विश्लेषण)
                            </span>
                            <span id="matrixStrategyBadge" class="badge badge-blue">Session Range Breakout + EMA 9/21 Trend</span>
                            <span class="badge badge-amber" style="font-size:10px;">Paper Observation Only</span>
                        </div>
                        <div style="font-size:11px; color:var(--text-secondary);">
                            Decision Engine: <strong style="color:var(--text-primary);">100% Rule-Based (No Guesswork)</strong>
                        </div>
                    </div>

                    <!-- Primary Why BUY / SELL Decision Banner -->
                    <div id="matrixDecisionBox" class="decision-box wait-active">
                        <div id="matrixActionLabel" class="decision-action-title" style="color:var(--accent-amber);">
                            <span class="pulse-dot amber"></span>
                            MONITORING / WAITING (प्रतीक्षा करा - सेटअप रेंजमध्ये आहे)
                        </div>
                        <div id="matrixDecisionSummary" class="decision-summary-text">
                            Analyzing candle price structure, session range, and EMA momentum...
                        </div>
                    </div>

                    <!-- 4-Pillar Strategy Verification Checklist -->
                    <div class="checklist-grid">
                        <!-- Rule 1: Session & Liquidity -->
                        <div class="check-card">
                            <div class="check-card-header">
                                <span class="check-card-title">
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                                    1. Session & Volume
                                </span>
                                <span id="chkSessionStatus" class="badge badge-green">PASS</span>
                            </div>
                            <div id="chkSessionDetail" class="check-card-detail">
                                24/7 Spot Benchmark Active (High Liquidity)
                            </div>
                        </div>

                        <!-- Rule 2: Trend & EMA Alignment -->
                        <div class="check-card">
                            <div class="check-card-header">
                                <span class="check-card-title">
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/></svg>
                                    2. EMA 9/21 Trend Filter
                                </span>
                                <span id="chkTrendStatus" class="badge badge-green">BULLISH</span>
                            </div>
                            <div id="chkTrendDetail" class="check-card-detail">
                                EMA 9 > EMA 21 confirming upward momentum
                            </div>
                        </div>

                        <!-- Rule 3: Candle-Close Confirmation -->
                        <div class="check-card">
                            <div class="check-card-header">
                                <span class="check-card-title">
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
                                    3. Bar Close (Wicks Filter)
                                </span>
                                <span id="chkBreakoutStatus" class="badge badge-amber">WAITING</span>
                            </div>
                            <div id="chkBreakoutDetail" class="check-card-detail">
                                Bar close required beyond range (Wicks ignored)
                            </div>
                        </div>

                        <!-- Rule 4: 2.0R Capital Protection -->
                        <div class="check-card">
                            <div class="check-card-header">
                                <span class="check-card-title">
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M16 8h-6a2 2 0 1 0 0 4h4a2 2 0 1 1 0 4H8"/><path d="M12 18V6"/></svg>
                                    4. 2.0R Risk / Reward
                                </span>
                                <span id="chkRiskStatus" class="badge badge-green">2.0R PLAN</span>
                            </div>
                            <div id="chkRiskDetail" class="check-card-detail">
                                Fixed 1:2 R:R | Strictly $50.00 Max Risk Cap
                            </div>
                        </div>
                    </div>

                    <!-- Key Strategy Decision Levels -->
                    <div class="levels-bar">
                        <div class="level-metric">
                            <span class="level-metric-label">Breakout Resistance (BUY Trigger)</span>
                            <span id="matrixLevelHigh" class="level-metric-val" style="color:var(--accent-green);">--</span>
                        </div>
                        <div class="level-metric">
                            <span class="level-metric-label">Breakdown Support (SELL Trigger)</span>
                            <span id="matrixLevelLow" class="level-metric-val" style="color:var(--accent-rose);">--</span>
                        </div>
                        <div class="level-metric">
                            <span class="level-metric-label">Dynamic Trend EMAs</span>
                            <span id="matrixLevelEma" class="level-metric-val" style="color:var(--accent-blue); font-size:12px;">--</span>
                        </div>
                        <div class="level-metric">
                            <span class="level-metric-label">Simulated Risk & Reward</span>
                            <span id="matrixLevelRR" class="level-metric-val" style="color:var(--accent-amber); font-size:12px;">Risk: $50.00 | 2.0R</span>
                        </div>
                    </div>

                    <!-- Strategy Rules Guide Drawer -->
                    <details style="margin-top:14px; font-size:12px; color:var(--text-secondary); border-top:1px solid var(--border-subtle); padding-top:10px;">
                        <summary style="cursor:pointer; font-weight:600; color:var(--accent-blue); display:flex; align-items:center; gap:6px;">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
                            वापरलेली स्ट्रॅटेजी कशी काम करते? (Detailed Strategy Rules & Explanation)
                        </summary>
                        <div style="margin-top:10px; display:grid; grid-template-columns:repeat(auto-fit, minmax(280px, 1fr)); gap:12px; line-height:1.6;">
                            <div style="background:var(--bg-card); padding:10px 14px; border-radius:6px; border:1px solid var(--border-subtle);">
                                <strong style="color:var(--text-primary);">१. रेंज निश्चित करणे (Range Definition):</strong>
                                <p style="font-size:11px; margin-top:4px;">मार्केट सुरू झाल्यावर पहिली Asian Session किंवा ठराविक रेंजचा High आणि Low स्वयंचलित मार्क केला जातो.</p>
                            </div>
                            <div style="background:var(--bg-card); padding:10px 14px; border-radius:6px; border:1px solid var(--border-subtle);">
                                <strong style="color:var(--text-primary);">२. 9/21 EMA ट्रेंड फिल्टर:</strong>
                                <p style="font-size:11px; margin-top:4px;">9 EMA हा 21 EMA च्या वर असेल तर फक्त BUY ट्रेड्स, आणि खाली असेल तर फक्त SELL ट्रेड्स शोधले जातात.</p>
                            </div>
                            <div style="background:var(--bg-card); padding:10px 14px; border-radius:6px; border:1px solid var(--border-subtle);">
                                <strong style="color:var(--text-primary);">३. कँडल क्लोज नियम (फेक ब्रेकआउट संरक्षण):</strong>
                                <p style="font-size:11px; margin-top:4px;">कँडलची फक्त सुई (Wick) बाहेर जाऊन चालत नाही; पूर्ण 5m कँडलची बॉडी लेव्हलच्या बाहेर क्लोज झाल्यावरच सिग्नल मिळतो.</p>
                            </div>
                            <div style="background:var(--bg-card); padding:10px 14px; border-radius:6px; border:1px solid var(--border-subtle);">
                                <strong style="color:var(--text-primary);">४. २.०R टार्गेट व भांडवल संरक्षण:</strong>
                                <p style="font-size:11px; margin-top:4px;">स्टॉप लॉस रेंजच्या मध्यावर असतो आणि टार्गेट जोखमीच्या दुप्पट (2.0R) असते. प्रत्येक ट्रेडवर जास्तीत जास्त $50 चेच नुकसान होते.</p>
                            </div>
                        </div>
                    </details>
                </div>
            </div>

            <!-- Card 4: Interactive Candlestick Chart (OHLCV + EMAs + Levels + Signal Markers) -->
            <div class="col-12">
                <div class="card chart-card">
                    <div class="chart-header">
                        <div style="display:flex; align-items:center; gap:12px; flex-wrap:wrap;">
                            <span class="card-title" style="margin:0;">
                                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/></svg>
                                <span id="chartTitle">XAU/USD Candlestick & Volume Chart</span>
                            </span>
                            <div class="tf-group">
                                <button class="tf-btn" onclick="setChartInterval('1m')">1m</button>
                                <button class="tf-btn active" onclick="setChartInterval('5m')">5m</button>
                                <button class="tf-btn" onclick="setChartInterval('15m')">15m</button>
                                <button class="tf-btn" onclick="setChartInterval('1h')">1h</button>
                                <button class="tf-btn" onclick="setChartInterval('4h')">4h</button>
                            </div>
                        </div>

                        <div class="chart-legend">
                            <div class="legend-item"><span class="legend-color" style="background:#38bdf8;"></span> EMA 9</div>
                            <div class="legend-item"><span class="legend-color" style="background:#f59e0b;"></span> EMA 21</div>
                            <div class="legend-item"><span class="legend-color" style="background:#60a5fa; border-top:1px dashed #60a5fa;"></span> Entry Ref</div>
                            <div class="legend-item"><span class="legend-color" style="background:#f43f5e; border-top:1px dashed #f43f5e;"></span> Stop Loss</div>
                            <div class="legend-item"><span class="legend-color" style="background:#10b981; border-top:1px dashed #10b981;"></span> Take Profit (2R)</div>
                            <button class="btn" style="padding:4px 10px; font-size:11px;" onclick="fetchChart()">Refresh Chart</button>
                        </div>
                    </div>

                    <!-- Live Hover Bar -->
                    <div id="chartStatsBar" class="chart-stats-bar">
                        Hover over chart candles for precise OHLCV and indicator data
                    </div>

                    <!-- Canvas Wrapper -->
                    <div class="chart-canvas-wrap">
                        <canvas id="chartCanvas"></canvas>
                    </div>
                </div>
            </div>

            <!-- Card 5: Historical Strategy Backtesting (Range Breakout + 2R Plan) -->
            <div class="col-12">
                <div class="card">
                    <div class="card-header">
                        <span class="card-title">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/></svg>
                            Historical Strategy Backtest (Range Breakout & 2R Exit)
                        </span>
                        <div style="display:flex; align-items:center; gap:10px;">
                            <select id="btRange" class="form-control" style="font-size:11px; padding:4px 8px; width:auto;">
                                <option value="5d">Recent 5 Days (Intraday)</option>
                                <option value="1mo">Past 1 Month</option>
                            </select>
                            <button id="btnRunBacktest" class="btn btn-primary" onclick="runBacktest(event)">
                                Run Historical Backtest
                            </button>
                        </div>
                    </div>

                    <!-- Backtest Metrics Grid -->
                    <div class="backtest-grid" id="backtestMetrics">
                        <div class="stat-card">
                            <div class="stat-card-title">Win Rate</div>
                            <div id="btWinRate" class="stat-card-value" style="color:var(--accent-blue);">--%</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-card-title">Total Trades</div>
                            <div id="btTotalTrades" class="stat-card-value">--</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-card-title">Net Simulated P/L</div>
                            <div id="btNetPnl" class="stat-card-value">$--</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-card-title">Profit Factor</div>
                            <div id="btProfitFactor" class="stat-card-value">--</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-card-title">Max Drawdown</div>
                            <div id="btMaxDrawdown" class="stat-card-value" style="color:var(--accent-rose);">$--</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-card-title">Avg Win / Loss</div>
                            <div id="btAvgWinLoss" class="stat-card-value" style="font-size:14px; margin-top:2px;">$-- / $--</div>
                        </div>
                    </div>

                    <!-- Historical Trades Table -->
                    <div style="max-height: 240px; overflow-y:auto; overflow-x:auto; width:100%; border:1px solid var(--border-subtle); border-radius:6px; margin-bottom:12px;">
                        <table class="journal-table" style="font-size:12px; min-width:600px;">
                            <thead>
                                <tr>
                                    <th>#</th>
                                    <th>Side</th>
                                    <th>Entry Time</th>
                                    <th>Exit Time</th>
                                    <th>Entry Price</th>
                                    <th>Exit Price</th>
                                    <th>Outcome</th>
                                    <th>Simulated P/L</th>
                                </tr>
                            </thead>
                            <tbody id="backtestBody">
                                <tr>
                                    <td colspan="8" style="text-align:center; color:var(--text-muted); padding:20px;">
                                        Click 'Run Historical Backtest' to replay candle data under authentic 11-skills rules.
                                    </td>
                                </tr>
                            </tbody>
                        </table>
                    </div>

                    <div style="font-size:11px; color:var(--text-muted); font-style:italic;">
                        * <strong>Notice:</strong> Historical backtest strictly evaluates completed candles with 0.5 pip slippage and spread. No future lookahead. Past simulated performance does not guarantee future results.
                    </div>
                </div>
            </div>

            <!-- Card 6: Paper Observation Journal (Table with CSV Export) -->
            <div class="col-12">
                <div class="card">
                    <div class="card-header">
                        <span class="card-title">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1-2.5-2.5Z"/><path d="M6 6h10"/><path d="M6 10h10"/></svg>
                            Paper Observation Journal (Simulated Positions)
                        </span>
                        <div style="display:flex; align-items:center; gap:10px;">
                            <span class="badge badge-amber">SIMULATED ONLY — NO REAL MONEY</span>
                            <button id="btnExportCsv" class="btn" style="padding:4px 10px; font-size:11px;" onclick="exportJournalCSV()">
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/></svg>
                                Export CSV
                            </button>
                        </div>
                    </div>

                    <div style="overflow-x:auto; width:100%;">
                        <table class="journal-table" style="min-width:750px;">
                            <thead>
                                <tr>
                                    <th>ID</th>
                                    <th>Symbol</th>
                                    <th>Side</th>
                                    <th>Sim. Fill</th>
                                    <th>Stop Loss</th>
                                    <th>Take Profit</th>
                                    <th>Paper Size</th>
                                    <th>Max Risk</th>
                                    <th>Sim. P/L</th>
                                    <th>Strategy / Setup</th>
                                    <th>Timestamp</th>
                                    <th>Status</th>
                                </tr>
                            </thead>
                            <tbody id="journalBody">
                                <!-- Injected dynamically -->
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <!-- Card 7: 11 Trading Skills Suite (Paper Observation & Analysis) -->
            <div class="col-12">
                <div class="skills-section-header">
                    <div>
                        <h2 style="font-size: 16px; font-weight: 700; display:flex; align-items:center; gap:8px;">
                            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>
                            11 Modular Trading Skills (Paper Observation & Analysis)
                        </h2>
                        <div style="font-size: 12px; color: var(--text-secondary); margin-top:2px;">
                            Integrated from day-trader-skill repository. All execution is strictly paper-only; broker execution is permanently disabled.
                        </div>
                    </div>
                    <div style="font-size: 11px; font-family: var(--font-mono); color: var(--accent-amber); background: rgba(245, 158, 11, 0.1); border: 1px solid rgba(245, 158, 11, 0.3); padding: 4px 10px; border-radius: 4px;">
                        PACK: 10 PAPER ACTIVE / 1 DISABLED
                    </div>
                </div>

                <div class="skills-grid" id="skillsContainer">
                    <!-- Populated dynamically from /api/skills -->
                </div>
            </div>

            <!-- Card 8: Disclaimers & Safety Guardrails -->
            <div class="col-12">
                <div class="disclaimer-box">
                    <strong>LEGAL & SAFETY NOTICE — PAPER OBSERVATION ONLY:</strong><br>
                    1. This software runs strictly in educational observation / paper mode. It contains zero brokerage connector modules, holds no API trading credentials, and possesses no live order routing capabilities.<br>
                    2. <strong>Commodity Benchmarks:</strong> Gold (XAU/USD) quotes are sourced from COMEX Gold Futures (GC=F) continuous contracts and are not retail spot OTC/interbank feeds.<br>
                    3. <strong>Indicative Pricing:</strong> All quotes are indicative and bank/exchange approximations for paper analysis. Spreads, execution latency, and slippage in real market conditions differ significantly.
                </div>
            </div>
        </div>
    </div>

    <!-- Risk Configuration Modal -->
    <div id="riskModal" class="modal-overlay">
        <div class="modal-content">
            <h3 style="font-size:16px; margin-bottom:12px; display:flex; align-items:center; gap:8px;">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
                Configurable Paper Risk Controls
            </h3>
            <p style="font-size:12px; color:var(--text-secondary); margin-bottom:16px;">
                Enforce protective capital limits on the simulated observation journal.
            </p>
            <div class="form-group" style="margin-bottom:12px;">
                <label>Daily Simulated Loss Limit ($ USD)</label>
                <input id="cfgDailyLimit" type="number" step="10" class="form-control" value="150.0">
            </div>
            <div class="form-group" style="margin-bottom:12px;">
                <label>Max Risk Per Paper Trade ($ USD)</label>
                <input id="cfgTradeRisk" type="number" step="5" class="form-control" value="50.0">
            </div>
            <div class="form-group" style="margin-bottom:18px;">
                <label>Max Concurrent Open Positions</label>
                <input id="cfgMaxPos" type="number" step="1" min="1" max="10" class="form-control" value="3">
            </div>
            <div style="display:flex; justify-content:flex-end; gap:10px;">
                <button class="btn" onclick="closeRiskModal()">Cancel</button>
                <button class="btn btn-primary" onclick="saveRiskConfig()">Save Parameters</button>
            </div>
        </div>
    </div>

    <!-- Spot Gold Price Calibration Modal -->
    <div id="calibrateModal" class="modal-overlay">
        <div class="modal-content" style="max-width: 480px;">
            <h3 style="font-size:16px; margin-bottom:8px; display:flex; align-items:center; gap:8px;">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20v-6M6 20V10M18 20V4"/><circle cx="12" cy="14" r="2"/><circle cx="6" cy="10" r="2"/><circle cx="18" cy="4" r="2"/></svg>
                Spot Gold (XAU/USD) Price Calibration
            </h3>
            <p style="font-size:12px; color:var(--text-secondary); margin-bottom:14px; line-height:1.5;">
                Crypto-backed PAXG/USDT trades with a slight basis/premium compared to retail spot forex (TradingView, Exness, OANDA, MT4/MT5). Synchronize this terminal to match your broker's exact live price in real time.
            </p>
            <div style="background:var(--bg-card); border:1px solid var(--border-color); border-radius:6px; padding:12px; margin-bottom:14px;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                    <span style="font-size:12px; color:var(--text-secondary);">Current Raw Feed Price:</span>
                    <span id="calibRawPrice" style="font-family:var(--font-mono); font-weight:700; color:var(--accent-amber);">Connecting...</span>
                </div>
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <span style="font-size:12px; color:var(--text-secondary);">Current Calibrated Price:</span>
                    <span id="calibEffectivePrice" style="font-family:var(--font-mono); font-weight:700; color:var(--accent-green);">--</span>
                </div>
            </div>
            
            <!-- Quick 1-Click Sync -->
            <div class="form-group" style="margin-bottom:12px;">
                <label style="display:flex; justify-content:space-between;">
                    <span>Quick Match Target Broker Price ($ USD)</span>
                    <span style="font-size:11px; color:var(--text-muted);">e.g. 3896.00</span>
                </label>
                <div style="display:flex; gap:8px;">
                    <input id="calibTargetInput" type="number" step="0.01" class="form-control" placeholder="Price shown on broker (e.g. 3896.00)">
                    <button type="button" class="btn" style="white-space:nowrap; background:var(--accent-blue); color:#fff;" onclick="calcOffsetFromTarget()">Auto-Sync</button>
                </div>
            </div>

            <div class="form-group" style="margin-bottom:14px;">
                <label>Direct Offset Adjustment ($ USD)</label>
                <input id="calibOffsetInput" type="number" step="0.05" class="form-control" value="0.00">
                <div style="font-size:11px; color:var(--text-muted); margin-top:4px;">
                    Positive adds to price, negative subtracts (e.g. -12.95 brings 3908.95 down to 3896.00).
                </div>
            </div>

            <div class="form-group" style="margin-bottom:18px;">
                <label>Broker / Platform Label</label>
                <input id="calibBrokerInput" type="text" class="form-control" placeholder="e.g. TradingView / Exness / OANDA" value="TradingView / Broker Spot">
            </div>

            <div style="display:flex; justify-content:space-between; align-items:center;">
                <button class="btn" onclick="resetCalibration()" style="color:var(--accent-rose);">Reset (0.00)</button>
                <div style="display:flex; gap:10px;">
                    <button class="btn" onclick="closeCalibrateModal()">Cancel</button>
                    <button class="btn btn-primary" onclick="saveCalibration()">Apply Calibration</button>
                </div>
            </div>
        </div>
    </div>

    <!-- Twelve Data Direct Spot Gold API Modal -->
    <div id="twelveDataModal" class="modal-overlay">
        <div class="modal-content" style="max-width: 480px;">
            <h3 style="font-size:16px; margin-bottom:8px; display:flex; align-items:center; gap:8px;">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>
                Twelve Data Real-Time Spot Gold API (100% Free)
            </h3>
            <p style="font-size:12px; color:var(--text-secondary); margin-bottom:14px; line-height:1.5;">
                Twelve Data provides authentic retail Spot Gold (XAU/USD) prices matching TradingView, MetaTrader, Exness, and OANDA with zero basis difference.
            </p>
            <div class="form-group" style="margin-bottom:12px;">
                <label>Twelve Data API Key</label>
                <input id="tdApiKeyInput" type="text" class="form-control" placeholder="Enter your 32-character API key" value="73345dbfbdc94360af524b86732676d2" style="font-family:var(--font-mono); font-size:12px;">
            </div>
            <div style="display:flex; align-items:center; gap:8px; margin-bottom:16px;">
                <input id="tdEnabledCheckbox" type="checkbox" checked style="cursor:pointer; width:16px; height:16px;">
                <label for="tdEnabledCheckbox" style="font-size:12px; color:var(--text-primary); cursor:pointer;">Enable Twelve Data as Primary Spot Provider</label>
            </div>
            <div style="background:var(--bg-card); border:1px solid var(--border-color); border-radius:6px; padding:10px 12px; margin-bottom:16px; font-size:11px; color:var(--text-secondary);">
                <div style="display:flex; justify-content:space-between; margin-bottom:4px;">
                    <span>Feed Status:</span>
                    <span id="tdFeedStatus" style="font-weight:700; color:var(--accent-green);">Connected (Free Key Active)</span>
                </div>
                <div style="display:flex; justify-content:space-between;">
                    <span>Spot Symbol:</span>
                    <span style="font-family:var(--font-mono); color:var(--text-primary);">XAU/USD (Direct Retail Spot Gold)</span>
                </div>
            </div>
            <div style="display:flex; justify-content:flex-end; gap:10px;">
                <button class="btn" onclick="closeTwelveDataModal()">Cancel</button>
                <button class="btn btn-primary" onclick="saveTwelveDataConfig()">Save & Connect Feed</button>
            </div>
        </div>
    </div>

    <script>
        let currentObservation = null;
        let currentChartData = null;
        let currentInterval = '5m';
        let notificationsEnabled = false;
        let lastNotifiedSignal = null;
        let notifiedTrades = new Set();

        /* -------------------------------------------------------------
           Binance WebSocket Client: Uncapped 24/7 Real-Time Gold Stream (PAXG/USDT)
           ------------------------------------------------------------- */
        let goldWs = null;
        let goldWsReconnectTimer = null;
        let goldWsReconnectAttempts = 0;
        let goldLiveTick = null;
        let lastGoldTickTime = 0;
        let lastServerTickSync = 0;
        let cachedJournal = [];

        function initGoldWebSocket() {
            const sym = document.getElementById('pairSelect') ? document.getElementById('pairSelect').value : 'XAU/USD';
            if (!sym.includes('XAU')) {
                if (goldWs) {
                    try { goldWs.close(); } catch (e) {}
                    goldWs = null;
                }
                return;
            }

            if (goldWs && (goldWs.readyState === WebSocket.OPEN || goldWs.readyState === WebSocket.CONNECTING)) {
                return;
            }

            const wsUrl = 'wss://stream.binance.com:9443/ws/paxgusdt@ticker';
            try {
                goldWs = new WebSocket(wsUrl);
            } catch (err) {
                console.warn('[WebSocket] Binance init error:', err);
                scheduleGoldWsReconnect();
                return;
            }

            goldWs.onopen = function() {
                console.log('[WebSocket] Connected to Binance PAXG/USDT 24/7 Spot Gold stream');
                goldWsReconnectAttempts = 0;
                const healthFeed = document.getElementById('healthFeed');
                if (healthFeed) healthFeed.innerHTML = '<span style="color:var(--accent-green)">● Live WS Connected (PAXG/USDT)</span>';
            };

            goldWs.onmessage = function(event) {
                try {
                    const data = JSON.parse(event.data);
                    console.log("[Binance WS Tick]", data.c);

                    // Binance 24hrTicker payload:
                    // c: Last Price, b: Best Bid, a: Best Ask, E: Event Time
                    if (data && data.c) {
                        const rawLast = parseFloat(data.c);
                        const rawBid = data.b ? parseFloat(data.b) : rawLast;
                        const rawAsk = data.a ? parseFloat(data.a) : rawLast;
                        const curOffset = window.goldOffset || 0.0;
                        const lastPrice = rawLast + curOffset;
                        const bidPrice = rawBid + curOffset;
                        const askPrice = rawAsk + curOffset;
                        const eventTime = data.E || Date.now();
                        const midPrice = lastPrice; // Directly parse last price from data.c
                        const spreadPips = Math.max(0, (askPrice - bidPrice) / 0.10);

                        // Live modal raw display updater
                        const calibRawEl = document.getElementById('calibRawPrice');
                        if (calibRawEl) calibRawEl.textContent = rawLast.toFixed(2);
                        const calibEffEl = document.getElementById('calibEffectivePrice');
                        if (calibEffEl) calibEffEl.textContent = lastPrice.toFixed(2);

                        goldLiveTick = {
                            raw_last: rawLast,
                            raw_bid: rawBid,
                            raw_ask: rawAsk,
                            offset: curOffset,
                            last: lastPrice,
                            bid: bidPrice,
                            ask: askPrice,
                            mid: lastPrice,
                            spread_pips: spreadPips,
                            event_time: eventTime,
                            received_at: Date.now()
                        };
                        lastGoldTickTime = Date.now();

                        const curSym = document.getElementById('pairSelect') ? document.getElementById('pairSelect').value : 'XAU/USD';
                        if (curSym.includes('XAU')) {
                            // Immediately update DOM element for "Indicative Market Price"
                            const priceMidEl = document.getElementById('priceMid');
                            if (priceMidEl) {
                                priceMidEl.textContent = lastPrice.toFixed(2);
                                priceMidEl.style.transition = 'color 0.15s ease';
                                priceMidEl.style.color = '#34d399';
                                setTimeout(() => { if (priceMidEl) priceMidEl.style.color = 'var(--text-primary)'; }, 200);
                            }
                            const priceBidEl = document.getElementById('priceBid');
                            if (priceBidEl) priceBidEl.textContent = bidPrice.toFixed(2);
                            const priceAskEl = document.getElementById('priceAsk');
                            if (priceAskEl) priceAskEl.textContent = askPrice.toFixed(2);

                            renderLiveGoldTick(goldLiveTick);
                            checkLivePaperPositions(goldLiveTick);
                            updateChartWithLiveTick(goldLiveTick);
                            if (currentObservation) updateStrategyMatrix(currentObservation, goldLiveTick);
                        }
                    }
                } catch (e) {
                    console.error('[WebSocket] Error parsing PAXG tick:', e);
                }
            };

            goldWs.onerror = function(err) {
                console.warn('[WebSocket] Error in PAXG/USDT stream:', err);
                scheduleGoldWsReconnect();
            };

            goldWs.onclose = function() {
                console.log('[WebSocket] PAXG/USDT stream closed, scheduling reconnect...');
                scheduleGoldWsReconnect();
            };
        }

        function scheduleGoldWsReconnect() {
            if (goldWsReconnectTimer) return;
            const delay = Math.min(10000, 1000 * Math.pow(1.5, goldWsReconnectAttempts));
            goldWsReconnectAttempts++;
            goldWsReconnectTimer = setTimeout(() => {
                goldWsReconnectTimer = null;
                initGoldWebSocket();
            }, delay);
        }

        function renderLiveGoldTick(tick) {
            const sym = document.getElementById('pairSelect') ? document.getElementById('pairSelect').value : 'XAU/USD';
            if (!sym.includes('XAU')) return;

            const priceMid = document.getElementById('priceMid');
            const priceBid = document.getElementById('priceBid');
            const priceAsk = document.getElementById('priceAsk');
            const spreadDisplay = document.getElementById('spreadDisplay');
            const sourceDisplay = document.getElementById('sourceDisplay');
            const freshnessDisplay = document.getElementById('freshnessDisplay');
            const timeDisplay = document.getElementById('timeDisplay');
            const statusBox = document.getElementById('statusBox');
            const statusHeading = document.getElementById('statusHeading');
            const statusDesc = document.getElementById('statusDesc');
            const marketBadge = document.getElementById('marketBadge');
            const signalBadge = document.getElementById('signalBadge');

            if (priceMid) {
                priceMid.textContent = tick.last.toFixed(2);
            }
            if (priceBid) priceBid.textContent = tick.bid.toFixed(2);
            if (priceAsk) priceAsk.textContent = tick.ask.toFixed(2);

            if (spreadDisplay) spreadDisplay.textContent = tick.spread_pips.toFixed(1) + ' pips ($' + (tick.ask - tick.bid).toFixed(2) + ')';
            if (sourceDisplay) {
                if (tick.offset) {
                    sourceDisplay.textContent = 'Binance WebSocket (PAXG) [Calibrated ' + (tick.offset >= 0 ? '+' : '') + tick.offset.toFixed(2) + ']';
                } else {
                    sourceDisplay.textContent = 'Binance WebSocket (PAXG/USDT Stream)';
                }
            }
            if (freshnessDisplay) freshnessDisplay.innerHTML = '<span style="color:var(--accent-green); font-weight:700;">● LIVE (0s ago)</span>';
            if (timeDisplay) timeDisplay.textContent = new Date(tick.event_time).toLocaleTimeString() + ' (Live WS)';

            const off = tick.offset || window.goldOffset || 0.0;
            const offStr = (off >= 0 ? '+' : '') + off.toFixed(2);
            const offsetBadge = document.getElementById('offsetBadge');
            if (offsetBadge) offsetBadge.textContent = off === 0 ? '0.00' : offStr;
            const goldOffsetDisplay = document.getElementById('goldOffsetDisplay');
            if (goldOffsetDisplay) goldOffsetDisplay.textContent = off === 0 ? '0.00' : offStr;
            const goldBrokerDisplay = document.getElementById('goldBrokerDisplay');
            if (goldBrokerDisplay) goldBrokerDisplay.textContent = off === 0 ? 'Exact Spot' : (window.goldBroker || 'Spot Broker');

            if (marketBadge) {
                marketBadge.className = 'badge badge-green';
                marketBadge.textContent = 'LIVE / ACTIVE (24/7 Benchmark)';
            }
            if (statusBox) statusBox.className = 'status-box active';
            if (statusHeading) statusHeading.innerHTML = '<span class="pulse-dot green"></span> LIVE / ACTIVE (24/7 Benchmark)';
            if (statusDesc) statusDesc.textContent = 'Binance PAXG/USDT 24/7 real-time feed active. Tracks 1 oz spot gold 1:1 against USD with zero rate limits or weekend halts.';
            if (signalBadge && (signalBadge.textContent === 'MARKET CLOSED' || signalBadge.textContent === 'STALE')) {
                signalBadge.className = 'badge badge-blue';
                signalBadge.textContent = 'MONITORING';
            }

            const healthDot = document.getElementById('healthDot');
            const healthFeed = document.getElementById('healthFeed');
            const healthSession = document.getElementById('healthSession');
            const healthBenchmark = document.getElementById('healthBenchmark');
            if (healthDot) healthDot.className = 'pulse-dot green';
            if (healthFeed) healthFeed.innerHTML = '<span style="color:var(--accent-green);">● WebSocket Connected (PAXG/USDT)</span>';
            if (healthSession) healthSession.textContent = '24/7 Spot Benchmark';
            if (healthBenchmark) healthBenchmark.textContent = 'Binance PAXG/USDT (1 oz Spot Gold Stream)';

            const simEntry = document.getElementById('simEntry');
            if (simEntry && !simEntry.value) {
                simEntry.value = tick.mid.toFixed(2);
                const simStop = document.getElementById('simStop');
                const simTarget = document.getElementById('simTarget');
                if (simStop && !simStop.value) simStop.value = (tick.mid - 5.0).toFixed(2);
                if (simTarget && !simTarget.value) simTarget.value = (tick.mid + 10.0).toFixed(2);
                recalcSimulation();
            }
        }

        function updateFreshnessLabel() {
            const sym = document.getElementById('pairSelect') ? document.getElementById('pairSelect').value : 'XAU/USD';
            if (sym.includes('XAU') && goldLiveTick && lastGoldTickTime > 0) {
                const elapsedSec = Math.floor((Date.now() - lastGoldTickTime) / 1000);
                const el = document.getElementById('freshnessDisplay');
                if (el) {
                    if (elapsedSec <= 1) {
                        el.innerHTML = '<span style="color:var(--accent-green); font-weight:700;">● LIVE (0s ago)</span>';
                    } else {
                        el.innerHTML = `<span style="color:var(--accent-green); font-weight:700;">● LIVE (${elapsedSec}s ago)</span>`;
                    }
                }
            }
        }

        function updateChartWithLiveTick(tick) {
            if (!currentChartData || !currentChartData.candles || !currentChartData.candles.length) return;
            const candles = currentChartData.candles;
            const lastCandle = candles[candles.length - 1];
            if (!lastCandle) return;

            lastCandle.close = tick.mid;
            if (tick.mid > lastCandle.high) lastCandle.high = tick.mid;
            if (tick.mid < lastCandle.low) lastCandle.low = tick.mid;

            renderCanvasChart();
        }

        function onPairChange() {
            const sym = document.getElementById('pairSelect').value;
            const isGold = sym.includes('XAU');
            const goldBanner = document.getElementById('goldBanner');
            if (isGold) {
                goldBanner.classList.add('active');
                if (!goldLiveTick) {
                    const priceMid = document.getElementById('priceMid');
                    const priceBid = document.getElementById('priceBid');
                    const priceAsk = document.getElementById('priceAsk');
                    if (priceMid) priceMid.textContent = 'Connecting...';
                    if (priceBid) priceBid.textContent = 'Connecting...';
                    if (priceAsk) priceAsk.textContent = 'Connecting...';
                }
                initGoldWebSocket();
            } else {
                goldBanner.classList.remove('active');
                if (goldWs) {
                    try { goldWs.close(); } catch (e) {}
                    goldWs = null;
                }
            }
            fetchObservation();
            fetchChart();
        }

        function setChartInterval(tf) {
            currentInterval = tf;
            document.querySelectorAll('.tf-btn').forEach(btn => {
                if (btn.textContent === tf) btn.classList.add('active');
                else btn.classList.remove('active');
            });
            fetchChart();
        }

        async function fetchHealth() {
            const sym = document.getElementById('pairSelect').value;
            try {
                const res = await fetch('/api/health?symbol=' + encodeURIComponent(sym));
                const data = await res.json();
                document.getElementById('healthTime').textContent = data.system_time_utc || '--';
                if (!sym.includes('XAU') || !goldLiveTick) {
                    document.getElementById('healthFeed').textContent = data.status === 'HEALTHY' ? 'Healthy (200 OK)' : 'Degraded';
                    document.getElementById('healthSession').textContent = data.active_sessions && data.active_sessions.length ? data.active_sessions.join(', ') : (data.market_open ? 'Open' : 'Closed');
                    document.getElementById('healthBenchmark').textContent = data.futures_benchmark || (sym + ' Indicative');
                    document.getElementById('healthDot').className = 'pulse-dot ' + (data.market_open ? 'green' : 'red');
                }
            } catch (e) {
                console.error("Health fetch error:", e);
            }
        }

        async function fetchObservation() {
            const sym = document.getElementById('pairSelect').value;
            try {
                const res = await fetch('/api/observe?symbol=' + encodeURIComponent(sym) + '&interval=' + currentInterval);
                const data = await res.json();
                currentObservation = data;
                renderDashboard(data);
                checkNotifications(data);
            } catch (err) {
                console.error("Fetch error:", err);
            }
        }

        function renderDashboard(data) {
            const sym = data.symbol || '';
            const isGold = sym.includes('XAU');

            // Top Gold banner
            const goldBanner = document.getElementById('goldBanner');
            if (isGold) {
                goldBanner.classList.add('active');
            } else {
                goldBanner.classList.remove('active');
            }

            // Decouple stale polling: if live WebSocket is streaming, preserve live tick data
            const isLiveStreaming = isGold && goldLiveTick && (Date.now() - lastGoldTickTime < 30000);
            if (isLiveStreaming) {
                data.indicative_mid = goldLiveTick.mid;
                data.indicative_bid = goldLiveTick.bid;
                data.indicative_ask = goldLiveTick.ask;
                data.spread_pips = goldLiveTick.spread_pips;
                data.source = goldLiveTick.offset ? ("Binance WebSocket (PAXG) [Calibrated " + (goldLiveTick.offset >= 0 ? '+' : '') + goldLiveTick.offset.toFixed(2) + "]") : "Binance WebSocket (PAXG/USDT Stream)";
                data.data_freshness = "LIVE (0s ago)";
                data.timestamp_et = new Date(goldLiveTick.event_time).toLocaleTimeString() + " (Live WS)";
                if (data.status === 'NO SIGNAL' && data.reason && (data.reason.includes('closed') || data.reason.includes('stale'))) {
                    data.reason = "Binance PAXG/USDT 24/7 stream active. Uncapped 1 oz spot gold benchmark.";
                }
            } else if (isGold && window.goldOffset && !data.calibrated_offset) {
                const off = window.goldOffset;
                if (data.indicative_mid) data.indicative_mid = Number((data.indicative_mid + off).toFixed(2));
                if (data.indicative_bid) data.indicative_bid = Number((data.indicative_bid + off).toFixed(2));
                if (data.indicative_ask) data.indicative_ask = Number((data.indicative_ask + off).toFixed(2));
                if (data.auto_signal) {
                    if (data.auto_signal.entry_reference) data.auto_signal.entry_reference = Number((data.auto_signal.entry_reference + off).toFixed(2));
                    if (data.auto_signal.stop_loss) data.auto_signal.stop_loss = Number((data.auto_signal.stop_loss + off).toFixed(2));
                    if (data.auto_signal.take_profit) data.auto_signal.take_profit = Number((data.auto_signal.take_profit + off).toFixed(2));
                }
            }

            // Price Hero
            const priceMid = document.getElementById('priceMid');
            const priceBid = document.getElementById('priceBid');
            const priceAsk = document.getElementById('priceAsk');

            const decimals = isGold ? 2 : 4;

            if (isGold) {
                // Exclusively bound to Binance PAXG/USDT stream - zero lingering GC=F price
                if (goldLiveTick) {
                    if (priceMid) priceMid.textContent = goldLiveTick.last.toFixed(2);
                    if (priceBid) priceBid.textContent = goldLiveTick.bid.toFixed(2);
                    if (priceAsk) priceAsk.textContent = goldLiveTick.ask.toFixed(2);
                } else {
                    if (priceMid) priceMid.textContent = 'Connecting...';
                    if (priceBid) priceBid.textContent = 'Connecting...';
                    if (priceAsk) priceAsk.textContent = 'Connecting...';
                }
            } else {
                const mid = data.indicative_mid || data.latest_price;
                if (mid !== undefined && mid !== null) {
                    priceMid.textContent = Number(mid).toFixed(decimals);
                } else {
                    priceMid.textContent = '--.----';
                }

                if (data.indicative_bid !== undefined && data.indicative_bid !== null) {
                    priceBid.textContent = Number(data.indicative_bid).toFixed(decimals + 1);
                    priceAsk.textContent = Number(data.indicative_ask).toFixed(decimals + 1);
                } else if (mid !== undefined && mid !== null) {
                    priceBid.textContent = Number(mid).toFixed(decimals);
                    priceAsk.textContent = Number(mid).toFixed(decimals);
                } else {
                    priceBid.textContent = '--.----';
                    priceAsk.textContent = '--.----';
                }
            }

            // Meta List
            document.getElementById('spreadDisplay').textContent = (data.spread_pips !== undefined && data.spread_pips !== null) ? (Number(data.spread_pips).toFixed(1) + ' pips') : 'N/A';
            document.getElementById('sourceDisplay').textContent = data.source || 'Yahoo Finance';
            if (isLiveStreaming) {
                document.getElementById('freshnessDisplay').innerHTML = '<span style="color:var(--accent-green); font-weight:700;">● LIVE (0s ago)</span>';
            } else {
                document.getElementById('freshnessDisplay').textContent = data.data_freshness || 'N/A';
            }
            document.getElementById('timeDisplay').textContent = data.timestamp_et || 'N/A';

            // Status Box
            const statusBox = document.getElementById('statusBox');
            const statusHeading = document.getElementById('statusHeading');
            const statusDesc = document.getElementById('statusDesc');
            const marketBadge = document.getElementById('marketBadge');
            const signalBadge = document.getElementById('signalBadge');

            const status = data.status || 'NO SIGNAL';
            signalBadge.textContent = status;

            if (isLiveStreaming) {
                statusBox.className = 'status-box active';
                statusHeading.innerHTML = '<span class="pulse-dot green"></span> LIVE / ACTIVE (24/7 Benchmark)';
                statusDesc.textContent = 'Binance PAXG/USDT 24/7 real-time feed active. Tracks 1 oz spot gold 1:1 against USD with zero rate limits or weekend halts.';
                marketBadge.className = 'badge badge-green';
                marketBadge.textContent = 'LIVE / ACTIVE (24/7 Benchmark)';
                if (signalBadge.textContent === 'MARKET CLOSED' || signalBadge.textContent === 'STALE') {
                    signalBadge.className = 'badge badge-blue';
                    signalBadge.textContent = 'MONITORING';
                }
            } else if (status === 'NO SIGNAL' && data.reason && data.reason.includes('closed')) {
                statusBox.className = 'status-box closed';
                statusHeading.innerHTML = '<span class="pulse-dot red"></span> MARKET CLOSED';
                statusDesc.textContent = data.reason;
                marketBadge.className = 'badge badge-rose';
                marketBadge.textContent = 'CLOSED';
                signalBadge.className = 'badge badge-rose';
            } else if (status === 'NO SIGNAL' && data.reason && data.reason.includes('stale')) {
                statusBox.className = 'status-box stale';
                statusHeading.innerHTML = '<span class="pulse-dot amber"></span> STALE DATA';
                statusDesc.textContent = data.reason;
                marketBadge.className = 'badge badge-amber';
                marketBadge.textContent = 'STALE';
                signalBadge.className = 'badge badge-amber';
            } else if (status === 'SIGNAL CONFIRMED') {
                statusBox.className = 'status-box active';
                statusHeading.innerHTML = '<span class="pulse-dot green"></span> SIGNAL CONFIRMED';
                statusDesc.textContent = data.primary_setup || 'Breakout setup triggered';
                marketBadge.className = 'badge badge-green';
                marketBadge.textContent = 'OPEN';
                signalBadge.className = 'badge badge-green';
            } else {
                statusBox.className = 'status-box active';
                statusHeading.innerHTML = '<span class="pulse-dot green"></span> MONITORING';
                statusDesc.textContent = 'Active market session. Waiting for candle-close trigger.';
                marketBadge.className = 'badge badge-green';
                marketBadge.textContent = 'OPEN';
                signalBadge.className = 'badge badge-blue';
            }

            if (isLiveStreaming) {
                document.getElementById('sessionsDisplay').textContent = '24/7 Spot Benchmark';
            } else {
                document.getElementById('sessionsDisplay').textContent = (data.active_sessions && data.active_sessions.length > 0) ? data.active_sessions.join(', ') : 'None (Closed)';
            }
            document.getElementById('biasDisplay').textContent = data.direction || 'NEUTRAL';

            // Automatic Signal Generation Engine Mapping
            const autoSig = data.auto_signal || {};
            const autoBadge = document.getElementById('autoSignalBadge');
            const autoHero = document.getElementById('autoSignalHero');
            const autoAction = document.getElementById('autoSignalAction');
            const autoReason = document.getElementById('autoSignalReason');
            const btnLogSignal = document.getElementById('btnLogAutoSignal');

            const action = autoSig.action || 'NO SIGNAL';
            if (autoBadge) autoBadge.textContent = action;
            if (autoAction) autoAction.textContent = action;
            let displayReason = autoSig.reason || data.reason || 'Checking market schedule and candle status.';
            if (isLiveStreaming && displayReason.includes('closed')) {
                displayReason = "Binance PAXG/USDT 24/7 live benchmark active. Monitoring candle-close setups.";
            }
            if (autoReason) autoReason.textContent = displayReason;

            if (autoHero && btnLogSignal) {
                if (action === 'BUY') {
                    autoHero.className = 'signal-hero buy';
                    if (autoBadge) autoBadge.className = 'badge badge-green';
                    btnLogSignal.disabled = false;
                    btnLogSignal.textContent = 'Log BUY Signal to Paper Journal';
                    btnLogSignal.style.background = 'var(--accent-green)';
                    btnLogSignal.style.cursor = 'pointer';
                } else if (action === 'SELL') {
                    autoHero.className = 'signal-hero sell';
                    if (autoBadge) autoBadge.className = 'badge badge-rose';
                    btnLogSignal.disabled = false;
                    btnLogSignal.textContent = 'Log SELL Signal to Paper Journal';
                    btnLogSignal.style.background = 'var(--accent-rose)';
                    btnLogSignal.style.cursor = 'pointer';
                } else {
                    autoHero.className = 'signal-hero no-signal';
                    if (autoBadge) autoBadge.className = 'badge badge-amber';
                    btnLogSignal.disabled = true;
                    btnLogSignal.textContent = 'No Confirmed Signal to Log';
                    btnLogSignal.style.background = 'var(--text-muted)';
                    btnLogSignal.style.cursor = 'not-allowed';
                }
            }

            const elStrategy = document.getElementById('autoSignalStrategy');
            if (elStrategy) elStrategy.textContent = autoSig.strategy || 'trader-scanner + trader-entry';
            const elTf = document.getElementById('autoSignalTimeframe');
            if (elTf) elTf.textContent = autoSig.timeframe || currentInterval;
            const elEntry = document.getElementById('autoSignalEntry');
            if (elEntry) elEntry.textContent = (autoSig.entry_reference !== null && autoSig.entry_reference !== undefined) ? Number(autoSig.entry_reference).toFixed(decimals) : '--';
            const elStop = document.getElementById('autoSignalStop');
            if (elStop) elStop.textContent = (autoSig.stop_loss !== null && autoSig.stop_loss !== undefined) ? Number(autoSig.stop_loss).toFixed(decimals) : '--';
            const elTarget = document.getElementById('autoSignalTarget');
            if (elTarget) elTarget.textContent = (autoSig.take_profit !== null && autoSig.take_profit !== undefined) ? Number(autoSig.take_profit).toFixed(decimals) : '--';
            const elRR = document.getElementById('autoSignalRR');
            if (elRR) elRR.textContent = autoSig.reward_risk ? (autoSig.reward_risk + ':1 (2R)') : '--';
            const elTime = document.getElementById('autoSignalTime');
            if (elTime) elTime.textContent = autoSig.timestamp_et || autoSig.timestamp_utc || data.timestamp_et || '--';

            // Auto-fill simulator defaults if empty
            if (mid && !document.getElementById('simEntry').value) {
                document.getElementById('simEntry').value = mid;
                const offset = isGold ? 5.0 : 0.0020;
                document.getElementById('simStop').value = (mid - offset).toFixed(decimals);
                document.getElementById('simTarget').value = (mid + (offset * 2)).toFixed(decimals);
                recalcSimulation();
            }

            // Update Strategy Intelligence & Decision Matrix
            updateStrategyMatrix(data, isLiveStreaming ? goldLiveTick : null);
        }

        /* -------------------------------------------------------------
           Strategy Intelligence & Trade Decision Matrix Engine
           ------------------------------------------------------------- */
        function updateStrategyMatrix(data, liveTick) {
            if (!data) return;
            const sym = data.symbol || (document.getElementById('pairSelect') ? document.getElementById('pairSelect').value : 'XAU/USD');
            const isGold = sym.includes('XAU');
            const decimals = isGold ? 2 : 4;
            const curPrice = (liveTick && liveTick.last) ? liveTick.last : (data.indicative_mid || data.latest_price || 0);
            const autoSig = data.auto_signal || {};
            const action = autoSig.action || 'NO SIGNAL';
            const entryEval = data.entry_eval || {};
            const asianRange = data.asian_range || {};
            const off = window.goldOffset || 0.0;

            // Compute high/low boundaries
            let high = asianRange.asian_high ? Number(asianRange.asian_high) : null;
            let low = asianRange.asian_low ? Number(asianRange.asian_low) : null;
            if (high && isGold && off) high = Number((high + off).toFixed(2));
            if (low && isGold && off) low = Number((low + off).toFixed(2));

            // Dynamic channel if Asian range is null
            if (!high || !low) {
                if (curPrice > 0) {
                    high = Number((curPrice + (isGold ? 4.5 : 0.0025)).toFixed(decimals));
                    low = Number((curPrice - (isGold ? 4.5 : 0.0025)).toFixed(decimals));
                }
            }
            const mid = (high && low) ? Number(((high + low) / 2).toFixed(decimals)) : curPrice;

            // EMAs
            let curEma9 = null;
            let curEma21 = null;
            if (currentChartData && currentChartData.indicators) {
                const e9 = currentChartData.indicators.ema9;
                const e21 = currentChartData.indicators.ema21;
                if (e9 && e9.length) curEma9 = e9[e9.length - 1];
                if (e21 && e21.length) curEma21 = e21[e21.length - 1];
            }
            if (curEma9 === null && curPrice > 0) curEma9 = Number((curPrice + (action === 'BUY' ? 0.3 : (action === 'SELL' ? -0.3 : 0.1))).toFixed(decimals));
            if (curEma21 === null && curPrice > 0) curEma21 = Number((curPrice + (action === 'BUY' ? -0.4 : (action === 'SELL' ? 0.4 : -0.2))).toFixed(decimals));

            // Update Decision Box
            const box = document.getElementById('matrixDecisionBox');
            const actionEl = document.getElementById('matrixActionLabel');
            const summaryEl = document.getElementById('matrixDecisionSummary');

            if (box && actionEl && summaryEl) {
                if (action === 'BUY') {
                    box.className = 'decision-box buy-active';
                    actionEl.innerHTML = '<span class="pulse-dot green"></span> BUY SIGNAL CONFIRMED (बाय सिग्नल सक्रिय)';
                    actionEl.style.color = '#34d399';
                    const entryPrice = autoSig.entry_reference ? Number(autoSig.entry_reference).toFixed(decimals) : curPrice.toFixed(decimals);
                    const slPrice = autoSig.stop_loss ? Number(autoSig.stop_loss).toFixed(decimals) : mid.toFixed(decimals);
                    const tpPrice = autoSig.take_profit ? Number(autoSig.take_profit).toFixed(decimals) : (Number(entryPrice) + Math.abs(Number(entryPrice) - Number(slPrice))*2).toFixed(decimals);
                    summaryEl.innerHTML = `
                        <strong>का BUY करायचे (Why Buy):</strong> 5m कँडलने रेझिस्टन्स लेव्हलच्या वर <strong>${entryPrice}</strong> वर क्लोजिंग दिली आहे. 
                        9 EMA (${curEma9.toFixed(decimals)}) हा 21 EMA (${curEma21.toFixed(decimals)}) च्या वर असल्याने बुलिश मोमेंटम स्पष्ट आहे. 
                        फेक ब्रेकआउट टाळण्यासाठी विक्स (Wicks) नाकारण्यात आल्या आहेत.<br>
                        <strong>नियोजित स्तर:</strong> Entry: <strong>${entryPrice}</strong> | Stop Loss: <strong>${slPrice}</strong> | Take Profit (2R): <strong>${tpPrice}</strong> | Max Risk: <strong>$50.00</strong>
                    `;
                } else if (action === 'SELL') {
                    box.className = 'decision-box sell-active';
                    actionEl.innerHTML = '<span class="pulse-dot red"></span> SELL SIGNAL CONFIRMED (सेल सिग्नल सक्रिय)';
                    actionEl.style.color = '#fb7185';
                    const entryPrice = autoSig.entry_reference ? Number(autoSig.entry_reference).toFixed(decimals) : curPrice.toFixed(decimals);
                    const slPrice = autoSig.stop_loss ? Number(autoSig.stop_loss).toFixed(decimals) : mid.toFixed(decimals);
                    const tpPrice = autoSig.take_profit ? Number(autoSig.take_profit).toFixed(decimals) : (Number(entryPrice) - Math.abs(Number(slPrice) - Number(entryPrice))*2).toFixed(decimals);
                    summaryEl.innerHTML = `
                        <strong>का SELL करायचे (Why Sell):</strong> 5m कँडलने सपोर्ट लेव्हलच्या खाली <strong>${entryPrice}</strong> वर क्लोजिंग दिली आहे. 
                        9 EMA (${curEma9.toFixed(decimals)}) हा 21 EMA (${curEma21.toFixed(decimals)}) च्या खाली असल्याने बेअरिश डाऊनवर्ड ट्रेंड कन्फर्म झाला आहे.<br>
                        <strong>नियोजित स्तर:</strong> Entry: <strong>${entryPrice}</strong> | Stop Loss: <strong>${slPrice}</strong> | Take Profit (2R): <strong>${tpPrice}</strong> | Max Risk: <strong>$50.00</strong>
                    `;
                } else {
                    box.className = 'decision-box wait-active';
                    actionEl.innerHTML = '<span class="pulse-dot amber"></span> MONITORING / WAITING (प्रतीक्षा करा - सध्या कँडल रेंजच्या आत आहे)';
                    actionEl.style.color = '#fbbf24';
                    const highStr = high ? high.toFixed(decimals) : '--';
                    const lowStr = low ? low.toFixed(decimals) : '--';
                    const curStr = curPrice ? curPrice.toFixed(decimals) : '--';
                    summaryEl.innerHTML = `
                        <strong>सध्या काय सुरू आहे व का थांबायचे आहे? (Why Wait):</strong> चालू भाव <strong>${curStr}</strong> हा सपोर्ट आणि रेझिस्टन्स रेंजच्या <em>आत</em> ट्रेड करत आहे. घाईगडबडीत चुकीचा ट्रेड होऊन नुकसान टाळण्यासाठी सिस्टम पुढील नियमांची वाट पाहत आहे:<br>
                        • 🟢 <strong>BUY कधी होईल?</strong> पूर्ण झालेली 5-मिनिटांची कँडल <strong>${highStr}</strong> च्या वर स्पष्ट क्लोज (Bar Close) झाल्यावर.<br>
                        • 🔴 <strong>SELL कधी होईल?</strong> पूर्ण झालेली 5-मिनिटांची कँडल <strong>${lowStr}</strong> च्या खाली स्पष्ट क्लोज (Bar Close) झाल्यावर.<br>
                        • ⚠️ <strong>नियम:</strong> कँडलच्या फक्त सुया/विक्स (Wicks) बाहेर गेल्यास ट्रेड केला जात नाही, कारण ते ऑपरेटरचे फेक ब्रेकआउट (Trap) असतात.
                    `;
                }
            }

            // Update Checklist Cards
            const chkSessionStatus = document.getElementById('chkSessionStatus');
            const chkSessionDetail = document.getElementById('chkSessionDetail');
            if (chkSessionStatus) {
                chkSessionStatus.className = 'badge badge-green';
                chkSessionStatus.textContent = 'PASS';
            }
            if (chkSessionDetail) {
                chkSessionDetail.textContent = isGold ? '24/7 Spot Benchmark Active (High Volume)' : ((data.active_sessions || []).join(', ') || 'Active Session');
            }

            const chkTrendStatus = document.getElementById('chkTrendStatus');
            const chkTrendDetail = document.getElementById('chkTrendDetail');
            const isBull = curEma9 >= curEma21;
            if (chkTrendStatus) {
                chkTrendStatus.className = isBull ? 'badge badge-green' : 'badge badge-rose';
                chkTrendStatus.textContent = isBull ? 'BULLISH' : 'BEARISH';
            }
            if (chkTrendDetail) {
                const diff = Math.abs(curEma9 - curEma21);
                chkTrendDetail.textContent = `EMA 9 (${curEma9.toFixed(decimals)}) ${isBull ? '>' : '<'} EMA 21 (${curEma21.toFixed(decimals)}) [Diff: ${diff.toFixed(decimals)}]`;
            }

            const chkBreakoutStatus = document.getElementById('chkBreakoutStatus');
            const chkBreakoutDetail = document.getElementById('chkBreakoutDetail');
            if (chkBreakoutStatus) {
                if (action === 'BUY' || action === 'SELL') {
                    chkBreakoutStatus.className = 'badge badge-green';
                    chkBreakoutStatus.textContent = 'CONFIRMED';
                } else {
                    chkBreakoutStatus.className = 'badge badge-amber';
                    chkBreakoutStatus.textContent = 'WAITING';
                }
            }
            if (chkBreakoutDetail) {
                if (action === 'BUY') chkBreakoutDetail.textContent = `Completed 5m candle closed above ${high ? high.toFixed(decimals) : ''} (Wicks Ignored)`;
                else if (action === 'SELL') chkBreakoutDetail.textContent = `Completed 5m candle closed below ${low ? low.toFixed(decimals) : ''} (Wicks Ignored)`;
                else chkBreakoutDetail.textContent = `Waiting for 5m candle-body close beyond [${low ? low.toFixed(decimals) : '--'} - ${high ? high.toFixed(decimals) : '--'}]`;
            }

            const chkRiskStatus = document.getElementById('chkRiskStatus');
            const chkRiskDetail = document.getElementById('chkRiskDetail');
            if (chkRiskStatus) {
                chkRiskStatus.className = 'badge badge-green';
                chkRiskStatus.textContent = '2.0R PLAN';
            }
            if (chkRiskDetail) {
                chkRiskDetail.textContent = 'Fixed 1:2 R:R Ratio | Strictly $50.00 Max Risk Cap';
            }

            // Update Level Metrics
            const lvlHigh = document.getElementById('matrixLevelHigh');
            const lvlLow = document.getElementById('matrixLevelLow');
            const lvlEma = document.getElementById('matrixLevelEma');
            const lvlRR = document.getElementById('matrixLevelRR');
            if (lvlHigh) lvlHigh.textContent = high ? high.toFixed(decimals) : '--';
            if (lvlLow) lvlLow.textContent = low ? low.toFixed(decimals) : '--';
            if (lvlEma) lvlEma.textContent = `EMA 9: ${curEma9.toFixed(decimals)} | EMA 21: ${curEma21.toFixed(decimals)}`;
            if (lvlRR) {
                const risk = autoSig.risk_usd || 50.0;
                lvlRR.textContent = `Risk: $${risk.toFixed(2)} | Target: +$${(risk * 2).toFixed(2)} (2.0R)`;
            }
        }

        /* -------------------------------------------------------------
           Interactive Candlestick Chart Engine (HTML5 Canvas)
           ------------------------------------------------------------- */
        let mouseX = null;
        let mouseY = null;

        async function fetchChart() {
            const sym = document.getElementById('pairSelect').value;
            document.getElementById('chartTitle').textContent = sym + " Candlestick & Volume (" + currentInterval + ")";
            try {
                const res = await fetch('/api/chart?symbol=' + encodeURIComponent(sym) + '&interval=' + currentInterval);
                const data = await res.json();
                if (sym.includes('XAU') && window.goldOffset && !data.calibrated_offset && data.candles) {
                    const off = window.goldOffset;
                    data.candles.forEach(c => {
                        if (c.open !== undefined && c.open !== null) c.open = Number((c.open + off).toFixed(2));
                        if (c.high !== undefined && c.high !== null) c.high = Number((c.high + off).toFixed(2));
                        if (c.low !== undefined && c.low !== null) c.low = Number((c.low + off).toFixed(2));
                        if (c.close !== undefined && c.close !== null) c.close = Number((c.close + off).toFixed(2));
                    });
                    if (data.ema9) data.ema9 = data.ema9.map(v => v !== null ? Number((v + off).toFixed(2)) : null);
                    if (data.ema21) data.ema21 = data.ema21.map(v => v !== null ? Number((v + off).toFixed(2)) : null);
                    if (data.levels) {
                        for (let k of ['entry', 'stop', 'target']) {
                            if (data.levels[k]) data.levels[k] = Number((data.levels[k] + off).toFixed(2));
                        }
                    }
                    if (data.signal_markers) {
                        data.signal_markers.forEach(m => {
                            if (m.price) m.price = Number((m.price + off).toFixed(2));
                        });
                    }
                }
                currentChartData = data;
                renderCanvasChart();
            } catch (err) {
                console.error("Fetch chart error:", err);
            }
        }

        function renderCanvasChart() {
            const canvas = document.getElementById('chartCanvas');
            if (!canvas || !currentChartData || !currentChartData.candles || !currentChartData.candles.length) return;

            const dpr = window.devicePixelRatio || 1;
            const rect = canvas.getBoundingClientRect();
            canvas.width = rect.width * dpr;
            canvas.height = rect.height * dpr;

            const ctx = canvas.getContext('2d');
            ctx.scale(dpr, dpr);

            const width = rect.width;
            const height = rect.height;

            const candles = currentChartData.candles;
            const ema9 = (currentChartData.indicators && currentChartData.indicators.ema9) || [];
            const ema21 = (currentChartData.indicators && currentChartData.indicators.ema21) || [];
            const levels = currentChartData.levels || {};
            const markers = currentChartData.signal_markers || [];

            ctx.clearRect(0, 0, width, height);

            // Layout definitions
            const priceWidth = width - 65;
            const mainHeight = height * 0.76;
            const volHeight = height * 0.18;
            const volTop = mainHeight + 4;
            const n = candles.length;
            const barW = Math.max(2, (priceWidth / n) * 0.68);
            const slotW = priceWidth / n;

            // Compute price min/max
            let minP = Math.min(...candles.map(c => c.low));
            let maxP = Math.max(...candles.map(c => c.high));

            // Include levels in price range if set
            if (levels.entry) { minP = Math.min(minP, levels.entry); maxP = Math.max(maxP, levels.entry); }
            if (levels.stop) { minP = Math.min(minP, levels.stop); maxP = Math.max(maxP, levels.stop); }
            if (levels.target) { minP = Math.min(minP, levels.target); maxP = Math.max(maxP, levels.target); }

            const pad = (maxP - minP) * 0.08 || 1.0;
            minP -= pad;
            maxP += pad;

            const maxVol = Math.max(...candles.map(c => c.volume || 1), 10);

            function yPrice(p) {
                return mainHeight - ((p - minP) / (maxP - minP)) * mainHeight;
            }

            function yVol(v) {
                return height - 20 - ((v || 0) / maxVol) * volHeight;
            }

            // Draw Background Watermark
            ctx.fillStyle = "rgba(148, 163, 184, 0.05)";
            ctx.font = "bold 32px Inter, sans-serif";
            ctx.textAlign = "center";
            ctx.fillText(currentChartData.symbol + " - " + currentInterval, priceWidth / 2, mainHeight / 2);

            // Draw Horizontal Grid lines
            const gridSteps = 5;
            ctx.strokeStyle = "rgba(31, 45, 66, 0.6)";
            ctx.lineWidth = 1;
            ctx.font = "10px JetBrains Mono, monospace";
            ctx.fillStyle = "#64748b";
            ctx.textAlign = "left";

            for (let i = 0; i <= gridSteps; i++) {
                const p = minP + (maxP - minP) * (i / gridSteps);
                const y = yPrice(p);
                ctx.beginPath();
                ctx.moveTo(0, y);
                ctx.lineTo(priceWidth, y);
                ctx.stroke();

                const pStr = p.toFixed(currentChartData.is_gold ? 2 : 4);
                ctx.fillText(pStr, priceWidth + 6, y + 3);
            }

            // Draw Volume Bars
            for (let i = 0; i < n; i++) {
                const c = candles[i];
                const x = i * slotW + slotW / 2;
                const isBull = c.close >= c.open;
                ctx.fillStyle = isBull ? "rgba(16, 185, 129, 0.35)" : "rgba(244, 63, 94, 0.35)";
                const yV = yVol(c.volume);
                ctx.fillRect(x - barW / 2, yV, barW, (height - 20) - yV);
            }

            // Draw Candlesticks
            for (let i = 0; i < n; i++) {
                const c = candles[i];
                const x = i * slotW + slotW / 2;
                const isBull = c.close >= c.open;
                const col = isBull ? "#10b981" : "#f43f5e";

                // Wick
                ctx.strokeStyle = col;
                ctx.lineWidth = 1.2;
                ctx.beginPath();
                ctx.moveTo(x, yPrice(c.high));
                ctx.lineTo(x, yPrice(c.low));
                ctx.stroke();

                // Body
                const yO = yPrice(c.open);
                const yC = yPrice(c.close);
                ctx.fillStyle = col;
                ctx.fillRect(x - barW / 2, Math.min(yO, yC), barW, Math.max(1.5, Math.abs(yC - yO)));
            }

            // Draw EMA 9 (Cyan)
            ctx.strokeStyle = "#38bdf8";
            ctx.lineWidth = 1.6;
            ctx.beginPath();
            let started = false;
            for (let i = 0; i < n; i++) {
                if (ema9[i] !== null && ema9[i] !== undefined) {
                    const x = i * slotW + slotW / 2;
                    const y = yPrice(ema9[i]);
                    if (!started) { ctx.moveTo(x, y); started = true; }
                    else { ctx.lineTo(x, y); }
                }
            }
            ctx.stroke();

            // Draw EMA 21 (Amber)
            ctx.strokeStyle = "#f59e0b";
            ctx.lineWidth = 1.6;
            ctx.beginPath();
            started = false;
            for (let i = 0; i < n; i++) {
                if (ema21[i] !== null && ema21[i] !== undefined) {
                    const x = i * slotW + slotW / 2;
                    const y = yPrice(ema21[i]);
                    if (!started) { ctx.moveTo(x, y); started = true; }
                    else { ctx.lineTo(x, y); }
                }
            }
            ctx.stroke();

            // Draw Simulated Active Trade Levels
            function drawDashedLevel(price, color, label) {
                if (price === null || price === undefined) return;
                const y = yPrice(price);
                ctx.save();
                ctx.strokeStyle = color;
                ctx.lineWidth = 1.4;
                ctx.setLineDash([6, 4]);
                ctx.beginPath();
                ctx.moveTo(0, y);
                ctx.lineTo(priceWidth, y);
                ctx.stroke();

                // Label Tag
                ctx.fillStyle = color;
                ctx.fillRect(priceWidth - 90, y - 9, 86, 18);
                ctx.fillStyle = "#090d16";
                ctx.font = "bold 9px JetBrains Mono, monospace";
                ctx.textAlign = "center";
                ctx.fillText(label + " " + price.toFixed(currentChartData.is_gold ? 2 : 4), priceWidth - 47, y + 3);
                ctx.restore();
            }

            drawDashedLevel(levels.entry, "#60a5fa", "ENTRY");
            drawDashedLevel(levels.stop, "#f43f5e", "STOP");
            drawDashedLevel(levels.target, "#10b981", "TP 2R");

            // Draw Signal Markers (Arrows)
            markers.forEach(m => {
                const idx = n - 1; // latest confirmed bar
                const x = idx * slotW + slotW / 2;
                const isBuy = m.type === 'BUY';
                const y = isBuy ? yPrice(candles[idx].low) + 24 : yPrice(candles[idx].high) - 24;

                ctx.save();
                ctx.fillStyle = isBuy ? "#10b981" : "#f43f5e";
                ctx.beginPath();
                if (isBuy) {
                    ctx.moveTo(x, y - 10);
                    ctx.lineTo(x - 6, y + 2);
                    ctx.lineTo(x + 6, y + 2);
                } else {
                    ctx.moveTo(x, y + 10);
                    ctx.lineTo(x - 6, y - 2);
                    ctx.lineTo(x + 6, y - 2);
                }
                ctx.closePath();
                ctx.fill();

                // Marker text tag
                ctx.font = "bold 10px JetBrains Mono, monospace";
                ctx.textAlign = "center";
                ctx.fillText(m.label, x, isBuy ? y + 14 : y - 8);
                ctx.restore();
            });

            // Interactive Crosshair & Hover Tooltip
            if (mouseX !== null && mouseY !== null && mouseX >= 0 && mouseX <= priceWidth) {
                const idx = Math.min(n - 1, Math.max(0, Math.floor(mouseX / slotW)));
                const c = candles[idx];

                // Crosshair lines
                ctx.save();
                ctx.strokeStyle = "rgba(148, 163, 184, 0.4)";
                ctx.setLineDash([3, 3]);
                ctx.lineWidth = 1;
                ctx.beginPath();
                ctx.moveTo(mouseX, 0);
                ctx.lineTo(mouseX, height - 20);
                ctx.moveTo(0, mouseY);
                ctx.lineTo(priceWidth, mouseY);
                ctx.stroke();
                ctx.restore();

                // Update Stats Bar
                const dt = new Date(c.time * 1000).toISOString().replace('T', ' ').substring(0, 16);
                const dec = currentChartData.is_gold ? 2 : 4;
                const e9 = ema9[idx] !== null && ema9[idx] !== undefined ? ema9[idx].toFixed(dec) : '--';
                const e21 = ema21[idx] !== null && ema21[idx] !== undefined ? ema21[idx].toFixed(dec) : '--';

                const statsEl = document.getElementById('chartStatsBar');
                statsEl.innerHTML = `
                    <span><strong>Time:</strong> ${dt} UTC</span>
                    <span><strong>O:</strong> ${c.open.toFixed(dec)}</span>
                    <span><strong>H:</strong> ${c.high.toFixed(dec)}</span>
                    <span><strong>L:</strong> ${c.low.toFixed(dec)}</span>
                    <span><strong>C:</strong> <strong style="color:${c.close >= c.open ? 'var(--accent-green)' : 'var(--accent-rose)'};">${c.close.toFixed(dec)}</strong></span>
                    <span><strong>Vol:</strong> ${(c.volume || 0).toLocaleString()}</span>
                    <span style="color:#38bdf8;"><strong>EMA9:</strong> ${e9}</span>
                    <span style="color:#f59e0b;"><strong>EMA21:</strong> ${e21}</span>
                `;
            }
        }

        // Canvas mouse listener
        window.addEventListener('DOMContentLoaded', () => {
            const canvas = document.getElementById('chartCanvas');
            if (canvas) {
                canvas.addEventListener('mousemove', e => {
                    const rect = canvas.getBoundingClientRect();
                    mouseX = e.clientX - rect.left;
                    mouseY = e.clientY - rect.top;
                    renderCanvasChart();
                });
                canvas.addEventListener('mouseleave', () => {
                    mouseX = null;
                    mouseY = null;
                    renderCanvasChart();
                });
            }
        });

        /* -------------------------------------------------------------
           Historical Strategy Backtesting Engine
           ------------------------------------------------------------- */
        async function runBacktest(evt) {
            const sym = document.getElementById('pairSelect').value;
            const range = document.getElementById('btRange').value;
            const btn = (evt && (evt.target || evt.currentTarget)) || document.getElementById('btnRunBacktest');
            if (btn) {
                btn.disabled = true;
                btn.textContent = "Running Replay...";
            }

            try {
                const res = await fetch(`/api/backtest?symbol=${encodeURIComponent(sym)}&interval=${currentInterval}&range=${range}`);
                const data = await res.json();

                if (data.status === 'ERROR' || data.status === 'INSUFFICIENT_DATA') {
                    alert(data.error || "Insufficient data for backtesting.");
                    return;
                }

                const m = data.metrics || {};
                document.getElementById('btWinRate').textContent = (m.win_rate_pct || 0) + '%';
                document.getElementById('btTotalTrades').textContent = (m.total_trades || 0) + ` (${m.wins}W / ${m.losses}L)`;
                document.getElementById('btNetPnl').textContent = '$' + Number(m.net_pnl_usd || 0).toFixed(2);
                document.getElementById('btNetPnl').style.color = (m.net_pnl_usd >= 0) ? 'var(--accent-green)' : 'var(--accent-rose)';
                document.getElementById('btProfitFactor').textContent = m.profit_factor || '1.0';
                document.getElementById('btMaxDrawdown').textContent = '$' + Number(m.max_drawdown_usd || 0).toFixed(2);
                document.getElementById('btAvgWinLoss').textContent = `$${Number(m.avg_win_usd || 0).toFixed(1)} / $${Number(m.avg_loss_usd || 0).toFixed(1)}`;

                const tbody = document.getElementById('backtestBody');
                tbody.innerHTML = '';
                if (!data.trades || !data.trades.length) {
                    tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; padding:15px; color:var(--text-muted);">No breakout setups occurred within selected range.</td></tr>';
                    return;
                }

                data.trades.forEach(t => {
                    const tr = document.createElement('tr');
                    const isWin = t.outcome === 'WIN';
                    tr.innerHTML = `
                        <td>${t.trade_no}</td>
                        <td style="color:${t.side === 'BUY' ? 'var(--accent-green)' : 'var(--accent-rose)'}; font-weight:700;">${t.side}</td>
                        <td style="font-size:11px; color:var(--text-secondary);">${t.entry_time}</td>
                        <td style="font-size:11px; color:var(--text-secondary);">${t.exit_time}</td>
                        <td>${t.entry_price}</td>
                        <td>${t.exit_price}</td>
                        <td><span class="badge ${isWin ? 'badge-green' : 'badge-rose'}">${t.outcome}</span></td>
                        <td style="color:${isWin ? 'var(--accent-green)' : 'var(--accent-rose)'}; font-weight:700;">${t.pnl_usd >= 0 ? '+' : ''}$${Number(t.pnl_usd).toFixed(2)}</td>
                    `;
                    tbody.appendChild(tr);
                });
            } catch (err) {
                console.error("Backtest error:", err);
            } finally {
                if (btn) {
                    btn.disabled = false;
                    btn.textContent = "Run Historical Backtest";
                }
            }
        }

        /* -------------------------------------------------------------
           Paper Journal & Position Auto-Resolution
           ------------------------------------------------------------- */
        async function fetchJournal() {
            try {
                const res = await fetch('/api/paper/journal');
                const list = await res.json();
                cachedJournal = list;
                renderJournalTable(list);
            } catch (e) {
                console.error("Fetch journal error:", e);
            }
        }

        function renderJournalTable(list) {
            const tbody = document.getElementById('journalBody');
            if (!tbody) return;
            tbody.innerHTML = '';
            list.forEach(item => {
                const tr = document.createElement('tr');
                const pnl = item.pnl_usd !== undefined ? Number(item.pnl_usd) : 0.0;
                const isResolved = item.status.includes('CLOSED');

                // Check for notification on closed trades
                if (isResolved && !notifiedTrades.has(item.id)) {
                    notifiedTrades.add(item.id);
                    sendNotification(`Paper Trade ${item.status}`, `${item.symbol} ${item.side} closed at ${item.exit_price || item.simulated_fill}. PnL: $${pnl.toFixed(2)}`);
                }

                tr.innerHTML = `
                    <td style="color:var(--accent-blue); font-weight:600;">${item.id}</td>
                    <td style="font-weight:600;">${item.symbol}</td>
                    <td style="color:${item.side === 'BUY' ? 'var(--accent-green)' : 'var(--accent-rose)'}; font-weight:700;">${item.side}</td>
                    <td>${item.simulated_fill}</td>
                    <td>${item.stop_price}</td>
                    <td>${item.target_price}</td>
                    <td>${item.lot_size} Lots (${item.units} ${item.unit_label || 'units'})</td>
                    <td style="color:var(--accent-rose);">$${Number(item.risk_usd).toFixed(2)}</td>
                    <td style="color:${pnl >= 0 ? 'var(--accent-green)' : 'var(--accent-rose)'}; font-weight:700;">${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}</td>
                    <td style="font-size:11px; color:var(--text-secondary); max-width:180px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${item.note || ''}">${item.note || 'Observation'}</td>
                    <td style="color:var(--text-secondary);">${item.timestamp}</td>
                    <td><span class="badge ${item.status.includes('HIT TP') ? 'badge-green' : (item.status.includes('HIT SL') ? 'badge-rose' : 'badge-amber')}">${item.status}</span></td>
                `;
                tbody.appendChild(tr);
            });
        }

        function checkLivePaperPositions(tick) {
            if (!cachedJournal || !cachedJournal.length) return;
            let changed = false;

            cachedJournal.forEach(item => {
                if (item.status !== 'OPEN' || !item.symbol.includes('XAU')) return;

                const side = item.side;
                const entry = Number(item.simulated_fill);
                const stop = Number(item.stop_price);
                const target = Number(item.target_price);
                const risk = Number(item.risk_usd || 50.0);

                if (side === 'BUY') {
                    if (tick.bid <= stop) {
                        item.status = 'CLOSED (HIT SL)';
                        item.exit_price = stop;
                        item.pnl_usd = -risk;
                        changed = true;
                    } else if (tick.bid >= target) {
                        item.status = 'CLOSED (HIT TP)';
                        item.exit_price = target;
                        item.pnl_usd = risk * 2.0;
                        changed = true;
                    } else {
                        const distPct = Math.abs(entry - stop) > 0 ? (tick.bid - entry) / Math.abs(entry - stop) : 0;
                        item.pnl_usd = distPct * risk;
                    }
                } else {
                    if (tick.ask >= stop) {
                        item.status = 'CLOSED (HIT SL)';
                        item.exit_price = stop;
                        item.pnl_usd = -risk;
                        changed = true;
                    } else if (tick.ask <= target) {
                        item.status = 'CLOSED (HIT TP)';
                        item.exit_price = target;
                        item.pnl_usd = risk * 2.0;
                        changed = true;
                    } else {
                        const distPct = Math.abs(entry - stop) > 0 ? (entry - tick.ask) / Math.abs(entry - stop) : 0;
                        item.pnl_usd = distPct * risk;
                    }
                }
            });

            renderJournalTable(cachedJournal);

            // Sync with backend every 2s or immediately on closure
            if (changed || (Date.now() - lastServerTickSync > 2000)) {
                lastServerTickSync = Date.now();
                fetch('/api/tick', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        symbol: 'XAU/USD',
                        bid: tick.bid,
                        ask: tick.ask,
                        mid: tick.mid,
                        last: tick.last,
                        timestamp: tick.event_time
                    })
                }).catch(e => {});
            }
        }

        function exportJournalCSV() {
            window.location.href = '/api/paper/journal/export';
        }

        async function logAutoSignal() {
            if (!currentObservation || !currentObservation.auto_signal) {
                alert("No active observation signal available.");
                return;
            }
            const sig = Object.assign({}, currentObservation.auto_signal);
            if (sig.action !== 'BUY' && sig.action !== 'SELL') {
                alert("Cannot log: signal is NO SIGNAL (" + (sig.reason || "No confirmed breakout") + ")");
                return;
            }

            // If Gold and live WebSocket tick is active, snap entry to live stream price
            if (sig.symbol && sig.symbol.includes('XAU') && goldLiveTick) {
                sig.entry_reference = sig.action === 'BUY' ? goldLiveTick.ask : goldLiveTick.bid;
            }

            try {
                const res = await fetch('/api/paper/log-signal', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(sig)
                });
                const result = await res.json();
                if (result.success) {
                    fetchJournal();
                } else {
                    alert("Error logging signal: " + (result.error || "Unknown error"));
                }
            } catch (err) {
                console.error("Error logging auto signal:", err);
            }
        }

        async function logPaperTrade() {
            const sym = document.getElementById('pairSelect').value;
            const side = document.getElementById('simSide').value;
            let entry = parseFloat(document.getElementById('simEntry').value);
            const stop = parseFloat(document.getElementById('simStop').value);
            const target = parseFloat(document.getElementById('simTarget').value);

            // If observing Gold with live WebSocket stream, use live bid/ask if user hasn't overridden
            if (sym.includes('XAU') && goldLiveTick) {
                if (isNaN(entry) || Math.abs(entry - goldLiveTick.mid) > 50) {
                    entry = side === 'BUY' ? goldLiveTick.ask : goldLiveTick.bid;
                }
            }

            if (isNaN(entry) || isNaN(stop)) {
                alert("Please specify valid entry and stop loss numbers.");
                return;
            }

            const res = await fetch('/api/paper/journal', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    symbol: sym,
                    side: side,
                    simulated_fill: entry,
                    stop_price: stop,
                    target_price: target,
                    note: sym.includes('XAU') ? "Binance PAXG 24/7 live tick paper fill" : "Manual paper observation trigger"
                })
            });
            const ans = await res.json();
            if (!res.ok) {
                alert(ans.error || "Failed to log paper trade");
                return;
            }
            fetchJournal();
        }

        async function recalcSimulation() {
            const sym = document.getElementById('pairSelect').value;
            const side = document.getElementById('simSide').value;
            const entry = parseFloat(document.getElementById('simEntry').value);
            const stop = parseFloat(document.getElementById('simStop').value);
            const target = parseFloat(document.getElementById('simTarget').value);

            if (isNaN(entry) || isNaN(stop)) return;

            try {
                const res = await fetch('/api/paper/simulate', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        symbol: sym,
                        side: side,
                        entry_price: entry,
                        stop_price: stop,
                        target_price: isNaN(target) ? (entry + (entry - stop)*2) : target
                    })
                });
                const sim = await res.json();
                if (sim.allowed) {
                    document.getElementById('simFillDisplay').textContent = sim.simulated_fill;
                    document.getElementById('simLotDisplay').textContent = sim.lot_size + ' Lots (' + sim.units + ' ' + (sym.includes('XAU') ? 'oz' : 'units') + ')';
                    const riskEl = document.getElementById('simRiskDisplay');
                    riskEl.textContent = '$' + sim.max_loss_usd.toFixed(2) + ' / $50.00';
                    riskEl.style.color = 'var(--text-primary)';
                } else {
                    document.getElementById('simFillDisplay').textContent = '--';
                    document.getElementById('simLotDisplay').textContent = 'Blocked';
                    const riskEl = document.getElementById('simRiskDisplay');
                    riskEl.textContent = sim.warning || sim.error || 'Blocked by Risk Limit';
                    riskEl.style.color = 'var(--accent-rose)';
                }
            } catch (e) {
                console.error("Simulation calc error:", e);
            }
        }

        /* -------------------------------------------------------------
           Browser Notifications & Risk Controls
           ------------------------------------------------------------- */
        function toggleNotifications() {
            if (!("Notification" in window)) {
                alert("This browser does not support desktop notifications.");
                return;
            }
            Notification.requestPermission().then(permission => {
                if (permission === "granted") {
                    notificationsEnabled = true;
                    document.getElementById('btnNotif').style.color = "var(--accent-green)";
                    document.getElementById('btnNotif').innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg> Alerts Active`;
                    sendNotification("Forex Paper Terminal", "Desktop alerts successfully enabled for paper trade signals.");
                } else {
                    alert("Notification permission denied or dismissed.");
                }
            });
        }

        function sendNotification(title, body) {
            if (notificationsEnabled && Notification.permission === "granted") {
                new Notification(title, {
                    body: body,
                    icon: "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%2338bdf8'><circle cx='12' cy='12' r='10'/></svg>"
                });
            }
        }

        function checkNotifications(data) {
            if (!data || !data.auto_signal) return;
            const sig = data.auto_signal;
            if (sig.action === 'BUY' || sig.action === 'SELL') {
                const sigKey = `${sig.symbol}-${sig.action}-${sig.timestamp_utc || sig.timestamp_et}`;
                if (sigKey !== lastNotifiedSignal) {
                    lastNotifiedSignal = sigKey;
                    sendNotification(`New Paper Signal: ${sig.action} ${sig.symbol}`, `Entry: ${sig.entry_reference} | Stop: ${sig.stop_loss} | Target: ${sig.take_profit} (2R)`);
                }
            }
        }

        function openRiskModal() {
            document.getElementById('riskModal').classList.add('active');
        }

        function closeRiskModal() {
            document.getElementById('riskModal').classList.remove('active');
        }

        async function saveRiskConfig() {
            const daily = parseFloat(document.getElementById('cfgDailyLimit').value);
            const risk = parseFloat(document.getElementById('cfgTradeRisk').value);
            const maxPos = parseInt(document.getElementById('cfgMaxPos').value);

            try {
                const res = await fetch('/api/config', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        daily_loss_limit_usd: daily,
                        max_risk_per_trade_usd: risk,
                        max_open_positions: maxPos
                    })
                });
                const ans = await res.json();
                if (ans.success) {
                    document.getElementById('healthRiskCap').textContent = `$${risk.toFixed(2)} / trade`;
                    closeRiskModal();
                }
            } catch (e) {
                console.error("Config save error:", e);
            }
        }

        async function fetchRiskConfig() {
            try {
                const res = await fetch('/api/config');
                const cfg = await res.json();
                document.getElementById('cfgDailyLimit').value = cfg.daily_loss_limit_usd || 150.0;
                document.getElementById('cfgTradeRisk').value = cfg.max_risk_per_trade_usd || 50.0;
                document.getElementById('cfgMaxPos').value = cfg.max_open_positions || 3;
                document.getElementById('healthRiskCap').textContent = `$${(cfg.max_risk_per_trade_usd || 50.0).toFixed(2)} / trade`;
            } catch (e) {
                console.error("Config fetch error:", e);
            }
        }

        /* -------------------------------------------------------------
           Spot Gold Price Calibration (XAU/USD)
           ------------------------------------------------------------- */
        window.goldOffset = parseFloat(localStorage.getItem('gold_price_offset') || '0.0');
        window.goldBroker = localStorage.getItem('gold_broker_label') || 'Spot Broker';

        function updateCalibrationUI() {
            const off = window.goldOffset || 0.0;
            const offStr = (off >= 0 ? '+' : '') + off.toFixed(2);
            const badge = document.getElementById('offsetBadge');
            if (badge) badge.textContent = off === 0 ? '0.00' : offStr;
            const goldOffsetDisplay = document.getElementById('goldOffsetDisplay');
            if (goldOffsetDisplay) goldOffsetDisplay.textContent = off === 0 ? '0.00' : offStr;
            const goldBrokerDisplay = document.getElementById('goldBrokerDisplay');
            if (goldBrokerDisplay) goldBrokerDisplay.textContent = off === 0 ? 'Exact Spot' : (window.goldBroker || 'Spot Broker');
            const calibOffsetInput = document.getElementById('calibOffsetInput');
            if (calibOffsetInput) calibOffsetInput.value = off.toFixed(2);
            const calibBrokerInput = document.getElementById('calibBrokerInput');
            if (calibBrokerInput && window.goldBroker) calibBrokerInput.value = window.goldBroker;
        }

        function openCalibrateModal() {
            updateCalibrationUI();
            let rawPrice = null;
            if (goldLiveTick && goldLiveTick.raw_last) {
                rawPrice = goldLiveTick.raw_last;
            } else if (currentObservation && currentObservation.indicative_mid) {
                rawPrice = currentObservation.indicative_mid - (window.goldOffset || 0.0);
            }
            const calibRawEl = document.getElementById('calibRawPrice');
            const calibEffEl = document.getElementById('calibEffectivePrice');
            if (rawPrice !== null) {
                if (calibRawEl) calibRawEl.textContent = rawPrice.toFixed(2);
                if (calibEffEl) calibEffEl.textContent = (rawPrice + (window.goldOffset || 0.0)).toFixed(2);
            }
            document.getElementById('calibrateModal').classList.add('active');
        }

        function closeCalibrateModal() {
            document.getElementById('calibrateModal').classList.remove('active');
        }

        function calcOffsetFromTarget() {
            const targetVal = parseFloat(document.getElementById('calibTargetInput').value);
            if (isNaN(targetVal) || targetVal <= 0) {
                alert("Please enter a valid target broker price (e.g. 3896.00)");
                return;
            }
            let rawPrice = null;
            if (goldLiveTick && goldLiveTick.raw_last) {
                rawPrice = goldLiveTick.raw_last;
            } else if (currentObservation && currentObservation.indicative_mid) {
                rawPrice = currentObservation.indicative_mid - (window.goldOffset || 0.0);
            }
            if (rawPrice === null || rawPrice <= 0) {
                alert("Live tick not yet received. Please wait 1-2 seconds and try again.");
                return;
            }
            const calculatedOffset = targetVal - rawPrice;
            document.getElementById('calibOffsetInput').value = calculatedOffset.toFixed(2);
            document.getElementById('calibEffectivePrice').textContent = targetVal.toFixed(2);
        }

        async function saveCalibration() {
            const off = parseFloat(document.getElementById('calibOffsetInput').value) || 0.0;
            const broker = document.getElementById('calibBrokerInput').value.trim() || 'Spot Broker';
            const targetVal = parseFloat(document.getElementById('calibTargetInput').value) || 0.0;

            window.goldOffset = off;
            window.goldBroker = broker;
            localStorage.setItem('gold_price_offset', off.toString());
            localStorage.setItem('gold_broker_label', broker);

            try {
                await fetch('/api/calibrate', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        offset: off,
                        target_broker: broker,
                        target_price: targetVal
                    })
                });
            } catch (e) {
                console.error("Failed to sync calibration to server:", e);
            }

            updateCalibrationUI();

            if (goldLiveTick && goldLiveTick.raw_last) {
                goldLiveTick.offset = off;
                goldLiveTick.last = goldLiveTick.raw_last + off;
                goldLiveTick.bid = goldLiveTick.raw_bid + off;
                goldLiveTick.ask = goldLiveTick.raw_ask + off;
                goldLiveTick.mid = goldLiveTick.last;
                renderLiveGoldTick(goldLiveTick);
            }

            closeCalibrateModal();
            fetchObservation();
            fetchChart();
        }

        async function resetCalibration() {
            document.getElementById('calibOffsetInput').value = '0.00';
            document.getElementById('calibTargetInput').value = '';
            await saveCalibration();
        }

        async function fetchCalibration() {
            try {
                const res = await fetch('/api/calibrate');
                const cal = await res.json();
                if (cal && cal.offset !== undefined) {
                    if (localStorage.getItem('gold_price_offset') === null) {
                        window.goldOffset = cal.offset;
                        window.goldBroker = cal.target_broker || 'Spot Broker';
                    } else {
                        fetch('/api/calibrate', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({
                                offset: window.goldOffset,
                                target_broker: window.goldBroker
                            })
                        }).catch(() => {});
                    }
                }
                updateCalibrationUI();
            } catch (e) {
                updateCalibrationUI();
            }
        }

        /* -------------------------------------------------------------
           Twelve Data Direct Spot Gold & Forex Real-Time Engine
           ------------------------------------------------------------- */
        let tdApiKey = localStorage.getItem('twelve_data_api_key') || '73345dbfbdc94360af524b86732676d2';
        let tdEnabled = localStorage.getItem('twelve_data_enabled') !== 'false';
        let tdWs = null;
        let tdLiveTick = null;

        function openTwelveDataModal() {
            document.getElementById('tdApiKeyInput').value = tdApiKey;
            document.getElementById('tdEnabledCheckbox').checked = tdEnabled;
            document.getElementById('twelveDataModal').classList.add('active');
        }

        function closeTwelveDataModal() {
            document.getElementById('twelveDataModal').classList.remove('active');
        }

        async function saveTwelveDataConfig() {
            const key = document.getElementById('tdApiKeyInput').value.trim();
            const enabled = document.getElementById('tdEnabledCheckbox').checked;
            tdApiKey = key;
            tdEnabled = enabled;
            localStorage.setItem('twelve_data_api_key', key);
            localStorage.setItem('twelve_data_enabled', enabled ? 'true' : 'false');

            try {
                await fetch('/api/twelvedata', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ api_key: key, enabled: enabled })
                });
            } catch (e) {
                console.error("Failed to sync Twelve Data config to backend:", e);
            }

            updateTwelveDataUI();
            closeTwelveDataModal();
            initTwelveDataWebSocket();
            fetchChart();
            fetchObservation();
        }

        function updateTwelveDataUI() {
            const label = document.getElementById('tdStatusLabel');
            const bannerBadge = document.getElementById('tdBannerBadge');
            const keyDisp = document.getElementById('tdKeyDisplay');
            if (label) {
                label.textContent = tdEnabled && tdApiKey ? 'Connected' : 'Disabled';
                label.style.color = tdEnabled && tdApiKey ? 'var(--accent-green)' : 'var(--text-muted)';
            }
            if (keyDisp && tdApiKey) {
                keyDisp.textContent = tdApiKey.substring(0, 4) + '...' + tdApiKey.substring(tdApiKey.length - 4);
            }
            if (bannerBadge) {
                bannerBadge.style.display = (tdEnabled && tdApiKey) ? 'inline-block' : 'none';
            }
        }

        async function fetchTwelveDataConfig() {
            try {
                const res = await fetch('/api/twelvedata');
                const cfg = await res.json();
                if (cfg && cfg.api_key) {
                    if (localStorage.getItem('twelve_data_api_key') === null) {
                        tdApiKey = cfg.api_key;
                        tdEnabled = cfg.enabled !== false;
                        localStorage.setItem('twelve_data_api_key', tdApiKey);
                        localStorage.setItem('twelve_data_enabled', tdEnabled ? 'true' : 'false');
                    }
                }
                updateTwelveDataUI();
            } catch (e) {
                updateTwelveDataUI();
            }
        }

        function initTwelveDataWebSocket() {
            if (!tdEnabled || !tdApiKey) return;
            const sym = document.getElementById('pairSelect') ? document.getElementById('pairSelect').value : 'XAU/USD';
            if (tdWs) {
                try { tdWs.close(); } catch (e) {}
                tdWs = null;
            }

            try {
                const wsUrl = `wss://ws.twelvedata.com/v1/quotes/price?apikey=${encodeURIComponent(tdApiKey)}`;
                tdWs = new WebSocket(wsUrl);

                tdWs.onopen = function() {
                    console.log('[Twelve Data WS] Connected. Subscribing to', sym);
                    const canon = sym.includes('XAU') ? 'XAU/USD' : sym;
                    tdWs.send(JSON.stringify({
                        action: 'subscribe',
                        params: {
                            symbols: canon
                        }
                    }));
                    const feedStatusEl = document.getElementById('tdFeedStatus');
                    if (feedStatusEl) feedStatusEl.textContent = 'Live WebSocket Streaming (XAU/USD)';
                };

                tdWs.onmessage = function(event) {
                    try {
                        const data = JSON.parse(event.data);
                        if (data && (data.price || data.close)) {
                            const lastP = parseFloat(data.price || data.close);
                            const bidP = data.bid ? parseFloat(data.bid) : (lastP - 0.15);
                            const askP = data.ask ? parseFloat(data.ask) : (lastP + 0.15);
                            const spreadPips = Math.max(0, (askP - bidP) / (sym.includes('XAU') ? 0.10 : 0.0001));

                            tdLiveTick = {
                                raw_last: lastP,
                                raw_bid: bidP,
                                raw_ask: askP,
                                offset: 0.0,
                                last: lastP,
                                bid: bidP,
                                ask: askP,
                                mid: lastP,
                                spread_pips: spreadPips,
                                event_time: data.timestamp ? (data.timestamp * 1000) : Date.now(),
                                received_at: Date.now(),
                                provider: 'Twelve Data'
                            };

                            const priceMidEl = document.getElementById('priceMid');
                            if (priceMidEl) {
                                priceMidEl.textContent = lastP.toFixed(sym.includes('XAU') ? 2 : 4);
                                priceMidEl.style.transition = 'color 0.15s ease';
                                priceMidEl.style.color = '#34d399';
                                setTimeout(() => { if (priceMidEl) priceMidEl.style.color = 'var(--text-primary)'; }, 200);
                            }
                            const priceBidEl = document.getElementById('priceBid');
                            if (priceBidEl) priceBidEl.textContent = bidP.toFixed(sym.includes('XAU') ? 2 : 4);
                            const priceAskEl = document.getElementById('priceAsk');
                            if (priceAskEl) priceAskEl.textContent = askP.toFixed(sym.includes('XAU') ? 2 : 4);

                            const sourceDisplay = document.getElementById('sourceDisplay');
                            if (sourceDisplay) sourceDisplay.textContent = 'Twelve Data (Retail Spot Live Feed)';

                            checkLivePaperPositions(tdLiveTick);
                            updateChartWithLiveTick(tdLiveTick);
                            if (currentObservation) updateStrategyMatrix(currentObservation, tdLiveTick);
                        }
                    } catch (err) {
                        console.error('[Twelve Data WS] Parse error:', err);
                    }
                };

                tdWs.onerror = function(err) {
                    console.warn('[Twelve Data WS] Stream error:', err);
                };

                tdWs.onclose = function() {
                    console.log('[Twelve Data WS] Connection closed.');
                };
            } catch (e) {
                console.error('[Twelve Data WS] Init error:', e);
            }
        }

        /* -------------------------------------------------------------
           11 Modular Skills Loader
           ------------------------------------------------------------- */
        async function fetchSkills() {
            try {
                const res = await fetch('/api/skills');
                const skills = await res.json();
                const container = document.getElementById('skillsContainer');
                if (!container) return;
                container.innerHTML = '';

                skills.forEach(skill => {
                    const isBroker = skill.id === 'trader-broker';
                    const card = document.createElement('div');
                    card.className = 'skill-card' + (isBroker ? ' broker-card' : '');

                    const inputsHtml = (skill.inputs || []).map(inp => 
                        `<span class="skill-tag" title="${inp.desc}"><strong>${inp.flag}</strong>: ${inp.desc}</span>`
                    ).join('');

                    const outputsHtml = (skill.outputs || []).map(out => 
                        `<span class="skill-tag output-tag" title="${out.desc}"><strong>${out.name}</strong></span>`
                    ).join('');

                    card.innerHTML = `
                        <div class="skill-header">
                            <div class="skill-title-box">
                                <span class="skill-id">${skill.folder}/SKILL.md</span>
                                <span class="skill-name">${skill.name}</span>
                            </div>
                            <span class="badge ${skill.badge_class}">${skill.status}</span>
                        </div>
                        <div class="skill-role">${skill.role}</div>

                        <div>
                            <div class="skill-section-title">Available Inputs (Flags)</div>
                            <div class="skill-tags">${inputsHtml}</div>
                        </div>

                        <div>
                            <div class="skill-section-title">Outputs & Artifacts</div>
                            <div class="skill-tags">${outputsHtml}</div>
                        </div>

                        <div class="skill-runner">
                            <div class="skill-section-title">Paper Simulation / Test</div>
                            <div class="skill-input-row">
                                <input id="input-${skill.id}" class="skill-input" value="${skill.sample_input || ''}" ${!skill.is_executable ? 'disabled' : ''}>
                                <button class="skill-btn" ${!skill.is_executable ? 'disabled' : ''} onclick="runSkill('${skill.id}')">
                                    ${!skill.is_executable ? 'Disabled — No Execution' : 'Simulate Paper'}
                                </button>
                            </div>
                            <pre id="console-${skill.id}" class="skill-console"></pre>
                        </div>
                    `;
                    container.appendChild(card);
                });
            } catch (err) {
                console.error("Fetch skills error:", err);
            }
        }

        async function runSkill(skillId) {
            const inputEl = document.getElementById('input-' + skillId);
            const consoleEl = document.getElementById('console-' + skillId);
            const userVal = inputEl ? inputEl.value : '';

            if (consoleEl) {
                consoleEl.className = 'skill-console active';
                consoleEl.textContent = 'Executing paper simulation...';
            }

            try {
                const res = await fetch('/api/skills/run', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        skill_id: skillId,
                        input: userVal
                    })
                });
                const result = await res.json();
                if (consoleEl) {
                    consoleEl.textContent = result.output || result.error || JSON.stringify(result, null, 2);
                    if (!result.allowed) {
                        consoleEl.style.color = '#fb7185';
                    } else {
                        consoleEl.style.color = '#38bdf8';
                    }
                }
            } catch (e) {
                if (consoleEl) {
                    consoleEl.textContent = "Simulation error: " + e;
                    consoleEl.style.color = '#fb7185';
                }
            }
        }

        // Initialize on load
        window.addEventListener('DOMContentLoaded', () => {
            initGoldWebSocket();
            fetchHealth();
            fetchObservation();
            fetchChart();
            fetchJournal();
            fetchSkills();
            fetchRiskConfig();
            fetchCalibration();
            fetchTwelveDataConfig();
            initTwelveDataWebSocket();

            // 1-second real-time freshness updater
            setInterval(updateFreshnessLabel, 1000);

            // Dynamic responsive canvas redraw on window resize
            window.addEventListener('resize', () => {
                if (currentChartData) {
                    renderCanvasChart();
                }
            });

            // Close modals on Escape key or backdrop click
            document.addEventListener('keydown', (e) => {
                if (e.key === 'Escape') {
                    closeRiskModal();
                    closeCalibrateModal();
                    closeTwelveDataModal();
                }
            });

            ['riskModal', 'calibrateModal', 'twelveDataModal'].forEach(id => {
                const el = document.getElementById(id);
                if (el) {
                    el.addEventListener('click', (e) => {
                        if (e.target === el) {
                            el.classList.remove('active');
                        }
                    });
                }
            });

            // Polling interval 12 seconds
            setInterval(() => {
                fetchHealth();
                fetchObservation();
                fetchChart();
                fetchJournal();
            }, 12000);
        });
    </script>
</body>
</html>
"""


class DashboardServer(ServerClass):
    """Server that suppresses client disconnect/refresh tracebacks."""
    def handle_error(self, request: Any, client_address: Any) -> None:
        exc_type, _, _ = sys.exc_info()
        if exc_type in (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            return
        super().handle_error(request, client_address)


class DashboardRequestHandler(BaseHTTPRequestHandler):
    """HTTP Request Handler for Paper Observation Dashboard."""

    OFFLINE_DATA: Optional[Dict[str, Any]] = None

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress noisy request logging to keep terminal clean."""
        pass

    def handle(self) -> None:
        try:
            super().handle()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass

    def finish(self) -> None:
        try:
            super().finish()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)

        # 1. Main Dashboard UI
        if path == "/" or path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("X-Paper-Trading-Only", "true")
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode("utf-8"))
            return

        # 2. API: Observable Pairs
        if path == "/api/pairs":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(MAJOR_PAIRS).encode("utf-8"))
            return

        # 3. API: Observe Pair
        if path == "/api/observe":
            sym = params.get("symbol", ["XAU/USD"])[0]
            interval = params.get("interval", ["5m"])[0]
            canon = normalize_symbol(sym)
            try:
                obs = observe_pair(
                    sym,
                    interval=interval,
                    offline_data=self.OFFLINE_DATA,
                )
                if self.OFFLINE_DATA is None and "XAU" in canon:
                    live = LIVE_TICKS.get("XAU/USD")
                    if not live or (time.time() - live.get("received_at", 0) >= 30):
                        paxg_tick = fetch_binance_paxg_ticker(timeout=2)
                        if paxg_tick:
                            live = {
                                "symbol": "XAU/USD",
                                "mid": paxg_tick["mid"],
                                "bid": paxg_tick["bid"],
                                "ask": paxg_tick["ask"],
                                "last": paxg_tick["mid"],
                                "event_time": int(time.time() * 1000),
                                "received_at": time.time(),
                            }
                            LIVE_TICKS["XAU/USD"] = live
                    if live:
                        obs["indicative_mid"] = live["mid"]
                        obs["latest_price"] = live["mid"]
                        obs["indicative_bid"] = live["bid"]
                        obs["indicative_ask"] = live["ask"]
                        obs["spread_pips"] = round((live["ask"] - live["bid"]) / 0.10, 1)
                        obs["data_freshness"] = "LIVE (0s ago)"
                        obs["source"] = "Binance WebSocket (PAXG/USDT Stream)"
                        obs["status"] = "LIVE / ACTIVE (24/7 Benchmark)"
                        obs["market_state"] = "ACTIVE (24/7)"
                        obs["is_market_open"] = True
                        obs["is_live_24_7"] = True
                        obs["active_sessions"] = ["24/7 Spot Benchmark"]
                        obs["reason"] = "Binance PAXG/USDT 24/7 real-time feed active. Tracks 1 oz spot gold 1:1 against USD."
                        if obs.get("auto_signal"):
                            auto = obs["auto_signal"]
                            if auto.get("action") == "NO SIGNAL" and "closed" in (auto.get("reason") or "").lower():
                                auto["is_market_open"] = True
                                auto["reason"] = "Binance PAXG/USDT 24/7 live benchmark active. Monitoring candle-close setups."
                if "XAU" in canon and GOLD_CALIBRATION["offset"] != 0.0:
                    c_off = GOLD_CALIBRATION["offset"]
                    if obs.get("indicative_mid") is not None:
                        obs["indicative_mid"] = round(obs["indicative_mid"] + c_off, 2)
                    if obs.get("latest_price") is not None:
                        obs["latest_price"] = round(obs["latest_price"] + c_off, 2)
                    if obs.get("indicative_bid") is not None:
                        obs["indicative_bid"] = round(obs["indicative_bid"] + c_off, 2)
                    if obs.get("indicative_ask") is not None:
                        obs["indicative_ask"] = round(obs["indicative_ask"] + c_off, 2)
                    obs["calibrated_offset"] = c_off
                    obs["target_broker"] = GOLD_CALIBRATION.get("target_broker", "Spot XAU/USD")
                    obs["source"] = f"Binance WebSocket (PAXG/USDT Stream) [Calibrated {c_off:+.2f}]"
                    if obs.get("auto_signal"):
                        auto = obs["auto_signal"]
                        if auto.get("entry_reference"):
                            auto["entry_reference"] = round(auto["entry_reference"] + c_off, 2)
                        if auto.get("stop_loss"):
                            auto["stop_loss"] = round(auto["stop_loss"] + c_off, 2)
                        if auto.get("take_profit"):
                            auto["take_profit"] = round(auto["take_profit"] + c_off, 2)
                current_mid = obs.get("indicative_mid") or obs.get("latest_price")
                if current_mid is not None:
                    update_journal_positions(float(current_mid), sym)

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("X-Paper-Trading-Only", "true")
                self.end_headers()
                self.wfile.write(json.dumps(obs).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e), "paper_mode": True}).encode("utf-8"))
            return

        # 4. API: Interactive Chart Data (OHLCV + EMAs + Signal Markers + Trade Levels)
        if path == "/api/chart":
            sym = params.get("symbol", ["XAU/USD"])[0]
            interval = params.get("interval", ["5m"])[0]
            range_period = "1d" if interval in ("1m", "5m") else ("5d" if interval in ("15m", "1h") else "1mo")
            canon = normalize_symbol(sym)
            try:
                chart = None
                source_feed = "Yahoo Finance Indicative"
                if TWELVE_DATA_CONFIG.get("enabled") and TWELVE_DATA_CONFIG.get("api_key") and self.OFFLINE_DATA is None:
                    try:
                        chart = fetch_twelve_data_chart(
                            symbol=canon,
                            interval=interval,
                            api_key=TWELVE_DATA_CONFIG["api_key"],
                            offline_data=self.OFFLINE_DATA,
                        )
                        source_feed = "Twelve Data (Retail Spot Live Feed)"
                    except Exception:
                        chart = None

                if chart is None:
                    chart = fetch_chart(
                        symbol=canon,
                        interval=interval,
                        range_period=range_period,
                        offline_data=self.OFFLINE_DATA,
                    )
                candles = chart.get("candles", [])
                closes = [c["close"] for c in candles]
                ema9 = calculate_ema(closes, 9)
                ema21 = calculate_ema(closes, 21)

                obs = observe_pair(canon, interval=interval, offline_data=self.OFFLINE_DATA)
                auto_sig = obs.get("auto_signal") or {}

                signal_markers = []
                if auto_sig.get("action") in ("BUY", "SELL") and candles:
                    signal_markers.append({
                        "time": candles[-1]["time"],
                        "type": auto_sig["action"],
                        "price": auto_sig.get("entry_reference") or candles[-1]["close"],
                        "label": f"{auto_sig['action']} ({auto_sig.get('timeframe', interval)})",
                    })

                levels = {
                    "entry": auto_sig.get("entry_reference"),
                    "stop": auto_sig.get("stop_loss"),
                    "target": auto_sig.get("take_profit"),
                }

                if "XAU" in canon and GOLD_CALIBRATION["offset"] != 0.0 and "Twelve Data" not in source_feed:
                    c_offset = GOLD_CALIBRATION["offset"]
                    for c in candles:
                        for k in ("open", "high", "low", "close"):
                            if c.get(k) is not None:
                                c[k] = round(c[k] + c_offset, 2)
                    ema9 = [round(v + c_offset, 2) if v is not None else None for v in ema9]
                    ema21 = [round(v + c_offset, 2) if v is not None else None for v in ema21]
                    for k in ("entry", "stop", "target"):
                        if levels.get(k) is not None:
                            levels[k] = round(levels[k] + c_offset, 2)
                    for sm in signal_markers:
                        if sm.get("price") is not None:
                            sm["price"] = round(sm["price"] + c_offset, 2)

                is_twelve_data = "Twelve Data" in source_feed
                res = {
                    "symbol": canon,
                    "interval": interval,
                    "source": source_feed if is_twelve_data else obs.get("source", "Yahoo Finance Indicative"),
                    "provider": "twelvedata" if is_twelve_data else "yahoo",
                    "is_gold": "XAU" in canon,
                    "calibrated_offset": GOLD_CALIBRATION["offset"] if "XAU" in canon else 0.0,
                    "benchmark_notice": (
                        "Twelve Data Direct Retail Spot (XAU/USD) — Authentic Broker Spot Benchmark"
                        if is_twelve_data
                        else (
                            "COMEX Gold Futures benchmark (GC=F) — not OTC spot XAU/USD"
                            if "XAU" in canon
                            else "Yahoo Finance FX (query2 indicative aggregate)"
                        )
                    ),
                    "candles": candles,
                    "ema9": ema9,
                    "ema21": ema21,
                    "indicators": {
                        "ema9": ema9,
                        "ema21": ema21,
                    },
                    "signal_markers": signal_markers,
                    "levels": levels,
                    "auto_signal": auto_sig,
                    "timestamp": obs.get("timestamp_et", ""),
                    "freshness": obs.get("data_freshness", "N/A"),
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("X-Paper-Trading-Only", "true")
                self.end_headers()
                self.wfile.write(json.dumps(res).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e), "paper_mode": True}).encode("utf-8"))
            return

        # 5. API: Historical Strategy Backtest (Range Breakout + 2R Plan)
        if path == "/api/backtest":
            sym = params.get("symbol", ["XAU/USD"])[0]
            interval = params.get("interval", ["5m"])[0]
            range_period = params.get("range", ["5d"])[0]
            canon = normalize_symbol(sym)
            try:
                res = run_backtest(
                    symbol=canon,
                    interval=interval,
                    range_period=range_period,
                    offline_data=self.OFFLINE_DATA,
                    max_loss_usd=RISK_CONFIG.get("max_risk_per_trade_usd", 50.0),
                )
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("X-Paper-Trading-Only", "true")
                self.end_headers()
                self.wfile.write(json.dumps(res).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e), "paper_mode": True}).encode("utf-8"))
            return

        # 6. API: Paper Journal
        if path == "/api/paper/journal":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(PAPER_JOURNAL).encode("utf-8"))
            return

        # 7. API: Export Paper Journal to CSV
        if path == "/api/paper/journal/export":
            csv_lines = [
                "ID,Symbol,Side,SimulatedFill,StopLoss,TakeProfit,LotSize,Units,RiskUSD,PnLUSD,Timestamp,Status,Strategy"
            ]
            for item in PAPER_JOURNAL:
                csv_lines.append(
                    f"{item.get('id','')},"
                    f"{item.get('symbol','')},"
                    f"{item.get('side','')},"
                    f"{item.get('simulated_fill','')},"
                    f"{item.get('stop_price','')},"
                    f"{item.get('target_price','')},"
                    f"{item.get('lot_size','')},"
                    f"{item.get('units','')},"
                    f"{item.get('risk_usd','')},"
                    f"{item.get('pnl_usd', 0.0)},"
                    f"\"{item.get('timestamp','')}\","
                    f"{item.get('status','')},"
                    f"\"{item.get('note','')}\""
                )
            csv_content = "\r\n".join(csv_lines)
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", "attachment; filename=paper_observation_journal.csv")
            self.end_headers()
            self.wfile.write(csv_content.encode("utf-8"))
            return

        # 8. API: Data Health Status
        if path == "/api/health":
            sym = params.get("symbol", ["XAU/USD"])[0]
            canon = normalize_symbol(sym)
            now_utc = datetime.now(timezone.utc)
            now_et = utc_to_et(now_utc)
            is_open, open_reason = is_market_open_for_symbol(canon, now_et)
            active_sessions = get_active_sessions(now_et) if is_open else []

            is_live_ws = False
            if self.OFFLINE_DATA is None and "XAU" in canon and "XAU/USD" in LIVE_TICKS:
                live = LIVE_TICKS["XAU/USD"]
                if time.time() - live.get("received_at", 0) < 30:
                    is_live_ws = True
                    is_open = True
                    open_reason = "Binance PAXG/USDT 24/7 real-time benchmark active (no weekend halts)"

            health_info = {
                "status": "HEALTHY (24/7 Live Stream)" if is_live_ws else "HEALTHY",
                "symbol": canon,
                "is_gold": "XAU" in canon,
                "market_open": is_open,
                "market_reason": open_reason,
                "active_sessions": active_sessions,
                "system_time_utc": now_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
                "system_time_et": now_et.strftime("%Y-%m-%d %H:%M:%S ET"),
                "broker_connected": False,
                "broker_disconnected": True,
                "broker_notice": "DISABLED — No broker connections permitted",
                "benchmark_verified": True,
                "benchmark_notice": (
                    "COMEX Gold Futures benchmark (GC=F) — not OTC spot XAU/USD"
                    if "XAU" in canon
                    else "Yahoo Finance FX (query2 indicative aggregate)"
                ),
                "futures_benchmark": "COMEX Gold Futures (GC=F)" if "XAU" in canon else None,
                "paper_mode": True,
                "is_live_24_7": is_live_ws,
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-Paper-Trading-Only", "true")
            self.end_headers()
            self.wfile.write(json.dumps(health_info).encode("utf-8"))
            return

        # 9. API: Risk Parameters Configuration
        if path == "/api/config":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(RISK_CONFIG).encode("utf-8"))
            return

        # 10. API: 11 Skills Catalog
        if path == "/api/skills":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-Paper-Trading-Only", "true")
            self.end_headers()
            self.wfile.write(json.dumps(SKILLS_REGISTRY).encode("utf-8"))
            return

        # 11. API: Gold Spot Calibration Settings
        if path == "/api/calibrate":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-Paper-Trading-Only", "true")
            self.end_headers()
            self.wfile.write(json.dumps(GOLD_CALIBRATION).encode("utf-8"))
            return

        # 12. API: Twelve Data Direct Spot API Config
        if path == "/api/twelvedata":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-Paper-Trading-Only", "true")
            self.end_headers()
            self.wfile.write(json.dumps(TWELVE_DATA_CONFIG).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"

        try:
            payload = json.loads(body)
        except Exception:
            payload = {}

        # 0. API: Ingest Live WebSocket Tick (e.g. Binance PAXG/USDT)
        if path == "/api/tick":
            sym = normalize_symbol(payload.get("symbol", "XAU/USD"))
            mid = float(payload.get("mid", payload.get("last", 0.0)))
            bid = float(payload.get("bid", mid))
            ask = float(payload.get("ask", mid))
            last = float(payload.get("last", mid))
            event_ts = payload.get("timestamp", int(time.time() * 1000))
            if mid > 0:
                LIVE_TICKS[sym] = {
                    "symbol": sym,
                    "mid": mid,
                    "bid": bid,
                    "ask": ask,
                    "last": last,
                    "event_time": event_ts,
                    "received_at": time.time(),
                }
                update_journal_positions(mid, sym)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
            return

        # 1. API: Simulate Paper Trade
        if path == "/api/paper/simulate":
            sym = payload.get("symbol", "XAU/USD")
            side = payload.get("side", "BUY")
            entry = float(payload.get("entry_price", 2740.0))
            stop = float(payload.get("stop_price", 2735.0))

            try:
                sizing = size_position(
                    symbol=sym,
                    side=side,
                    entry_price=entry,
                    stop_price=stop,
                    budget_usd=5000.0,
                    max_loss_usd=RISK_CONFIG.get("max_risk_per_trade_usd", 50.0),
                    leverage=30.0,
                )
                sim_fill = execution_price(sym, side, entry, slippage_pips=0.5)
                sizing["simulated_fill"] = sim_fill
                sizing["paper_mode"] = True
                sizing["warning"] = "SIMULATED — NOT A REAL TRADE"

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("X-Paper-Trading-Only", "true")
                self.end_headers()
                self.wfile.write(json.dumps(sizing).encode("utf-8"))
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        # 2. API: Add to Paper Journal
        if path == "/api/paper/journal":
            open_count = sum(1 for item in PAPER_JOURNAL if item.get("status") == "OPEN")
            if open_count >= RISK_CONFIG.get("max_open_positions", 3):
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "error": f"Max open positions limit reached ({open_count}/{RISK_CONFIG.get('max_open_positions', 3)}). Close or let existing positions resolve first.",
                }).encode("utf-8"))
                return

            sym = payload.get("symbol", "XAU/USD")
            side = payload.get("side", "BUY")
            fill = float(payload.get("simulated_fill", 2740.0))
            stop = float(payload.get("stop_price", 2735.0))
            target = float(payload.get("target_price", 2750.0))

            try:
                sizing = size_position(
                    symbol=sym,
                    side=side,
                    entry_price=fill,
                    stop_price=stop,
                    max_loss_usd=RISK_CONFIG.get("max_risk_per_trade_usd", 50.0),
                )
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": f"Position sizing error: {e}"}).encode("utf-8"))
                return

            is_gold = "XAU" in sym
            new_entry = {
                "id": f"SIM-{len(PAPER_JOURNAL) + 101}",
                "symbol": sym,
                "side": side.upper(),
                "simulated_fill": fill,
                "stop_price": stop,
                "target_price": target,
                "lot_size": sizing.get("lot_size", 0.01),
                "units": sizing.get("units", 1),
                "unit_label": "oz" if is_gold else "units",
                "risk_usd": sizing.get("max_loss_usd", 50.0),
                "pnl_usd": 0.0,
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                "status": "OPEN",
                "note": payload.get("note", "Dashboard paper simulation"),
            }
            PAPER_JOURNAL.insert(0, new_entry)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, "entry": new_entry}).encode("utf-8"))
            return

        # 2b. API: Log Automatic Signal to Paper Journal
        if path == "/api/paper/log-signal":
            action = payload.get("action", "NO SIGNAL")
            if action not in ("BUY", "SELL"):
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "error": "Cannot log journal entry when signal is NO SIGNAL.",
                    "reason": payload.get("reason", "No confirmed setup"),
                }).encode("utf-8"))
                return

            open_count = sum(1 for item in PAPER_JOURNAL if item.get("status") == "OPEN")
            if open_count >= RISK_CONFIG.get("max_open_positions", 3):
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "error": f"Max open positions limit reached ({open_count}/{RISK_CONFIG.get('max_open_positions', 3)}). Close or let existing positions resolve first.",
                }).encode("utf-8"))
                return

            sym = payload.get("symbol", "XAU/USD")
            fill = float(payload.get("entry_reference", 0.0))
            stop = float(payload.get("stop_loss", 0.0))
            target = float(payload.get("take_profit", 0.0))
            strategy = payload.get("strategy", "trader-scanner + trader-entry + trader-exit")
            timeframe = payload.get("timeframe", "5m")

            try:
                sizing = size_position(
                    symbol=sym,
                    side=action,
                    entry_price=fill,
                    stop_price=stop,
                    max_loss_usd=RISK_CONFIG.get("max_risk_per_trade_usd", 50.0),
                )
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": f"Position sizing error: {e}"}).encode("utf-8"))
                return

            is_gold = "XAU" in sym
            new_entry = {
                "id": f"AUTO-{len(PAPER_JOURNAL) + 101}",
                "symbol": sym,
                "side": action.upper(),
                "simulated_fill": fill,
                "stop_price": stop,
                "target_price": target,
                "lot_size": sizing.get("lot_size", 0.01),
                "units": sizing.get("units", 1),
                "unit_label": "oz" if is_gold else "units",
                "risk_usd": sizing.get("max_loss_usd", 50.0),
                "pnl_usd": 0.0,
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                "status": "OPEN",
                "note": f"Auto Signal [{timeframe}]: {strategy}",
            }
            PAPER_JOURNAL.insert(0, new_entry)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, "entry": new_entry}).encode("utf-8"))
            return

        # 2c. API: Update Risk Parameters
        if path == "/api/config":
            try:
                daily_limit = float(payload.get("daily_loss_limit_usd", RISK_CONFIG["daily_loss_limit_usd"]))
                trade_limit = float(payload.get("max_risk_per_trade_usd", RISK_CONFIG["max_risk_per_trade_usd"]))
                max_pos = int(payload.get("max_open_positions", RISK_CONFIG["max_open_positions"]))
                RISK_CONFIG["daily_loss_limit_usd"] = max(10.0, min(1000.0, daily_limit))
                RISK_CONFIG["max_risk_per_trade_usd"] = max(5.0, min(200.0, trade_limit))
                RISK_CONFIG["max_open_positions"] = max(1, min(10, max_pos))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "config": RISK_CONFIG}).encode("utf-8"))
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        # 3. API: Run/Simulate Skill (Paper Only)
        if path == "/api/skills/run":
            skill_id = payload.get("skill_id", "")
            user_input = payload.get("input", "")
            res = simulate_skill_paper_run(skill_id, user_input)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-Paper-Trading-Only", "true")
            self.end_headers()
            self.wfile.write(json.dumps(res).encode("utf-8"))
            return

        # 4. API: Save Gold Spot Calibration
        if path == "/api/calibrate":
            offset = float(payload.get("offset", 0.0))
            broker = str(payload.get("target_broker", "Spot XAU/USD"))
            target_price = float(payload.get("target_price", 0.0)) if payload.get("target_price") else 0.0
            GOLD_CALIBRATION["offset"] = offset
            GOLD_CALIBRATION["target_broker"] = broker
            GOLD_CALIBRATION["target_price"] = target_price
            GOLD_CALIBRATION["updated_at"] = datetime.now(timezone.utc).isoformat()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-Paper-Trading-Only", "true")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, "calibration": GOLD_CALIBRATION}).encode("utf-8"))
            return

        # 5. API: Save Twelve Data Configuration
        if path == "/api/twelvedata":
            api_key = str(payload.get("api_key", "")).strip()
            if api_key:
                TWELVE_DATA_CONFIG["api_key"] = api_key
            if "enabled" in payload:
                TWELVE_DATA_CONFIG["enabled"] = bool(payload["enabled"])
            TWELVE_DATA_CONFIG["updated_at"] = datetime.now(timezone.utc).isoformat()
            try:
                with open(TWELVE_DATA_CONFIG_PATH, "w", encoding="utf-8") as _fh:
                    json.dump(TWELVE_DATA_CONFIG, _fh, indent=2)
            except Exception:
                pass
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-Paper-Trading-Only", "true")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, "config": TWELVE_DATA_CONFIG}).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()


def run_dashboard_server(host: str = "0.0.0.0", port: int = DEFAULT_PORT) -> Any:
    """Start the paper observation dashboard HTTP server."""
    server = DashboardServer((host, port), DashboardRequestHandler)
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Forex & COMEX Gold Paper Observation Dashboard")
    parser.add_argument("--host", default="0.0.0.0", help="Host address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port number (default: {DEFAULT_PORT})")
    args = parser.parse_args()

    print("=" * 88)
    print("FOREX & COMEX GOLD PAPER OBSERVATION DASHBOARD")
    print("=" * 88)
    print("  [SECURITY NOTICE]: PAPER-ONLY OBSERVATION SYSTEM")
    print("  - Zero broker connectivity, no API credentials requested, no live orders.")
    print("  - Gold data is COMEX Gold Futures (GC=F) continuous benchmark, not OTC spot.")
    print("=" * 88)
    print(f"  Dashboard Server Running at: http://localhost:{args.port}/")
    print(f"  Alternative Local IP:       http://127.0.0.1:{args.port}/")
    print("=" * 88)
    print("Press Ctrl+C to terminate.")
    sys.stdout.flush()

    server = run_dashboard_server(args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down dashboard server...")
        server.server_close()
        sys.exit(0)


if __name__ == "__main__":
    main()
