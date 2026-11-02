"""Forex session-based market scanner for London and New York sessions.

Scans major currency pairs for:
1. Asian Range Breakouts (London Open Breakouts - Bullish / Bearish)
2. London/NY Trend Continuation (MA20 Momentum - Long / Short)
3. Overbought / Oversold Mean Reversion (RSI 14 - Long / Short)
4. Volatility & ATR (Average True Range in pips)

Standard library only, Python 3.8+ compatible.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple

from .pairs import to_pips, get_pair_info
from .data import fetch_chart, MAJOR_PAIRS, INDICATIVE_DISCLAIMER
from .sessions import utc_to_et, get_active_sessions


def calculate_sma(closes: List[float], period: int = 20) -> List[Optional[float]]:
    """Simple Moving Average."""
    sma: List[Optional[float]] = []
    for i in range(len(closes)):
        if i + 1 < period:
            sma.append(None)
        else:
            window = closes[i + 1 - period : i + 1]
            sma.append(sum(window) / period)
    return sma


def calculate_rsi(closes: List[float], period: int = 14) -> List[Optional[float]]:
    """Wilder's RSI (14-period)."""
    if len(closes) <= period:
        return [None] * len(closes)

    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(0.0, diff))
        losses.append(max(0.0, -diff))

    rsi: List[Optional[float]] = [None] * (period)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    if avg_loss == 0:
        rsi.append(100.0)
    else:
        rs = avg_gain / avg_loss
        rsi.append(100.0 - (100.0 / (1.0 + rs)))

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi.append(round(100.0 - (100.0 / (1.0 + rs)), 2))

    return rsi


def calculate_atr_pips(candles: List[Dict[str, Any]], symbol: str, period: int = 14) -> Optional[float]:
    """Average True Range in pips."""
    if len(candles) < period + 1:
        return None

    tr_list = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        prev_c = candles[i - 1]["close"]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        tr_list.append(tr)

    if len(tr_list) < period:
        return None

    atr_price = sum(tr_list[-period:]) / period
    return to_pips(symbol, atr_price)


def detect_asian_range_breakout(
    candles: List[Dict[str, Any]], symbol: str
) -> Dict[str, Any]:
    """
    Detects breakout of the Asian session range (00:00 to 07:00 UTC).
    
    Returns setup verdict (LONG, SHORT, INSIDE, or INSUFFICIENT_DATA).
    """
    if not candles:
        return {"setup": "NONE", "direction": "NEUTRAL", "reason": "No candles available"}

    # Separate Asian session candles (00:00 - 07:00 UTC) from current session
    asian_high = -1.0
    asian_low = 999999.0
    asian_count = 0
    post_asian_candles = []

    for c in candles:
        dt = datetime.fromtimestamp(c["timestamp"], tz=timezone.utc)
        hour = dt.hour

        if 0 <= hour < 7:
            asian_high = max(asian_high, c["high"])
            asian_low = min(asian_low, c["low"])
            asian_count += 1
        elif hour >= 7:
            post_asian_candles.append(c)

    if asian_count < 6:
        return {
            "setup": "NONE",
            "direction": "NEUTRAL",
            "reason": "Incomplete Asian range bars",
        }

    asian_range_pips = to_pips(symbol, asian_high - asian_low)

    if not post_asian_candles:
        return {
            "setup": "ASIAN_RANGE_BUILDING",
            "direction": "NEUTRAL",
            "asian_high": asian_high,
            "asian_low": asian_low,
            "range_pips": asian_range_pips,
            "reason": "London session has not commenced yet",
        }

    latest = post_asian_candles[-1]
    curr_close = latest["close"]

    # Bullish Breakout: London / NY candle closed above Asian High
    if curr_close > asian_high:
        pips_above = to_pips(symbol, curr_close - asian_high)
        return {
            "setup": "ASIAN_BREAKOUT_BULLISH",
            "direction": "LONG",
            "trigger_price": curr_close,
            "reference_level": asian_high,
            "pips_beyond": pips_above,
            "range_pips": asian_range_pips,
            "reason": f"Closed {pips_above:.1f} pips above Asian High ({asian_high})",
        }

    # Bearish Breakdown: London / NY candle closed below Asian Low
    if curr_close < asian_low:
        pips_below = to_pips(symbol, asian_low - curr_close)
        return {
            "setup": "ASIAN_BREAKOUT_BEARISH",
            "direction": "SHORT",
            "trigger_price": curr_close,
            "reference_level": asian_low,
            "pips_beyond": pips_below,
            "range_pips": asian_range_pips,
            "reason": f"Closed {pips_below:.1f} pips below Asian Low ({asian_low})",
        }

    return {
        "setup": "ASIAN_CONSOLIDATION",
        "direction": "NEUTRAL",
        "asian_high": asian_high,
        "asian_low": asian_low,
        "range_pips": asian_range_pips,
        "reason": f"Within Asian range ({asian_low} - {asian_high})",
    }


def analyze_pair(
    symbol: str,
    interval: str = "5m",
    range_period: str = "1d",
    offline_file: Optional[str] = None,
    offline_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Perform multi-factor scan on a currency pair."""
    chart = fetch_chart(
        symbol=symbol,
        interval=interval,
        range_period=range_period,
        offline_file=offline_file,
        offline_data=offline_data,
    )

    candles = chart.get("candles", [])
    if len(candles) < 20:
        return {
            "symbol": symbol,
            "status": "INSUFFICIENT_DATA",
            "candles_count": len(candles),
            "direction": "NEUTRAL",
        }

    closes = [c["close"] for c in candles]
    latest_close = closes[-1]
    info = get_pair_info(symbol)

    # 1. Technical Indicators
    smas = calculate_sma(closes, period=20)
    rsis = calculate_rsi(closes, period=14)
    atr = calculate_atr_pips(candles, symbol, period=14)

    latest_sma = smas[-1]
    latest_rsi = rsis[-1]

    # 2. Asian Range Setup
    orb_setup = detect_asian_range_breakout(candles, symbol)

    # 3. Momentum & Trend Setup
    trend = "NEUTRAL"
    if latest_sma is not None:
        if latest_close > latest_sma and closes[-1] > closes[-5]:
            trend = "BULLISH"
        elif latest_close < latest_sma and closes[-1] < closes[-5]:
            trend = "BEARISH"

    # 4. Overall Direction Synthesis
    signals = []
    direction = "NEUTRAL"

    if orb_setup["direction"] == "LONG":
        signals.append(f"Asian Breakout Long (+{orb_setup.get('pips_beyond', 0):.1f}p)")
        direction = "BULLISH"
    elif orb_setup["direction"] == "SHORT":
        signals.append(f"Asian Breakdown Short (-{orb_setup.get('pips_beyond', 0):.1f}p)")
        direction = "BEARISH"

    if latest_rsi is not None:
        if latest_rsi < 30.0:
            signals.append(f"RSI Oversold ({latest_rsi:.1f})")
            if direction == "NEUTRAL":
                direction = "BULLISH"
        elif latest_rsi > 70.0:
            signals.append(f"RSI Overbought ({latest_rsi:.1f})")
            if direction == "NEUTRAL":
                direction = "BEARISH"

    if trend != "NEUTRAL":
        signals.append(f"20-period {trend.title()} Trend")

    primary_setup = signals[0] if signals else "Consolidation / Range"

    return {
        "symbol": symbol,
        "status": "OK",
        "latest_price": latest_close,
        "decimals": info["decimals"],
        "atr_pips": atr or 0.0,
        "rsi": latest_rsi,
        "sma20": latest_sma,
        "trend": trend,
        "direction": direction,
        "primary_setup": primary_setup,
        "signals": signals,
        "asian_setup": orb_setup,
        "disclaimer": INDICATIVE_DISCLAIMER,
    }


def scan_market(
    pairs: Optional[List[str]] = None,
    interval: str = "5m",
    range_period: str = "1d",
    offline_file: Optional[str] = None,
    offline_data: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Scan list of pairs and return ranked analysis."""
    if pairs is None:
        pairs = MAJOR_PAIRS

    results = []
    for sym in pairs:
        try:
            res = analyze_pair(
                symbol=sym,
                interval=interval,
                range_period=range_period,
                offline_file=offline_file,
                offline_data=offline_data,
            )
            results.append(res)
        except Exception as e:
            results.append({
                "symbol": sym,
                "status": "ERROR",
                "error": str(e),
                "direction": "NEUTRAL",
            })
    return results


def format_scan_output(results: List[Dict[str, Any]]) -> str:
    """Format scan results into a clean CLI dashboard."""
    now_utc = datetime.now(timezone.utc)
    now_et = utc_to_et(now_utc)
    active_sessions = get_active_sessions(now_et)
    session_str = ", ".join(active_sessions) if active_sessions else "Inter-session"

    lines = [
        "=" * 82,
        "FOREX MARKET SCANNER (LONDON & NEW YORK SESSIONS)",
        "=" * 82,
        f"Time:        {now_et.strftime('%Y-%m-%d %H:%M:%S ET')} (Active: {session_str})",
        f"Granularity: 5-minute bars (Indicative Mid Rates from Yahoo Finance)",
        f"Notice:      {INDICATIVE_DISCLAIMER}",
        "-" * 82,
        f"{'PAIR':<10} {'PRICE':<10} {'DIR':<9} {'ATR(14)':<9} {'RSI(14)':<9} {'PRIMARY SETUP':<30}",
        "-" * 82,
    ]

    for r in results:
        sym = r.get("symbol", "")
        if r.get("status") != "OK":
            lines.append(f"{sym:<10} {'NO DATA':<10} {'--':<9} {'--':<9} {'--':<9} {r.get('error', 'Incomplete bars'):<30}")
            continue

        price = f"{r['latest_price']:.{r['decimals']}f}"
        direction = r["direction"]
        atr = f"{r['atr_pips']:.1f}p"
        rsi = f"{r['rsi']:.1f}" if r["rsi"] is not None else "--"
        setup = r["primary_setup"][:28]

        lines.append(f"{sym:<10} {price:<10} {direction:<9} {atr:<9} {rsi:<9} {setup:<30}")

    lines.append("=" * 82)
    lines.append("Next: Review setup in forex/entry.py or plan exit with forex/sizer.py.")
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Forex Market Scanner")
    parser.add_argument("--pairs", help="Comma-separated currency pairs (e.g. EUR/USD,GBP/USD)")
    parser.add_argument("--interval", default="5m", choices=["5m", "15m", "1h", "1d"])
    parser.add_argument("--offline", help="Path to offline JSON chart bundle")
    parser.add_argument("--write", help="Save output to forex/watchlist.json", action="store_true")
    args = parser.parse_args()

    pair_list = [p.strip() for p in args.pairs.split(",")] if args.pairs else MAJOR_PAIRS
    scan_results = scan_market(pairs=pair_list, interval=args.interval, offline_file=args.offline)

    print(format_scan_output(scan_results))

    if args.write:
        out_path = os.path.join(os.path.dirname(__file__), "watchlist.json")
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump({
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "disclaimer": INDICATIVE_DISCLAIMER,
                "hits": scan_results,
            }, fh, indent=2)
        print(f"Wrote scan results to {out_path}.")
