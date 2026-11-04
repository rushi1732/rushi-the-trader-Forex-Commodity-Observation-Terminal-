"""Forex entry timer for Long and Short setups using candle-close rules.

A wick or touch is NEVER an entry. The candle must COMPLETE and CLOSE beyond the level.
Supports:
- Asian Range Breakout (London Open) Long and Short
- Key Level Breakout / Breakdown Long and Short
- Spread adjustment (Buy fills at Ask, Sell fills at Bid)

Standard library only, Python 3.8+ compatible.
"""

import argparse
import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple

from .pairs import normalize_symbol, get_pair_info, to_pips, from_pips
from .pricing import calc_spread_prices, execution_price
from .data import fetch_chart, INDICATIVE_DISCLAIMER
from .sessions import is_forex_market_open, utc_to_et


def evaluate_entry_from_candles(
    symbol: str,
    side: str,
    candles: List[Dict[str, Any]],
    setup: str = "asian",
    level: Optional[float] = None,
    min_breakout_pips: float = 0.0,
    spread_pips: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Evaluates bar-by-bar entry conditions across candles:
    - Long (BUY): First completed candle that CLOSES > Level (+ min_breakout_pips)
    - Short (SELL): First completed candle that CLOSES < Level (- min_breakout_pips)
    """
    canon = normalize_symbol(symbol)
    info = get_pair_info(canon)
    pip_size = info["pip_size"]
    decimals = info["decimals"]
    side_clean = side.strip().upper()
    is_long = side_clean in ("BUY", "LONG")

    if spread_pips is None:
        spread_pips = info.get("default_spread", 1.0)

    if not candles:
        return {
            "symbol": canon,
            "status": "NO_DATA",
            "side": "BUY" if is_long else "SELL",
            "reason": "No candle data available to evaluate entry.",
            "disclaimer": INDICATIVE_DISCLAIMER,
        }

    # Determine reference levels
    asian_high = -1.0
    asian_low = 999999.0
    asian_count = 0
    post_asian_candles = []

    for c in candles:
        dt = datetime.fromtimestamp(c["timestamp"], tz=timezone.utc)
        if 0 <= dt.hour < 7:
            asian_high = max(asian_high, c["high"])
            asian_low = min(asian_low, c["low"])
            asian_count += 1
        elif dt.hour >= 7:
            post_asian_candles.append(c)

    asian_range = {
        "asian_high": asian_high if asian_count > 0 else None,
        "asian_low": asian_low if asian_count > 0 else None,
        "range_pips": to_pips(canon, asian_high - asian_low) if asian_count > 0 else 0.0,
    }

    if setup == "asian":
        if asian_count < 6:
            return {
                "symbol": canon,
                "status": "WAITING",
                "side": "BUY" if is_long else "SELL",
                "setup": "asian_breakout",
                "reason": "Asian session range is still forming (requires 00:00 - 07:00 UTC).",
                "disclaimer": INDICATIVE_DISCLAIMER,
            }
        target_level = asian_high if is_long else asian_low
        eval_candles = post_asian_candles
    else:
        if level is None:
            raise ValueError("Level must be specified when setup is not 'asian'.")
        target_level = float(level)
        eval_candles = candles

    breakout_offset = min_breakout_pips * pip_size

    # Bar-by-bar search for first completed bar closing beyond level
    for i, c in enumerate(eval_candles):
        close_price = c["close"]
        ts = c["timestamp"]
        dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
        dt_et = utc_to_et(dt_utc)

        if is_long and close_price > (target_level + breakout_offset):
            # Long trigger confirmed
            bid, ask = calc_spread_prices(canon, close_price, spread_pips)
            pips_beyond = to_pips(canon, close_price - target_level)
            return {
                "symbol": canon,
                "status": "TRIGGERED",
                "side": "BUY",
                "setup": setup,
                "level": target_level,
                "trigger_bar_index": i + 1,
                "total_bars_evaluated": len(eval_candles),
                "bars_since_trigger": len(eval_candles) - (i + 1),
                "trigger_time_utc": dt_utc.isoformat(),
                "trigger_time_et": dt_et.strftime("%Y-%m-%d %H:%M ET"),
                "trigger_close": close_price,
                "indicative_fill_ask": ask,
                "spread_pips": spread_pips,
                "pips_beyond": pips_beyond,
                "asian_range": asian_range,
                "reason": f"Bar {i+1} closed at {close_price:.{decimals}f}, +{pips_beyond:.1f} pips above level ({target_level:.{decimals}f})",
                "disclaimer": INDICATIVE_DISCLAIMER,
            }

        elif not is_long and close_price < (target_level - breakout_offset):
            # Short trigger confirmed
            bid, ask = calc_spread_prices(canon, close_price, spread_pips)
            pips_beyond = to_pips(canon, target_level - close_price)
            return {
                "symbol": canon,
                "status": "TRIGGERED",
                "side": "SELL",
                "setup": setup,
                "level": target_level,
                "trigger_bar_index": i + 1,
                "total_bars_evaluated": len(eval_candles),
                "bars_since_trigger": len(eval_candles) - (i + 1),
                "trigger_time_utc": dt_utc.isoformat(),
                "trigger_time_et": dt_et.strftime("%Y-%m-%d %H:%M ET"),
                "trigger_close": close_price,
                "indicative_fill_bid": bid,
                "spread_pips": spread_pips,
                "pips_beyond": pips_beyond,
                "asian_range": asian_range,
                "reason": f"Bar {i+1} closed at {close_price:.{decimals}f}, -{pips_beyond:.1f} pips below level ({target_level:.{decimals}f})",
                "disclaimer": INDICATIVE_DISCLAIMER,
            }

    # No breakout yet -> WAITING
    latest = eval_candles[-1] if eval_candles else candles[-1]
    latest_close = latest["close"]
    dist_pips = to_pips(canon, abs(target_level - latest_close))
    dt_latest = utc_to_et(datetime.fromtimestamp(latest["timestamp"], tz=timezone.utc))

    return {
        "symbol": canon,
        "status": "WAITING",
        "side": "BUY" if is_long else "SELL",
        "setup": setup,
        "level": target_level,
        "last_close": latest_close,
        "distance_pips": dist_pips,
        "last_bar_time_et": dt_latest.strftime("%Y-%m-%d %H:%M ET"),
        "asian_range": asian_range,
        "reason": f"Waiting for close beyond {target_level:.{decimals}f} (currently {dist_pips:.1f} pips away at {latest_close:.{decimals}f})",
        "disclaimer": INDICATIVE_DISCLAIMER,
    }


def check_entry(
    symbol: str,
    side: str = "BUY",
    setup: str = "asian",
    level: Optional[float] = None,
    min_breakout_pips: float = 0.0,
    interval: str = "5m",
    range_period: str = "1d",
    spread_pips: Optional[float] = None,
    offline_file: Optional[str] = None,
    offline_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Fetch candles and evaluate entry signal."""
    chart = fetch_chart(
        symbol=symbol,
        interval=interval,
        range_period=range_period,
        offline_file=offline_file,
        offline_data=offline_data,
    )
    candles = chart.get("candles", [])
    return evaluate_entry_from_candles(
        symbol=symbol,
        side=side,
        candles=candles,
        setup=setup,
        level=level,
        min_breakout_pips=min_breakout_pips,
        spread_pips=spread_pips,
    )


def format_entry_report(res: Dict[str, Any]) -> str:
    """Format entry result into readable text."""
    sym = res.get("symbol", "")
    side = res.get("side", "")
    status = res.get("status", "")
    setup = res.get("setup", "")
    disclaimer = res.get("disclaimer", "")

    lines = [
        "=" * 72,
        f"FOREX ENTRY TIMER: {sym} ({side})",
        "=" * 72,
        f"Status:      {status}",
        f"Setup:       {setup}",
        f"Notice:      {disclaimer}",
        "-" * 72,
        f"Details:     {res.get('reason', '')}",
    ]

    if status == "TRIGGERED":
        fill_price = res.get("indicative_fill_ask") if side == "BUY" else res.get("indicative_fill_bid")
        lines.append(f"Trigger Bar: Closed at {res.get('trigger_close')} at {res.get('trigger_time_et')}")
        lines.append(f"Fill Price:  {fill_price} (includes {res.get('spread_pips')} pip indicative spread)")
        lines.append(f"Level Beat:  {res.get('level')} by {res.get('pips_beyond', 0):.1f} pips")
        lines.append("-" * 72)
        lines.append("Next: Plan pip-based stop & target: python -m forex.exit plan --from-entry")
    elif status == "WAITING":
        lines.append(f"Target Level: {res.get('level')}")
        lines.append(f"Last Close:   {res.get('last_close')} ({res.get('distance_pips', 0):.1f} pips away)")

    lines.append("=" * 72)
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Forex Entry Confirmation Timer")
    parser.add_argument("symbol", help="Currency pair (e.g. EUR/USD)")
    parser.add_argument("--side", default="BUY", choices=["BUY", "SELL", "buy", "sell"])
    parser.add_argument("--setup", default="asian", choices=["asian", "level"])
    parser.add_argument("--level", type=float, help="Custom price breakout level")
    parser.add_argument("--min-pips", type=float, default=0.0, help="Minimum breakout pips")
    parser.add_argument("--interval", default="5m", choices=["5m", "15m", "1h"])
    parser.add_argument("--offline", help="Path to offline JSON chart")
    parser.add_argument("--write", help="Write result to forex/entry.json", action="store_true")
    args = parser.parse_args()

    result = check_entry(
        symbol=args.symbol,
        side=args.side,
        setup=args.setup,
        level=args.level,
        min_breakout_pips=args.min_pips,
        interval=args.interval,
        offline_file=args.offline,
    )

    print(format_entry_report(result))

    if args.write:
        out_path = os.path.join(os.path.dirname(__file__), "entry.json")
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
        print(f"Wrote entry state to {out_path}.")
