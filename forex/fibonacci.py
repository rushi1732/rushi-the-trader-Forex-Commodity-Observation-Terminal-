"""Fibonacci Retracement and Extension Engine.

Calculates institutional Fibonacci levels:
- Swing High and Swing Low detection
- Key Retracements: 0.236, 0.382, 0.50 (Equilibrium), 0.618 (Golden Pocket), 0.707 (OTE Mid), 0.786 (Deep OTE), 0.886
- Key Extensions: -0.272 (1.272 TP1), -0.618 (1.618 TP2), 2.0 (TP3)
- Premium vs. Discount classification
- Optimal Trade Entry (OTE) zone verification (61.8% - 78.6%)

Standard library only, Python 3.8+ compatible.
"""

from typing import Dict, List, Optional, Any, Tuple


def find_recent_swing_points(
    candles: List[Dict[str, Any]],
    lookback: int = 5,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Find the most recent significant Swing High and Swing Low using a fractal lookback.
    Returns: (swing_high_dict, swing_low_dict)
    """
    if len(candles) < lookback * 2 + 1:
        # Fallback to simple min/max if not enough candles
        if not candles:
            return None, None
        highest_c = max(candles, key=lambda c: c["high"])
        lowest_c = min(candles, key=lambda c: c["low"])
        return (
            {"price": highest_c["high"], "index": candles.index(highest_c), "timestamp": highest_c.get("timestamp")},
            {"price": lowest_c["low"], "index": candles.index(lowest_c), "timestamp": lowest_c.get("timestamp")},
        )

    swing_highs = []
    swing_lows = []

    n = len(candles)
    for i in range(lookback, n - lookback):
        h = candles[i]["high"]
        l = candles[i]["low"]

        # Check if candle i is highest among lookback bars before and after
        is_high = True
        for j in range(i - lookback, i + lookback + 1):
            if j != i and candles[j]["high"] >= h:
                is_high = False
                break
        if is_high:
            swing_highs.append({"price": h, "index": i, "timestamp": candles[i].get("timestamp")})

        # Check if candle i is lowest among lookback bars before and after
        is_low = True
        for j in range(i - lookback, i + lookback + 1):
            if j != i and candles[j]["low"] <= l:
                is_low = False
                break
        if is_low:
            swing_lows.append({"price": l, "index": i, "timestamp": candles[i].get("timestamp")})

    # If no swing points found, fallback to max and min of the dataset
    if not swing_highs:
        highest_c = max(candles, key=lambda c: c["high"])
        swing_high = {"price": highest_c["high"], "index": candles.index(highest_c), "timestamp": highest_c.get("timestamp")}
    else:
        swing_high = swing_highs[-1]

    if not swing_lows:
        lowest_c = min(candles, key=lambda c: c["low"])
        swing_low = {"price": lowest_c["low"], "index": candles.index(lowest_c), "timestamp": lowest_c.get("timestamp")}
    else:
        swing_low = swing_lows[-1]

    return swing_high, swing_low


def calculate_fibonacci_levels(
    swing_high: float,
    swing_low: float,
    trend: str = "BULLISH",
) -> Dict[str, Any]:
    """
    Calculate institutional Fibonacci retracement and extension levels.
    
    If trend is BULLISH:
      - Impulse move is from swing_low (0.0% retracement) up to swing_high (100% of impulse / 0% retraced).
      - Retracement pulls back DOWN from high toward low.
      - 0.50 = 50% pullback level
      - 0.618 = Golden pocket pullback level
      - 0.707 = ICT OTE sweet spot
      - 0.786 = Deep pullback
      - Extensions expand UP above swing_high (1.272, 1.618)
      
    If trend is BEARISH:
      - Impulse move is from swing_high down to swing_low.
      - Retracement pulls back UP from low toward high.
      - 0.50 = 50% pullback level
      - 0.618 = Golden pocket pullback level
      - 0.707 = ICT OTE sweet spot
      - 0.786 = Deep pullback
      - Extensions expand DOWN below swing_low (1.272, 1.618)
    """
    diff = swing_high - swing_low
    if diff <= 0:
        return {"error": "Invalid swing points: swing_high must be greater than swing_low"}

    is_bullish = trend.upper() in ("BULLISH", "UP", "BUY", "LONG")

    levels: Dict[str, float] = {}

    if is_bullish:
        # Bullish trend: Retracing downwards from swing_high
        levels["0.0"] = round(swing_high, 5)
        levels["0.236"] = round(swing_high - 0.236 * diff, 5)
        levels["0.382"] = round(swing_high - 0.382 * diff, 5)
        levels["0.500"] = round(swing_high - 0.500 * diff, 5)  # Equilibrium
        levels["0.618"] = round(swing_high - 0.618 * diff, 5)  # Golden Pocket
        levels["0.707"] = round(swing_high - 0.707 * diff, 5)  # ICT OTE Midpoint
        levels["0.786"] = round(swing_high - 0.786 * diff, 5)  # OTE Lower Bound
        levels["0.886"] = round(swing_high - 0.886 * diff, 5)
        levels["1.000"] = round(swing_low, 5)
        # Extensions (Targets)
        levels["ext_1.272"] = round(swing_high + 0.272 * diff, 5)
        levels["ext_1.414"] = round(swing_high + 0.414 * diff, 5)
        levels["ext_1.618"] = round(swing_high + 0.618 * diff, 5)
        levels["ext_2.000"] = round(swing_high + 1.000 * diff, 5)

        ote_zone = {
            "top": levels["0.618"],
            "bottom": levels["0.786"],
            "sweet_spot": levels["0.707"],
        }
    else:
        # Bearish trend: Retracing upwards from swing_low
        levels["0.0"] = round(swing_low, 5)
        levels["0.236"] = round(swing_low + 0.236 * diff, 5)
        levels["0.382"] = round(swing_low + 0.382 * diff, 5)
        levels["0.500"] = round(swing_low + 0.500 * diff, 5)  # Equilibrium
        levels["0.618"] = round(swing_low + 0.618 * diff, 5)  # Golden Pocket
        levels["0.707"] = round(swing_low + 0.707 * diff, 5)  # ICT OTE Midpoint
        levels["0.786"] = round(swing_low + 0.786 * diff, 5)  # OTE Upper Bound
        levels["0.886"] = round(swing_low + 0.886 * diff, 5)
        levels["1.000"] = round(swing_high, 5)
        # Extensions (Targets)
        levels["ext_1.272"] = round(swing_low - 0.272 * diff, 5)
        levels["ext_1.414"] = round(swing_low - 0.414 * diff, 5)
        levels["ext_1.618"] = round(swing_low - 0.618 * diff, 5)
        levels["ext_2.000"] = round(swing_low - 1.000 * diff, 5)

        ote_zone = {
            "top": levels["0.786"],
            "bottom": levels["0.618"],
            "sweet_spot": levels["0.707"],
        }

    equilibrium = levels["0.500"]

    return {
        "trend": "BULLISH" if is_bullish else "BEARISH",
        "swing_high": swing_high,
        "swing_low": swing_low,
        "range_diff": round(diff, 5),
        "equilibrium": equilibrium,
        "levels": levels,
        "ote_zone": ote_zone,
    }


def analyze_fibonacci(
    candles: List[Dict[str, Any]],
    current_price: Optional[float] = None,
    lookback: int = 5,
) -> Dict[str, Any]:
    """
    Performs full Fibonacci analysis on provided candlestick data.
    Determines swing anchors, current zone (Premium/Discount), and OTE confluence.
    """
    if not candles:
        return {"status": "NO_DATA", "reason": "No candlestick data provided"}

    if current_price is None:
        current_price = candles[-1]["close"]

    swing_high_node, swing_low_node = find_recent_swing_points(candles, lookback=lookback)
    if not swing_high_node or not swing_low_node:
        return {"status": "INSUFFICIENT_DATA", "reason": "Unable to determine swing points"}

    sh_price = swing_high_node["price"]
    sl_price = swing_low_node["price"]
    sh_idx = swing_high_node["index"]
    sl_idx = swing_low_node["index"]

    # Trend direction based on whether the low occurred before high or high before low
    trend = "BULLISH" if sl_idx <= sh_idx else "BEARISH"

    fib = calculate_fibonacci_levels(sh_price, sl_price, trend=trend)
    if "error" in fib:
        return {"status": "ERROR", "reason": fib["error"]}

    eq = fib["equilibrium"]
    # Premium vs. Discount definition:
    # Above equilibrium (50%) = Premium (expensive - favor selling)
    # Below equilibrium (50%) = Discount (cheap - favor buying)
    zone = "PREMIUM" if current_price >= eq else "DISCOUNT"

    # Check if price is within ICT Optimal Trade Entry (OTE) zone (0.618 to 0.786)
    ote = fib["ote_zone"]
    min_ote = min(ote["top"], ote["bottom"])
    max_ote = max(ote["top"], ote["bottom"])
    in_ote = min_ote <= current_price <= max_ote

    # Distance to OTE sweet spot
    sweet_spot = ote["sweet_spot"]
    dist_to_sweet_spot = abs(current_price - sweet_spot)

    return {
        "status": "OK",
        "trend": trend,
        "current_price": current_price,
        "swing_high": swing_high_node,
        "swing_low": swing_low_node,
        "equilibrium": eq,
        "zone": zone,
        "in_ote_zone": in_ote,
        "ote_sweet_spot": sweet_spot,
        "distance_to_sweet_spot": round(dist_to_sweet_spot, 5),
        "levels": fib["levels"],
        "ote_zone": ote,
    }

