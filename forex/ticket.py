"""Paper-trading order ticket formatter for Forex."""

import json
from typing import Dict, Any


def format_forex_ticket(
    symbol: str,
    side: str,
    lot_size: float,
    entry_price: float,
    stop_price: float,
    target_price: float,
    pips_at_risk: float,
    target_pips: float,
    max_loss_usd: float,
    margin_required_usd: float,
    effective_leverage: float,
    setup_name: str = "Forex Session Momentum",
    status: str = "READY: PAPER TRADING ONLY"
) -> str:
    """Renders a clean human-readable Forex paper trading ticket."""
    from .pairs import get_pair_info, STANDARD_LOT_UNITS

    side_clean = side.upper()
    try:
        info = get_pair_info(symbol)
        contract_size = info.get("contract_size", STANDARD_LOT_UNITS)
        unit_label = "oz" if info.get("asset_type") == "metal" else "units"
    except Exception:
        contract_size = 100000
        unit_label = "units"

    raw_units = lot_size * contract_size
    units = int(raw_units) if contract_size >= 1000 or raw_units.is_integer() else round(raw_units, 2)
    units_disp = f"{int(raw_units):,}" if contract_size >= 1000 or raw_units.is_integer() else f"{raw_units:.2f}"
    rr = round(target_pips / pips_at_risk, 2) if pips_at_risk > 0 else 0.0

    ticket = [
        f"=======================================================",
        f"FOREX ORDER TICKET (PAPER TRADING ONLY)",
        f"=======================================================",
        f"  Pair:             {symbol}",
        f"  Direction:        {side_clean} ({'Long base currency' if side_clean == 'BUY' else 'Short base currency'})",
        f"  Position Size:    {lot_size:.2f} Lots ({units_disp} {unit_label})",
        f"  Entry:            {entry_price}",
        f"  Stop Loss:        {stop_price} ({pips_at_risk:.1f} pips)",
        f"  Take Profit:      {target_price} (+{target_pips:.1f} pips)",
        f"  Reward:Risk:      {rr}:1",
        f"  Max Loss at Stop: ${max_loss_usd:.2f} USD",
        f"  Margin Required:  ${margin_required_usd:.2f} USD (Leverage: {effective_leverage}:1)",
        f"  Setup:            {setup_name}",
        f"  Status:           {status}",
        f"-------------------------------------------------------",
        f"Paper execution payload:",
        f"  {json.dumps({'symbol': symbol, 'side': side_clean.lower(), 'lots': lot_size, 'units': units, 'entry': entry_price, 'stop_loss': stop_price, 'take_profit': target_price, 'paper': True})}",
        f"=======================================================",
    ]
    return "\n".join(ticket)
