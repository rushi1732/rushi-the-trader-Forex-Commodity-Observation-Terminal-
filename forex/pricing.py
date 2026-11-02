"""Pricing, bid/ask spreads, and execution simulator for Forex paper trading."""

from typing import Tuple, Optional
from .pairs import get_pair_info, from_pips


def calc_spread_prices(symbol: str, mid_price: float, spread_pips: Optional[float] = None) -> Tuple[float, float]:
    """
    Derive bid and ask prices from a mid price and spread in pips.
    
    Returns: (bid, ask)
    """
    info = get_pair_info(symbol)
    if spread_pips is None:
        spread_pips = info.get("default_spread", 1.0)

    half_spread = (spread_pips * info["pip_size"]) / 2.0
    decimals = info["decimals"] + 1

    bid = round(mid_price - half_spread, decimals)
    ask = round(mid_price + half_spread, decimals)
    return bid, ask


def execution_price(
    symbol: str,
    side: str,
    mid_price: float,
    spread_pips: Optional[float] = None,
    slippage_pips: float = 0.0
) -> float:
    """
    Determine the fill price for paper trading:
    - BUY (Long) fills at Ask (+ slippage)
    - SELL (Short) fills at Bid (- slippage)
    """
    info = get_pair_info(symbol)
    decimals = info["decimals"] + 1
    bid, ask = calc_spread_prices(symbol, mid_price, spread_pips)
    slippage_offset = slippage_pips * info["pip_size"]

    side_norm = side.strip().lower()
    if side_norm in ("buy", "long"):
        return round(ask + slippage_offset, decimals)
    elif side_norm in ("sell", "short"):
        return round(bid - slippage_offset, decimals)
    else:
        raise ValueError(f"Invalid side: {side}. Must be 'buy'/'long' or 'sell'/'short'.")


def is_stop_triggered(
    side: str,
    current_bid: float,
    current_ask: float,
    stop_price: float
) -> bool:
    """
    Evaluate if a stop-loss is triggered:
    - Long position is closed by selling at Bid: triggers if current_bid <= stop_price
    - Short position is closed by buying at Ask: triggers if current_ask >= stop_price
    """
    side_norm = side.strip().lower()
    if side_norm in ("buy", "long"):
        return current_bid <= stop_price
    elif side_norm in ("sell", "short"):
        return current_ask >= stop_price
    else:
        raise ValueError(f"Invalid side: {side}")


def is_target_triggered(
    side: str,
    current_bid: float,
    current_ask: float,
    target_price: float
) -> bool:
    """
    Evaluate if a take-profit target is triggered:
    - Long position: triggers if current_bid >= target_price
    - Short position: triggers if current_ask <= target_price
    """
    side_norm = side.strip().lower()
    if side_norm in ("buy", "long"):
        return current_bid >= target_price
    elif side_norm in ("sell", "short"):
        return current_ask <= target_price
    else:
        raise ValueError(f"Invalid side: {side}")
