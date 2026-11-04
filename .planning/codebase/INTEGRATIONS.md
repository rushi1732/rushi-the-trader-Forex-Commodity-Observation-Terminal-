---
last_mapped_commit: 4034834f86f2d6cc8a6d98ceb10b64e09ce2a387
last_mapped_at: 2026-11-03
---
# Integrations

## External Market Feeds

1. **Twelve Data REST API**:
   - URL: `https://api.twelvedata.com/time_series`
   - Config file: `forex/twelvedata_config.json`
   - Supplies real-time tick and candlestick data for Spot FX and XAU/USD.
2. **Yahoo Finance Indicative Chart API**:
   - Endpoint: `https://query2.finance.yahoo.com/v8/finance/chart/{symbol}`
   - Fallback provider for historical bars and currency pairs.
3. **Binance Public Spot Market**:
   - WebSocket / REST for `PAXGUSDT` (PAX Gold token) as a 24/7 weekend proxy.

## Local Services

- Embedded HTTP web dashboard running on `http://127.0.0.1:8765/`.
