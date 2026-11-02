"""24/5 Forex market session hours and calendar engine.

Standard library only, Python 3.8+ compatible.
"""

from datetime import datetime, timezone, timedelta
from typing import Dict, List, Tuple, Any

# New York time offset approximation (EDT -4 / EST -5)
# Forex market week runs Sunday 17:00 ET to Friday 17:00 ET.

SESSIONS: Dict[str, Tuple[int, int]] = {
    # (start_hour_et, end_hour_et)
    "Sydney": (17, 2),        # 17:00 to 02:00 next day ET
    "Tokyo": (19, 4),         # 19:00 to 04:00 next day ET
    "London": (3, 12),        # 03:00 to 12:00 ET
    "New York": (8, 17),      # 08:00 to 17:00 ET
}


def is_ny_dst(dt_utc: datetime) -> bool:
    """Check if New York is in Daylight Saving Time (second Sunday in March to first Sunday in Nov)."""
    year = dt_utc.year
    # Second Sunday in March
    mar1 = datetime(year, 3, 1, tzinfo=timezone.utc)
    dst_start = datetime(year, 3, 8 + (6 - mar1.weekday()) % 7, 7, 0, tzinfo=timezone.utc)
    # First Sunday in November
    nov1 = datetime(year, 11, 1, tzinfo=timezone.utc)
    dst_end = datetime(year, 11, 1 + (6 - nov1.weekday()) % 7, 6, 0, tzinfo=timezone.utc)
    return dst_start <= dt_utc < dst_end


def utc_to_et(dt_utc: datetime) -> datetime:
    """Convert UTC datetime to New York time (ET)."""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    offset_hours = -4 if is_ny_dst(dt_utc) else -5
    return dt_utc.astimezone(timezone(timedelta(hours=offset_hours)))


def is_forex_market_open(dt_et: datetime) -> Tuple[bool, str]:
    """
    Check if the spot Forex market is open at the given New York time.
    
    Forex opens Sunday 17:00 ET and closes Friday 17:00 ET.
    """
    weekday = dt_et.weekday()  # Monday = 0, ..., Friday = 4, Saturday = 5, Sunday = 6
    hour = dt_et.hour
    minute = dt_et.minute

    # Saturday: completely closed
    if weekday == 5:
        return False, "Market closed (Saturday weekend)"

    # Sunday: opens at 17:00 ET
    if weekday == 6:
        if hour < 17:
            return False, f"Market closed (Sunday pre-open, opens at 17:00 ET)"
        return True, "Market open (Sunday Asia/Sydney open)"

    # Friday: closes at 17:00 ET
    if weekday == 4:
        if hour >= 17:
            return False, "Market closed (Friday weekend close at 17:00 ET)"
        if hour == 16 and minute >= 30:
            return True, "Market open (Friday pre-weekend close warning)"
        return True, "Market open (Friday session)"

    # Monday through Thursday: open 24 hours
    return True, "Market open (regular 24-hour weekday trading)"


def is_gold_futures_market_open(dt_et: datetime) -> Tuple[bool, str]:
    """
    Check if the COMEX Gold Futures (GC=F / CME Globex) market is open at the given New York time.
    
    CME Globex Gold Trading Hours (ET):
    - Opens Sunday at 18:00 ET (closes Friday at 17:00 ET).
    - Daily maintenance halt: 17:00 ET to 18:00 ET Monday through Thursday (60-minute break).
    - Closed Friday 17:00 ET through Sunday 18:00 ET.
    """
    weekday = dt_et.weekday()  # Monday = 0, ..., Friday = 4, Saturday = 5, Sunday = 6
    hour = dt_et.hour
    minute = dt_et.minute

    # Saturday: completely closed
    if weekday == 5:
        return False, "Market closed (Saturday weekend)"

    # Sunday: opens at 18:00 ET (unlike Spot Forex which opens at 17:00 ET)
    if weekday == 6:
        if hour < 18:
            return False, "Market closed (Sunday pre-open, COMEX Gold opens at 18:00 ET)"
        return True, "Market open (Sunday CME Globex Gold open)"

    # Friday: closes at 17:00 ET for weekend
    if weekday == 4:
        if hour >= 17:
            return False, "Market closed (Friday weekend close at 17:00 ET)"
        if hour == 16 and minute >= 30:
            return True, "Market open (Friday pre-weekend close warning)"
        return True, "Market open (Friday regular session)"

    # Monday through Thursday: 60-minute maintenance halt from 17:00 to 18:00 ET
    if 17 <= hour < 18:
        return False, "Market closed (Daily maintenance trading halt 17:00-18:00 ET)"

    return True, "Market open (CME Globex Gold regular trading)"


def is_market_open_for_symbol(symbol: str, dt_et: datetime) -> Tuple[bool, str]:
    """Check market open status respecting asset class (Forex 24/5 vs COMEX Gold Futures)."""
    s = symbol.upper()
    if "XAU" in s or "GOLD" in s or "GC=F" in s or s == "GC":
        return is_gold_futures_market_open(dt_et)
    return is_forex_market_open(dt_et)


def get_active_sessions(dt_et: datetime) -> List[str]:
    """Identify which major trading centers are currently active in New York time."""
    hour = dt_et.hour
    active = []

    # Sydney: 17:00 to 02:00 ET
    if hour >= 17 or hour < 2:
        active.append("Sydney")

    # Tokyo: 19:00 to 04:00 ET
    if hour >= 19 or hour < 4:
        active.append("Tokyo")

    # London: 03:00 to 12:00 ET
    if 3 <= hour < 12:
        active.append("London")

    # New York: 08:00 to 17:00 ET
    if 8 <= hour < 17:
        active.append("New York")

    # London/NY Overlap (Prime Liquidity): 08:00 to 12:00 ET
    if "London" in active and "New York" in active:
        active.append("London/NY Overlap (Peak Volume)")

    return active


def session_status(dt_et: datetime) -> Dict[str, Any]:
    """Complete summary of Forex session state at a given time."""
    is_open, reason = is_forex_market_open(dt_et)
    sessions = get_active_sessions(dt_et) if is_open else []
    is_weekend_approaching = dt_et.weekday() == 4 and dt_et.hour >= 16

    return {
        "timestamp_et": dt_et.strftime("%Y-%m-%d %H:%M:%S ET"),
        "is_open": is_open,
        "reason": reason,
        "active_sessions": sessions,
        "weekend_warning": is_weekend_approaching,
    }
