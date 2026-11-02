"""Position and lot sizing engine for Forex paper trading."""

import math
from typing import Dict, Any, Optional
from .pairs import get_pair_info, to_pips, pip_value_usd, STANDARD_LOT_UNITS


def size_position(
    symbol: str,
    side: str,
    entry_price: float,
    stop_price: float,
    budget_usd: float = 5000.0,
    max_loss_usd: float = 50.0,
    leverage: float = 30.0,
    cash_buffer_pct: float = 10.0,
    min_lot: float = 0.01,
    max_lot: float = 5.0,
    current_rates: Optional[Dict[str, float]] = None
) -> Dict[str, Any]:
    """
    Calculate the appropriate lot size for a Forex trade bound by max dollar risk and margin limits.
    
    Returns structured sizing dict.
    """
    if current_rates is None:
        current_rates = {}

    info = get_pair_info(symbol)
    base = info["base"]
    quote = info["quote"]
    pip_size = info["pip_size"]

    side_norm = side.strip().lower()
    is_long = side_norm in ("buy", "long")

    # Stop orientation checks
    if is_long and stop_price >= entry_price:
        raise ValueError(f"Long trade stop ({stop_price}) must be below entry ({entry_price}).")
    if not is_long and stop_price <= entry_price:
        raise ValueError(f"Short trade stop ({stop_price}) must be above entry ({entry_price}).")

    price_diff = abs(entry_price - stop_price)
    pips_at_risk = round(price_diff / pip_size, 2)
    if pips_at_risk <= 0:
        raise ValueError("Stop loss must be distinct from entry price.")

    # 1 pip value in USD for 1.0 standard lot
    pip_val_1_lot = pip_value_usd(symbol, lot_size=1.0, current_rates=current_rates)
    if pip_val_1_lot <= 0:
        raise ValueError(f"Could not calculate pip value for {symbol}.")

    # 1. Sizing bound by Max Loss
    # max_loss = lot_size * pips_at_risk * pip_val_1_lot
    raw_lot_by_loss = max_loss_usd / (pips_at_risk * pip_val_1_lot)

    # 2. Sizing bound by Margin & Cash Buffer
    available_cash = budget_usd * (1.0 - (cash_buffer_pct / 100.0))

    # Base currency in USD rate
    if base == "USD":
        base_to_usd = 1.0
    elif quote == "USD":
        base_to_usd = entry_price
    else:
        # Cross rate: lookup base/USD or estimate from current_rates
        base_usd_pair = f"{base}/USD"
        usd_base_pair = f"USD/{base}"
        if base_usd_pair in current_rates:
            base_to_usd = current_rates[base_usd_pair]
        elif usd_base_pair in current_rates:
            base_to_usd = 1.0 / current_rates[usd_base_pair]
        else:
            base_to_usd = 1.0  # Parity approximation if unknown

    contract_size = info.get("contract_size", STANDARD_LOT_UNITS)

    # Margin required for 1.0 standard lot = (contract_size * base_to_usd) / leverage
    margin_per_lot = (contract_size * base_to_usd) / leverage
    raw_lot_by_margin = available_cash / margin_per_lot if margin_per_lot > 0 else max_lot

    # Smallest limit wins
    binding = "max_loss"
    chosen_lot = raw_lot_by_loss

    if raw_lot_by_margin < chosen_lot:
        chosen_lot = raw_lot_by_margin
        binding = "margin_and_cash_buffer"

    if chosen_lot > max_lot:
        chosen_lot = max_lot
        binding = "max_lot_cap"

    # Quantize to micro-lot step (0.01 lot)
    quantized_lot = math.floor(chosen_lot * 100.0) / 100.0

    if quantized_lot < min_lot:
        return {
            "symbol": symbol,
            "side": "BUY" if is_long else "SELL",
            "allowed": False,
            "reason": f"Required risk exceeds limit even at minimum {min_lot} micro lot.",
            "lot_size": 0.0,
            "units": 0,
            "pips_at_risk": pips_at_risk,
            "binding": "under_min_lot"
        }

    actual_max_loss = round(quantized_lot * pips_at_risk * pip_val_1_lot, 2)
    margin_required = round(quantized_lot * margin_per_lot, 2)
    raw_units = quantized_lot * contract_size
    total_units = int(raw_units) if contract_size >= 1000 or raw_units.is_integer() else round(raw_units, 2)
    notional_usd = round(total_units * base_to_usd, 2)
    effective_leverage = round(notional_usd / budget_usd, 2)

    return {
        "symbol": symbol,
        "side": "BUY" if is_long else "SELL",
        "allowed": True,
        "lot_size": quantized_lot,
        "units": total_units,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "pips_at_risk": pips_at_risk,
        "pip_value_usd": round(quantized_lot * pip_val_1_lot, 4),
        "max_loss_usd": actual_max_loss,
        "max_loss_budget_limit": max_loss_usd,
        "margin_required_usd": margin_required,
        "available_cash_usd": available_cash,
        "effective_leverage": effective_leverage,
        "notional_usd": notional_usd,
        "binding_constraint": binding,
    }
