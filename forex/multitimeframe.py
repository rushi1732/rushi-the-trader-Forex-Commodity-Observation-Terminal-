"""Multi-Timeframe (MTF) Confluence Engine.

Analyzes market across four institutional timeframes:
1. 4H (Macro / Higher Timeframe Trend & Dominant Order Blocks)
2. 1H (Intermediate Bias & Major Liquidity Pools)
3. 15M (Structural Alignment, BOS/CHoCH & Short-Term Momentum)
4. 5M (Micro Execution, Pullback Completion, RSI Hidden Divergence & Entry Trigger)

Standard library only, Python 3.8+ compatible.
"""

from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

from .pairs import normalize_symbol
from .data import fetch_chart
from .backtest import calculate_ema
from .divergence import calculate_rsi, detect_divergence
from .smc import analyze_smc
from .fibonacci import analyze_fibonacci


def aggregate_candles(candles_5m: List[Dict[str, Any]], target_minutes: int = 15) -> List[Dict[str, Any]]:
    """Synthesizes higher timeframe candles from 5m base bars with zero network latency."""
    if not candles_5m:
        return []
    bars_per_agg = max(1, target_minutes // 5)
    if bars_per_agg <= 1:
        return candles_5m

    agg = []
    for i in range(0, len(candles_5m), bars_per_agg):
        group = candles_5m[i:i + bars_per_agg]
        if not group:
            continue
        agg.append({
            "open": group[0].get("open", 0.0),
            "high": max(c.get("high", c.get("close", 0.0)) for c in group),
            "low": min(c.get("low", c.get("close", 0.0)) for c in group),
            "close": group[-1].get("close", 0.0),
            "volume": sum(c.get("volume", 0) for c in group),
            "timestamp": group[-1].get("timestamp", group[0].get("timestamp")),
        })
    return agg


def analyze_single_timeframe(candles: List[Dict[str, Any]], interval: str = "5m") -> Dict[str, Any]:
    """Analyzes a single timeframe slice for trend, RSI, divergence, and market structure."""
    if not candles or len(candles) < 5:
        return {
            "interval": interval,
            "status": "INSUFFICIENT_DATA",
            "trend": "NEUTRAL",
            "rsi": None,
            "rsi_state": "NEUTRAL",
            "divergence": {"detected": False, "type": "NONE"},
            "zone": "EQUILIBRIUM",
            "structure": "Consolidation",
            "candles_count": len(candles) if candles else 0,
        }

    closes = [c.get("close", 0.0) for c in candles]
    latest_close = closes[-1]

    # 1. RSI (14) Calculation
    rsis = calculate_rsi(closes, period=min(14, max(2, len(closes) - 1)))
    latest_rsi = rsis[-1] if rsis and rsis[-1] is not None else 50.0

    if latest_rsi < 30.0:
        rsi_state = "OVERSOLD"
    elif latest_rsi < 45.0:
        rsi_state = "DISCOUNT"
    elif latest_rsi > 70.0:
        rsi_state = "OVERBOUGHT"
    elif latest_rsi > 55.0:
        rsi_state = "PREMIUM"
    else:
        rsi_state = "EQUILIBRIUM"

    # 2. Trend & EMAs
    period_fast = min(9, len(closes) - 1)
    period_slow = min(21, len(closes) - 1)
    ema9 = calculate_ema(closes, period_fast)
    ema21 = calculate_ema(closes, period_slow)
    e9 = ema9[-1] if ema9 and ema9[-1] is not None else latest_close
    e21 = ema21[-1] if ema21 and ema21[-1] is not None else latest_close

    # 3. SMC Structure & Trend
    smc_data = analyze_smc(candles) if len(candles) >= 15 else {}
    smc_trend = smc_data.get("trend", "NEUTRAL")
    struct = smc_data.get("structure", {})

    structure_desc = "Consolidation"
    if struct.get("latest_bos"):
        structure_desc = f"BOS {struct['latest_bos'].get('type', '')}"
    elif struct.get("latest_choch"):
        structure_desc = f"CHoCH {struct['latest_choch'].get('type', '')}"
    elif smc_trend != "NEUTRAL":
        structure_desc = f"{smc_trend.title()} Structure"

    # Direction synthesis
    if smc_trend == "BULLISH" and e9 >= e21:
        trend = "BULLISH"
    elif smc_trend == "BEARISH" and e9 <= e21:
        trend = "BEARISH"
    elif e9 > e21 and latest_close >= e9:
        trend = "BULLISH"
    elif e9 < e21 and latest_close <= e9:
        trend = "BEARISH"
    else:
        trend = smc_trend if smc_trend != "NEUTRAL" else ("BULLISH" if e9 > e21 else ("BEARISH" if e9 < e21 else "NEUTRAL"))

    # 4. RSI Divergence (Hidden Continuation & Regular Reversal)
    div_data = detect_divergence(candles, rsis)

    # 5. Fibonacci Zone (Discount vs Premium)
    fib_data = analyze_fibonacci(candles) if len(candles) >= 15 else {}
    zone = fib_data.get("zone", "EQUILIBRIUM")

    return {
        "interval": interval,
        "status": "OK",
        "trend": trend,
        "rsi": round(latest_rsi, 1) if latest_rsi is not None else None,
        "rsi_state": rsi_state,
        "divergence": div_data,
        "zone": zone,
        "structure": structure_desc,
        "candles_count": len(candles),
        "latest_close": latest_close,
    }


def analyze_multi_timeframe(
    symbol: str = "XAU/USD",
    offline_data: Optional[Dict[str, Any]] = None,
    candles_by_tf: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    fetch_live: bool = False,
) -> Dict[str, Any]:
    """
    Performs comprehensive multi-timeframe analysis across 4H, 1H, 15M, and 5M.

    Returns:
    - timeframes: dict of analysis per interval (5m, 15m, 1h, 4h)
    - overall_bias: 'BULLISH' | 'BEARISH' | 'NEUTRAL'
    - alignment_score: 0 to 100
    - aligned_count: e.g. '4/4', '3/4', '2/4'
    - htf_bias: 4H & 1H institutional directional bias
    - ltf_trigger: 5M execution trigger (with RSI divergence)
    - verdict: summary status label
    - bonus_score: Confluence bonus points (+15 to +30)
    - summary_marathi: Human-readable explanation in Marathi
    """
    canon = normalize_symbol(symbol)
    tf_intervals = ["4h", "1h", "15m", "5m"]
    tf_minutes = {"4h": 240, "1h": 60, "15m": 15, "5m": 5}
    tf_ranges = {"4h": "3mo", "1h": "1mo", "15m": "5d", "5m": "1d"}

    timeframe_results: Dict[str, Any] = {}
    base_5m = candles_by_tf.get("5m") if candles_by_tf else None

    for tf in tf_intervals:
        candles: List[Dict[str, Any]] = []
        if candles_by_tf and tf in candles_by_tf and candles_by_tf[tf]:
            candles = candles_by_tf[tf]
        elif base_5m and not fetch_live:
            candles = aggregate_candles(base_5m, target_minutes=tf_minutes[tf])
        elif fetch_live or (offline_data is not None):
            try:
                chart = fetch_chart(
                    symbol=canon,
                    interval=tf,
                    range_period=tf_ranges.get(tf, "5d"),
                    offline_data=offline_data,
                )
                candles = chart.get("candles", [])
            except Exception:
                if base_5m:
                    candles = aggregate_candles(base_5m, target_minutes=tf_minutes[tf])
                else:
                    candles = []
        elif base_5m:
            candles = aggregate_candles(base_5m, target_minutes=tf_minutes[tf])

        timeframe_results[tf] = analyze_single_timeframe(candles, interval=tf)

    # Calculate Alignment
    bullish_count = sum(1 for tf in tf_intervals if timeframe_results[tf]["trend"] == "BULLISH")
    bearish_count = sum(1 for tf in tf_intervals if timeframe_results[tf]["trend"] == "BEARISH")
    total_valid = sum(1 for tf in tf_intervals if timeframe_results[tf]["status"] == "OK") or 4

    htf_4h = timeframe_results["4h"]["trend"]
    htf_1h = timeframe_results["1h"]["trend"]
    ltf_15m = timeframe_results["15m"]["trend"]
    ltf_5m = timeframe_results["5m"]["trend"]

    div_5m = timeframe_results["5m"].get("divergence", {})
    div_15m = timeframe_results["15m"].get("divergence", {})

    overall_bias = "NEUTRAL"
    verdict = "NEUTRAL / MIXED"
    alignment_score = 50
    bonus_score = 0
    aligned_ratio = f"{max(bullish_count, bearish_count)}/{total_valid}"

    # 1. Full 4/4 Quadruple Confluence Alignment
    if bullish_count == 4:
        overall_bias = "BULLISH"
        verdict = "FULL QUADRUPLE ALIGNMENT (4H + 1H + 15M + 5M BULLISH)"
        alignment_score = 100
        bonus_score = 30
    elif bearish_count == 4:
        overall_bias = "BEARISH"
        verdict = "FULL QUADRUPLE ALIGNMENT (4H + 1H + 15M + 5M BEARISH)"
        alignment_score = 100
        bonus_score = 30

    # 2. Strong 3/4 Institutional HTF Alignment
    elif bullish_count >= 3 and htf_4h == "BULLISH":
        overall_bias = "BULLISH"
        verdict = f"STRONG HTF ALIGNMENT ({bullish_count}/4 BULLISH)"
        alignment_score = 80
        bonus_score = 20
    elif bearish_count >= 3 and htf_4h == "BEARISH":
        overall_bias = "BEARISH"
        verdict = f"STRONG HTF ALIGNMENT ({bearish_count}/4 BEARISH)"
        alignment_score = 80
        bonus_score = 20

    # 3. Intermediate 3/4 Alignment
    elif bullish_count >= 3:
        overall_bias = "BULLISH"
        verdict = "BULLISH MOMENTUM (3/4 TIMEFRAMES)"
        alignment_score = 75
        bonus_score = 15
    elif bearish_count >= 3:
        overall_bias = "BEARISH"
        verdict = "BEARISH MOMENTUM (3/4 TIMEFRAMES)"
        alignment_score = 75
        bonus_score = 15

    # 4. Counter-trend / Pullback setup
    elif htf_4h == "BULLISH" and htf_1h == "BULLISH" and (ltf_5m == "BEARISH" or ltf_15m == "BEARISH"):
        overall_bias = "BULLISH"
        if div_5m.get("type") == "HIDDEN_BULLISH" or div_15m.get("type") == "HIDDEN_BULLISH":
            verdict = "HTF BULLISH (5M/15M Pullback with Hidden Bullish Divergence)"
            alignment_score = 85
            bonus_score = 25
        else:
            verdict = "HTF BULLISH (Pullback in progress on 5M/15M)"
            alignment_score = 65
            bonus_score = 10
    elif htf_4h == "BEARISH" and htf_1h == "BEARISH" and (ltf_5m == "BULLISH" or ltf_15m == "BULLISH"):
        overall_bias = "BEARISH"
        if div_5m.get("type") == "HIDDEN_BEARISH" or div_15m.get("type") == "HIDDEN_BEARISH":
            verdict = "HTF BEARISH (5M/15M Bounce with Hidden Bearish Divergence)"
            alignment_score = 85
            bonus_score = 25
        else:
            verdict = "HTF BEARISH (Bounce in progress on 5M/15M)"
            alignment_score = 65
            bonus_score = 10
    else:
        overall_bias = "NEUTRAL"
        verdict = f"MIXED MARKET STRUCTURE ({bullish_count} Bull / {bearish_count} Bear)"
        alignment_score = 40
        bonus_score = 0

    # Generate Marathi summary
    marathi_tf_parts = []
    for tf_key, tf_label in [("4h", "४ तास"), ("1h", "१ तास"), ("15m", "१५ मिनिटे"), ("5m", "५ मिनिटे")]:
        t_val = timeframe_results[tf_key]["trend"]
        t_mr = "अपट्रेंड" if t_val == "BULLISH" else ("डाऊनट्रेंड" if t_val == "BEARISH" else "साइडवेज")
        marathi_tf_parts.append(f"{tf_label}: {t_mr}")

    tf_summary_str = ", ".join(marathi_tf_parts)

    if alignment_score >= 80:
        marathi_concl = f"सर्व महत्त्वाच्या टाईमफ्रेममध्ये मजबूत संस्थात्मक सुसंवाद ({aligned_ratio}) आहे. ट्रेडची दिशा स्पष्ट आहे."
    elif alignment_score >= 65:
        marathi_concl = "मोठ्या टाईमफ्रेमचा ट्रेंड कायम असून छोट्या टाईमफ्रेमवर रिट्रेसमेंट/पुलबॅक चालू आहे."
    else:
        marathi_concl = "टाईमफ्रेममध्ये संमिश्र स्थिती असून कन्सॉलिडेशन दिसून येत आहे."

    summary_marathi = f"{tf_summary_str} | {marathi_concl}"

    return {
        "symbol": canon,
        "overall_bias": overall_bias,
        "alignment_score": alignment_score,
        "aligned_count": aligned_ratio,
        "bullish_count": bullish_count,
        "bearish_count": bearish_count,
        "htf_trend_4h": htf_4h,
        "htf_trend_1h": htf_1h,
        "ltf_trend_15m": ltf_15m,
        "ltf_trend_5m": ltf_5m,
        "divergence_5m": div_5m,
        "divergence_15m": div_15m,
        "verdict": verdict,
        "bonus_score": bonus_score,
        "timeframes": timeframe_results,
        "summary_marathi": summary_marathi,
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }
