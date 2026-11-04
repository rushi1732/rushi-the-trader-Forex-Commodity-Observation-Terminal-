# Requirements

## Functional Requirements

### 1. Smart Money Concepts (SMC) Engine (`forex/smc.py`)
- **REQ-SMC-1**: Automatic detection of Swing Highs and Swing Lows (pivot points with customizable lookback).
- **REQ-SMC-2**: Identification of Market Structure:
  - Bullish / Bearish Break of Structure (BOS).
  - Change of Character (CHoCH) signaling early trend reversal.
- **REQ-SMC-3**: Identification of Order Blocks (OB):
  - Bullish Order Block: Last down-close candle prior to strong upward displacement breaking structure.
  - Bearish Order Block: Last up-close candle prior to strong downward displacement breaking structure.
  - Active vs. mitigated status tracking.
- **REQ-SMC-4**: Fair Value Gap (FVG) / Imbalance detection:
  - Bullish FVG: Candle 1 High < Candle 3 Low (Gap between wick 1 and wick 3).
  - Bearish FVG: Candle 1 Low > Candle 3 High.
  - Track if FVG has been filled/mitigated.
- **REQ-SMC-5**: Premium vs. Discount Zones:
  - Calculate range equilibrium (50%).
  - Flag whether price is in Discount (< 50% = favorable for Longs) or Premium (> 50% = favorable for Shorts).

### 2. Inner Circle Trader (ICT) & Session Strategy Engine (`forex/ict.py` / `forex/sessions.py`)
- **REQ-ICT-1**: Active Session & Killzone Tracking:
  - Asian Session: 20:00 - 00:00 ET (Asian Range accumulation high/low).
  - London Open Killzone: 02:00 - 05:00 ET (Judas swing / expansion).
  - New York Open Killzone: 07:00 - 10:00 ET (high-volume continuation/reversal).
  - ICT Silver Bullet windows: London (03:00 - 04:00 ET), NY AM (10:00 - 11:00 ET), NY PM (14:00 - 15:00 ET).
- **REQ-ICT-2**: Optimal Trade Entry (OTE):
  - Fibonacci retracement calculation into the 0.618 - 0.786 sweet spot (especially 0.707).
- **REQ-ICT-3**: Judas Swing / Session Manipulation detection:
  - False breakout through Asian session high/low during London open, trapping breakout traders.

### 3. Liquidity & Stop-Loss Hunt Engine (`forex/liquidity.py`)
- **REQ-LIQ-1**: Buy-Side Liquidity (BSL) and Sell-Side Liquidity (SSL) mapping:
  - Equal Highs (EQH) and Equal Lows (EQL).
  - Previous Day High/Low (PDH / PDL), Previous Session High/Low.
- **REQ-LIQ-2**: Liquidity Sweep & Stop-Loss Hunt Detection (Turtle Soup):
  - Price sweeps above BSL / below SSL with a wick or false break.
  - Candle closes back inside the prior key level.
  - Followed by displacement in the opposite direction.

### 4. Fibonacci Calculation Engine (`forex/fibonacci.py`)
- **REQ-FIB-1**: Anchor identification between recent major Swing High and Swing Low.
- **REQ-FIB-2**: Precise retracement calculation: 0.236, 0.382, 0.50 (Eq), 0.618 (Golden Pocket), 0.707 (OTE), 0.786, 0.886.
- **REQ-FIB-3**: Extensions calculation for take-profit targets: 1.272, 1.414, 1.618.

### 5. Automated Confluence Signal Generator (`forex/signals.py` / `forex/observe.py`)
- **REQ-SIG-1**: Multi-factor confluence scoring combining SMC + ICT + Fibonacci + Sessions + Liquidity Hunt.
- **REQ-SIG-2**: Actionable output: `BUY`, `SELL`, or `NEUTRAL / WAITING FOR SETUP`.
- **REQ-SIG-3**: Full trade plan with:
  - Exact Entry price (limit at OB/FVG/OTE or candle close confirmation).
  - Invalidation Stop Loss (placed beyond the liquidity sweep or order block).
  - Take Profit 1 (conservative 1:2 R:R or internal liquidity).
  - Take Profit 2 (1:3+ R:R or external liquidity pool).
  - Risk-to-Reward ratio, Confluence checklist, and Confidence score (A+, A, B).
- **REQ-SIG-4**: Multi-pair automated scanning across all major pairs and Gold (XAU/USD).

### 6. Interactive Web Dashboard UI Enhancements (`forex/dashboard.py`)
- **REQ-UI-1**: Visual SMC / ICT Overlay on Chart:
  - Order Block zones (colored rectangular zones).
  - FVG zones (shaded highlight boxes).
  - Fibonacci levels displayed cleanly.
  - Session high/low lines and Killzone markers.
  - Liquidity sweep & BOS/CHoCH markers.
- **REQ-UI-2**: Dedicated Institutional Strategy & Confluence Panel:
  - Displays current Market Structure (BOS/CHoCH, Trend, Premium/Discount).
  - Active Session & Killzone status (e.g. "London Open Killzone Active").
  - Liquidity hunt alerts (e.g. "Sell-Side Liquidity Swept - Bullish Reversal Alert").
  - Fibonacci Retracement status (e.g. "In OTE Zone 61.8% - 70.7%").
- **REQ-UI-3**: Live Automated Signals Table & One-Click Paper Trade Execution.
- **REQ-UI-4**: Responsive, sleek dark-themed trading terminal design following UI rules (no generic emoji icons, crisp SVG/Lucide style, high contrast, clean typography).

