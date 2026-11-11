"""RSI Hidden & Regular Divergence Detection Engine.

Detects:
1. Hidden Bullish Divergence (Trend Continuation in Uptrend):
   - Price makes a Higher Low (HL) while RSI makes a Lower Low (LL).
   - Signals institutional absorption and pullback completion for BUY continuation.
2. Hidden Bearish Divergence (Trend Continuation in Downtrend):
   - Price makes a Lower High (LH) while RSI makes a Higher High (HH).
   - Signals institutional distribution and bounce completion for SELL continuation.
3. Regular Bullish Divergence (Reversal Exhaustion):
   - Price makes a Lower Low (LL) while RSI makes a Higher Low (HL).
4. Regular Bearish Divergence (Reversal Exhaustion):
   - Price makes a Higher High (HH) while RSI makes a Lower High (LH).

Designed for Forex (XAU/USD Gold, BTC/USD, Major FX pairs) and SMC/ICT confluence.
Standard library only, Python 3.8+ compatible.
"""

from typing import Dict, List, Optional, Any, Tuple


def calculate_rsi(closes: List[float], period: int = 14) -> List[Optional[float]]:
    """
    Wilder's Relative Strength Index (RSI).
    Returns list of RSI values aligned with closes.
    """
    if len(closes) <= period:
        return [None] * len(closes)

    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(0.0, diff))
        losses.append(max(0.0, -diff))

    rsi: List[Optional[float]] = [None] * period
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


def find_divergence_swings(
    candles: List[Dict[str, Any]],
    rsis: List[Optional[float]],
    lookback: int = 2,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Identify local fractal swing highs and swing lows paired with RSI values.
    Returns (swing_highs, swing_lows).
    """
    swing_highs = []
    swing_lows = []
    n = len(candles)
    if n < lookback * 2 + 1:
        return swing_highs, swing_lows

    for i in range(lookback, n - lookback):
        c = candles[i]
        h = c.get("high", c.get("close", 0.0))
        l = c.get("low", c.get("close", 0.0))
        r = rsis[i] if i < len(rsis) else None

        if r is None:
            continue

        # Check Swing High
        is_sh = True
        has_strictly_lower = False
        for j in range(i - lookback, i + lookback + 1):
            if j != i:
                val = candles[j].get("high", candles[j].get("close", 0.0))
                if val > h:
                    is_sh = False
                    break
                if val < h:
                    has_strictly_lower = True
        if is_sh and has_strictly_lower:
            swing_highs.append({
                "index": i,
                "price": h,
                "rsi": r,
                "timestamp": c.get("timestamp"),
            })

        # Check Swing Low
        is_sl = True
        has_strictly_higher = False
        for j in range(i - lookback, i + lookback + 1):
            if j != i:
                val = candles[j].get("low", candles[j].get("close", 0.0))
                if val < l:
                    is_sl = False
                    break
                if val > l:
                    has_strictly_higher = True
        if is_sl and has_strictly_higher:
            swing_lows.append({
                "index": i,
                "price": l,
                "rsi": r,
                "timestamp": c.get("timestamp"),
            })

    return swing_highs, swing_lows


def detect_divergence(
    candles: List[Dict[str, Any]],
    rsis: Optional[List[Optional[float]]] = None,
    lookback: int = 2,
    min_bar_distance: int = 4,
    max_bar_distance: int = 35,
    max_recent_bars: int = 8,
) -> Dict[str, Any]:
    """
    Scans candles and RSI for Hidden & Regular Divergences.

    Returns structured analysis:
    - detected: bool
    - type: 'HIDDEN_BULLISH' | 'HIDDEN_BEARISH' | 'REGULAR_BULLISH' | 'REGULAR_BEARISH' | 'NONE'
    - bias: 'BUY' | 'SELL' | 'NEUTRAL'
    - description: Human readable summary
    - grade_boost: Confluence bonus points (+15 to +25)
    - details: Swing points and RSI values
    """
    if not candles or len(candles) < 20:
        return {
            "detected": False,
            "type": "NONE",
            "bias": "NEUTRAL",
            "description": "Insufficient candle history for divergence analysis",
            "grade_boost": 0,
            "details": {},
        }

    closes = [c.get("close", 0.0) for c in candles]
    if rsis is None or len(rsis) != len(closes):
        rsis = calculate_rsi(closes, period=14)

    swing_highs, swing_lows = find_divergence_swings(candles, rsis, lookback=lookback)
    n = len(candles)
    latest_index = n - 1

    # 1. Check Hidden Bullish Divergence (Uptrend Continuation)
    # Price makes Higher Low (HL) while RSI makes Lower Low (LL)
    if len(swing_lows) >= 2:
        for i in range(len(swing_lows) - 1, 0, -1):
            curr_l = swing_lows[i]
            prev_l = swing_lows[i - 1]

            bars_ago = latest_index - curr_l["index"]
            distance = curr_l["index"] - prev_l["index"]

            if bars_ago <= max_recent_bars and min_bar_distance <= distance <= max_bar_distance:
                # Price Higher Low & RSI Lower Low
                if curr_l["price"] > prev_l["price"] and curr_l["rsi"] < prev_l["rsi"]:
                    return {
                        "detected": True,
                        "type": "HIDDEN_BULLISH",
                        "bias": "BUY",
                        "is_continuation": True,
                        "description": (
                            f"Hidden Bullish Divergence (Price HL {curr_l['price']:.2f} > {prev_l['price']:.2f} "
                            f"vs RSI LL {curr_l['rsi']:.1f} < {prev_l['rsi']:.1f})  -  Trend Continuation"
                        ),
                        "grade_boost": 25,
                        "details": {
                            "category": "HIDDEN_BULLISH",
                            "price_prev": prev_l["price"],
                            "price_curr": curr_l["price"],
                            "rsi_prev": prev_l["rsi"],
                            "rsi_curr": curr_l["rsi"],
                            "bars_between": distance,
                            "bars_ago": bars_ago,
                        },
                    }

    # 2. Check Hidden Bearish Divergence (Downtrend Continuation)
    # Price makes Lower High (LH) while RSI makes Higher High (HH)
    if len(swing_highs) >= 2:
        for i in range(len(swing_highs) - 1, 0, -1):
            curr_h = swing_highs[i]
            prev_h = swing_highs[i - 1]

            bars_ago = latest_index - curr_h["index"]
            distance = curr_h["index"] - prev_h["index"]

            if bars_ago <= max_recent_bars and min_bar_distance <= distance <= max_bar_distance:
                # Price Lower High & RSI Higher High
                if curr_h["price"] < prev_h["price"] and curr_h["rsi"] > prev_h["rsi"]:
                    return {
                        "detected": True,
                        "type": "HIDDEN_BEARISH",
                        "bias": "SELL",
                        "is_continuation": True,
                        "description": (
                            f"Hidden Bearish Divergence (Price LH {curr_h['price']:.2f} < {prev_h['price']:.2f} "
                            f"vs RSI HH {curr_h['rsi']:.1f} > {prev_h['rsi']:.1f})  -  Trend Continuation"
                        ),
                        "grade_boost": 25,
                        "details": {
                            "category": "HIDDEN_BEARISH",
                            "price_prev": prev_h["price"],
                            "price_curr": curr_h["price"],
                            "rsi_prev": prev_h["rsi"],
                            "rsi_curr": curr_h["rsi"],
                            "bars_between": distance,
                            "bars_ago": bars_ago,
                        },
                    }

    # 3. Check Regular Bullish Divergence (Reversal)
    # Price makes Lower Low (LL) while RSI makes Higher Low (HL)
    if len(swing_lows) >= 2:
        for i in range(len(swing_lows) - 1, 0, -1):
            curr_l = swing_lows[i]
            prev_l = swing_lows[i - 1]

            bars_ago = latest_index - curr_l["index"]
            distance = curr_l["index"] - prev_l["index"]

            if bars_ago <= max_recent_bars and min_bar_distance <= distance <= max_bar_distance:
                if curr_l["price"] < prev_l["price"] and curr_l["rsi"] > prev_l["rsi"]:
                    return {
                        "detected": True,
                        "type": "REGULAR_BULLISH",
                        "bias": "BUY",
                        "is_continuation": False,
                        "description": (
                            f"Regular Bullish Divergence (Price LL {curr_l['price']:.2f} < {prev_l['price']:.2f} "
                            f"vs RSI HL {curr_l['rsi']:.1f} > {prev_l['rsi']:.1f})  -  Potential Reversal"
                        ),
                        "grade_boost": 15,
                        "details": {
                            "category": "REGULAR_BULLISH",
                            "price_prev": prev_l["price"],
                            "price_curr": curr_l["price"],
                            "rsi_prev": prev_l["rsi"],
                            "rsi_curr": curr_l["rsi"],
                            "bars_between": distance,
                            "bars_ago": bars_ago,
                        },
                    }

    # 4. Check Regular Bearish Divergence (Reversal)
    # Price makes Higher High (HH) while RSI makes Lower High (LH)
    if len(swing_highs) >= 2:
        for i in range(len(swing_highs) - 1, 0, -1):
            curr_h = swing_highs[i]
            prev_h = swing_highs[i - 1]

            bars_ago = latest_index - curr_h["index"]
            distance = curr_h["index"] - prev_h["index"]

            if bars_ago <= max_recent_bars and min_bar_distance <= distance <= max_bar_distance:
                if curr_h["price"] > prev_h["price"] and curr_h["rsi"] < prev_h["rsi"]:
                    return {
                        "detected": True,
                        "type": "REGULAR_BEARISH",
                        "bias": "SELL",
                        "is_continuation": False,
                        "description": (
                            f"Regular Bearish Divergence (Price HH {curr_h['price']:.2f} > {prev_h['price']:.2f} "
                            f"vs RSI LH {curr_h['rsi']:.1f} < {prev_h['rsi']:.1f})  -  Potential Reversal"
                        ),
                        "grade_boost": 15,
                        "details": {
                            "category": "REGULAR_BEARISH",
                            "price_prev": prev_h["price"],
                            "price_curr": curr_h["price"],
                            "rsi_prev": prev_h["rsi"],
                            "rsi_curr": curr_h["rsi"],
                            "bars_between": distance,
                            "bars_ago": bars_ago,
                        },
                    }

    return {
        "detected": False,
        "type": "NONE",
        "bias": "NEUTRAL",
        "description": "No significant divergence detected",
        "grade_boost": 0,
        "details": {},
    }
