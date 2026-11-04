"""Smart Money Concepts (SMC) Engine.

Analyzes:
- Swing Highs and Swing Lows (Fractal Structure)
- Break of Structure (BOS) and Change of Character (CHoCH)
- Institutional Order Blocks (Bullish Demand / Bearish Supply)
- Fair Value Gaps (FVG) / Imbalances (Bullish & Bearish)
- Premium vs. Discount Dealing Ranges

Standard library only, Python 3.8+ compatible.
"""

from typing import Dict, List, Optional, Any, Tuple


def find_swings(candles: List[Dict[str, Any]], lookback: int = 4) -> List[Dict[str, Any]]:
    """
    Find fractal swing highs and swing lows.
    Returns chronologically sorted list of swing points.
    """
    swings = []
    n = len(candles)
    if n < lookback * 2 + 1:
        return swings

    for i in range(lookback, n - lookback):
        c = candles[i]
        h = c["high"]
        l = c["low"]

        # Check Swing High
        is_sh = True
        for j in range(i - lookback, i + lookback + 1):
            if j != i and candles[j]["high"] >= h:
                is_sh = False
                break
        if is_sh:
            swings.append({
                "type": "HIGH",
                "price": h,
                "index": i,
                "timestamp": c.get("timestamp"),
                "candle": c,
            })

        # Check Swing Low
        is_sl = True
        for j in range(i - lookback, i + lookback + 1):
            if j != i and candles[j]["low"] <= l:
                is_sl = False
                break
        if is_sl:
            swings.append({
                "type": "LOW",
                "price": l,
                "index": i,
                "timestamp": c.get("timestamp"),
                "candle": c,
            })

    swings.sort(key=lambda x: x["index"])
    return swings


def detect_market_structure(
    candles: List[Dict[str, Any]],
    swings: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Detects Trend, Break of Structure (BOS), and Change of Character (CHoCH).
    Strict rule: Candle must CLOSE beyond the structural level (wick touches don't count).
    """
    if len(swings) < 3:
        return {
            "trend": "NEUTRAL",
            "bos": None,
            "choch": None,
            "structure_points": [],
            "recent_events": [],
        }

    highs = [s for s in swings if s["type"] == "HIGH"]
    lows = [s for s in swings if s["type"] == "LOW"]

    events = []
    current_trend = "NEUTRAL"

    # Analyze swing transitions
    if len(highs) >= 2 and len(lows) >= 2:
        prev_h = highs[-2]["price"]
        curr_h = highs[-1]["price"]
        prev_l = lows[-2]["price"]
        curr_l = lows[-1]["price"]

        if curr_h > prev_h and curr_l > prev_l:
            current_trend = "BULLISH"  # Higher Highs & Higher Lows
        elif curr_h < prev_h and curr_l < prev_l:
            current_trend = "BEARISH"  # Lower Highs & Lower Lows
        else:
            current_trend = "RANGING"

    # Look for BOS / CHoCH in recent candles
    last_bos = None
    last_choch = None

    # Check the latest swing levels against subsequent candle closes
    if highs and lows:
        latest_sh = highs[-1]
        latest_sl = lows[-1]

        # Scan candles after the swing points
        for i in range(max(latest_sh["index"], latest_sl["index"]) + 1, len(candles)):
            c = candles[i]
            close = c["close"]

            # Bullish Break
            if close > latest_sh["price"]:
                event_type = "BOS_BULLISH" if current_trend == "BULLISH" else "CHOCH_BULLISH"
                evt = {
                    "type": event_type,
                    "price": latest_sh["price"],
                    "broken_at_index": i,
                    "candle_close": close,
                    "timestamp": c.get("timestamp"),
                    "direction": "BULLISH",
                }
                events.append(evt)
                if "CHOCH" in event_type:
                    last_choch = evt
                    current_trend = "BULLISH"
                else:
                    last_bos = evt

            # Bearish Break
            if close < latest_sl["price"]:
                event_type = "BOS_BEARISH" if current_trend == "BEARISH" else "CHOCH_BEARISH"
                evt = {
                    "type": event_type,
                    "price": latest_sl["price"],
                    "broken_at_index": i,
                    "candle_close": close,
                    "timestamp": c.get("timestamp"),
                    "direction": "BEARISH",
                }
                events.append(evt)
                if "CHOCH" in event_type:
                    last_choch = evt
                    current_trend = "BEARISH"
                else:
                    last_bos = evt

    return {
        "trend": current_trend,
        "latest_bos": last_bos,
        "latest_choch": last_choch,
        "recent_events": events[-5:],
        "recent_highs": [h["price"] for h in highs[-3:]],
        "recent_lows": [l["price"] for l in lows[-3:]],
    }


def detect_order_blocks(
    candles: List[Dict[str, Any]],
    swings: List[Dict[str, Any]],
    lookback_bars: int = 60,
) -> Dict[str, Any]:
    """
    Identifies institutional Order Blocks (OB):
    - Bullish OB: Last down-close candle before a rapid upward impulsive displacement.
    - Bearish OB: Last up-close candle before a rapid downward impulsive displacement.
    Tracks whether the Order Block is unmitigated (fresh) or mitigated.
    """
    n = len(candles)
    start_idx = max(0, n - lookback_bars)
    current_price = candles[-1]["close"] if candles else 0.0

    order_blocks = []

    for i in range(start_idx, n - 3):
        c0 = candles[i]
        c1 = candles[i + 1]
        c2 = candles[i + 2]

        body0 = abs(c0["close"] - c0["open"])
        disp_up = c2["close"] - c0["close"]
        disp_down = c0["close"] - c2["close"]

        # Bullish Order Block candidate:
        # c0 is red (close < open), followed by strong green expansion
        if c0["close"] < c0["open"]:
            # Check for strong upward displacement (at least 2 consecutive strong green bars or large move)
            if c1["close"] > c1["open"] and c2["close"] > c2["open"] and (c2["close"] > c0["high"]):
                ob_top = c0["high"]
                ob_bottom = c0["low"]
                # Check mitigation from i+3 to n
                mitigated = False
                tapped = False
                for k in range(i + 3, n):
                    bar = candles[k]
                    # If price dips into OB zone
                    if bar["low"] <= ob_top:
                        tapped = True
                        # If price closes below OB bottom, it is fully invalidated/broken
                        if bar["close"] < ob_bottom:
                            mitigated = True
                            break

                order_blocks.append({
                    "type": "BULLISH_OB",
                    "index": i,
                    "top": round(ob_top, 5),
                    "bottom": round(ob_bottom, 5),
                    "mean_threshold": round((ob_top + ob_bottom) / 2.0, 5),
                    "timestamp": c0.get("timestamp"),
                    "mitigated": mitigated,
                    "tapped": tapped,
                    "active": not mitigated,
                })

        # Bearish Order Block candidate:
        # c0 is green (close > open), followed by strong red displacement
        if c0["close"] > c0["open"]:
            if c1["close"] < c1["open"] and c2["close"] < c2["open"] and (c2["close"] < c0["low"]):
                ob_top = c0["high"]
                ob_bottom = c0["low"]
                # Check mitigation
                mitigated = False
                tapped = False
                for k in range(i + 3, n):
                    bar = candles[k]
                    # If price rallies into OB zone
                    if bar["high"] >= ob_bottom:
                        tapped = True
                        # If price closes above OB top, invalidated
                        if bar["close"] > ob_top:
                            mitigated = True
                            break

                order_blocks.append({
                    "type": "BEARISH_OB",
                    "index": i,
                    "top": round(ob_top, 5),
                    "bottom": round(ob_bottom, 5),
                    "mean_threshold": round((ob_top + ob_bottom) / 2.0, 5),
                    "timestamp": c0.get("timestamp"),
                    "mitigated": mitigated,
                    "tapped": tapped,
                    "active": not mitigated,
                })

    active_bullish = [ob for ob in order_blocks if ob["type"] == "BULLISH_OB" and ob["active"]]
    active_bearish = [ob for ob in order_blocks if ob["type"] == "BEARISH_OB" and ob["active"]]

    # Nearest active Bullish OB below current price
    nearest_demand = None
    bullish_below = [ob for ob in active_bullish if ob["top"] <= current_price or (ob["bottom"] <= current_price <= ob["top"])]
    if bullish_below:
        nearest_demand = max(bullish_below, key=lambda ob: ob["top"])

    # Nearest active Bearish OB above current price
    nearest_supply = None
    bearish_above = [ob for ob in active_bearish if ob["bottom"] >= current_price or (ob["bottom"] <= current_price <= ob["top"])]
    if bearish_above:
        nearest_supply = min(bearish_above, key=lambda ob: ob["bottom"])

    # Check if price is currently inside an active OB
    inside_ob = None
    for ob in (active_bullish + active_bearish):
        if ob["bottom"] <= current_price <= ob["top"]:
            inside_ob = ob
            break

    return {
        "all_order_blocks": order_blocks[-8:],
        "active_bullish_ob": active_bullish[-3:],
        "active_bearish_ob": active_bearish[-3:],
        "nearest_demand": nearest_demand,
        "nearest_supply": nearest_supply,
        "inside_order_block": inside_ob,
    }


def detect_fair_value_gaps(
    candles: List[Dict[str, Any]],
    lookback_bars: int = 50,
) -> Dict[str, Any]:
    """
    Detects Fair Value Gaps (FVG) / Imbalances:
    - Bullish FVG: Candle 1 High < Candle 3 Low (Gap between candle 1 and 3 wicks).
    - Bearish FVG: Candle 1 Low > Candle 3 High.
    Tracks whether the FVG is unmitigated (open) or filled.
    """
    n = len(candles)
    if n < 3:
        return {"all_fvgs": [], "active_fvgs": [], "nearest_bullish_fvg": None, "nearest_bearish_fvg": None}

    start_idx = max(0, n - lookback_bars)
    current_price = candles[-1]["close"]
    fvgs = []

    for i in range(start_idx + 2, n):
        c1 = candles[i - 2]
        c2 = candles[i - 1]
        c3 = candles[i]

        # Bullish FVG
        if c1["high"] < c3["low"]:
            gap_bottom = c1["high"]
            gap_top = c3["low"]
            # Check mitigation by bars after i
            filled = False
            for k in range(i + 1, n):
                if candles[k]["low"] <= gap_bottom:
                    filled = True
                    break

            fvgs.append({
                "type": "BULLISH_FVG",
                "index": i - 1,
                "top": round(gap_top, 5),
                "bottom": round(gap_bottom, 5),
                "consequent_encroachment": round((gap_top + gap_bottom) / 2.0, 5),  # 50% FVG mid
                "timestamp": c2.get("timestamp"),
                "filled": filled,
                "active": not filled,
            })

        # Bearish FVG
        if c1["low"] > c3["high"]:
            gap_top = c1["low"]
            gap_bottom = c3["high"]
            filled = False
            for k in range(i + 1, n):
                if candles[k]["high"] >= gap_top:
                    filled = True
                    break

            fvgs.append({
                "type": "BEARISH_FVG",
                "index": i - 1,
                "top": round(gap_top, 5),
                "bottom": round(gap_bottom, 5),
                "consequent_encroachment": round((gap_top + gap_bottom) / 2.0, 5),
                "timestamp": c2.get("timestamp"),
                "filled": filled,
                "active": not filled,
            })

    active_fvgs = [f for f in fvgs if f["active"]]
    active_bullish = [f for f in active_fvgs if f["type"] == "BULLISH_FVG"]
    active_bearish = [f for f in active_fvgs if f["type"] == "BEARISH_FVG"]

    nearest_bullish_fvg = None
    if active_bullish:
        nearest_bullish_fvg = min(active_bullish, key=lambda f: abs(current_price - f["top"]))

    nearest_bearish_fvg = None
    if active_bearish:
        nearest_bearish_fvg = min(active_bearish, key=lambda f: abs(current_price - f["bottom"]))

    inside_fvg = None
    for f in active_fvgs:
        if f["bottom"] <= current_price <= f["top"]:
            inside_fvg = f
            break

    return {
        "all_fvgs": fvgs[-10:],
        "active_fvgs": active_fvgs[-6:],
        "nearest_bullish_fvg": nearest_bullish_fvg,
        "nearest_bearish_fvg": nearest_bearish_fvg,
        "inside_fvg": inside_fvg,
    }


def analyze_smc(
    candles: List[Dict[str, Any]],
    current_price: Optional[float] = None,
    lookback: int = 4,
) -> Dict[str, Any]:
    """
    Complete Smart Money Concepts (SMC) evaluation:
    - Swing fractals
    - Market Structure (BOS & CHoCH)
    - Order Blocks
    - Fair Value Gaps (FVG)
    - Dealing Range & Equilibrium
    """
    if not candles:
        return {"status": "NO_DATA", "reason": "No candle data available"}

    if current_price is None:
        current_price = candles[-1]["close"]

    swings = find_swings(candles, lookback=lookback)
    structure = detect_market_structure(candles, swings)
    order_blocks = detect_order_blocks(candles, swings)
    fvgs = detect_fair_value_gaps(candles)

    # Dealing range calculation
    if swings:
        recent_high = max([s["price"] for s in swings[-6:] if s["type"] == "HIGH"], default=candles[-1]["high"])
        recent_low = min([s["price"] for s in swings[-6:] if s["type"] == "LOW"], default=candles[-1]["low"])
    else:
        recent_high = max([c["high"] for c in candles[-20:]], default=candles[-1]["high"])
        recent_low = min([c["low"] for c in candles[-20:]], default=candles[-1]["low"])

    range_diff = max(0.00001, recent_high - recent_low)
    eq = (recent_high + recent_low) / 2.0
    zone = "PREMIUM" if current_price >= eq else "DISCOUNT"

    return {
        "status": "OK",
        "trend": structure["trend"],
        "structure": structure,
        "order_blocks": order_blocks,
        "fvgs": fvgs,
        "dealing_range": {
            "high": round(recent_high, 5),
            "low": round(recent_low, 5),
            "equilibrium": round(eq, 5),
            "zone": zone,
        },
        "current_price": current_price,
    }

