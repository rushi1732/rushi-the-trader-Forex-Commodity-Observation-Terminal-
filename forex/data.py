"""Forex market data fetcher with retry handling, offline caching, and parsing.

Fetches free public candles from Yahoo Finance endpoints (EURUSD=X, etc.).
Note: Yahoo Finance Forex prices are indicative aggregate bank quotes, not live broker-executable prices.
Standard library only, Python 3.8+ compatible.
"""

import gzip
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any

from .pairs import normalize_symbol

USER_AGENT = "day-trader-skill-forex/2.0 (compatible; public data; contact@example.com)"
INDICATIVE_DISCLAIMER_FOREX = (
    "INDICATIVE ONLY: Yahoo Finance Forex quotes are aggregate bank indicators, "
    "not real-time broker-executable prices. Spreads and fills vary by broker."
)
INDICATIVE_DISCLAIMER_GOLD = (
    "INDICATIVE FUTURES BENCHMARK: Gold quotes are derived from COMEX Gold Futures (GC=F), "
    "not OTC spot interbank feeds. Prices reflect front-month futures contracts and may include "
    "futures basis/roll differences compared to retail spot XAU/USD. For paper observation only."
)
INDICATIVE_DISCLAIMER = INDICATIVE_DISCLAIMER_FOREX

MAJOR_PAIRS = [
    "XAU/USD",
    "BTC/USD",
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "USD/CHF",
    "USD/CAD",
    "AUD/USD",
    "NZD/USD",
    "EUR/GBP",
    "EUR/JPY",
    "GBP/JPY",
]


def pair_to_yahoo_ticker(symbol: str) -> str:
    """Convert 'EUR/USD' -> 'EURUSD=X', 'XAU/USD' -> 'GC=F', 'BTC/USD' -> 'BTC-USD' for Yahoo Finance."""
    canon = normalize_symbol(symbol)
    if canon == "XAU/USD":
        return "GC=F"
    if canon == "BTC/USD":
        return "BTC-USD"
    return canon.replace("/", "") + "=X"


def yahoo_ticker_to_pair(ticker: str) -> str:
    """Convert 'EURUSD=X' -> 'EUR/USD', 'GC=F' -> 'XAU/USD', 'BTC-USD' -> 'BTC/USD'."""
    clean = ticker.upper().strip()
    if clean in ("GC=F", "GC", "MGC=F"):
        return "XAU/USD"
    if clean in ("BTC-USD", "BTCUSD=X", "BTC=F"):
        return "BTC/USD"
    clean = clean.replace("=X", "")
    if len(clean) == 6:
        return f"{clean[:3]}/{clean[3:]}"
    return clean


def http_get(url: str, timeout: int = 5) -> str:
    """Perform HTTP GET with standard headers."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip" or content[:2] == b"\x1f\x8b":
            content = gzip.decompress(content)
        return content.decode("utf-8")


TWELVE_DATA_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "twelvedata_config.json")


def load_twelve_data_config() -> Dict[str, Any]:
    """Load Twelve Data API configuration from disk."""
    if os.path.exists(TWELVE_DATA_CONFIG_PATH):
        try:
            with open(TWELVE_DATA_CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"api_key": "73345dbfbdc94360af524b86732676d2", "enabled": True, "provider": "twelvedata"}


def fetch_twelve_data_quote(symbol: str, api_key: str = "", timeout: int = 4) -> Optional[Dict[str, Any]]:
    """Fetch live real-time spot quote for any symbol from Twelve Data API."""
    canon = normalize_symbol(symbol)
    if not api_key:
        cfg = load_twelve_data_config()
        api_key = cfg.get("api_key", "73345dbfbdc94360af524b86732676d2")
    if not api_key:
        return None

    try:
        url = f"https://api.twelvedata.com/quote?symbol={canon}&apikey={api_key}"
        raw = http_get(url, timeout=timeout)
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("status") == "error":
            return None
        last_price = float(data.get("close") or data.get("price") or 0.0)
        bid = float(data.get("bid") or last_price)
        ask = float(data.get("ask") or last_price)
        if bid <= 0 or ask <= 0:
            bid = last_price
            ask = last_price
        mid = (bid + ask) / 2.0 if (bid > 0 and ask > 0) else last_price

        is_gold = "XAU" in canon
        is_btc = "BTC" in canon
        pip_unit = 0.10 if is_gold else (1.0 if is_btc else (0.01 if "JPY" in canon else 0.0001))
        spread_pips = round(max(0.0, ask - bid) / pip_unit, 2)

        return {
            "symbol": canon,
            "feed": "Twelve Data Real-Time (Retail Spot Feed)",
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "last": last_price,
            "spread_pips": spread_pips,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "timestamp": int(time.time()),
            "is_live_24_7": is_gold or is_btc,
        }
    except Exception:
        return None


def fetch_chart(
    symbol: str,
    interval: str = "5m",
    range_period: str = "1d",
    cache_dir: Optional[str] = None,
    offline_file: Optional[str] = None,
    offline_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Fetch OHLCV chart data for a currency pair with fallback and offline support.
    
    interval: '5m', '15m', '1h', '1d'
    range_period: '1d', '5d', '1mo', '60d'
    """
    canon = normalize_symbol(symbol)
    ticker = pair_to_yahoo_ticker(canon)

    # 1. Offline dict injection (used in fast offline unit tests)
    if offline_data is not None:
        raw_json = offline_data.get(ticker) or offline_data.get(canon) or offline_data
        if isinstance(raw_json, dict) and "candles" in raw_json:
            return raw_json
        return parse_yahoo_chart(raw_json, canon)

    # 2. Offline file support
    if offline_file is not None and os.path.exists(offline_file):
        with open(offline_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        raw_json = data.get(ticker) or data.get(canon) or data
        return parse_yahoo_chart(raw_json, canon)

    # 3. Cache read
    cache_path = None
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        cache_path = os.path.join(cache_dir, f"{ticker}_{interval}_{range_period}.json")
        if os.path.exists(cache_path):
            mtime = os.path.getmtime(cache_path)
            # 5-minute freshness for live bars
            if time.time() - mtime < 300:
                with open(cache_path, "r", encoding="utf-8") as fh:
                    return parse_yahoo_chart(json.load(fh), canon)

    # 3b. Real-Time Twelve Data Feed (if enabled in twelvedata_config.json)
    if offline_data is None and offline_file is None:
        cfg = load_twelve_data_config()
        if cfg.get("enabled") and cfg.get("api_key"):
            try:
                td_chart = fetch_twelve_data_chart(
                    symbol=canon,
                    interval=interval,
                    api_key=cfg["api_key"],
                )
                if td_chart and td_chart.get("candles_count", 0) >= 10:
                    return td_chart
            except Exception:
                pass

    # 4. Live network fetch with query2 -> query1 fallback & retry
    endpoints = [
        f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}?range={range_period}&interval={interval}",
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range={range_period}&interval={interval}",
    ]

    last_err = None
    raw_text = None

    for url in endpoints:
        for attempt in range(2):
            try:
                raw_text = http_get(url)
                break
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code == 429:
                    time.sleep(2.0)
                    continue
                break
            except Exception as e:
                last_err = e
                break
        if raw_text:
            break

    if not raw_text:
        raise RuntimeError(f"Could not fetch chart for {canon} ({ticker}): {last_err}")

    parsed_raw = json.loads(raw_text)

    # Cache write
    if cache_path:
        try:
            with open(cache_path, "w", encoding="utf-8") as fh:
                json.dump(parsed_raw, fh)
        except OSError:
            pass

    return parse_yahoo_chart(parsed_raw, canon)


def parse_yahoo_chart(data: Dict[str, Any], symbol: str) -> Dict[str, Any]:
    """Parse raw Yahoo chart JSON into clean candle dictionaries."""
    chart = data.get("chart", {})
    err = chart.get("error")
    if err:
        raise ValueError(f"Yahoo chart error for {symbol}: {err}")

    results = chart.get("result", [])
    if not results:
        raise ValueError(f"No chart data found for {symbol}")

    res = results[0]
    meta = res.get("meta", {})
    timestamps = res.get("timestamp", [])
    indicators = res.get("indicators", {}).get("quote", [{}])[0]

    opens = indicators.get("open", [])
    highs = indicators.get("high", [])
    lows = indicators.get("low", [])
    closes = indicators.get("close", [])
    volumes = indicators.get("volume", [])

    candles: List[Dict[str, Any]] = []

    for i in range(len(timestamps)):
        ts = timestamps[i]
        o = opens[i] if i < len(opens) else None
        h = highs[i] if i < len(highs) else None
        l = lows[i] if i < len(lows) else None
        c = closes[i] if i < len(closes) else None
        v = volumes[i] if i < len(volumes) else 0

        # Skip incomplete or null ticks
        if None in (o, h, l, c):
            continue

        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        candles.append({
            "timestamp": ts,
            "datetime_utc": dt.isoformat(),
            "open": round(float(o), 5),
            "high": round(float(h), 5),
            "low": round(float(l), 5),
            "close": round(float(c), 5),
            "volume": int(v or 0),
        })

    latest_close = candles[-1]["close"] if candles else meta.get("regularMarketPrice")

    return {
        "symbol": normalize_symbol(symbol),
        "yahoo_ticker": meta.get("symbol", pair_to_yahoo_ticker(symbol)),
        "currency": meta.get("currency", "USD"),
        "granularity": meta.get("dataGranularity", "5m"),
        "candles_count": len(candles),
        "latest_price": latest_close,
        "price_type": "INDICATIVE_MID",
        "disclaimer": INDICATIVE_DISCLAIMER,
        "candles": candles,
    }


def parse_twelve_data_chart(data: Dict[str, Any], symbol: str, interval: str = "5m") -> Dict[str, Any]:
    """Parse raw Twelve Data time_series JSON into standard candle dictionaries."""
    if not isinstance(data, dict):
        raise ValueError("Invalid Twelve Data response")
    if data.get("status") == "error":
        raise ValueError(data.get("message", "Twelve Data API error"))

    values = data.get("values", [])
    if not values:
        raise ValueError(f"No candle data in Twelve Data response for {symbol}")

    candles: List[Dict[str, Any]] = []
    canon = normalize_symbol(symbol)
    decimals = 2 if "XAU" in canon else 5

    # Twelve Data returns newest first; reverse to chronological order
    for v in reversed(values):
        try:
            dt_str = v.get("datetime", "")
            # Example datetime: "2026-11-02 11:35:00" or ISO format
            clean_dt = dt_str.replace(" ", "T")
            if "T" in clean_dt and len(clean_dt) == 16:
                clean_dt += ":00"
            dt = datetime.fromisoformat(clean_dt).replace(tzinfo=timezone.utc)
            ts = int(dt.timestamp())
            o = float(v["open"])
            h = float(v["high"])
            l = float(v["low"])
            c = float(v["close"])
            vol = float(v.get("volume") or 0.0)
            candles.append({
                "timestamp": ts,
                "datetime_utc": dt.isoformat(),
                "time": dt.strftime("%H:%M"),
                "open": round(o, decimals),
                "high": round(h, decimals),
                "low": round(l, decimals),
                "close": round(c, decimals),
                "volume": int(vol),
            })
        except Exception:
            continue

    latest_close = candles[-1]["close"] if candles else None
    meta = data.get("meta", {})
    return {
        "symbol": canon,
        "source": "Twelve Data (Retail Spot Broker Feed)",
        "currency": meta.get("currency_quote", "USD"),
        "granularity": interval,
        "candles_count": len(candles),
        "latest_price": latest_close,
        "price_type": "SPOT_RETAIL_MID",
        "disclaimer": "Twelve Data API Spot Feed — Real-time Spot Forex/Gold Rates",
        "candles": candles,
    }


def fetch_twelve_data_chart(
    symbol: str,
    interval: str = "5m",
    api_key: str = "",
    offline_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Fetch spot candles from Twelve Data API."""
    canon = normalize_symbol(symbol)
    if offline_data:
        if "values" in offline_data:
            return parse_twelve_data_chart(offline_data, canon, interval)
        if isinstance(offline_data, dict):
            sub = offline_data.get(canon) or offline_data.get(f"twelvedata_{canon}")
            if isinstance(sub, dict) and "values" in sub:
                return parse_twelve_data_chart(sub, canon, interval)
            raise KeyError(f"No offline Twelve Data format found for {canon}")

    if not api_key:
        raise ValueError("Twelve Data API key is required.")

    td_interval = "5min"
    if interval in ("1m", "1min"):
        td_interval = "1min"
    elif interval in ("5m", "5min"):
        td_interval = "5min"
    elif interval in ("15m", "15min"):
        td_interval = "15min"
    elif interval in ("1h", "60min"):
        td_interval = "1h"
    elif interval in ("4h", "240min"):
        td_interval = "4h"
    elif interval in ("1d", "1day"):
        td_interval = "1day"

    url = f"https://api.twelvedata.com/time_series?symbol={canon}&interval={td_interval}&outputsize=60&apikey={api_key}"
    raw_text = http_get(url)
    data = json.loads(raw_text)
    return parse_twelve_data_chart(data, canon, interval)
