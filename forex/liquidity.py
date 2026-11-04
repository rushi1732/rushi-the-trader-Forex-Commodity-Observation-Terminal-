"""Liquidity Pools & Stop-Loss Hunt (Turtle Soup) Engine.

Identifies:
- Buy-Side Liquidity (BSL): Resting stops above Equal Highs (EQH), Session Highs, Swing Highs.
- Sell-Side Liquidity (SSL): Resting stops below Equal Lows (EQL), Session Lows, Swing Lows.
- Liquidity Sweeps / Stop-Loss Hunts (Turtle Soup):
  Price wicks beyond liquidity levels to trigger retail stops, then closes back inside,
  setting up high-probability institutional reversals.

Standard library only, Python 3.8+ compatible.
"""

from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone
from .pairs import to_pips, get_pair_info


def find_equal_highs_and_lows(
    candles: List[Dict[str, Any]],
    symbol: str,
    pip_tolerance: float = 3.0,
    lookback: int = 40,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Find Equal Highs (EQH) and Equal Lows (EQL) within pip_tolerance.
    These act as liquidity magnets for stop hunts.
    """
    if len(candles) < 10:
        return {"eqh": [], "eql": []}

    subset = candles[-lookback:]
    eqh_list = []
    eql_list = []

    # Check pairs of local peaks and troughs
    for i in range(len(subset) - 2):
        for j in range(i + 3, len(subset)):
            # Check high equality
            diff_pips_h = abs(to_pips(symbol, subset[i]["high"] - subset[j]["high"]))
            if diff_pips_h <= pip_tolerance:
                eqh_list.append({
                    "price": round((subset[i]["high"] + subset[j]["high"]) / 2.0, 5),
                    "pips_diff": round(diff_pips_h, 1),
                    "indices": (i, j),
                    "type": "EQH",
                    "description": f"Equal Highs (BSL) within {diff_pips_h:.1f} pips",
                })

            # Check low equality
            diff_pips_l = abs(to_pips(symbol, subset[i]["low"] - subset[j]["low"]))
            if diff_pips_l <= pip_tolerance:
                eql_list.append({
                    "price": round((subset[i]["low"] + subset[j]["low"]) / 2.0, 5),
                    "pips_diff": round(diff_pips_l, 1),
                    "indices": (i, j),
                    "type": "EQL",
                    "description": f"Equal Lows (SSL) within {diff_pips_l:.1f} pips",
                })

    # Deduplicate close clusters
    dedup_eqh = []
    for h in eqh_list:
        if not any(abs(to_pips(symbol, h["price"] - x["price"])) < pip_tolerance for x in dedup_eqh):
            dedup_eqh.append(h)

    dedup_eql = []
    for l in eql_list:
        if not any(abs(to_pips(symbol, l["price"] - x["price"])) < pip_tolerance for x in dedup_eql):
            dedup_eql.append(l)

    return {
        "eqh": dedup_eqh[-3:],
        "eql": dedup_eql[-3:],
    }


def detect_liquidity_sweeps(
    candles: List[Dict[str, Any]],
    symbol: str,
    key_levels: Optional[List[Dict[str, Any]]] = None,
    lookback_bars: int = 30,
) -> Dict[str, Any]:
    """
    Detects Liquidity Sweeps / Stop-Loss Hunts (Turtle Soup pattern):
    - Bullish Liquidity Sweep (SSL Hunt):
      Price wicks below a key low (SSL / Asian Low / Equal Lows), triggering stops,
      but the candle CLOSES back above that key low, followed by bullish momentum.
    - Bearish Liquidity Sweep (BSL Hunt):
      Price wicks above a key high (BSL / Asian High / Equal Highs), triggering breakout buys/stops,
      but the candle CLOSES back below that key high, followed by bearish momentum.
    """
    if len(candles) < 5:
        return {
            "recent_sweeps": [],
            "latest_sweep": None,
            "has_bullish_sweep": False,
            "has_bearish_sweep": False,
        }

    n = len(candles)
    start_idx = max(5, n - lookback_bars)
    sweeps = []

    # Gather swing high/low levels as reference liquidity pools
    levels_to_test = []
    if key_levels:
        levels_to_test.extend(key_levels)

    # Derive local high/low levels
    for i in range(start_idx - 5, n - 2):
        if i >= 2 and i < n - 2:
            # Local high
            if candles[i]["high"] > candles[i-1]["high"] and candles[i]["high"] > candles[i+1]["high"]:
                levels_to_test.append({"type": "BSL", "price": candles[i]["high"], "index": i})
            # Local low
            if candles[i]["low"] < candles[i-1]["low"] and candles[i]["low"] < candles[i+1]["low"]:
                levels_to_test.append({"type": "SSL", "price": candles[i]["low"], "index": i})

    # Test recent candles (last 10 candles) for sweep patterns
    test_range = range(max(start_idx, n - 10), n)

    for i in test_range:
        c = candles[i]
        c_open = c["open"]
        c_close = c["close"]
        c_high = c["high"]
        c_low = c["low"]

        # Check BSL Sweeps (Bearish Turtle Soup)
        for lvl in levels_to_test:
            if lvl["type"] in ("BSL", "EQH", "HIGH") and lvl["index"] < i - 1:
                ref_price = lvl["price"]
                # Condition: High broke above reference, but Close failed and closed BELOW reference
                if c_high > ref_price and c_close < ref_price:
                    sweep_pips = to_pips(symbol, c_high - ref_price)
                    if sweep_pips >= 0.5:  # meaningful sweep
                        sweeps.append({
                            "type": "BEARISH_BSL_SWEEP",
                            "sentiment": "BEARISH",
                            "level_price": ref_price,
                            "high_wick": c_high,
                            "close_price": c_close,
                            "sweep_pips": round(sweep_pips, 1),
                            "candle_index": i,
                            "timestamp": c.get("timestamp"),
                            "description": f"Buy-Side Liquidity swept by {sweep_pips:.1f} pips at {ref_price} (Stop Hunt / Fakeout)",
                        })

            # Check SSL Sweeps (Bullish Turtle Soup)
            if lvl["type"] in ("SSL", "EQL", "LOW") and lvl["index"] < i - 1:
                ref_price = lvl["price"]
                # Condition: Low broke below reference, but Close rejected and closed ABOVE reference
                if c_low < ref_price and c_close > ref_price:
                    sweep_pips = to_pips(symbol, ref_price - c_low)
                    if sweep_pips >= 0.5:
                        sweeps.append({
                            "type": "BULLISH_SSL_SWEEP",
                            "sentiment": "BULLISH",
                            "level_price": ref_price,
                            "low_wick": c_low,
                            "close_price": c_close,
                            "sweep_pips": round(sweep_pips, 1),
                            "candle_index": i,
                            "timestamp": c.get("timestamp"),
                            "description": f"Sell-Side Liquidity swept by {sweep_pips:.1f} pips at {ref_price} (Stop Hunt / Fakeout)",
                        })

    latest_sweep = sweeps[-1] if sweeps else None
    has_bullish = any(s["sentiment"] == "BULLISH" for s in sweeps[-3:])
    has_bearish = any(s["sentiment"] == "BEARISH" for s in sweeps[-3:])

    return {
        "recent_sweeps": sweeps[-5:],
        "latest_sweep": latest_sweep,
        "has_bullish_sweep": has_bullish,
        "has_bearish_sweep": has_bearish,
    }


def analyze_liquidity(
    candles: List[Dict[str, Any]],
    symbol: str,
    asian_range: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Comprehensive liquidity pools and stop hunt analysis.
    """
    if not candles:
        return {"status": "NO_DATA"}

    current_price = candles[-1]["close"]
    eq_pools = find_equal_highs_and_lows(candles, symbol)

    # Key external liquidity levels
    key_levels = []
    for eqh in eq_pools["eqh"]:
        key_levels.append({"type": "EQH", "price": eqh["price"], "index": eqh["indices"][1]})
    for eql in eq_pools["eql"]:
        key_levels.append({"type": "EQL", "price": eql["price"], "index": eql["indices"][1]})

    if asian_range:
        if asian_range.get("asian_high"):
            key_levels.append({"type": "BSL", "price": asian_range["asian_high"], "index": 0})
        if asian_range.get("asian_low"):
            key_levels.append({"type": "SSL", "price": asian_range["asian_low"], "index": 0})

    sweep_analysis = detect_liquidity_sweeps(candles, symbol, key_levels=key_levels)

    # Distances to nearest BSL and SSL pools
    highs = [c["high"] for c in candles[-30:]]
    lows = [c["low"] for c in candles[-30:]]

    bsl_pool = max(highs) if highs else current_price
    ssl_pool = min(lows) if lows else current_price

    dist_bsl_pips = to_pips(symbol, max(0.0, bsl_pool - current_price))
    dist_ssl_pips = to_pips(symbol, max(0.0, current_price - ssl_pool))

    return {
        "status": "OK",
        "current_price": current_price,
        "bsl_target": round(bsl_pool, 5),
        "ssl_target": round(ssl_pool, 5),
        "dist_bsl_pips": round(dist_bsl_pips, 1),
        "dist_ssl_pips": round(dist_ssl_pips, 1),
        "equal_highs": eq_pools["eqh"],
        "equal_lows": eq_pools["eql"],
        "sweeps": sweep_analysis,
    }

