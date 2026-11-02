"""Pip-based stop-loss and take-profit planner for Forex trades.

Decides the exit parameters BEFORE the trade is entered.
Supports Long and Short positions, pip calculation, and weekend flattening.
Standard library only, Python 3.8+ compatible.
"""

import argparse
import json
import os
from typing import Dict, Any, Optional, Tuple

from .pairs import normalize_symbol, get_pair_info, to_pips, from_pips
from .data import INDICATIVE_DISCLAIMER


def plan_exit(
    symbol: str,
    side: str,
    entry_price: float,
    stop_rule: str = "range-mid",
    target_rule: str = "2R",
    asian_range: Optional[Dict[str, Any]] = None,
    atr_pips: Optional[float] = None,
    flat_by: str = "Fri 16:30 ET",
) -> Dict[str, Any]:
    """
    Formulate a pip-based exit plan for a Long or Short trade.
    
    stop_rule: 'range-mid', 'range-bound', 'pips:X', 'atr:K', 'price:X'
    target_rule: '1R', '1.5R', '2R', '3R', 'pips:X', 'price:X'
    """
    canon = normalize_symbol(symbol)
    info = get_pair_info(canon)
    pip_size = info["pip_size"]
    decimals = info["decimals"]
    side_clean = side.strip().upper()
    is_long = side_clean in ("BUY", "LONG")

    # 1. Determine Stop Price
    stop_price: float
    stop_desc = stop_rule

    if stop_rule == "range-mid":
        if not asian_range or asian_range.get("asian_high") is None or asian_range.get("asian_low") is None:
            raise ValueError("Stop rule 'range-mid' requires asian_range with 'asian_high' and 'asian_low'.")
        h = float(asian_range["asian_high"])
        l = float(asian_range["asian_low"])
        mid = (h + l) / 2.0
        stop_price = round(mid, decimals + 1)
        stop_desc = f"Asian Range Midpoint (({h} + {l}) / 2)"

    elif stop_rule == "range-bound":
        if not asian_range or asian_range.get("asian_high") is None or asian_range.get("asian_low") is None:
            raise ValueError("Stop rule 'range-bound' requires asian_range bounds.")
        h = float(asian_range["asian_high"])
        l = float(asian_range["asian_low"])
        stop_price = round(l if is_long else h, decimals + 1)
        stop_desc = f"Opposite Asian Range Bound ({l if is_long else h})"

    elif stop_rule.startswith("pips:"):
        offset_pips = float(stop_rule.split(":")[1])
        offset = offset_pips * pip_size
        stop_price = round(entry_price - offset if is_long else entry_price + offset, decimals + 1)
        stop_desc = f"{offset_pips:.1f} Pips Fixed Stop"

    elif stop_rule.startswith("atr:"):
        multiplier = float(stop_rule.split(":")[1])
        if atr_pips is None:
            atr_pips = 15.0  # fallback estimation if ATR not provided
        offset = multiplier * atr_pips * pip_size
        stop_price = round(entry_price - offset if is_long else entry_price + offset, decimals + 1)
        stop_desc = f"{multiplier:.1f}x ATR ({atr_pips:.1f}p) Stop"

    elif stop_rule.startswith("price:"):
        stop_price = float(stop_rule.split(":")[1])
        stop_desc = f"Explicit Price Level ({stop_price})"

    else:
        raise ValueError(f"Unknown stop rule: {stop_rule}")

    # Validate Stop Orientation
    if is_long and stop_price >= entry_price:
        raise ValueError(f"Long stop price ({stop_price}) must be strictly below entry ({entry_price}).")
    if not is_long and stop_price <= entry_price:
        raise ValueError(f"Short stop price ({stop_price}) must be strictly above entry ({entry_price}).")

    risk_distance = abs(entry_price - stop_price)
    risk_pips = round(risk_distance / pip_size, 2)

    # Minimum risk guard (prevent stops too tight to survive spread)
    min_spread = info.get("default_spread", 1.0)
    if risk_pips < (min_spread * 2.0):
        raise ValueError(
            f"Stop distance ({risk_pips:.1f} pips) is too tight for {canon} (spread {min_spread:.1f} pips). Minimum 3x spread required."
        )

    # 2. Determine Target Price
    target_price: float
    target_desc = target_rule

    if target_rule.endswith("R") or target_rule.endswith("r"):
        multiplier = float(target_rule[:-1])
        reward_pips = round(risk_pips * multiplier, 2)
        target_offset = reward_pips * pip_size
        target_price = round(entry_price + target_offset if is_long else entry_price - target_offset, decimals + 1)
        target_desc = f"{multiplier:.1f}R ({reward_pips:.1f} pips)"

    elif target_rule.startswith("pips:"):
        reward_pips = float(target_rule.split(":")[1])
        target_offset = reward_pips * pip_size
        target_price = round(entry_price + target_offset if is_long else entry_price - target_offset, decimals + 1)
        target_desc = f"{reward_pips:.1f} Pips Fixed Target"

    elif target_rule.startswith("price:"):
        target_price = float(target_rule.split(":")[1])
        reward_pips = round(abs(target_price - entry_price) / pip_size, 2)
        target_desc = f"Explicit Price Level ({target_price})"

    else:
        raise ValueError(f"Unknown target rule: {target_rule}")

    # Validate Target Orientation
    if is_long and target_price <= entry_price:
        raise ValueError(f"Long target price ({target_price}) must be strictly above entry ({entry_price}).")
    if not is_long and target_price >= entry_price:
        raise ValueError(f"Short target price ({target_price}) must be strictly below entry ({entry_price}).")

    reward_risk_ratio = round(reward_pips / risk_pips, 2) if risk_pips > 0 else 0.0

    return {
        "symbol": canon,
        "side": "BUY" if is_long else "SELL",
        "entry_price": entry_price,
        "stop_price": stop_price,
        "target_price": target_price,
        "stop_rule": stop_rule,
        "stop_description": stop_desc,
        "target_rule": target_rule,
        "target_description": target_desc,
        "risk_pips": risk_pips,
        "reward_pips": reward_pips,
        "reward_risk_ratio": reward_risk_ratio,
        "flat_by": flat_by,
        "asian_range": asian_range,
        "disclaimer": INDICATIVE_DISCLAIMER,
    }


def format_plan_report(plan: Dict[str, Any]) -> str:
    """Format exit plan into readable output."""
    sym = plan["symbol"]
    side = plan["side"]
    entry = plan["entry_price"]
    stop = plan["stop_price"]
    target = plan["target_price"]
    risk_p = plan["risk_pips"]
    rew_p = plan["reward_pips"]
    rr = plan["reward_risk_ratio"]
    info = get_pair_info(sym)
    dec = info["decimals"]

    lines = [
        "=" * 72,
        f"FOREX EXIT PLAN: {sym} ({side})",
        "=" * 72,
        f"  Entry:            {entry:.{dec}f}",
        f"  Stop Loss:        {stop:.{dec}f}  (-{risk_p:.1f} pips) [{plan['stop_description']}]",
        f"  Take Profit:      {target:.{dec}f}  (+{rew_p:.1f} pips) [{plan['target_description']}]",
        f"  Reward:Risk:      {rr}:1",
        f"  Weekend Flat By:  {plan['flat_by']}",
        "-" * 72,
        f"Notice:             {plan['disclaimer']}",
        "-" * 72,
        "Rules:",
        "  - Stops and targets must be set BEFORE entering, never moved wider.",
        "  - A stop-loss is an order, not a guarantee; fast news events can slip past it.",
        "  - Next: Size to your exact $50 max risk with: python -m forex.sizer",
        "=" * 72,
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Forex Exit Planner")
    parser.add_argument("--symbol", help="Currency pair (e.g. EUR/USD)")
    parser.add_argument("--side", default="BUY", choices=["BUY", "SELL", "buy", "sell"])
    parser.add_argument("--entry", type=float, help="Entry price")
    parser.add_argument("--stop", default="pips:25", help="Stop rule: range-mid, range-bound, pips:X, atr:K, price:X")
    parser.add_argument("--target", default="2R", help="Target rule: 1R, 1.5R, 2R, 3R, pips:X, price:X")
    parser.add_argument("--from-entry", help="Read entry from forex/entry.json", action="store_true")
    parser.add_argument("--write", help="Save plan to forex/plan.json", action="store_true")
    args = parser.parse_args()

    entry_data = {}
    if args.from_entry:
        entry_file = os.path.join(os.path.dirname(__file__), "entry.json")
        if not os.path.exists(entry_file):
            print(f"Error: {entry_file} does not exist. Run forex/entry.py first.")
            exit(1)
        with open(entry_file, "r", encoding="utf-8") as fh:
            entry_data = json.load(fh)

    sym = args.symbol or entry_data.get("symbol")
    side = args.side if args.symbol else entry_data.get("side", args.side)
    entry_val = args.entry or (
        entry_data.get("indicative_fill_ask") if side.upper() == "BUY" else entry_data.get("indicative_fill_bid")
    ) or entry_data.get("trigger_close")

    if not sym or entry_val is None:
        print("Error: Missing symbol or entry price. Provide --symbol and --entry, or use --from-entry.")
        exit(1)

    asian_range = entry_data.get("asian_range")
    plan = plan_exit(
        symbol=sym,
        side=side,
        entry_price=float(entry_val),
        stop_rule=args.stop if args.stop != "range-mid" or asian_range else "pips:25",
        target_rule=args.target,
        asian_range=asian_range,
    )

    print(format_plan_report(plan))

    if args.write:
        out_path = os.path.join(os.path.dirname(__file__), "plan.json")
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(plan, fh, indent=2)
        print(f"Wrote plan to {out_path}.")
