"""Currency pair registry and pip calculation engine for Forex paper trading.

Standard library only, Python 3.8+ compatible.
"""

from typing import Dict, Optional, Tuple, Any

STANDARD_LOT_UNITS = 100000

# Canonical pair metadata: base, quote, pip_size, decimal_places
PAIRS: Dict[str, Dict[str, Any]] = {
    # Major Pairs (vs USD)
    "EUR/USD": {"base": "EUR", "quote": "USD", "pip_size": 0.0001, "decimals": 4, "default_spread": 0.8},
    "GBP/USD": {"base": "GBP", "quote": "USD", "pip_size": 0.0001, "decimals": 4, "default_spread": 1.2},
    "USD/JPY": {"base": "USD", "quote": "JPY", "pip_size": 0.01,   "decimals": 2, "default_spread": 0.9},
    "USD/CHF": {"base": "USD", "quote": "CHF", "pip_size": 0.0001, "decimals": 4, "default_spread": 1.2},
    "USD/CAD": {"base": "USD", "quote": "CAD", "pip_size": 0.0001, "decimals": 4, "default_spread": 1.4},
    "AUD/USD": {"base": "AUD", "quote": "USD", "pip_size": 0.0001, "decimals": 4, "default_spread": 1.1},
    "NZD/USD": {"base": "NZD", "quote": "USD", "pip_size": 0.0001, "decimals": 4, "default_spread": 1.5},

    # Cross Pairs
    "EUR/GBP": {"base": "EUR", "quote": "GBP", "pip_size": 0.0001, "decimals": 4, "default_spread": 1.4},
    "EUR/JPY": {"base": "EUR", "quote": "JPY", "pip_size": 0.01,   "decimals": 2, "default_spread": 1.5},
    "GBP/JPY": {"base": "GBP", "quote": "JPY", "pip_size": 0.01,   "decimals": 2, "default_spread": 2.2},
    "AUD/JPY": {"base": "AUD", "quote": "JPY", "pip_size": 0.01,   "decimals": 2, "default_spread": 1.8},
    "CAD/JPY": {"base": "CAD", "quote": "JPY", "pip_size": 0.01,   "decimals": 2, "default_spread": 1.9},
    "EUR/AUD": {"base": "EUR", "quote": "AUD", "pip_size": 0.0001, "decimals": 4, "default_spread": 2.0},
    "GBP/AUD": {"base": "GBP", "quote": "AUD", "pip_size": 0.0001, "decimals": 4, "default_spread": 2.5},
    "AUD/NZD": {"base": "AUD", "quote": "NZD", "pip_size": 0.0001, "decimals": 4, "default_spread": 2.2},

    # Precious Metals (CFD/Spot vs USD)
    "XAU/USD": {
        "base": "XAU",
        "quote": "USD",
        "pip_size": 0.10,          # 1 pip = $0.10 move (10 points / $1.00 = 10 pips)
        "decimals": 2,             # 2 decimal places (e.g. 2740.50)
        "contract_size": 100,      # 100 Troy Ounces per standard lot (1 micro lot = 1 oz)
        "default_spread": 2.5,     # 2.5 pips = $0.25 spread
        "asset_type": "metal",
    },
}


def normalize_symbol(symbol: str) -> str:
    """Normalize symbols like 'EURUSD', 'GC=F', 'XAUUSD', 'eur/usd' to canonical format."""
    s = symbol.strip().upper()
    if s in ("GC=F", "GOLD", "XAUUSD", "XAU-USD", "XAU_USD"):
        return "XAU/USD"
    if s.endswith("=X"):
        s = s[:-2]
    s = s.replace("-", "/").replace("_", "/")
    if "/" not in s and len(s) == 6:
        s = s[:3] + "/" + s[3:]
    return s


def get_pair_info(symbol: str) -> Dict[str, Any]:
    """Retrieve metadata for a currency pair."""
    canon = normalize_symbol(symbol)
    if canon not in PAIRS:
        # Fallback heuristic for unlisted pairs
        if "/" in canon:
            base, quote = canon.split("/")
        elif len(canon) == 6:
            base, quote = canon[:3], canon[3:]
        else:
            raise ValueError(f"Unknown currency pair symbol: {symbol}")
        is_jpy = quote == "JPY"
        pip_size = 0.01 if is_jpy else 0.0001
        decimals = 2 if is_jpy else 4
        return {
            "symbol": f"{base}/{quote}",
            "base": base,
            "quote": quote,
            "pip_size": pip_size,
            "decimals": decimals,
            "default_spread": 2.0,
            "contract_size": STANDARD_LOT_UNITS,
            "asset_type": "forex",
        }
    info = dict(PAIRS[canon])
    info["symbol"] = canon
    info.setdefault("contract_size", STANDARD_LOT_UNITS)
    info.setdefault("asset_type", "forex")
    return info


def to_pips(symbol: str, price_difference: float) -> float:
    """Convert an absolute price difference into pips."""
    info = get_pair_info(symbol)
    return round(price_difference / info["pip_size"], 2)


def from_pips(symbol: str, pips: float) -> float:
    """Convert pips into an absolute price offset."""
    info = get_pair_info(symbol)
    return round(pips * info["pip_size"], info["decimals"] + 1)


def pip_value_usd(symbol: str, lot_size: float = 1.0, current_rates: Optional[Dict[str, float]] = None) -> float:
    """
    Calculate the USD value of 1 pip for a given lot size.
    
    lot_size: Number of standard lots (1.0 = contract_size units/oz).
    current_rates: Optional dict of current mid rates for currency conversion (e.g. {'USD/JPY': 150.0}).
    """
    if current_rates is None:
        current_rates = {}

    info = get_pair_info(symbol)
    base = info["base"]
    quote = info["quote"]
    pip_size = info["pip_size"]
    contract_size = info.get("contract_size", STANDARD_LOT_UNITS)
    units = lot_size * contract_size

    # Case 1: Quote currency is USD (e.g. EUR/USD, GBP/USD, AUD/USD, XAU/USD)
    # 1 pip = units * pip_size in USD directly
    if quote == "USD":
        return round(units * pip_size, 4)

    # Case 2: Base currency is USD (e.g. USD/JPY, USD/CAD, USD/CHF)
    # 1 pip = (units * pip_size) / rate
    if base == "USD":
        pair_sym = f"{base}/{quote}"
        rate = current_rates.get(pair_sym) or current_rates.get(symbol)
        if rate is None:
            # Reasonable reference fallbacks if live rate not provided
            default_rates = {"USD/JPY": 150.0, "USD/CAD": 1.35, "USD/CHF": 0.88}
            rate = default_rates.get(pair_sym, 1.0)
        return round((units * pip_size) / rate, 4)

    # Case 3: Cross currency pair (e.g. EUR/GBP, EUR/JPY, GBP/JPY)
    # Value in quote currency = units * pip_size
    pip_in_quote = units * pip_size

    # Convert quote to USD
    # If quote/USD exists (e.g. GBP/USD):
    quote_usd_pair = f"{quote}/USD"
    if quote_usd_pair in current_rates:
        return round(pip_in_quote * current_rates[quote_usd_pair], 4)

    # If USD/quote exists (e.g. USD/JPY):
    usd_quote_pair = f"USD/{quote}"
    if usd_quote_pair in current_rates:
        return round(pip_in_quote / current_rates[usd_quote_pair], 4)

    # Common cross defaults
    if quote == "JPY":
        usd_jpy = current_rates.get("USD/JPY", 150.0)
        return round(pip_in_quote / usd_jpy, 4)
    if quote == "GBP":
        gbp_usd = current_rates.get("GBP/USD", 1.28)
        return round(pip_in_quote * gbp_usd, 4)
    if quote == "EUR":
        eur_usd = current_rates.get("EUR/USD", 1.08)
        return round(pip_in_quote * eur_usd, 4)
    if quote == "AUD":
        aud_usd = current_rates.get("AUD/USD", 0.65)
        return round(pip_in_quote * aud_usd, 4)

    # General fallback assuming parity
    return round(pip_in_quote, 4)
