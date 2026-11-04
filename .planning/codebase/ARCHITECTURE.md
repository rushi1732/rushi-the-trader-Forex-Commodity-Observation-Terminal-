---
last_mapped_commit: 4034834f86f2d6cc8a6d98ceb10b64e09ce2a387
last_mapped_at: 2026-11-03
---
# Architecture

## System Overview

The Day Trader Skill & Forex Paper Trading Platform is structured into two main operational pillars:

```mermaid
graph TD
    UI["Web Dashboard (forex/dashboard.py:8765)"]
    API["HTTP REST API Endpoints (/api/*)"]
    OBS["Market Observer (forex/observe.py)"]
    SCAN["Market Scanner & Strategies (forex/scanner.py)"]
    SESSIONS["Session Clock & Hours (forex/sessions.py)"]
    ENTRY["Entry Confirmation Engine (forex/entry.py)"]
    EXIT["Risk & Exit Planner (forex/exit.py, risk.py, sizer.py)"]
    DATA["Data Fetcher & Normalizer (forex/data.py)"]
    JOURNAL["Paper Journal & Execution (forex/dashboard.py)"]
    SKILLS["Claude Skills Suite (trader-*)"]

    UI --> API
    API --> OBS
    API --> DATA
    API --> JOURNAL
    API --> SKILLS
    OBS --> SCAN
    OBS --> SESSIONS
    OBS --> ENTRY
    OBS --> EXIT
    OBS --> DATA
```

## Component Roles

1. **`forex/dashboard.py`**:
   - HTTP server hosting single-page dashboard application.
   - Handles REST API requests (`/api/pairs`, `/api/observe`, `/api/chart`, `/api/backtest`, `/api/paper/*`, `/api/config`, `/api/calibrate`).
   - Maintains in-memory paper trading state and live ticks.
2. **`forex/data.py`**:
   - Multi-source price fetcher: Twelve Data, Yahoo Finance, Binance PAXG proxy.
   - Robust fallback and retry logic.
3. **`forex/sessions.py`**:
   - Wall-clock session engine with daylight saving time calculations (London, New York, Tokyo, Sydney).
   - Market open/closed assertions for Spot Forex and COMEX Gold Futures.
4. **`forex/scanner.py` & `forex/entry.py`**:
   - Rule-based setup detection (Asian breakout, moving average trends, RSI).
   - Candle-close confirmation rules (ignoring mere wicks).
5. **`forex/exit.py` & `forex/sizer.py` & `forex/risk.py`**:
   - 2R risk-to-reward exit planning, lot sizing based on max USD loss limits, risk limits verification.
6. **`trader-*` modules**:
   - Standalone CLI skills designed for US equity markets, SEC disclosures, Congressional disclosures.
