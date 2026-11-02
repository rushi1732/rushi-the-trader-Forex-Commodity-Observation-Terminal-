"""Price alerts, session bells, and 24/5 market notifications for Forex.

Watches stop-loss, take-profit, session opens, and pre-weekend flattening alerts.
Standard library only, Python 3.8+ compatible.
"""

import argparse
import json
import os
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple

from .pairs import normalize_symbol, to_pips, get_pair_info
from .data import fetch_chart, INDICATIVE_DISCLAIMER
from .sessions import is_forex_market_open, get_active_sessions, utc_to_et, session_status
from .pricing import is_stop_triggered, is_target_triggered, calc_spread_prices


def get_alerts_state_path() -> str:
    """Return path to alerts state file."""
    state_dir = os.path.join(os.path.dirname(__file__), "state")
    os.makedirs(state_dir, exist_ok=True)
    return os.path.join(state_dir, "alerts.json")


def load_alerts() -> Dict[str, Any]:
    """Load existing alerts configuration and history."""
    path = get_alerts_state_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            pass
    return {"rules": [], "fired": []}


def save_alerts(state: Dict[str, Any]) -> None:
    """Persist alerts state to disk."""
    path = get_alerts_state_path()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


def add_alert(
    symbol: str,
    alert_type: str,
    price: Optional[float] = None,
    side: Optional[str] = None,
    label: str = "",
) -> Dict[str, Any]:
    """Add a new price alert rule."""
    state = load_alerts()
    canon = normalize_symbol(symbol)
    alert_id = f"{canon.replace('/', '').lower()}-{alert_type}-{int(time.time())}"

    rule = {
        "id": alert_id,
        "symbol": canon,
        "type": alert_type,  # 'stop', 'target', 'above', 'below', 'weekend_close'
        "price": price,
        "side": side.upper() if side else None,
        "label": label or f"{canon} {alert_type}",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "active": True,
    }

    state["rules"].append(rule)
    save_alerts(state)
    return rule


def add_from_plan(plan_file: Optional[str] = None) -> List[Dict[str, Any]]:
    """Automatically create stop, target, and weekend flat alerts from a plan.json."""
    if plan_file is None:
        plan_file = os.path.join(os.path.dirname(__file__), "plan.json")

    if not os.path.exists(plan_file):
        raise FileNotFoundError(f"Plan file not found: {plan_file}. Run forex/exit.py first.")

    with open(plan_file, "r", encoding="utf-8") as fh:
        plan = json.load(fh)

    sym = plan["symbol"]
    side = plan["side"]
    stop_p = plan["stop_price"]
    target_p = plan["target_price"]

    rules = [
        add_alert(sym, "stop", price=stop_p, side=side, label=f"{sym} {side} Stop Loss"),
        add_alert(sym, "target", price=target_p, side=side, label=f"{sym} {side} Take Profit"),
        add_alert(sym, "weekend_close", label="Friday Pre-Weekend Market Close Warning"),
    ]
    return rules


def evaluate_alerts(
    quotes: Optional[Dict[str, float]] = None,
    now_et: Optional[datetime] = None,
    offline_data: Optional[Dict[str, Any]] = None,
) -> Tuple[List[str], List[str]]:
    """
    Check all active alerts against latest market prices.
    
    Returns: (fired_messages, remaining_active_labels)
    """
    state = load_alerts()
    rules = state.get("rules", [])
    fired_ids = set(state.get("fired", []))

    if now_et is None:
        now_utc = datetime.now(timezone.utc)
        now_et = utc_to_et(now_utc)

    # Fetch quotes if not supplied
    if quotes is None:
        quotes = {}
        for r in rules:
            sym = r.get("symbol")
            if sym and sym not in quotes:
                try:
                    c = fetch_chart(sym, interval="5m", range_period="1d", offline_data=offline_data)
                    quotes[sym] = c["latest_price"]
                except Exception:
                    pass

    fired_messages = []
    active_labels = []

    for r in rules:
        rid = r["id"]
        if rid in fired_ids:
            continue

        sym = r.get("symbol")
        rtype = r.get("type")
        target_price = r.get("price")
        side = r.get("side", "BUY")
        label = r.get("label", rid)

        # 1. Weekend Close Warning
        if rtype == "weekend_close":
            if now_et.weekday() == 4 and now_et.hour >= 16 and now_et.minute >= 30:
                fired_messages.append(
                    f"ALERT: [WEEKEND FLATTEN] Friday 16:30 ET reached. Market closes at 17:00 ET. Flatten paper trades!"
                )
                fired_ids.add(rid)
            else:
                active_labels.append(label)
            continue

        curr_price = quotes.get(sym)
        if curr_price is None or target_price is None:
            active_labels.append(label)
            continue

        bid, ask = calc_spread_prices(sym, curr_price)

        # 2. Stop Loss Trigger
        if rtype == "stop":
            triggered = is_stop_triggered(side, current_bid=bid, current_ask=ask, stop_price=target_price)
            if triggered:
                fill_price = bid if side in ("BUY", "LONG") else ask
                fired_messages.append(
                    f"ALERT: [STOP LOSS HIT] {label}: {sym} at {fill_price} hit stop {target_price}!"
                )
                fired_ids.add(rid)
            else:
                active_labels.append(label)

        # 3. Take Profit Target Trigger
        elif rtype == "target":
            triggered = is_target_triggered(side, current_bid=bid, current_ask=ask, target_price=target_price)
            if triggered:
                fill_price = bid if side in ("BUY", "LONG") else ask
                fired_messages.append(
                    f"ALERT: [TAKE PROFIT HIT] {label}: {sym} at {fill_price} hit target {target_price}!"
                )
                fired_ids.add(rid)
            else:
                active_labels.append(label)

        # 4. Simple Price Above
        elif rtype == "above":
            if curr_price >= target_price:
                fired_messages.append(f"ALERT: {label}: {sym} ({curr_price}) crossed above {target_price}")
                fired_ids.add(rid)
            else:
                active_labels.append(label)

        # 5. Simple Price Below
        elif rtype == "below":
            if curr_price <= target_price:
                fired_messages.append(f"ALERT: {label}: {sym} ({curr_price}) crossed below {target_price}")
                fired_ids.add(rid)
            else:
                active_labels.append(label)

    state["fired"] = list(fired_ids)
    save_alerts(state)
    return fired_messages, active_labels


def clock_report() -> str:
    """Print 24/5 market hours, active sessions, and status."""
    now_utc = datetime.now(timezone.utc)
    now_et = utc_to_et(now_utc)
    status = session_status(now_et)

    lines = [
        "=" * 72,
        "FOREX 24/5 MARKET CLOCK & ACTIVE SESSIONS",
        "=" * 72,
        f"New York Time:   {status['timestamp_et']}",
        f"Market Status:   {'OPEN' if status['is_open'] else 'CLOSED'} ({status['reason']})",
        f"Active Sessions: {', '.join(status['active_sessions']) if status['active_sessions'] else 'None (Inter-session)'}",
        f"Weekend Warning: {'ACTIVE (Friday pre-close)' if status['weekend_warning'] else 'Normal weekday operations'}",
        "-" * 72,
        "Major Global Trading Hours (New York Time):",
        "  Sydney:        17:00 - 02:00 ET",
        "  Tokyo:         19:00 - 04:00 ET",
        "  London:        03:00 - 12:00 ET",
        "  New York:      08:00 - 17:00 ET",
        "  London/NY:     08:00 - 12:00 ET (Peak Liquidity Window)",
        "-" * 72,
        f"Notice:          {INDICATIVE_DISCLAIMER}",
        "=" * 72,
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Forex Alerts & Session Clock")
    sub = parser.add_subparsers(dest="command")

    # clock
    sub.add_parser("clock", help="Display 24/5 Forex market clock and active sessions")

    # add
    p_add = sub.add_parser("add", help="Add alert rule")
    p_add.add_argument("--symbol", help="Currency pair (e.g. EUR/USD)")
    p_add.add_argument("--above", type=float, help="Price alert above level")
    p_add.add_argument("--below", type=float, help="Price alert below level")
    p_add.add_argument("--from-plan", action="store_true", help="Add stop and target from forex/plan.json")
    p_add.add_argument("--label", default="", help="Description label")

    # check
    sub.add_parser("check", help="Check active alerts against latest prices")

    # list
    sub.add_parser("list", help="List registered alert rules")

    # reset
    p_res = sub.add_parser("reset", help="Reset alert state")
    p_res.add_argument("--all", action="store_true", help="Clear all fired states")

    args = parser.parse_args()

    if args.command == "clock":
        print(clock_report())

    elif args.command == "add":
        if args.from_plan:
            created = add_from_plan()
            print(f"Added {len(created)} alert rules from forex/plan.json:")
            for c in created:
                print(f"  - {c['label']} ({c['type']} at {c.get('price')})")
        elif args.symbol:
            if args.above:
                r = add_alert(args.symbol, "above", price=args.above, label=args.label)
                print(f"Added alert: {r['label']} (Price >= {args.above})")
            elif args.below:
                r = add_alert(args.symbol, "below", price=args.below, label=args.label)
                print(f"Added alert: {r['label']} (Price <= {args.below})")
        else:
            print("Specify --symbol and --above/--below, or --from-plan.")

    elif args.command == "check":
        fired, active = evaluate_alerts()
        if fired:
            for m in fired:
                print(m)
        else:
            print(f"No alerts triggered. {len(active)} active rule(s) watched.")

    elif args.command == "list":
        st = load_alerts()
        rules = st.get("rules", [])
        fired_set = set(st.get("fired", []))
        print(f"Registered Alert Rules ({len(rules)} total):")
        for r in rules:
            status = "FIRED" if r["id"] in fired_set else "ACTIVE"
            print(f"  [{status}] {r.get('symbol', 'ALL')} {r.get('label')} ({r.get('type')} @ {r.get('price')})")

    elif args.command == "reset":
        st = load_alerts()
        st["fired"] = []
        save_alerts(st)
        print("Reset all fired alerts.")

    else:
        parser.print_help()
