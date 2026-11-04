"""Inner Circle Trader (ICT) Concepts Engine.

Analyzes:
- ICT Killzones (Asian, London Open, New York Open, London Close)
- ICT Silver Bullet Windows (03:00-04:00 ET, 10:00-11:00 ET, 14:00-15:00 ET)
- Judas Swing / Session Manipulation detection
- Optimal Trade Entry (OTE) alignment
- Fair Value Gap (FVG) reaction during Killzones

Standard library only, Python 3.8+ compatible.
"""

from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone
from .sessions import utc_to_et
from .fibonacci import analyze_fibonacci
from .smc import detect_fair_value_gaps, detect_order_blocks


ICT_KILLZONES = {
    "Asian Range": {
        "start_hour": 20, "start_min": 0,
        "end_hour": 0, "end_min": 0,
        "description": "Asian Session range accumulation & liquidity build",
    },
    "London Open (LOKZ)": {
        "start_hour": 2, "start_min": 0,
        "end_hour": 5, "end_min": 0,
        "description": "Judas Swing & High/Low of the Day (HOTD/LOTD) formation",
    },
    "New York Open (NYOKZ)": {
        "start_hour": 7, "start_min": 0,
        "end_hour": 10, "end_min": 0,
        "description": "Institutional expansion, continuation or major reversal",
    },
    "London Close": {
        "start_hour": 10, "start_min": 0,
        "end_hour": 12, "end_min": 0,
        "description": "Profit-taking and counter-trend retracements",
    },
}

ICT_SILVER_BULLETS = {
    "London Silver Bullet": {
        "start_hour": 3, "start_min": 0,
        "end_hour": 4, "end_min": 0,
        "description": "High-probability FVG setup targeting London liquidity",
    },
    "NY AM Silver Bullet": {
        "start_hour": 10, "start_min": 0,
        "end_hour": 11, "end_min": 0,
        "description": "Post-NY open FVG setup targeting morning session extremes",
    },
    "NY PM Silver Bullet": {
        "start_hour": 14, "start_min": 0,
        "end_hour": 15, "end_min": 0,
        "description": "Afternoon liquidity run before US cash equity close",
    },
}


def get_ict_killzone_status(dt_et: datetime) -> Dict[str, Any]:
    """
    Evaluates which ICT Killzones and Silver Bullet windows are currently active.
    Times are in New York Time (ET).
    """
    hour = dt_et.hour
    minute = dt_et.minute
    total_minutes = hour * 60 + minute

    active_kz = []
    for name, kz in ICT_KILLZONES.items():
        s_m = kz["start_hour"] * 60 + kz["start_min"]
        e_m = kz["end_hour"] * 60 + kz["end_min"]

        # Overnight handling (e.g. Asian 20:00 to 00:00)
        if s_m > e_m:
            is_in = (total_minutes >= s_m) or (total_minutes < e_m)
        else:
            is_in = s_m <= total_minutes < e_m

        if is_in:
            active_kz.append({
                "name": name,
                "description": kz["description"],
            })

    active_sb = []
    for name, sb in ICT_SILVER_BULLETS.items():
        s_m = sb["start_hour"] * 60 + sb["start_min"]
        e_m = sb["end_hour"] * 60 + sb["end_min"]
        if s_m <= total_minutes < e_m:
            active_sb.append({
                "name": name,
                "description": sb["description"],
            })

    is_high_probability_time = len(active_kz) > 0 or len(active_sb) > 0

    return {
        "current_time_et": dt_et.strftime("%H:%M:%S ET"),
        "active_killzones": active_kz,
        "active_silver_bullets": active_sb,
        "is_killzone_active": len(active_kz) > 0,
        "is_silver_bullet_active": len(active_sb) > 0,
        "is_high_probability_window": is_high_probability_time,
        "primary_session": active_kz[0]["name"] if active_kz else "Off-Peak / Inter-session",
    }


def detect_judas_swing(
    candles: List[Dict[str, Any]],
    asian_range: Optional[Dict[str, Any]],
    now_et: datetime,
) -> Dict[str, Any]:
    """
    Detects the classic ICT Judas Swing:
    - Occurs during London Open (02:00 - 05:00 ET).
    - Market makes a sharp, deceptive run sweeping the Asian session high or low.
    - Quickly rejects and prints an impulsive reversal candle back inside the range.
    """
    if not asian_range or not asian_range.get("asian_high") or not asian_range.get("asian_low"):
        return {"detected": False, "type": "NONE", "reason": "No Asian range available"}

    ah = asian_range["asian_high"]
    al = asian_range["asian_low"]

    hour = now_et.hour
    is_london = 2 <= hour < 7

    if not is_london:
        return {"detected": False, "type": "NONE", "reason": "Not in London session window"}

    # Inspect the most recent candles for false breakout of Asian boundaries
    recent = candles[-12:]
    for c in reversed(recent):
        # Bearish Judas: Ran above Asian High then closed below it
        if c["high"] > ah and c["close"] < ah:
            return {
                "detected": True,
                "type": "BEARISH_JUDAS_SWING",
                "bias": "SHORT",
                "level_swept": ah,
                "description": "Deceptive London rally swept Asian High (BSL) and got rejected. Prime short setup.",
                "candle_close": c["close"],
            }
        # Bullish Judas: Ran below Asian Low then closed above it
        if c["low"] < al and c["close"] > al:
            return {
                "detected": True,
                "type": "BULLISH_JUDAS_SWING",
                "bias": "LONG",
                "level_swept": al,
                "description": "Deceptive London drop swept Asian Low (SSL) and got rejected. Prime long setup.",
                "candle_close": c["close"],
            }

    return {"detected": False, "type": "NONE", "reason": "No Judas swing detected"}


def analyze_ict(
    candles: List[Dict[str, Any]],
    symbol: str,
    now_et: Optional[datetime] = None,
    asian_range: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Full ICT analysis combining Killzones, Silver Bullet, Judas Swing, and OTE confluence.
    """
    if not candles:
        return {"status": "NO_DATA"}

    if now_et is None:
        now_et = utc_to_et(datetime.now(timezone.utc))

    kz_status = get_ict_killzone_status(now_et)
    judas = detect_judas_swing(candles, asian_range, now_et)
    fib_res = analyze_fibonacci(candles)

    # Silver Bullet setup detection
    sb_setup = None
    if kz_status["is_silver_bullet_active"]:
        fvgs = detect_fair_value_gaps(candles, lookback_bars=15)
        if fvgs.get("active_fvgs"):
            nearest_fvg = fvgs["active_fvgs"][-1]
            sb_setup = {
                "active": True,
                "target_fvg": nearest_fvg,
                "bias": "BULLISH" if nearest_fvg["type"] == "BULLISH_FVG" else "BEARISH",
                "description": f"Active {kz_status['active_silver_bullets'][0]['name']}: Price interacting with {nearest_fvg['type']}",
            }

    return {
        "status": "OK",
        "killzones": kz_status,
        "judas_swing": judas,
        "silver_bullet_setup": sb_setup,
        "ote_confluence": {
            "in_ote": fib_res.get("in_ote_zone", False),
            "sweet_spot_0_707": fib_res.get("ote_sweet_spot"),
            "zone": fib_res.get("zone"),
        },
    }

