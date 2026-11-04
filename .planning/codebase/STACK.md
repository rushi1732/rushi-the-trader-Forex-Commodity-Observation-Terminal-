---
last_mapped_commit: 4034834f86f2d6cc8a6d98ceb10b64e09ce2a387
last_mapped_at: 2026-11-03
---
# Technology Stack

## Runtime & Core Engine

- **Runtime**: Python 3.8+ (currently running on Python 3.12.14 via `C:\Program Files\LibreOffice\program\python.exe`)
- **Philosophy**: Standard library only where possible (`http.server`, `urllib.request`, `json`, `datetime`, `threading`, `math`)
- **Zero Heavy Dependencies**: Operates without external heavy frameworks (no Flask/Django, no Pandas required for base runtime).

## Web & Dashboard

- **Server**: Native Python `ThreadingHTTPServer` (`forex/dashboard.py`) on port 8765
- **Frontend**: Vanilla JavaScript (ES6+), Modern Responsive CSS, HTML5 Canvas charting and embedded charting UI
- **Live Streaming**: Server-Sent Events (SSE) / Polling endpoints (`/api/tick`, `/api/observe`, `/api/chart`)

## Data Providers & Market Feeds

- **Yahoo Finance API**: Public chart endpoints for spot forex (`EURUSD=X`, `GBPUSD=X`, `USDJPY=X`) and COMEX Gold Futures (`GC=F`)
- **Twelve Data API**: Direct retail spot FX and spot Gold (`XAU/USD`) with custom API key support
- **Binance WebSocket/Ticker**: 24/7 PAXG/USDT indicative gold proxy when CME/COMEX markets are in weekend/holiday closure
- **SEC EDGAR & House Clerk APIs**: Public data scrapers for US equities/options in `trader-*` skills

## Architecture

- Modular Python packages: `forex` (FX & Gold Paper Observation Engine) and `trader-*` (Day Trading Skill CLI tools)
