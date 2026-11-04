# Roadmap: Smart Money, ICT & Confluence Trading Platform

## Milestone 1: Institutional Core Analysis Engines
- [x] Phase 1: Smart Money Concepts (SMC) Engine (`forex/smc.py`)
  - Swing High/Low detection, BOS, CHoCH, Order Blocks, FVGs, Premium/Discount zones.
- [x] Phase 2: ICT, Sessions & Fibonacci Engines (`forex/ict.py`, `forex/fibonacci.py`, `forex/sessions.py`)
  - Killzones (London, NY, Asian, Silver Bullet), Judas swing, OTE Fibonacci calculations.
- [x] Phase 3: Liquidity Pools & Stop-Loss Hunt Engine (`forex/liquidity.py`)
  - BSL, SSL, Equal Highs/Lows, Liquidity sweeps, Stop-loss raid detection (Turtle Soup).

## Milestone 2: Automated Multi-Factor Confluence Signal Generator
- [x] Phase 4: Confluence Scoring & Automated Signal Generator (`forex/signals.py`, `forex/observe.py`)
  - Confluence checklist, Confidence grade (A+, A, B), Entry, SL, TP1, TP2, R:R calculation, Multi-pair scanner.

## Milestone 3: Interactive Dashboard UI & Visual Overlays
- [x] Phase 5: Dashboard Chart Overlays & SMC/ICT Strategy UI (`forex/dashboard.py`)
  - Visual Order Block & FVG markers, Fibonacci retracement lines, Session Killzone banners, Confluence Signal cards with 1-click paper execution.
- [x] Phase 6: Automated Signal API & Multi-Pair Scanner Live Feed
  - Real-time endpoints `/api/smc`, `/api/confluence`, `/api/signals/scan`.

## Milestone 4: Verification & Testing
- [x] Phase 7: Comprehensive Unit Tests & Real Market Verification
  - Verification of mathematical accuracy for SMC, ICT, Fib, Liquidity, and end-to-end signal generation.

