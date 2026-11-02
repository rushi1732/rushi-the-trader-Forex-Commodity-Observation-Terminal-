"""Pre-trade risk validator for Forex paper trading."""

from datetime import datetime
from typing import Dict, Any, List, Optional
from .pairs import get_pair_info, to_pips, pip_value_usd, STANDARD_LOT_UNITS
from .sessions import is_forex_market_open, is_market_open_for_symbol, utc_to_et


def validate_forex_trade(
    symbol: str,
    side: str,
    entry_price: float,
    stop_price: float,
    target_price: float,
    lot_size: float,
    config: Dict[str, Any],
    open_positions: Optional[List[Dict[str, Any]]] = None,
    now_et: Optional[datetime] = None,
    current_rates: Optional[Dict[str, float]] = None
) -> Dict[str, Any]:
    """
    Validate a proposed Forex order against risk limits.
    
    Returns dict with 'passed': bool, 'status': 'PASS'|'BLOCK', 'reasons': List[str], 'metrics': Dict.
    """
    if open_positions is None:
        open_positions = []
    if current_rates is None:
        current_rates = {}

    side_norm = side.strip().upper()
    is_long = side_norm in ("BUY", "LONG")
    blocks: List[str] = []
    warnings: List[str] = []

    # 1. Market Open & Weekend Cutoff
    if now_et is not None:
        is_open, open_reason = is_market_open_for_symbol(symbol, now_et)
        if not is_open:
            blocks.append(f"market_closed: {open_reason}")
        elif now_et.weekday() == 4 and now_et.hour >= 16 and now_et.minute >= 30:
            blocks.append("weekend_cutoff: No new entries allowed after Friday 16:30 ET to avoid weekend gaps.")

    # 2. Pair Info & Pip Sizing
    try:
        info = get_pair_info(symbol)
    except ValueError as e:
        return {"passed": False, "status": "BLOCK", "reasons": [str(e)], "warnings": []}

    pip_size = info["pip_size"]
    base = info["base"]
    quote = info["quote"]

    # 3. Stop & Target Checks
    if is_long:
        if stop_price >= entry_price:
            blocks.append(f"invalid_stop: Long stop ({stop_price}) must be below entry ({entry_price}).")
        if target_price <= entry_price:
            blocks.append(f"invalid_target: Long target ({target_price}) must be above entry ({entry_price}).")
    else:
        if stop_price <= entry_price:
            blocks.append(f"invalid_stop: Short stop ({stop_price}) must be above entry ({entry_price}).")
        if target_price >= entry_price:
            blocks.append(f"invalid_target: Short target ({target_price}) must be below entry ({entry_price}).")

    stop_pips = round(abs(entry_price - stop_price) / pip_size, 2)
    target_pips = round(abs(target_price - entry_price) / pip_size, 2)

    # 4. Minimum Reward:Risk Ratio
    min_rr = config.get("min_reward_risk", 1.0)
    rr_ratio = round(target_pips / stop_pips, 2) if stop_pips > 0 else 0.0
    if rr_ratio < min_rr:
        blocks.append(f"reward_risk: Reward:Risk ratio ({rr_ratio}) is below required minimum ({min_rr}).")

    # 5. Dollar Loss at Stop Check
    pip_val_1_lot = pip_value_usd(symbol, lot_size=1.0, current_rates=current_rates)
    max_loss_usd = round(lot_size * stop_pips * pip_val_1_lot, 2)
    allowed_loss_usd = config.get("max_loss_per_trade_usd", 50.0)

    if max_loss_usd > allowed_loss_usd:
        blocks.append(
            f"max_loss: Risk at stop is ${max_loss_usd:.2f}, exceeding limit of ${allowed_loss_usd:.2f}."
        )

    # 6. Leverage & Margin Check
    budget_usd = config.get("budget", 5000.0)
    max_leverage = config.get("leverage", 30.0)
    
    # Base in USD rate
    if base == "USD":
        base_to_usd = 1.0
    elif quote == "USD":
        base_to_usd = entry_price
    else:
        base_to_usd = current_rates.get(f"{base}/USD", 1.0)

    contract_size = info.get("contract_size", STANDARD_LOT_UNITS)
    units = lot_size * contract_size
    notional_usd = round(units * base_to_usd, 2)
    margin_required = round(notional_usd / max_leverage, 2)
    effective_leverage = round(notional_usd / budget_usd, 2)

    if effective_leverage > max_leverage:
        blocks.append(f"leverage_exceeded: Effective leverage {effective_leverage}:1 exceeds limit {max_leverage}:1.")

    if margin_required > budget_usd:
        blocks.append(f"insufficient_margin: Required margin ${margin_required:.2f} exceeds budget ${budget_usd:.2f}.")

    # 7. Currency Cluster Exposure Check
    # Avoid having > 50% of portfolio concentrated in a single currency
    max_cluster_pct = config.get("max_currency_exposure_pct", 50.0)
    max_cluster_notional = budget_usd * max_leverage * (max_cluster_pct / 100.0)

    current_base_notional = notional_usd
    for pos in open_positions:
        pos_sym = pos.get("symbol", "")
        if base in pos_sym:
            current_base_notional += pos.get("notional_usd", 0.0)

    if current_base_notional > max_cluster_notional:
        warnings.append(
            f"cluster_exposure: Total exposure in {base} (${current_base_notional:.2f}) approaches cluster limit."
        )

    passed = len(blocks) == 0
    return {
        "passed": passed,
        "status": "PASS" if passed else "BLOCK",
        "symbol": symbol,
        "side": side_norm,
        "lot_size": lot_size,
        "units": int(units),
        "stop_pips": stop_pips,
        "target_pips": target_pips,
        "reward_risk": rr_ratio,
        "max_loss_usd": max_loss_usd,
        "margin_required_usd": margin_required,
        "effective_leverage": effective_leverage,
        "reasons": blocks,
        "warnings": warnings,
    }
