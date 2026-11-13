"""Institutional-Grade Multi-Factor SMT Execution Engine & Inter-Market Triad Feed.

Synthesizes:
1. Inter-Market Triad Benchmarks (Rate-Limit Safe & Weekend-Aware):
   - Gold (XAU/USD): Benchmark 1 = XAG/USD (Silver), Benchmark 2 = UUP / DXY (US Dollar Index)
   - Bitcoin (BTC/USD): Benchmark 1 = ETH/USD (Ethereum), Benchmark 2 = QQQ / IXIC (Nasdaq 100)
2. Layer 1: 15-Minute HTF Context & Liquidity Sweep Lock (BSL/SSL sweep within last 6 bars OR active 15M OB/FVG)
3. Layer 2: Triple-Asset Triad SMT Engine (5M Fractal Pivots with 2-candle left/right confirmation)
4. Layer 3: Mandatory CISD + FVG Displacement & Spread Guardrail (< 4.5 pips for Gold)
5. Layer 4: Confluence Scoring (+15% Pair SMT, +30% Triad SMT -> 90%+ A+ Sniper Confluence)

Standard library only, Python 3.8+ compatible.
Strictly Paper Observation Only  -  No Real-Money Order Routing.
"""

from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone
import time
import os
import json

from .pairs import normalize_symbol, to_pips, from_pips
from .smc import analyze_smc, find_swings
from .liquidity import analyze_liquidity
from .fibonacci import analyze_fibonacci

# Benchmark configuration for institutional SMT
TRIAD_CONFIG: Dict[str, Dict[str, Any]] = {
    "XAU/USD": {
        "benchmark_1": {"symbol": "XAG/USD", "name": "Silver", "correlation": "positive"},
        "benchmark_2": {"symbol": "UUP", "alt_symbol": "DXY", "name": "US Dollar Index", "correlation": "inverse"},
        "max_safe_spread_pips": 4.5,
    },
    "BTC/USD": {
        "benchmark_1": {"symbol": "ETH/USD", "name": "Ethereum", "correlation": "positive"},
        "benchmark_2": {"symbol": "QQQ", "alt_symbol": "IXIC", "name": "Nasdaq 100", "correlation": "positive"},
        "max_safe_spread_pips": 50.0,
    },
}

# In-memory rate-limit cache for benchmark series: (symbol, interval) -> (cached_at_ts, chart_dict)
_BENCHMARK_CACHE: Dict[Tuple[str, str], Tuple[float, Dict[str, Any]]] = {}


def aggregate_5m_to_15m(candles_5m: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Synthesize 15-minute candles from 5-minute candles when native 15m feed is unavailable.
    Groups every 3 consecutive completed 5m bars into a standard 15m OHLC bar.
    """
    if not candles_5m or len(candles_5m) < 3:
        return []

    candles_15m: List[Dict[str, Any]] = []
    # Work with multiples of 3
    num_full_bars = len(candles_5m) // 3
    start_idx = len(candles_5m) % 3

    for b in range(num_full_bars):
        slice_3 = candles_5m[start_idx + b * 3 : start_idx + (b + 1) * 3]
        if len(slice_3) != 3:
            continue
        o = slice_3[0]["open"]
        h = max(c["high"] for c in slice_3)
        l = min(c["low"] for c in slice_3)
        c = slice_3[-1]["close"]
        vol = sum(c.get("volume", 0) for c in slice_3)
        ts = slice_3[-1]["timestamp"]
        dt_utc = slice_3[-1].get("datetime_utc", "")
        candles_15m.append({
            "timestamp": ts,
            "datetime_utc": dt_utc,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": vol,
        })
    return candles_15m


def fetch_benchmark_candles(
    benchmark_symbol: str,
    interval: str = "5m",
    offline_data: Optional[Dict[str, Any]] = None,
    now_ts: Optional[float] = None,
) -> Tuple[List[Dict[str, Any]], bool, str]:
    """
    Rate-limit safe candle fetcher for Triad benchmarks with in-memory TTL caching.
    
    Returns: (candles, is_fresh, status_note)
    """
    canon = normalize_symbol(benchmark_symbol)
    cache_key = (canon, interval)
    current_time = now_ts or time.time()

    # 1. Offline mock data injection (always priority for tests)
    if offline_data is not None:
        raw = (
            offline_data.get(canon)
            or offline_data.get(benchmark_symbol)
            or offline_data.get(canon.replace("/", ""))
        )
        if isinstance(raw, dict) and "candles" in raw:
            return raw["candles"], True, "Offline Test Feed"
        if isinstance(raw, list):
            return raw, True, "Offline Test Feed"

    # 2. Check in-memory cache
    cached = _BENCHMARK_CACHE.get(cache_key)
    ttl_seconds = 240 if interval == "5m" else 720  # Rate-limit safe TTL
    if cached and (current_time - cached[0] < ttl_seconds):
        return cached[1].get("candles", []), True, "Rate-Limit Cached Feed"

    # 3. Live fetch via data.py
    try:
        from .data import fetch_chart
        chart = fetch_chart(symbol=canon, interval=interval, range_period="2d")
        candles = chart.get("candles", [])
        if candles:
            last_ts = candles[-1]["timestamp"]
            # Weekend / Market-Hours freshness check
            age_hours = (current_time - last_ts) / 3600.0
            is_stale_or_closed = age_hours > 72.0
            status_note = "Stale / Market Closed" if is_stale_or_closed else "Fresh Real-Time Feed"
            _BENCHMARK_CACHE[cache_key] = (current_time, chart)
            return candles, not is_stale_or_closed, status_note
    except Exception as e:
        if cached:
            return cached[1].get("candles", []), False, f"Fallback Cache ({e})"

    return [], False, "Unavailable / Offline"


def evaluate_15m_htf_context(
    candles_15m: List[Dict[str, Any]],
    current_price: float,
    lookback_bars: int = 6,
    symbol: str = "XAU/USD",
) -> Dict[str, Any]:
    """
    Layer 1: 15-Minute HTF Context & Liquidity Sweep Lock.
    
    Verifies that the primary asset has either:
    a) Swept a confirmed 15m swing high (BSL) or 15m swing low (SSL) within the last 6 bars, OR
    b) Tapped into an active 15m Order Block or 15m Fair Value Gap (FVG).
    
    If neither condition is met, SMT is downgraded to WEAK / NO TRADE.
    """
    if not candles_15m or len(candles_15m) < 10:
        return {
            "has_context": False,
            "bias": "NEUTRAL",
            "type": "NONE",
            "description": "Insufficient 15m candle history (< 10 bars)",
            "details": {},
        }

    canon = normalize_symbol(symbol)
    n = len(candles_15m)
    recent_window = candles_15m[-lookback_bars:]

    # 1. Check 15m Liquidity Sweeps
    liq_15m = analyze_liquidity(candles_15m, symbol=canon)
    sweeps = liq_15m.get("sweeps", {}).get("recent_sweeps", [])
    recent_sweeps = [s for s in sweeps if s.get("candle_index", 0) >= (n - lookback_bars)]

    has_ssl_sweep = any(s.get("sentiment") == "BULLISH" for s in recent_sweeps)
    has_bsl_sweep = any(s.get("sentiment") == "BEARISH" for s in recent_sweeps)

    # 2. Check 15m Order Blocks & FVGs
    smc_15m = analyze_smc(candles_15m, current_price=current_price)
    ob_data = smc_15m.get("order_blocks", {})
    fvg_data = smc_15m.get("fvgs", {})

    inside_ob = ob_data.get("inside_order_block")
    nearest_demand = ob_data.get("nearest_demand")
    nearest_supply = ob_data.get("nearest_supply")

    inside_fvg = fvg_data.get("inside_fvg")

    # Demand OB tap: price low in recent window touched demand zone
    tapped_demand = False
    if nearest_demand:
        for c in recent_window:
            if nearest_demand["bottom"] <= c["low"] <= nearest_demand["top"] or (c["low"] <= nearest_demand["top"] and c["close"] >= nearest_demand["bottom"]):
                tapped_demand = True
                break

    # Supply OB tap: price high in recent window touched supply zone
    tapped_supply = False
    if nearest_supply:
        for c in recent_window:
            if nearest_supply["bottom"] <= c["high"] <= nearest_supply["top"] or (c["high"] >= nearest_supply["bottom"] and c["close"] <= nearest_supply["top"]):
                tapped_supply = True
                break

    # FVG tap
    tapped_bullish_fvg = False
    tapped_bearish_fvg = False
    for fvg in fvg_data.get("all_fvgs", []):
        if not fvg.get("mitigated"):
            if fvg["type"] == "BULLISH_FVG":
                for c in recent_window:
                    if fvg["bottom"] <= c["low"] <= fvg["top"]:
                        tapped_bullish_fvg = True
                        break
            elif fvg["type"] == "BEARISH_FVG":
                for c in recent_window:
                    if fvg["bottom"] <= c["high"] <= fvg["top"]:
                        tapped_bearish_fvg = True
                        break

    # Synthesize HTF verdict
    bullish_reasons = []
    bearish_reasons = []

    if has_ssl_sweep:
        bullish_reasons.append("15M Sell-Side Liquidity (SSL) swept within last 6 bars")
    if tapped_demand or (inside_ob and inside_ob.get("type") == "BULLISH_OB"):
        bullish_reasons.append("Tapped active 15M Demand Order Block")
    if tapped_bullish_fvg or (inside_fvg and inside_fvg.get("type") == "BULLISH_FVG"):
        bullish_reasons.append("Filled 15M Bullish Fair Value Gap (FVG)")

    if has_bsl_sweep:
        bearish_reasons.append("15M Buy-Side Liquidity (BSL) swept within last 6 bars")
    if tapped_supply or (inside_ob and inside_ob.get("type") == "BEARISH_OB"):
        bearish_reasons.append("Tapped active 15M Supply Order Block")
    if tapped_bearish_fvg or (inside_fvg and inside_fvg.get("type") == "BEARISH_FVG"):
        bearish_reasons.append("Filled 15M Bearish Fair Value Gap (FVG)")

    if bullish_reasons and not bearish_reasons:
        return {
            "has_context": True,
            "bias": "BULLISH",
            "type": "15M_BULLISH_HTF_LOCK",
            "description": " | ".join(bullish_reasons),
            "details": {
                "ssl_sweep": has_ssl_sweep,
                "demand_ob": tapped_demand,
                "bullish_fvg": tapped_bullish_fvg,
            },
        }
    elif bearish_reasons and not bullish_reasons:
        return {
            "has_context": True,
            "bias": "BEARISH",
            "type": "15M_BEARISH_HTF_LOCK",
            "description": " | ".join(bearish_reasons),
            "details": {
                "bsl_sweep": has_bsl_sweep,
                "supply_ob": tapped_supply,
                "bearish_fvg": tapped_bearish_fvg,
            },
        }
    elif bullish_reasons and bearish_reasons:
        # Both present; choose the more recent one
        return {
            "has_context": True,
            "bias": "CONFLUENT",
            "type": "15M_DUAL_KEY_LEVEL",
            "description": f"Bullish: {bullish_reasons[0]} | Bearish: {bearish_reasons[0]}",
            "details": {"bullish": bullish_reasons, "bearish": bearish_reasons},
        }

    return {
        "has_context": False,
        "bias": "NEUTRAL",
        "type": "MID_RANGE_EQUILIBRIUM",
        "description": "Mid-range equilibrium without 15M HTF liquidity sweep or OB/FVG key level tap",
        "details": {},
    }


def find_fractal_pivots_5m(
    candles: List[Dict[str, Any]],
    lookback_window: int = 25,
    fractal_n: int = 2,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Detect confirmed 5m fractal swing highs and swing lows across the last lookback_window bars.
    Requires fractal_n (2) candles left and fractal_n (2) candles right strictly conforming.
    """
    swing_highs = []
    swing_lows = []
    if not candles or len(candles) < fractal_n * 2 + 1:
        return swing_highs, swing_lows

    subset = candles[-lookback_window:] if len(candles) > lookback_window else candles
    offset = len(candles) - len(subset)
    m = len(subset)

    for i in range(fractal_n, m - fractal_n):
        h = subset[i]["high"]
        l = subset[i]["low"]
        ts = subset[i].get("timestamp")

        # Check Swing High
        is_sh = True
        for j in range(i - fractal_n, i + fractal_n + 1):
            if j != i and subset[j]["high"] >= h:
                is_sh = False
                break
        if is_sh:
            swing_highs.append({
                "local_index": i,
                "global_index": offset + i,
                "price": h,
                "timestamp": ts,
            })

        # Check Swing Low
        is_sl = True
        for j in range(i - fractal_n, i + fractal_n + 1):
            if j != i and subset[j]["low"] <= l:
                is_sl = False
                break
        if is_sl:
            swing_lows.append({
                "local_index": i,
                "global_index": offset + i,
                "price": l,
                "timestamp": ts,
            })

    return swing_highs, swing_lows


def detect_smt_divergence(
    primary_symbol: str,
    primary_candles_5m: List[Dict[str, Any]],
    b1_candles_5m: Optional[List[Dict[str, Any]]] = None,
    b2_candles_5m: Optional[List[Dict[str, Any]]] = None,
    b1_symbol: str = "",
    b2_symbol: str = "",
    b2_is_fresh: bool = True,
) -> Dict[str, Any]:
    """
    Layer 2: Triple-Asset Triad SMT Engine (5M Fractal Pivots).
    
    Detects two most recent confirmed 5m swing highs (SH1, SH2) and swing lows (SL1, SL2).
    Compares primary asset vs Benchmark 1 and Benchmark 2 (aligned within ±3 candles).
    
    Returns structured SMT analysis dictionary.
    """
    canon = normalize_symbol(primary_symbol)
    triad_cfg = TRIAD_CONFIG.get(canon, {})
    if not b1_symbol and triad_cfg:
        b1_symbol = triad_cfg["benchmark_1"]["symbol"]
    if not b2_symbol and triad_cfg:
        b2_symbol = triad_cfg["benchmark_2"]["symbol"]

    # 1. Primary Swings
    p_highs, p_lows = find_fractal_pivots_5m(primary_candles_5m, lookback_window=25, fractal_n=2)
    if len(p_highs) < 2 and len(p_lows) < 2:
        return {
            "detected": False,
            "type": "NONE",
            "tier": "SCANNING",
            "bias": "NEUTRAL",
            "description": "Insufficient 5m fractal pivots for SMT analysis",
            "confluence_bonus": 0,
            "details": {},
        }

    # 2. Benchmark 1 Swings
    b1_highs, b1_lows = ([], [])
    if b1_candles_5m and len(b1_candles_5m) >= 5:
        b1_highs, b1_lows = find_fractal_pivots_5m(b1_candles_5m, lookback_window=25, fractal_n=2)

    # 3. Benchmark 2 Swings
    b2_highs, b2_lows = ([], [])
    if b2_candles_5m and len(b2_candles_5m) >= 5 and b2_is_fresh:
        b2_highs, b2_lows = find_fractal_pivots_5m(b2_candles_5m, lookback_window=25, fractal_n=2)

    # Latest Candle for Invalidation Check
    latest_primary_bar = primary_candles_5m[-1] if primary_candles_5m else {}
    curr_close = latest_primary_bar.get("close", 0.0)

    # -------------------------------------------------------------
    # A. Check Bearish SMT (Distribution at Highs)
    # -------------------------------------------------------------
    if len(p_highs) >= 2 and len(b1_highs) >= 2:
        p_sh1, p_sh2 = p_highs[-2], p_highs[-1]
        b1_sh1, b1_sh2 = b1_highs[-2], b1_highs[-1]

        # Temporal pivot alignment check (within ±3 candles)
        idx_diff_1 = abs(p_sh1["local_index"] - b1_sh1["local_index"])
        idx_diff_2 = abs(p_sh2["local_index"] - b1_sh2["local_index"])

        if idx_diff_1 <= 3 and idx_diff_2 <= 3:
            # Check Primary Lower High (SH2 < SH1)
            p_lh = p_sh2["price"] < p_sh1["price"]
            p_hh = p_sh2["price"] > p_sh1["price"]
            # Check Benchmark 1 Higher High (SH2 > SH1)
            b1_hh = b1_sh2["price"] > b1_sh1["price"]
            b1_lh = b1_sh2["price"] < b1_sh1["price"]

            # Bearish SMT Divergence condition: Primary LH while Benchmark 1 HH (or vice versa)
            pair_bearish = (p_lh and b1_hh) or (p_hh and b1_lh)

            if pair_bearish:
                # Check Auto-Invalidation: If primary candle body closed above SH2
                if curr_close > p_sh2["price"]:
                    return {
                        "detected": False,
                        "type": "INVALIDATED",
                        "tier": "INVALIDATED",
                        "bias": "NEUTRAL",
                        "description": f"Bearish SMT invalidated: 5m candle body closed above SH2 ({p_sh2['price']:.2f})",
                        "confluence_bonus": 0,
                        "details": {"sh2": p_sh2["price"], "close": curr_close},
                    }

                # Check Benchmark 2 Triad Alignment
                triad_bearish = False
                b2_note = ""
                if len(b2_highs) >= 2 or len(b2_lows) >= 2:
                    if "XAU" in canon:
                        # Gold: UUP / DXY is inversely correlated.
                        # Bearish Gold SMT requires USD strength: UUP makes Higher Low (SL2 > SL1)
                        if len(b2_lows) >= 2:
                            uup_sl1, uup_sl2 = b2_lows[-2], b2_lows[-1]
                            if uup_sl2["price"] > uup_sl1["price"]:
                                triad_bearish = True
                                b2_note = f"{b2_symbol} Higher Low (USD Strength)"
                    else:
                        # BTC: QQQ is positively correlated.
                        # Bearish BTC SMT requires QQQ Higher High (SH2 > SH1) while BTC makes Lower High
                        if len(b2_highs) >= 2:
                            qqq_sh1, qqq_sh2 = b2_highs[-2], b2_highs[-1]
                            if qqq_sh2["price"] > qqq_sh1["price"]:
                                triad_bearish = True
                                b2_note = f"{b2_symbol} Higher High"

                if triad_bearish:
                    desc = f"TRIAD SMT (3-Asset): {b1_symbol} Swept BSL (HH) + {b2_note} vs {canon} Lower High (Distribution)"
                    return {
                        "detected": True,
                        "type": "BEARISH_TRIAD_SMT",
                        "tier": "TRIAD SMT (A+)",
                        "bias": "SELL",
                        "description": desc,
                        "confluence_bonus": 30,
                        "sh2_invalidation": p_sh2["price"],
                        "details": {
                            "primary": {"sh1": p_sh1["price"], "sh2": p_sh2["price"], "pattern": "LH" if p_lh else "HH"},
                            "benchmark_1": {"symbol": b1_symbol, "sh1": b1_sh1["price"], "sh2": b1_sh2["price"], "pattern": "HH" if b1_hh else "LH"},
                            "benchmark_2": {"symbol": b2_symbol, "status": b2_note},
                            "tier": "TRIAD SMT (3-ASSET)",
                        },
                    }
                else:
                    desc = f"PAIR SMT (2-Asset): {b1_symbol} Swept BSL (HH) vs {canon} Lower High (Weakness)"
                    return {
                        "detected": True,
                        "type": "BEARISH_PAIR_SMT",
                        "tier": "PAIR SMT (A)",
                        "bias": "SELL",
                        "description": desc,
                        "confluence_bonus": 15,
                        "sh2_invalidation": p_sh2["price"],
                        "details": {
                            "primary": {"sh1": p_sh1["price"], "sh2": p_sh2["price"], "pattern": "LH" if p_lh else "HH"},
                            "benchmark_1": {"symbol": b1_symbol, "sh1": b1_sh1["price"], "sh2": b1_sh2["price"], "pattern": "HH" if b1_hh else "LH"},
                            "benchmark_2": {"symbol": b2_symbol, "status": "Closed / Divergence Neutral"},
                            "tier": "PAIR SMT (2-ASSET)",
                        },
                    }

    # -------------------------------------------------------------
    # B. Check Bullish SMT (Accumulation at Lows)
    # -------------------------------------------------------------
    if len(p_lows) >= 2 and len(b1_lows) >= 2:
        p_sl1, p_sl2 = p_lows[-2], p_lows[-1]
        b1_sl1, b1_sl2 = b1_lows[-2], b1_lows[-1]

        # Temporal alignment check (within ±3 candles)
        idx_diff_1 = abs(p_sl1["local_index"] - b1_sl1["local_index"])
        idx_diff_2 = abs(p_sl2["local_index"] - b1_sl2["local_index"])

        if idx_diff_1 <= 3 and idx_diff_2 <= 3:
            # Check Primary Higher Low (SL2 > SL1)
            p_hl = p_sl2["price"] > p_sl1["price"]
            p_ll = p_sl2["price"] < p_sl1["price"]
            # Check Benchmark 1 Lower Low (SL2 < SL1)
            b1_ll = b1_sl2["price"] < b1_sl1["price"]
            b1_hl = b1_sl2["price"] > b1_sl1["price"]

            # Bullish SMT Divergence condition: Primary HL while Benchmark 1 LL (or vice versa)
            pair_bullish = (p_hl and b1_ll) or (p_ll and b1_hl)

            if pair_bullish:
                # Check Auto-Invalidation: If primary candle body closed below SL2
                if curr_close < p_sl2["price"]:
                    return {
                        "detected": False,
                        "type": "INVALIDATED",
                        "tier": "INVALIDATED",
                        "bias": "NEUTRAL",
                        "description": f"Bullish SMT invalidated: 5m candle body closed below SL2 ({p_sl2['price']:.2f})",
                        "confluence_bonus": 0,
                        "details": {"sl2": p_sl2["price"], "close": curr_close},
                    }

                # Check Benchmark 2 Triad Alignment
                triad_bullish = False
                b2_note = ""
                if len(b2_highs) >= 2 or len(b2_lows) >= 2:
                    if "XAU" in canon:
                        # Gold: UUP makes Lower High (SH2 < SH1, USD weakness)
                        if len(b2_highs) >= 2:
                            uup_sh1, uup_sh2 = b2_highs[-2], b2_highs[-1]
                            if uup_sh2["price"] < uup_sh1["price"]:
                                triad_bullish = True
                                b2_note = f"{b2_symbol} Lower High (USD Weakness)"
                    else:
                        # BTC: QQQ makes Lower Low (SL2 < SL1)
                        if len(b2_lows) >= 2:
                            qqq_sl1, qqq_sl2 = b2_lows[-2], b2_lows[-1]
                            if qqq_sl2["price"] < qqq_sl1["price"]:
                                triad_bullish = True
                                b2_note = f"{b2_symbol} Lower Low"

                if triad_bullish:
                    desc = f"TRIAD SMT (3-Asset): {b1_symbol} Swept SSL (LL) + {b2_note} vs {canon} Higher Low (Accumulation)"
                    return {
                        "detected": True,
                        "type": "BULLISH_TRIAD_SMT",
                        "tier": "TRIAD SMT (A+)",
                        "bias": "BUY",
                        "description": desc,
                        "confluence_bonus": 30,
                        "sl2_invalidation": p_sl2["price"],
                        "details": {
                            "primary": {"sl1": p_sl1["price"], "sl2": p_sl2["price"], "pattern": "HL" if p_hl else "LL"},
                            "benchmark_1": {"symbol": b1_symbol, "sl1": b1_sl1["price"], "sl2": b1_sl2["price"], "pattern": "LL" if b1_ll else "HL"},
                            "benchmark_2": {"symbol": b2_symbol, "status": b2_note},
                            "tier": "TRIAD SMT (3-ASSET)",
                        },
                    }
                else:
                    desc = f"PAIR SMT (2-Asset): {b1_symbol} Swept SSL (LL) vs {canon} Higher Low (Strength)"
                    return {
                        "detected": True,
                        "type": "BULLISH_PAIR_SMT",
                        "tier": "PAIR SMT (A)",
                        "bias": "BUY",
                        "description": desc,
                        "confluence_bonus": 15,
                        "sl2_invalidation": p_sl2["price"],
                        "details": {
                            "primary": {"sl1": p_sl1["price"], "sl2": p_sl2["price"], "pattern": "HL" if p_hl else "LL"},
                            "benchmark_1": {"symbol": b1_symbol, "sl1": b1_sl1["price"], "sl2": b1_sl2["price"], "pattern": "LL" if b1_ll else "HL"},
                            "benchmark_2": {"symbol": b2_symbol, "status": "Closed / Divergence Neutral"},
                            "tier": "PAIR SMT (2-ASSET)",
                        },
                    }

    return {
        "detected": False,
        "type": "NONE",
        "tier": "SCANNING",
        "bias": "NEUTRAL",
        "description": "Scanning 5m fractal pivots for Inter-Market SMT divergence",
        "confluence_bonus": 0,
        "details": {},
    }


def evaluate_cisd_displacement_and_spread(
    candles_5m: List[Dict[str, Any]],
    smt_bias: str,
    live_spread_pips: float,
    symbol: str = "XAU/USD",
) -> Dict[str, Any]:
    """
    Layer 3: Mandatory CISD + FVG Displacement & Spread Guardrail.
    
    Requires:
    1. Confirmed 5m Change in State of Delivery (CISD):
       - Bullish: Candle body closes beyond the opposing manipulation down-candle body.
       - Bearish: Candle body closes beyond the opposing manipulation up-candle body.
    2. Accompanied by a valid 3-candle 5m Fair Value Gap (FVG).
    3. Spread Guardrail: For Gold, spread must be <= 4.5 pips.
    """
    canon = normalize_symbol(symbol)
    triad_cfg = TRIAD_CONFIG.get(canon, {})
    max_spread = triad_cfg.get("max_safe_spread_pips", 4.5 if "XAU" in canon else 50.0)

    # Spread Guardrail Check
    is_spread_safe = live_spread_pips <= max_spread
    spread_note = f"Spread {live_spread_pips:.1f} pips (Safe <= {max_spread} pips)" if is_spread_safe else f"Spread LOCKED: {live_spread_pips:.1f} pips > {max_spread} pips threshold"

    if smt_bias not in ("BUY", "SELL"):
        return {
            "displacement_confirmed": False,
            "has_cisd": False,
            "has_fvg": False,
            "is_spread_safe": is_spread_safe,
            "status_badge": "SCANNING",
            "description": spread_note,
        }

    if not candles_5m or len(candles_5m) < 4:
        return {
            "displacement_confirmed": False,
            "has_cisd": False,
            "has_fvg": False,
            "is_spread_safe": is_spread_safe,
            "status_badge": "WAITING",
            "description": f"Insufficient bars for CISD verification | {spread_note}",
        }

    recent_4 = candles_5m[-4:]
    has_cisd = False
    has_fvg = False
    fvg_details = {}

    if smt_bias == "BUY":
        # Bullish CISD: Find the last bearish candle in recent bars
        # A subsequent green displacement candle body must close above that bearish candle's open/body-high
        bearish_candle = None
        for c in reversed(recent_4[:-1]):
            if c["close"] < c["open"]:
                bearish_candle = c
                break

        if bearish_candle:
            opposing_body_high = max(bearish_candle["open"], bearish_candle["close"])
            # Check latest candle body close
            disp_c = recent_4[-1]
            if disp_c["close"] > disp_c["open"] and disp_c["close"] > opposing_body_high:
                has_cisd = True

        # Bullish FVG check across recent 3 candles (low[i] > high[i-2])
        for idx in range(len(candles_5m) - 3, len(candles_5m)):
            c1 = candles_5m[idx - 2]
            c3 = candles_5m[idx]
            if c3["low"] > c1["high"]:
                has_fvg = True
                fvg_details = {"type": "BULLISH_FVG", "bottom": c1["high"], "top": c3["low"]}
                break

    elif smt_bias == "SELL":
        # Bearish CISD: Find the last bullish candle in recent bars
        # A subsequent red displacement candle body must close below that bullish candle's open/body-low
        bullish_candle = None
        for c in reversed(recent_4[:-1]):
            if c["close"] > c["open"]:
                bullish_candle = c
                break

        if bullish_candle:
            opposing_body_low = min(bullish_candle["open"], bullish_candle["close"])
            # Check latest candle body close
            disp_c = recent_4[-1]
            if disp_c["close"] < disp_c["open"] and disp_c["close"] < opposing_body_low:
                has_cisd = True

        # Bearish FVG check across recent 3 candles (high[i] < low[i-2])
        for idx in range(len(candles_5m) - 3, len(candles_5m)):
            c1 = candles_5m[idx - 2]
            c3 = candles_5m[idx]
            if c3["high"] < c1["low"]:
                has_fvg = True
                fvg_details = {"type": "BEARISH_FVG", "top": c1["low"], "bottom": c3["high"]}
                break

    displacement_confirmed = has_cisd and has_fvg and is_spread_safe

    if displacement_confirmed:
        badge = "CONFIRMED (SAFE)"
        desc = f"5M CISD Body Displacement & FVG Gap Confirmed | {spread_note}"
    elif not is_spread_safe:
        badge = "SPREAD LOCKED"
        desc = f"Blocked by High Volatility / News Spike | {spread_note}"
    else:
        badge = "WAITING DISPLACEMENT"
        waiting_parts = []
        if not has_cisd:
            waiting_parts.append("Awaiting CISD candle close")
        if not has_fvg:
            waiting_parts.append("Awaiting 5m FVG imbalance")
        desc = " | ".join(waiting_parts) + f" | {spread_note}"

    return {
        "displacement_confirmed": displacement_confirmed,
        "has_cisd": has_cisd,
        "has_fvg": has_fvg,
        "is_spread_safe": is_spread_safe,
        "status_badge": badge,
        "description": desc,
        "fvg_details": fvg_details,
    }


def analyze_institutional_triad_smt(
    primary_symbol: str,
    primary_candles_5m: List[Dict[str, Any]],
    primary_candles_15m: Optional[List[Dict[str, Any]]] = None,
    live_spread_pips: float = 1.8,
    offline_data: Optional[Dict[str, Any]] = None,
    now_ts: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Main entry point for Institutional-Grade Multi-Factor SMT Execution Engine.
    Integrates Layer 1 (15m HTF), Layer 2 (Triad 5M SMT), Layer 3 (CISD & Spread),
    and Layer 4 (Confluence Weighting).
    """
    canon = normalize_symbol(primary_symbol)
    current_time = now_ts or time.time()

    if not primary_candles_5m or len(primary_candles_5m) < 15:
        return {
            "status": "INSUFFICIENT_DATA",
            "tier": "SCANNING",
            "smt_type": "NONE",
            "bias": "NEUTRAL",
            "confluence_bonus": 0,
            "htf_context": {"has_context": False, "description": "Insufficient bars"},
            "smt": {"detected": False, "description": "Insufficient 5m bars"},
            "displacement": {"displacement_confirmed": False, "description": "Awaiting data"},
            "summary_en": "Insufficient 5M candle history for SMT analysis",
            "summary_mr": "SMT विश्लेषणासाठी 5m कॅण्डल्स अपुऱ्या आहेत",
        }

    current_price = primary_candles_5m[-1]["close"]

    # 1. 15-Minute HTF Context (Layer 1)
    if not primary_candles_15m or len(primary_candles_15m) < 10:
        primary_candles_15m = aggregate_5m_to_15m(primary_candles_5m)

    htf_res = evaluate_15m_htf_context(primary_candles_15m, current_price=current_price, symbol=canon)

    # 2. Benchmark Candles Fetching (Rate-Limit Safe & Weekend-Aware)
    triad_cfg = TRIAD_CONFIG.get(canon, {})
    b1_sym = triad_cfg.get("benchmark_1", {}).get("symbol", "XAG/USD" if "XAU" in canon else "ETH/USD")
    b2_sym = triad_cfg.get("benchmark_2", {}).get("symbol", "UUP" if "XAU" in canon else "QQQ")

    b1_candles, b1_fresh, b1_note = fetch_benchmark_candles(b1_sym, "5m", offline_data, current_time)
    b2_candles, b2_fresh, b2_note = fetch_benchmark_candles(b2_sym, "5m", offline_data, current_time)

    # 3. Triple-Asset Triad SMT Engine (Layer 2)
    smt_res = detect_smt_divergence(
        primary_symbol=canon,
        primary_candles_5m=primary_candles_5m,
        b1_candles_5m=b1_candles,
        b2_candles_5m=b2_candles,
        b1_symbol=b1_sym,
        b2_symbol=b2_sym,
        b2_is_fresh=b2_fresh,
    )

    # Lock with Layer 1 HTF context: If SMT formed in mid-range without HTF context, downgrade
    if smt_res["detected"] and not htf_res["has_context"]:
        smt_res["detected"] = False
        smt_res["tier"] = "WEAK / NO TRADE"
        smt_res["description"] = f"{smt_res['description']} (Downgraded: Mid-range equilibrium without 15M HTF context)"
        smt_res["confluence_bonus"] = 0

    # 4. Mandatory CISD + FVG Displacement & Spread Guardrail (Layer 3)
    smt_bias = smt_res.get("bias", "NEUTRAL") if smt_res.get("detected") else "NEUTRAL"
    disp_res = evaluate_cisd_displacement_and_spread(
        candles_5m=primary_candles_5m,
        smt_bias=smt_bias,
        live_spread_pips=live_spread_pips,
        symbol=canon,
    )

    # 5. Confluence Scoring & Tier Classification (Layer 4)
    final_tier = smt_res.get("tier", "SCANNING")
    confluence_bonus = 0

    if smt_res["detected"] and htf_res["has_context"]:
        if "TRIAD" in smt_res.get("type", "") and disp_res["displacement_confirmed"]:
            confluence_bonus = 30
            final_tier = "TRIAD SMT (A+)"
        elif "PAIR" in smt_res.get("type", ""):
            confluence_bonus = 15
            final_tier = "PAIR SMT (A)"
        else:
            confluence_bonus = 15

    # Human-readable Bilingual Summaries (English + Marathi)
    if final_tier == "TRIAD SMT (A+)":
        summary_en = f"A+ SNIPER CONFLUENCE: Triad SMT ({smt_res['description']}) + 15M HTF Lock + 5M CISD Displacement Confirmed."
        summary_mr = f"A+ स्नायपर कॉन्फ्लुअन्स: 3-अॅसेट ट्रायड SMT डायव्हर्जन्स + 15M की-लेव्हल + 5M CISD डिस्प्लेसमेंट यशस्वी."
    elif final_tier == "PAIR SMT (A)":
        summary_en = f"High-Probability Setup: 2-Asset Pair SMT ({smt_res['description']}) backed by 15M HTF context."
        summary_mr = f"हाय-प्रॉबॅबिलिटी सेटअप: 2-अॅसेट पेअर SMT डायव्हर्जन्स + 15M HTF कॉन्टेक्स्ट सक्रिय."
    elif smt_res.get("type") == "INVALIDATED":
        summary_en = smt_res["description"]
        summary_mr = f"SMT इनव्हॅलिडेट: कॅण्डल बॉडीने मुख्य स्विंग लेव्हल तोडली आहे."
    else:
        summary_en = "Scanning 5M fractal pivots & 15M HTF key levels for Inter-Market SMT divergence."
        summary_mr = "मार्केटमध्ये SMT ट्रायड डायव्हर्जन्स आणि 15M की-लेव्हल्सची तपासणी सुरू आहे."

    return {
        "symbol": canon,
        "tier": final_tier,
        "smt_type": smt_res.get("type", "NONE"),
        "bias": smt_bias,
        "confluence_bonus": confluence_bonus,
        "is_sniper_grade": (confluence_bonus >= 30),
        "htf_context": htf_res,
        "smt": smt_res,
        "displacement": disp_res,
        "benchmarks": {
            "b1": {"symbol": b1_sym, "is_fresh": b1_fresh, "status": b1_note},
            "b2": {"symbol": b2_sym, "is_fresh": b2_fresh, "status": b2_note},
        },
        "summary_en": summary_en,
        "summary_mr": summary_mr,
    }
