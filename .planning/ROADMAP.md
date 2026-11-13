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

## Milestone 5: Institutional-Grade Multi-Factor SMT Execution Engine (XAU/USD & BTC/USD)
- [x] Phase 8: Inter-Market Triad Feed & Weekend-Aware Fallback (`forex/smt.py`, `forex/data.py`, `forex/pairs.py`)
  - Twelve Data rate-limit safe 5m/15m completed candle caching, XAU triad (XAG/USD + UUP/DXY), BTC triad (ETH/USD + QQQ).
  - Off-hours/weekend stale detection with seamless 2-Asset Pair SMT fallback.
- [x] Phase 9: 4-Layer SMT Verification & Execution Pipeline (`forex/smt.py`, `forex/signals.py`, `forex/observe.py`)
  - Layer 1: 15-Minute HTF context & liquidity sweep lock.
  - Layer 2: 5M fractal pivots (SH1/SH2, SL1/SL2) with 2-candle confirmation across 25 bars.
  - Layer 3: Mandatory CISD body displacement + 3-candle FVG gap & news spread guardrail (< 4.5 pips).
  - Layer 4: Dynamic confluence weighting (+15% Pair SMT, +30% Triad SMT -> 90%+ A+ Sniper Confluence) & TP2 prioritization.
- [x] Phase 10: Decision Matrix UI & Bilingual Voice Integration (`forex/dashboard.py`, `index.html`)
  - Check #8 (Inter-Market Triad SMT) and Check #9 (CISD + FVG Displacement & Spread Guard).
  - Marathi/English strategy rationale integration and Indian Voice Alert on 90%+ A+ Sniper signals.

