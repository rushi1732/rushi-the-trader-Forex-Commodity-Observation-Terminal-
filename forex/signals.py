"""Institutional Confluence & Automated Signal Engine.

Synthesizes:
1. Smart Money Concepts (SMC: BOS, CHoCH, Order Blocks, FVGs)
2. Inner Circle Trader (ICT: Killzones, Silver Bullet, Judas Swings)
3. Fibonacci Retracements & Optimal Trade Entry (OTE 0.618 - 0.786)
4. Session-based Strategies (Asian range accumulation, London/NY expansions)
5. Liquidity Pools & Stop-Loss Hunt Detection (BSL/SSL sweeps / Turtle Soup)

Generates accurate, institutional-grade automated paper trading signals with:
- Action (BUY / SELL / NEUTRAL)
- Entry Price, Invalidation Stop-Loss, TP1 (1:2 R:R), TP2 (1:3+ R:R)
- Confluence Checklist & Confidence Grade (Grade A+, A, B)
- Zero fake signals: Requires multi-factor institutional alignment.

Standard library only, Python 3.8+ compatible.
"""

from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone
import math

from .pairs import normalize_symbol, get_pair_info, to_pips, from_pips
from .sessions import utc_to_et, is_market_open_for_symbol
from .fibonacci import analyze_fibonacci
from .smc import analyze_smc
from .ict import analyze_ict
from .liquidity import analyze_liquidity
from .data import fetch_chart, MAJOR_PAIRS


def evaluate_confluence_signal(
    symbol: str,
    candles: List[Dict[str, Any]],
    interval: str = "5m",
    now_dt: Optional[datetime] = None,
    asian_range: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Evaluates multi-factor institutional confluence across SMC, ICT, Fib, and Liquidity.
    """
    canon = normalize_symbol(symbol)
    pair_info = get_pair_info(canon)
    pip_size = pair_info["pip_size"]
    decimals = pair_info["decimals"]

    if not candles or len(candles) < 15:
        return {
            "symbol": canon,
            "action": "NO_SIGNAL",
            "grade": "N/A",
            "confluence_score": 0,
            "reason": "Insufficient candlestick data (minimum 15 bars required)",
        }

    if now_dt is None:
        now_utc = datetime.now(timezone.utc)
    else:
        now_utc = now_dt if now_dt.tzinfo else now_dt.replace(tzinfo=timezone.utc)
    now_et = utc_to_et(now_utc)

    current_price = candles[-1]["close"]
    latest_candle = candles[-1]

    # 1. Component Analyses
    smc_data = analyze_smc(candles, current_price=current_price)
    fib_data = analyze_fibonacci(candles, current_price=current_price)
    liq_data = analyze_liquidity(candles, symbol=canon, asian_range=asian_range)
    ict_data = analyze_ict(candles, symbol=canon, now_et=now_et, asian_range=asian_range)

    # 2. Bullish vs Bearish Confluence Scoring
    bullish_score = 0
    bearish_score = 0
    bullish_factors = []
    bearish_factors = []

    # Factor A: Market Structure (BOS / CHoCH)
    structure = smc_data.get("structure", {})
    trend = smc_data.get("trend", "NEUTRAL")
    latest_bos = structure.get("latest_bos")
    latest_choch = structure.get("latest_choch")

    if trend == "BULLISH":
        bullish_score += 15
        bullish_factors.append("Market Structure is Bullish (Higher Highs & Higher Lows)")
    elif trend == "BEARISH":
        bearish_score += 15
        bearish_factors.append("Market Structure is Bearish (Lower Highs & Lower Lows)")

    if latest_choch:
        if latest_choch["direction"] == "BULLISH":
            bullish_score += 15
            bullish_factors.append(f"Bullish Change of Character (CHoCH) at {latest_choch['price']}")
        elif latest_choch["direction"] == "BEARISH":
            bearish_score += 15
            bearish_factors.append(f"Bearish Change of Character (CHoCH) at {latest_choch['price']}")
    elif latest_bos:
        if latest_bos["direction"] == "BULLISH":
            bullish_score += 10
            bullish_factors.append(f"Bullish Break of Structure (BOS) at {latest_bos['price']}")
        elif latest_bos["direction"] == "BEARISH":
            bearish_score += 10
            bearish_factors.append(f"Bearish Break of Structure (BOS) at {latest_bos['price']}")

    # Factor B: Order Block (Demand / Supply)
    ob_data = smc_data.get("order_blocks", {})
    inside_ob = ob_data.get("inside_order_block")
    nearest_demand = ob_data.get("nearest_demand")
    nearest_supply = ob_data.get("nearest_supply")

    if inside_ob:
        if inside_ob["type"] == "BULLISH_OB":
            bullish_score += 20
            bullish_factors.append(f"Price currently mitigating Bullish Order Block ({inside_ob['bottom']} - {inside_ob['top']})")
        elif inside_ob["type"] == "BEARISH_OB":
            bearish_score += 20
            bearish_factors.append(f"Price currently mitigating Bearish Order Block ({inside_ob['bottom']} - {inside_ob['top']})")
    else:
        # Check proximity to nearest demand/supply (within 5 pips)
        if nearest_demand and to_pips(canon, abs(current_price - nearest_demand["top"])) <= 5.0:
            bullish_score += 12
            bullish_factors.append(f"Price near active Bullish Demand OB at {nearest_demand['top']}")
        if nearest_supply and to_pips(canon, abs(current_price - nearest_supply["bottom"])) <= 5.0:
            bearish_score += 12
            bearish_factors.append(f"Price near active Bearish Supply OB at {nearest_supply['bottom']}")

    # Factor C: Fair Value Gap (FVG)
    fvg_data = smc_data.get("fvgs", {})
    inside_fvg = fvg_data.get("inside_fvg")
    if inside_fvg:
        if inside_fvg["type"] == "BULLISH_FVG":
            bullish_score += 15
            bullish_factors.append(f"Price filling Bullish FVG ({inside_fvg['bottom']} - {inside_fvg['top']})")
        elif inside_fvg["type"] == "BEARISH_FVG":
            bearish_score += 15
            bearish_factors.append(f"Price filling Bearish FVG ({inside_fvg['bottom']} - {inside_fvg['top']})")

    # Factor D: Fibonacci OTE & Dealing Range (Premium vs Discount)
    zone = fib_data.get("zone", "EQUILIBRIUM")
    in_ote = fib_data.get("in_ote_zone", False)
    sweet_spot = fib_data.get("ote_sweet_spot")

    if zone == "DISCOUNT":
        bullish_score += 10
        bullish_factors.append(f"Price in Discount Zone (< Equilibrium {fib_data.get('equilibrium')}) - Favorable for Longs")
    elif zone == "PREMIUM":
        bearish_score += 10
        bearish_factors.append(f"Price in Premium Zone (> Equilibrium {fib_data.get('equilibrium')}) - Favorable for Shorts")

    if in_ote:
        if zone == "DISCOUNT":
            bullish_score += 15
            bullish_factors.append(f"In ICT Optimal Trade Entry (OTE) 61.8% - 78.6% Golden Pocket (Sweet spot: {sweet_spot})")
        elif zone == "PREMIUM":
            bearish_score += 15
            bearish_factors.append(f"In ICT Optimal Trade Entry (OTE) 61.8% - 78.6% Golden Pocket (Sweet spot: {sweet_spot})")

    # Factor E: Liquidity Sweeps & Stop-Loss Hunt (Turtle Soup)
    sweeps = liq_data.get("sweeps", {})
    if sweeps.get("has_bullish_sweep"):
        bullish_score += 25
        latest_sw = sweeps.get("latest_sweep", {})
        desc = latest_sw.get("description", "Sell-Side Liquidity Swept (Stop Hunt)")
        bullish_factors.append(f"[LIQUIDITY HUNT] {desc}")

    if sweeps.get("has_bearish_sweep"):
        bearish_score += 25
        latest_sw = sweeps.get("latest_sweep", {})
        desc = latest_sw.get("description", "Buy-Side Liquidity Swept (Stop Hunt)")
        bearish_factors.append(f"[LIQUIDITY HUNT] {desc}")

    # Factor F: ICT Session & Killzones
    kz_data = ict_data.get("killzones", {})
    if kz_data.get("is_killzone_active"):
        active_names = ", ".join(k["name"] for k in kz_data.get("active_killzones", []))
        bullish_score += 10
        bearish_score += 10
        kz_note = f"[KILLZONE ACTIVE] {active_names}"
        bullish_factors.append(kz_note)
        bearish_factors.append(kz_note)

    if kz_data.get("is_silver_bullet_active"):
        sb_names = ", ".join(k["name"] for k in kz_data.get("active_silver_bullets", []))
        bullish_score += 10
        bearish_score += 10
        sb_note = f"[SILVER BULLET] {sb_names}"
        bullish_factors.append(sb_note)
        bearish_factors.append(sb_note)

    judas = ict_data.get("judas_swing", {})
    if judas.get("detected"):
        if judas["bias"] == "LONG":
            bullish_score += 20
            bullish_factors.append(f"[JUDAS SWING] {judas['description']}")
        elif judas["bias"] == "SHORT":
            bearish_score += 20
            bearish_factors.append(f"[JUDAS SWING] {judas['description']}")

    # 2.5 Entry Zone Verification
    # Identify valid institutional entry zones from SMC, ICT, Fibonacci, and Liquidity
    bullish_entry_zones = []
    bearish_entry_zones = []

    # A. Order Block Entry Zones (Demand / Supply)
    if inside_ob:
        if inside_ob.get("type") == "BULLISH_OB":
            bullish_entry_zones.append(f"Bullish Order Block ({inside_ob['bottom']} - {inside_ob['top']})")
        elif inside_ob.get("type") == "BEARISH_OB":
            bearish_entry_zones.append(f"Bearish Order Block ({inside_ob['bottom']} - {inside_ob['top']})")
    else:
        # Near Demand / Supply boundary (within 3 pips of the active zone edge)
        if nearest_demand and (nearest_demand["bottom"] - pip_size * 1.5) <= current_price <= (nearest_demand["top"] + pip_size * 2.5):
            bullish_entry_zones.append(f"Bullish Demand Zone ({nearest_demand['bottom']} - {nearest_demand['top']})")
        if nearest_supply and (nearest_supply["bottom"] - pip_size * 2.5) <= current_price <= (nearest_supply["top"] + pip_size * 1.5):
            bearish_entry_zones.append(f"Bearish Supply Zone ({nearest_supply['bottom']} - {nearest_supply['top']})")

    # B. Fair Value Gap (FVG) Entry Zones
    if inside_fvg:
        if inside_fvg.get("type") == "BULLISH_FVG":
            bullish_entry_zones.append(f"Bullish FVG Zone ({inside_fvg['bottom']} - {inside_fvg['top']})")
        elif inside_fvg.get("type") == "BEARISH_FVG":
            bearish_entry_zones.append(f"Bearish FVG Zone ({inside_fvg['bottom']} - {inside_fvg['top']})")

    # C. Fibonacci Optimal Trade Entry (OTE 61.8% - 78.6% Golden Pocket)
    if in_ote:
        ote_top = fib_data.get("ote_zone", {}).get("top", 0)
        ote_bot = fib_data.get("ote_zone", {}).get("bottom", 0)
        ote_range_str = f"({min(ote_top, ote_bot):.{decimals}f} - {max(ote_top, ote_bot):.{decimals}f})"
        if zone == "DISCOUNT":
            bullish_entry_zones.append(f"Fibonacci Discount OTE Zone {ote_range_str}")
        elif zone == "PREMIUM":
            bearish_entry_zones.append(f"Fibonacci Premium OTE Zone {ote_range_str}")

    # D. Liquidity Sweep & Stop-Loss Hunt (Turtle Soup Reversal Zone on latest/recent candle)
    recent_sweeps = liq_data.get("sweeps", {}).get("recent_sweeps", [])
    recent_ssl = [s for s in recent_sweeps if s.get("sentiment") == "BULLISH" and s.get("candle_index", 0) >= len(candles) - 2]
    if recent_ssl and to_pips(canon, abs(current_price - recent_ssl[-1]["level_price"])) <= 5.0:
        bullish_entry_zones.append(f"Turtle Soup SSL Sweep Zone at {recent_ssl[-1]['level_price']}")

    recent_bsl = [s for s in recent_sweeps if s.get("sentiment") == "BEARISH" and s.get("candle_index", 0) >= len(candles) - 2]
    if recent_bsl and to_pips(canon, abs(current_price - recent_bsl[-1]["level_price"])) <= 5.0:
        bearish_entry_zones.append(f"Turtle Soup BSL Sweep Zone at {recent_bsl[-1]['level_price']}")

    in_bullish_entry_zone = len(bullish_entry_zones) > 0
    in_bearish_entry_zone = len(bearish_entry_zones) > 0

    # 3. Determine Overall Signal Verdict
    # STRICT INVARIANT: Generate BUY or SELL ONLY when price reaches a valid entry zone!
    # If the price is NOT in a valid entry zone, verdict is strictly NEUTRAL / NO SIGNAL!
    signal_side = "NEUTRAL"
    confluence_score = 0
    grade = "NONE"
    active_factors = []
    active_entry_zone = None

    if bullish_score >= 50 and bullish_score > bearish_score + 15 and in_bullish_entry_zone:
        signal_side = "BUY"
        confluence_score = min(100, bullish_score)
        active_entry_zone = bullish_entry_zones[0]
        active_factors = [f"[ENTRY ZONE REACHED] {active_entry_zone}"] + bullish_factors
    elif bearish_score >= 50 and bearish_score > bullish_score + 15 and in_bearish_entry_zone:
        signal_side = "SELL"
        confluence_score = min(100, bearish_score)
        active_entry_zone = bearish_entry_zones[0]
        active_factors = [f"[ENTRY ZONE REACHED] {active_entry_zone}"] + bearish_factors
    else:
        signal_side = "NEUTRAL"
        confluence_score = max(bullish_score, bearish_score)
        base_factors = bullish_factors if bullish_score >= bearish_score else bearish_factors
        if bullish_score >= 50 or bearish_score >= 50:
            active_factors = ["[OUTSIDE ENTRY ZONE] Price is outside valid entry zones (OB/FVG/OTE/Sweep). Waiting for entry zone."] + base_factors
        else:
            active_factors = base_factors

    if confluence_score >= 80:
        grade = "A+ (Elite Institutional)"
    elif confluence_score >= 65:
        grade = "A (High Probability)"
    elif confluence_score >= 50:
        grade = "B (Valid Confluence)"
    else:
        grade = "C (Consolidation / Wait)"

    # 4. Construct Execution Plan (Entry, Stop Loss, TP1, TP2, R:R)
    entry_price = current_price
    stop_loss = 0.0
    take_profit_1 = 0.0
    take_profit_2 = 0.0
    risk_pips = 0.0
    reward_pips_tp1 = 0.0
    reward_pips_tp2 = 0.0
    rr_tp1 = 0.0
    rr_tp2 = 0.0

    # ATR / Volatility buffer
    recent_ranges = [c["high"] - c["low"] for c in candles[-14:]]
    avg_bar_range = sum(recent_ranges) / len(recent_ranges) if recent_ranges else (pip_size * 10)
    sl_buffer = avg_bar_range * 0.5

    if signal_side == "BUY":
        # Stop Loss below recent swing low or Order block bottom
        structural_low = min([c["low"] for c in candles[-5:]])
        if nearest_demand and nearest_demand["bottom"] < current_price:
            invalidation = min(structural_low, nearest_demand["bottom"]) - sl_buffer
        else:
            invalidation = structural_low - sl_buffer

        stop_loss = round(invalidation, decimals)
        risk_dist = max(pip_size * 5, current_price - stop_loss)
        risk_pips = round(to_pips(canon, risk_dist), 1)

        # TP1 = 1:2 R:R
        tp1_dist = risk_dist * 2.0
        take_profit_1 = round(current_price + tp1_dist, decimals)
        reward_pips_tp1 = round(to_pips(canon, tp1_dist), 1)
        rr_tp1 = 2.0

        # TP2 = 1:3.5 R:R or BSL liquidity pool
        bsl_target = liq_data.get("bsl_target", current_price + risk_dist * 3.5)
        if bsl_target > current_price + risk_dist * 2.5:
            take_profit_2 = round(bsl_target, decimals)
            tp2_dist = take_profit_2 - current_price
        else:
            tp2_dist = risk_dist * 3.5
            take_profit_2 = round(current_price + tp2_dist, decimals)

        reward_pips_tp2 = round(to_pips(canon, tp2_dist), 1)
        rr_tp2 = round(tp2_dist / risk_dist, 2)

    elif signal_side == "SELL":
        # Stop Loss above recent swing high or Order block top
        structural_high = max([c["high"] for c in candles[-5:]])
        if nearest_supply and nearest_supply["top"] > current_price:
            invalidation = max(structural_high, nearest_supply["top"]) + sl_buffer
        else:
            invalidation = structural_high + sl_buffer

        stop_loss = round(invalidation, decimals)
        risk_dist = max(pip_size * 5, stop_loss - current_price)
        risk_pips = round(to_pips(canon, risk_dist), 1)

        # TP1 = 1:2 R:R
        tp1_dist = risk_dist * 2.0
        take_profit_1 = round(current_price - tp1_dist, decimals)
        reward_pips_tp1 = round(to_pips(canon, tp1_dist), 1)
        rr_tp1 = 2.0

        # TP2 = 1:3.5 R:R or SSL liquidity pool
        ssl_target = liq_data.get("ssl_target", current_price - risk_dist * 3.5)
        if ssl_target < current_price - risk_dist * 2.5:
            take_profit_2 = round(ssl_target, decimals)
            tp2_dist = current_price - take_profit_2
        else:
            tp2_dist = risk_dist * 3.5
            take_profit_2 = round(current_price - tp2_dist, decimals)

        reward_pips_tp2 = round(to_pips(canon, tp2_dist), 1)
        rr_tp2 = round(tp2_dist / risk_dist, 2)

    else:
        # NEUTRAL / MONITORING: Compute planned institutional levels based on dominant bias
        bias_bullish = bullish_score >= bearish_score
        if bias_bullish:
            structural_low = min([c["low"] for c in candles[-5:]])
            invalidation = (nearest_demand["bottom"] if (nearest_demand and nearest_demand["bottom"] < current_price) else structural_low) - sl_buffer
            stop_loss = round(invalidation, decimals)
            risk_dist = max(pip_size * 5, current_price - stop_loss)
            risk_pips = round(to_pips(canon, risk_dist), 1)
            take_profit_1 = round(current_price + (risk_dist * 2.0), decimals)
            take_profit_2 = round(current_price + (risk_dist * 3.5), decimals)
        else:
            structural_high = max([c["high"] for c in candles[-5:]])
            invalidation = (nearest_supply["top"] if (nearest_supply and nearest_supply["top"] > current_price) else structural_high) + sl_buffer
            stop_loss = round(invalidation, decimals)
            risk_dist = max(pip_size * 5, stop_loss - current_price)
            risk_pips = round(to_pips(canon, risk_dist), 1)
            take_profit_1 = round(current_price - (risk_dist * 2.0), decimals)
            take_profit_2 = round(current_price - (risk_dist * 3.5), decimals)

        reward_pips_tp1 = round(to_pips(canon, risk_dist * 2.0), 1)
        reward_pips_tp2 = round(to_pips(canon, risk_dist * 3.5), 1)
        rr_tp1 = 2.0
        rr_tp2 = 3.5

    return {
        "symbol": canon,
        "action": signal_side,
        "grade": grade,
        "confluence_score": confluence_score,
        "bullish_score": bullish_score,
        "bearish_score": bearish_score,
        "timeframe": interval,
        "current_price": current_price,
        "is_in_entry_zone": (in_bullish_entry_zone if signal_side == "BUY" else (in_bearish_entry_zone if signal_side == "SELL" else (in_bullish_entry_zone or in_bearish_entry_zone))),
        "active_entry_zone": active_entry_zone,
        "bullish_entry_zones": bullish_entry_zones,
        "bearish_entry_zones": bearish_entry_zones,
        "execution_plan": {
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit_1": take_profit_1,
            "take_profit_2": take_profit_2,
            "risk_pips": risk_pips,
            "reward_pips_tp1": reward_pips_tp1,
            "reward_pips_tp2": reward_pips_tp2,
            "rr_ratio_tp1": rr_tp1,
            "rr_ratio_tp2": rr_tp2,
        },
        "confluence_factors": active_factors,
        "components": {
            "smc": smc_data,
            "fibonacci": fib_data,
            "liquidity": liq_data,
            "ict": ict_data,
        },
        "timestamp_et": now_et.strftime("%Y-%m-%d %H:%M:%S ET"),
        "timestamp_utc": now_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
    }


def scan_all_pairs_confluence(
    interval: str = "5m",
    pairs: Optional[List[str]] = None,
    offline_data: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Scans all major Forex pairs and Gold (XAU/USD) for institutional confluence setups.
    Returns list sorted by highest confluence score.
    """
    if pairs is None:
        pairs = MAJOR_PAIRS

    results = []
    for sym in pairs:
        try:
            chart = fetch_chart(symbol=sym, interval=interval, range_period="2d", offline_data=offline_data)
            candles = chart.get("candles", [])
            if candles:
                sig = evaluate_confluence_signal(symbol=sym, candles=candles, interval=interval)
                comps = sig.get("components", {})
                smc_c = comps.get("smc", {})
                fib_c = comps.get("fibonacci", {})
                liq_c = comps.get("liquidity", {})
                ict_c = comps.get("ict", {})
                kz_info = (ict_c.get("killzones") or {})

                # Enrich with direct summary attributes for dashboard scanner table
                sig["price"] = sig.get("current_price") or (candles[-1]["close"] if candles else None)
                sig["trend"] = smc_c.get("trend", "NEUTRAL")
                bos_obj = (smc_c.get("structure") or {}).get("latest_bos")
                choch_obj = (smc_c.get("structure") or {}).get("latest_choch")
                sig["bos_choch"] = (
                    f"BOS: {bos_obj['price']}" if bos_obj else (
                        f"CHoCH: {choch_obj['price']}" if choch_obj else "Normal Range"
                    )
                )
                sweeps = (liq_c.get("sweeps") or {})
                if sweeps.get("has_bullish_sweep"):
                    sig["liquidity_state"] = "SSL SWEPT (SOUP)"
                elif sweeps.get("has_bearish_sweep"):
                    sig["liquidity_state"] = "BSL SWEPT (SOUP)"
                else:
                    sig["liquidity_state"] = "POOLS MAPPED"

                sig["zone"] = fib_c.get("zone", "EQUILIBRIUM")
                sig["in_ote"] = fib_c.get("in_ote_zone", False)

                akz = kz_info.get("active_killzones", [])
                sig["killzone"] = akz[0]["name"] if akz else "Off-Peak"
                sig["is_killzone"] = kz_info.get("is_killzone_active", False)
                sig["is_silver_bullet"] = kz_info.get("is_silver_bullet_active", False)

                results.append(sig)
        except Exception as e:
            results.append({
                "symbol": sym,
                "action": "NO SIGNAL",
                "grade": "N/A",
                "confluence_score": 0,
                "reason": str(e),
                "trend": "NEUTRAL",
                "zone": "EQUILIBRIUM",
                "liquidity_state": "POOLS MAPPED",
                "killzone": "Off-Peak",
                "is_killzone": False,
                "is_silver_bullet": False,
            })

    results.sort(key=lambda x: x.get("confluence_score", 0), reverse=True)
    return results
