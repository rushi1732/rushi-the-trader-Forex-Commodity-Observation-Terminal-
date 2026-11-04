---
last_mapped_commit: 4034834f86f2d6cc8a6d98ceb10b64e09ce2a387
last_mapped_at: 2026-11-03
---
# Concerns & Technical Debt

1. **Dashboard Size**:
   - `forex/dashboard.py` is over 4,800 lines because all HTML, CSS, JavaScript, and backend HTTP handler logic are merged in one file.
   - Any enhancements to the UI or charting must carefully preserve existing working features (paper journal, Twelve Data config, calibration, WebSocket ticks, etc.).
2. **Strategy Completeness**:
   - Current logic only had simple Asian range breakout and basic moving averages/RSI.
   - Needs institutional trading methodologies: Smart Money Concepts (SMC - Order Blocks, FVG, BOS, CHoCH), ICT (Killzones, Silver Bullet, OTE), Fibonacci retracements/extensions, Session sweeps, Liquidity pools (BSL/SSL) & Stop-Loss hunts.
3. **Signal Automation & Accuracy**:
   - Needs a comprehensive multi-factor confluence scoring engine so automated signals are precise, high-probability, and include complete execution metrics (Entry, Stop Loss, TP1, TP2, R:R).
