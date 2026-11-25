"""Centralized Multi-API Market Data Management System.

Integrates and normalizes market data across four providers:
1. Twelve Data (Primary - Gold & Bitcoin live quotes, WebSocket, intraday & historical candles)
2. Finnhub (Backup - Bitcoin live quotes & market data)
3. Alpha Vantage (Historical & backup market data)
4. EODHD (Historical EOD candles & Bitcoin real-time data)

STRICT ASSET RESTRICTION:
Supports ONLY two assets:
- GOLD: XAU/USD (Precious Metal)
- BITCOIN: BTC/USD (Cryptocurrency)

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

USER_AGENT = "day-trader-skill-forex/3.0 (compatible; multi-api; observation-terminal)"
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "market_api_config.json")
LEGACY_TD_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "twelvedata_config.json")
ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")

# Canonical asset metadata - Strictly Gold and Bitcoin ONLY
SUPPORTED_ASSETS = {
    "XAU/USD": {
        "symbol": "XAU/USD",
        "name": "Gold",
        "market_type": "Precious Metal",
        "decimals": 2,
        "pip_unit": 0.10,
        "default_spread": 2.5,
    },
    "BTC/USD": {
        "symbol": "BTC/USD",
        "name": "Bitcoin",
        "market_type": "Cryptocurrency",
        "decimals": 2,
        "pip_unit": 1.0,
        "default_spread": 5.0,
    },
}

SUPPORTED_TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"]

DEFAULT_KEYS = {
    "twelvedata": "73345dbfbdc94360af524b86732676d2",
    "finnhub": "dc36sopr01quhc9m986gdc36sopr01quhc9m9870",
    "alphavantage": "XA2YB4NCNWP7XWX5",
    "eodhd": "6b066f56f31863.99311154",
}


def load_env_file() -> Dict[str, str]:
    """Parse local .env file if present in repository root without external libraries."""
    env_vars: Dict[str, str] = {}
    if os.path.exists(ENV_PATH):
        try:
            with open(ENV_PATH, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        env_vars[k.strip()] = v.strip().strip("'\"")
        except Exception:
            pass
    return env_vars


def get_env_or_config(key_name: str, default_val: str = "") -> str:
    """Read API key from environment variable, then .env file, then default."""
    val = os.environ.get(key_name)
    if val and val.strip():
        return val.strip()
    env_file = load_env_file()
    if key_name in env_file and env_file[key_name].strip():
        return env_file[key_name].strip()
    return default_val


def mask_api_key(key: str) -> str:
    """Mask API key for secure display on the dashboard UI (e.g. 7334...76d2)."""
    if not key or not str(key).strip():
        return ""
    k = str(key).strip()
    if len(k) <= 8:
        return "****"
    return f"{k[:4]}...{k[-4:]}"


def http_get(url: str, timeout: int = 5) -> str:
    """Execute standard HTTP GET with gzip decompression."""
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


class NormalizedQuote:
    """Standardized market quote across all providers."""

    def __init__(
        self,
        symbol: str,
        price: float,
        bid: Optional[float] = None,
        ask: Optional[float] = None,
        open_p: Optional[float] = None,
        high_p: Optional[float] = None,
        low_p: Optional[float] = None,
        close_p: Optional[float] = None,
        previous_close: Optional[float] = None,
        change: Optional[float] = None,
        change_percent: Optional[float] = None,
        volume: Optional[float] = None,
        timestamp: Optional[int] = None,
        provider: str = "Twelve Data",
        provider_id: str = "twelvedata",
        data_status: str = "LIVE",
        is_failover: bool = False,
        asset_name: Optional[str] = None,
        asset_type: Optional[str] = None,
        spread_pips: Optional[float] = None,
        status: Optional[str] = None,
        datetime_utc: Optional[str] = None,
        open: Optional[float] = None,
        high: Optional[float] = None,
        low: Optional[float] = None,
        close: Optional[float] = None,
    ):
        canon = normalize_symbol(symbol)
        meta = SUPPORTED_ASSETS.get(canon, SUPPORTED_ASSETS["XAU/USD"])
        self.symbol = canon
        self.asset_name = asset_name or meta["name"]
        self.asset_type = asset_type or meta["market_type"]
        self.decimals = meta["decimals"]
        self.pip_unit = meta["pip_unit"]

        self.price = round(float(price), self.decimals) if price is not None else 0.0
        self.bid = round(float(bid), self.decimals) if bid is not None else self.price
        self.ask = round(float(ask), self.decimals) if ask is not None else self.price
        self.open = round(float(open if open is not None else open_p), self.decimals) if (open is not None or open_p is not None) else None
        self.high = round(float(high if high is not None else high_p), self.decimals) if (high is not None or high_p is not None) else None
        self.low = round(float(low if low is not None else low_p), self.decimals) if (low is not None or low_p is not None) else None
        self.close = round(float(close if close is not None else close_p), self.decimals) if (close is not None or close_p is not None) else self.price
        self.previous_close = round(float(previous_close), self.decimals) if previous_close is not None else None

        # Compute change and % change if missing
        if change is not None:
            self.change = round(float(change), self.decimals)
        elif self.previous_close and self.previous_close > 0:
            self.change = round(self.price - self.previous_close, self.decimals)
        else:
            self.change = 0.0

        if change_percent is not None:
            self.change_percent = round(float(change_percent), 2)
        elif self.previous_close and self.previous_close > 0:
            self.change_percent = round(((self.price - self.previous_close) / self.previous_close) * 100.0, 2)
        else:
            self.change_percent = 0.0

        if spread_pips is not None:
            self.spread_pips = float(spread_pips)
        else:
            spread_raw = max(0.0, self.ask - self.bid)
            self.spread_pips = round(spread_raw / self.pip_unit, 1)
        self.volume = float(volume) if volume is not None else None

        self.timestamp = int(timestamp or time.time())
        self.datetime_utc = datetime_utc or datetime.fromtimestamp(self.timestamp, tz=timezone.utc).isoformat()
        self.provider = provider
        self.provider_id = provider_id
        self.data_status = status or data_status
        self.is_failover = is_failover

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "asset_name": self.asset_name,
            "asset_type": self.asset_type,
            "price": self.price,
            "bid": self.bid,
            "ask": self.ask,
            "mid": self.price,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "previous_close": self.previous_close,
            "change": self.change,
            "change_percent": self.change_percent,
            "spread_pips": self.spread_pips,
            "volume": self.volume,
            "timestamp": self.timestamp,
            "datetime_utc": self.datetime_utc,
            "provider": self.provider,
            "provider_id": self.provider_id,
            "data_status": self.data_status,
            "data_freshness": f"{self.data_status} (0s ago)" if self.data_status == "LIVE" else self.data_status,
            "is_failover": self.is_failover,
            "supported_assets": ["XAU/USD", "BTC/USD"],
        }


class NormalizedCandle:
    """Standardized OHLCV candle representation across all providers."""
    def __init__(
        self,
        timestamp: int,
        datetime_utc: str,
        open: float,
        high: float,
        low: float,
        close: float,
        volume: Optional[float] = None,
    ):
        self.timestamp = int(timestamp)
        self.datetime_utc = datetime_utc
        self.open = float(open)
        self.high = float(high)
        self.low = float(low)
        self.close = float(close)
        self.volume = float(volume) if volume is not None else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "time": self.timestamp,
            "datetime_utc": self.datetime_utc,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


# ==============================================================================
# PROVIDER ADAPTERS
# ==============================================================================

class TwelveDataProvider:
    """Twelve Data API Adapter (Primary for Gold and Bitcoin)."""
    id = "twelvedata"
    name = "Twelve Data"
    supported_assets = ["XAU/USD", "BTC/USD"]
    supported_timeframes = ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"]

    def __init__(self, api_key: Optional[str] = None, enabled: bool = True):
        if api_key is None:
            self.api_key = get_env_or_config("TWELVEDATA_API_KEY", DEFAULT_KEYS["twelvedata"])
        else:
            self.api_key = api_key
        self.enabled = enabled

    def get_masked_key(self) -> str:
        return mask_api_key(self.api_key)

    def fetch_quote(self, symbol: str, timeout: int = 5) -> Optional[NormalizedQuote]:
        canon = normalize_symbol(symbol)
        if canon not in self.supported_assets or not self.enabled or not self.api_key:
            return None
        url = f"https://api.twelvedata.com/quote?symbol={canon}&apikey={self.api_key}"
        raw = http_get(url, timeout=timeout)
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("status") == "error":
            raise ValueError(data.get("message", "Twelve Data quote error"))

        last_p = float(data.get("close") or data.get("price") or 0.0)
        bid = float(data.get("bid") or last_p)
        ask = float(data.get("ask") or last_p)
        open_p = float(data["open"]) if data.get("open") is not None else None
        high_p = float(data["high"]) if data.get("high") is not None else None
        low_p = float(data["low"]) if data.get("low") is not None else None
        prev_close = float(data["previous_close"]) if data.get("previous_close") is not None else None
        change = float(data["change"]) if data.get("change") is not None else None
        pct_change = float(data["percent_change"]) if data.get("percent_change") is not None else None
        vol = float(data["volume"]) if data.get("volume") is not None else None
        ts = int(data.get("timestamp") or time.time())

        return NormalizedQuote(
            symbol=canon,
            price=last_p,
            bid=bid,
            ask=ask,
            open_p=open_p,
            high_p=high_p,
            low_p=low_p,
            close_p=last_p,
            previous_close=prev_close,
            change=change,
            change_percent=pct_change,
            volume=vol,
            timestamp=ts,
            provider="Twelve Data",
            provider_id=self.id,
            data_status="LIVE",
        )

    def fetch_candles(self, symbol: str, interval: str = "5m", limit: int = 80, timeout: int = 6) -> List[Dict[str, Any]]:
        canon = normalize_symbol(symbol)
        if canon not in self.supported_assets or not self.enabled or not self.api_key:
            return []

        td_map = {
            "1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min",
            "1h": "1h", "4h": "4h", "1d": "1day", "1w": "1week"
        }
        td_interval = td_map.get(interval, "5min")
        url = f"https://api.twelvedata.com/time_series?symbol={canon}&interval={td_interval}&outputsize={limit}&apikey={self.api_key}"
        raw = http_get(url, timeout=timeout)
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("status") == "error":
            raise ValueError(data.get("message", "Twelve Data candle error"))

        values = data.get("values", [])
        candles = []
        decimals = 2
        for v in reversed(values):
            try:
                dt_str = v.get("datetime", "").replace(" ", "T")
                if "T" in dt_str and len(dt_str) == 16:
                    dt_str += ":00"
                dt = datetime.fromisoformat(dt_str).replace(tzinfo=timezone.utc)
                ts = int(dt.timestamp())
                candles.append({
                    "timestamp": ts,
                    "datetime_utc": dt.isoformat(),
                    "time": dt.strftime("%H:%M") if "d" not in interval and "w" not in interval else dt.strftime("%Y-%m-%d"),
                    "open": round(float(v["open"]), decimals),
                    "high": round(float(v["high"]), decimals),
                    "low": round(float(v["low"]), decimals),
                    "close": round(float(v["close"]), decimals),
                    "volume": int(float(v.get("volume") or 0.0)),
                })
            except Exception:
                continue
        return candles

    def test_connection(self) -> Dict[str, Any]:
        t0 = time.time()
        try:
            q = self.fetch_quote("XAU/USD", timeout=5)
            lat = int((time.time() - t0) * 1000)
            if q and q.price > 0:
                return {"success": True, "latency_ms": lat, "message": f"Connected (XAU/USD ${q.price:.2f})", "price": q.price}
            return {"success": False, "latency_ms": lat, "message": "No price returned"}
        except Exception as e:
            lat = int((time.time() - t0) * 1000)
            return {"success": False, "latency_ms": lat, "message": str(e)}


class FinnhubProvider:
    """Finnhub API Adapter (Backup Provider for Bitcoin & Market Quotes)."""
    id = "finnhub"
    name = "Finnhub"
    supported_assets = ["BTC/USD"]  # Finnhub free tier natively supports Crypto quotes
    supported_timeframes = ["1m", "5m", "15m", "30m", "1h", "1d"]

    def __init__(self, api_key: Optional[str] = None, enabled: bool = True):
        if api_key is None:
            self.api_key = get_env_or_config("FINNHUB_API_KEY", DEFAULT_KEYS["finnhub"])
        else:
            self.api_key = api_key
        self.enabled = enabled

    def get_masked_key(self) -> str:
        return mask_api_key(self.api_key)

    def fetch_quote(self, symbol: str, timeout: int = 5) -> Optional[NormalizedQuote]:
        canon = normalize_symbol(symbol)
        if canon != "BTC/USD" or not self.enabled or not self.api_key:
            return None

        # Finnhub Crypto quote via Binance or Coinbase benchmark
        for ticker in ("BINANCE:BTCUSDT", "COINBASE:BTC-USD"):
            try:
                url = f"https://finnhub.io/api/v1/quote?symbol={ticker}&token={self.api_key}"
                raw = http_get(url, timeout=timeout)
                data = json.loads(raw)
                if not isinstance(data, dict) or not data.get("c"):
                    continue

                c = float(data["c"])
                d = float(data.get("d") or 0.0)
                dp = float(data.get("dp") or 0.0)
                h = float(data.get("h") or c)
                l = float(data.get("l") or c)
                o = float(data.get("o") or c)
                pc = float(data.get("pc") or (c - d))
                ts = int(data.get("t") or time.time())

                return NormalizedQuote(
                    symbol="BTC/USD",
                    price=c,
                    bid=c - 2.5,
                    ask=c + 2.5,
                    open_p=o,
                    high_p=h,
                    low_p=l,
                    close_p=c,
                    previous_close=pc,
                    change=d,
                    change_percent=dp,
                    volume=None,
                    timestamp=ts,
                    provider="Finnhub (Backup)",
                    provider_id=self.id,
                    data_status="LIVE",
                    is_failover=True,
                )
            except Exception:
                continue
        raise ValueError("Finnhub quote request failed")

    def fetch_candles(self, symbol: str, interval: str = "5m", limit: int = 80, timeout: int = 6) -> List[Dict[str, Any]]:
        canon = normalize_symbol(symbol)
        if canon != "BTC/USD" or not self.enabled or not self.api_key:
            return []

        res_map = {"1m": "1", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "1d": "D"}
        res = res_map.get(interval, "5")
        now_ts = int(time.time())
        window_seconds = 80 * (300 if interval == "5m" else 3600)
        from_ts = now_ts - window_seconds

        for ticker in ("BINANCE:BTCUSDT", "COINBASE:BTC-USD"):
            try:
                url = f"https://finnhub.io/api/v1/crypto/candle?symbol={ticker}&resolution={res}&from={from_ts}&to={now_ts}&token={self.api_key}"
                raw = http_get(url, timeout=timeout)
                data = json.loads(raw)
                if data.get("s") != "ok" or not data.get("c"):
                    continue

                candles = []
                c_list = data["c"]
                o_list = data["o"]
                h_list = data["h"]
                l_list = data["l"]
                v_list = data.get("v", [0] * len(c_list))
                t_list = data["t"]

                for i in range(len(t_list)):
                    ts = t_list[i]
                    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                    candles.append({
                        "timestamp": ts,
                        "datetime_utc": dt.isoformat(),
                        "time": dt.strftime("%H:%M") if res != "D" else dt.strftime("%Y-%m-%d"),
                        "open": round(float(o_list[i]), 2),
                        "high": round(float(h_list[i]), 2),
                        "low": round(float(l_list[i]), 2),
                        "close": round(float(c_list[i]), 2),
                        "volume": int(float(v_list[i])),
                    })
                if candles:
                    return candles[-limit:]
            except Exception:
                continue
        return []

    def test_connection(self) -> Dict[str, Any]:
        t0 = time.time()
        try:
            q = self.fetch_quote("BTC/USD", timeout=5)
            lat = int((time.time() - t0) * 1000)
            if q and q.price > 0:
                return {"success": True, "latency_ms": lat, "message": f"Connected (BTC/USD ${q.price:.2f})", "price": q.price}
            return {"success": False, "latency_ms": lat, "message": "No price returned"}
        except Exception as e:
            lat = int((time.time() - t0) * 1000)
            return {"success": False, "latency_ms": lat, "message": str(e)}


class AlphaVantageProvider:
    """Alpha Vantage API Adapter (Historical & Additional Backup)."""
    id = "alphavantage"
    name = "Alpha Vantage"
    supported_assets = ["BTC/USD", "XAU/USD"]
    supported_timeframes = ["1d"]

    def __init__(self, api_key: Optional[str] = None, enabled: bool = True):
        if api_key is None:
            self.api_key = get_env_or_config("ALPHAVANTAGE_API_KEY", DEFAULT_KEYS["alphavantage"])
        else:
            self.api_key = api_key
        self.enabled = enabled

    def get_masked_key(self) -> str:
        return mask_api_key(self.api_key)

    def fetch_quote(self, symbol: str, timeout: int = 6) -> Optional[NormalizedQuote]:
        canon = normalize_symbol(symbol)
        if canon not in self.supported_assets or not self.enabled or not self.api_key:
            return None

        from_curr = "BTC" if canon == "BTC/USD" else "XAU"
        url = f"https://www.alphavantage.co/query?function=CURRENCY_EXCHANGE_RATE&from_currency={from_curr}&to_currency=USD&apikey={self.api_key}"
        raw = http_get(url, timeout=timeout)
        data = json.loads(raw)
        rate_obj = data.get("Realtime Currency Exchange Rate", {})
        if not rate_obj or "5. Exchange Rate" not in rate_obj:
            if "Information" in data or "Note" in data:
                raise ValueError("Alpha Vantage API rate limit exceeded (25 requests/day)")
            raise ValueError("Alpha Vantage currency exchange rate error")

        p = float(rate_obj["5. Exchange Rate"])
        bid = float(rate_obj.get("8. Bid Price") or p)
        ask = float(rate_obj.get("9. Ask Price") or p)

        return NormalizedQuote(
            symbol=canon,
            price=p,
            bid=bid,
            ask=ask,
            close_p=p,
            provider="Alpha Vantage (Backup)",
            provider_id=self.id,
            data_status="LIVE",
            is_failover=True,
        )

    def fetch_candles(self, symbol: str, interval: str = "1d", limit: int = 80, timeout: int = 7) -> List[Dict[str, Any]]:
        canon = normalize_symbol(symbol)
        if canon != "BTC/USD" or not self.enabled or not self.api_key:
            return []

        url = f"https://www.alphavantage.co/query?function=DIGITAL_CURRENCY_DAILY&symbol=BTC&market=USD&apikey={self.api_key}"
        raw = http_get(url, timeout=timeout)
        data = json.loads(raw)
        ts_data = data.get("Time Series (Digital Currency Daily)", {})
        if not ts_data:
            return []

        candles = []
        for date_str in sorted(ts_data.keys()):
            row = ts_data[date_str]
            try:
                dt = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
                candles.append({
                    "timestamp": int(dt.timestamp()),
                    "datetime_utc": dt.isoformat(),
                    "time": date_str,
                    "open": round(float(row["1. open"]), 2),
                    "high": round(float(row["2. high"]), 2),
                    "low": round(float(row["3. low"]), 2),
                    "close": round(float(row["4. close"]), 2),
                    "volume": int(float(row.get("5. volume", 0))),
                })
            except Exception:
                continue
        return candles[-limit:]

    def test_connection(self) -> Dict[str, Any]:
        t0 = time.time()
        try:
            q = self.fetch_quote("BTC/USD", timeout=6)
            lat = int((time.time() - t0) * 1000)
            if q and q.price > 0:
                return {"success": True, "latency_ms": lat, "message": f"Connected (BTC/USD ${q.price:.2f})", "price": q.price}
            return {"success": False, "latency_ms": lat, "message": "No rate returned"}
        except Exception as e:
            lat = int((time.time() - t0) * 1000)
            return {"success": False, "latency_ms": lat, "message": str(e)}


class EODHDProvider:
    """EODHD API Adapter (Historical EOD for Gold & BTC; Real-Time for BTC)."""
    id = "eodhd"
    name = "EODHD"
    supported_assets = ["BTC/USD", "XAU/USD"]
    supported_timeframes = ["1d"]

    def __init__(self, api_key: Optional[str] = None, enabled: bool = True):
        if api_key is None:
            self.api_key = get_env_or_config("EODHD_API_KEY", DEFAULT_KEYS["eodhd"])
        else:
            self.api_key = api_key
        self.enabled = enabled

    def get_masked_key(self) -> str:
        return mask_api_key(self.api_key)

    def fetch_quote(self, symbol: str, timeout: int = 5) -> Optional[NormalizedQuote]:
        canon = normalize_symbol(symbol)
        if not self.enabled or not self.api_key:
            return None

        if canon == "BTC/USD":
            url = f"https://eodhd.com/api/real-time/BTC-USD.CC?api_token={self.api_key}&fmt=json"
            raw = http_get(url, timeout=timeout)
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get("close") in ("NA", None):
                raise ValueError("EODHD real-time quote unavailable")

            close_p = float(data["close"])
            open_p = float(data["open"]) if data.get("open") != "NA" else close_p
            high_p = float(data["high"]) if data.get("high") != "NA" else close_p
            low_p = float(data["low"]) if data.get("low") != "NA" else close_p
            ts = int(data["timestamp"]) if data.get("timestamp") != "NA" else int(time.time())

            return NormalizedQuote(
                symbol="BTC/USD",
                price=close_p,
                bid=close_p - 2.5,
                ask=close_p + 2.5,
                open_p=open_p,
                high_p=high_p,
                low_p=low_p,
                close_p=close_p,
                volume=float(data["volume"]) if data.get("volume") != "NA" else None,
                timestamp=ts,
                provider="EODHD (Real-Time Backup)",
                provider_id=self.id,
                data_status="LIVE",
                is_failover=True,
            )

        elif canon == "XAU/USD":
            # EODHD returns end-of-day quote for Forex on free API
            candles = self.fetch_candles("XAU/USD", interval="1d", limit=2, timeout=timeout)
            if not candles:
                raise ValueError("EODHD Gold quote unavailable")
            last_c = candles[-1]
            prev_c = candles[-2] if len(candles) >= 2 else last_c
            return NormalizedQuote(
                symbol="XAU/USD",
                price=last_c["close"],
                bid=last_c["close"] - 0.25,
                ask=last_c["close"] + 0.25,
                open_p=last_c["open"],
                high_p=last_c["high"],
                low_p=last_c["low"],
                close_p=last_c["close"],
                previous_close=prev_c["close"],
                timestamp=last_c["timestamp"],
                provider="EODHD (EOD Backup)",
                provider_id=self.id,
                data_status="EOD",
                is_failover=True,
            )
        return None

    def fetch_candles(self, symbol: str, interval: str = "1d", limit: int = 80, timeout: int = 6) -> List[Dict[str, Any]]:
        canon = normalize_symbol(symbol)
        if not self.enabled or not self.api_key:
            return []

        ticker = "BTC-USD.CC" if canon == "BTC/USD" else "XAUUSD.FOREX"
        url = f"https://eodhd.com/api/eod/{ticker}?api_token={self.api_key}&fmt=json&limit={limit}"
        raw = http_get(url, timeout=timeout)
        data = json.loads(raw)
        if not isinstance(data, list) or not data:
            return []

        candles = []
        for row in data:
            try:
                date_str = row.get("date", "")
                dt = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
                candles.append({
                    "timestamp": int(dt.timestamp()),
                    "datetime_utc": dt.isoformat(),
                    "time": date_str,
                    "open": round(float(row["open"]), 2),
                    "high": round(float(row["high"]), 2),
                    "low": round(float(row["low"]), 2),
                    "close": round(float(row["close"]), 2),
                    "volume": int(float(row.get("volume") or 0)),
                })
            except Exception:
                continue
        return candles[-limit:]

    def test_connection(self) -> Dict[str, Any]:
        t0 = time.time()
        try:
            q = self.fetch_quote("BTC/USD", timeout=5)
            lat = int((time.time() - t0) * 1000)
            if q and q.price > 0:
                return {"success": True, "latency_ms": lat, "message": f"Connected (BTC/USD ${q.price:.2f})", "price": q.price}
            return {"success": False, "latency_ms": lat, "message": "No price returned"}
        except Exception as e:
            lat = int((time.time() - t0) * 1000)
            return {"success": False, "latency_ms": lat, "message": str(e)}


# ==============================================================================
# CENTRALIZED MARKET DATA & FAILOVER MANAGER
# ==============================================================================

class MarketDataManager:
    """
    Centralized Controller managing all 4 API providers, failover routing,
    caching, rate limit protection, and live card aggregation.
    """

    SUPPORTED_SYMBOLS = ["XAU/USD", "BTC/USD"]
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(MarketDataManager, cls).__new__(cls)
            cls._instance._init_manager()
        return cls._instance

    def _init_manager(self):
        self._quote_cache: Dict[str, Tuple[float, NormalizedQuote]] = {}
        self._chart_cache: Dict[str, Tuple[float, List[Dict[str, Any]], str]] = {}
        self._provider_status: Dict[str, Dict[str, Any]] = {}
        self._load_config()
        self._init_providers()

    def _load_config(self):
        """Load provider preferences and API keys from disk / env."""
        self.config = {
            "providers": {
                "twelvedata": {"enabled": True, "api_key": get_env_or_config("TWELVEDATA_API_KEY", DEFAULT_KEYS["twelvedata"])},
                "finnhub": {"enabled": True, "api_key": get_env_or_config("FINNHUB_API_KEY", DEFAULT_KEYS["finnhub"])},
                "alphavantage": {"enabled": True, "api_key": get_env_or_config("ALPHAVANTAGE_API_KEY", DEFAULT_KEYS["alphavantage"])},
                "eodhd": {"enabled": True, "api_key": get_env_or_config("EODHD_API_KEY", DEFAULT_KEYS["eodhd"])},
            },
            "preferences": {
                "XAU/USD": {"primary": "twelvedata", "backups": ["eodhd", "alphavantage", "finnhub"]},
                "BTC/USD": {"primary": "twelvedata", "backups": ["finnhub", "eodhd", "alphavantage"]},
            },
        }

        # Check legacy twelvedata_config.json
        if os.path.exists(LEGACY_TD_CONFIG_PATH):
            try:
                with open(LEGACY_TD_CONFIG_PATH, "r", encoding="utf-8") as fh:
                    td_cfg = json.load(fh)
                    if td_cfg.get("api_key"):
                        self.config["providers"]["twelvedata"]["api_key"] = td_cfg["api_key"]
                    if "enabled" in td_cfg:
                        self.config["providers"]["twelvedata"]["enabled"] = bool(td_cfg["enabled"])
            except Exception:
                pass

        # Check market_api_config.json
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                    disk_cfg = json.load(fh)
                    if "providers" in disk_cfg:
                        for p_id, p_val in disk_cfg["providers"].items():
                            if p_id in self.config["providers"]:
                                self.config["providers"][p_id].update(p_val)
                    if "preferences" in disk_cfg:
                        self.config["preferences"].update(disk_cfg["preferences"])
            except Exception:
                pass

    def save_config(self):
        """Persist current configuration to disk."""
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
                json.dump(self.config, fh, indent=2)
            # Sync Twelve Data legacy config
            with open(LEGACY_TD_CONFIG_PATH, "w", encoding="utf-8") as fh:
                json.dump({
                    "api_key": self.config["providers"]["twelvedata"]["api_key"],
                    "enabled": self.config["providers"]["twelvedata"]["enabled"],
                    "provider": "twelvedata",
                }, fh, indent=2)
        except Exception:
            pass

    def _init_providers(self):
        self.providers = {
            "twelvedata": TwelveDataProvider(
                api_key=self.config["providers"]["twelvedata"]["api_key"],
                enabled=self.config["providers"]["twelvedata"]["enabled"],
            ),
            "finnhub": FinnhubProvider(
                api_key=self.config["providers"]["finnhub"]["api_key"],
                enabled=self.config["providers"]["finnhub"]["enabled"],
            ),
            "alphavantage": AlphaVantageProvider(
                api_key=self.config["providers"]["alphavantage"]["api_key"],
                enabled=self.config["providers"]["alphavantage"]["enabled"],
            ),
            "eodhd": EODHDProvider(
                api_key=self.config["providers"]["eodhd"]["api_key"],
                enabled=self.config["providers"]["eodhd"]["enabled"],
            ),
        }

        for p_id, p_obj in self.providers.items():
            self._provider_status[p_id] = {
                "id": p_id,
                "name": p_obj.name,
                "enabled": p_obj.enabled,
                "status": "ONLINE" if p_obj.enabled else "DISABLED",
                "last_success": None,
                "last_error": None,
                "consecutive_failures": 0,
            }

    def get_quote(self, symbol: str, force_refresh: bool = False, timeout: int = 5) -> Optional[NormalizedQuote]:
        """Fetch quote with multi-API automatic failover."""
        canon = normalize_symbol(symbol)
        if canon not in self.SUPPORTED_SYMBOLS:
            return None

        now_t = time.time()
        cached = self._quote_cache.get(canon)
        if not force_refresh and cached and (now_t - cached[0] < 10):
            return cached[1]

        pref = self.config["preferences"].get(canon, {})
        primary_id = pref.get("primary", "twelvedata")
        backups = pref.get("backups", ["finnhub", "eodhd", "alphavantage"])
        candidate_ids = [primary_id] + [b for b in backups if b != primary_id]

        last_err = None
        for p_id in candidate_ids:
            prov = self.providers.get(p_id)
            if not prov or not prov.enabled or canon not in prov.supported_assets:
                continue

            try:
                q = prov.fetch_quote(canon, timeout=timeout)
                if q and q.price > 0:
                    self._quote_cache[canon] = (now_t, q)
                    self._provider_status[p_id]["status"] = "ONLINE"
                    self._provider_status[p_id]["last_success"] = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
                    self._provider_status[p_id]["consecutive_failures"] = 0
                    return q
            except Exception as e:
                last_err = e
                self._provider_status[p_id]["last_error"] = str(e)
                self._provider_status[p_id]["consecutive_failures"] += 1
                if self._provider_status[p_id]["consecutive_failures"] >= 3:
                    self._provider_status[p_id]["status"] = "DEGRADED"

        # Return cached quote if all live providers fail
        if cached:
            fallback = cached[1]
            fallback.data_status = "CACHED"
            fallback.is_failover = True
            return fallback

        raise RuntimeError(f"All market data providers failed for {canon}: {last_err}")

    def get_chart(
        self,
        symbol: str,
        interval: str = "5m",
        limit: int = 80,
        offline_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Fetch chart candle data with multi-provider failover."""
        canon = normalize_symbol(symbol)
        if canon not in self.SUPPORTED_SYMBOLS:
            return None

        if offline_data:
            # Bypass live fetch for test fixtures
            return {"symbol": canon, "candles": offline_data.get("candles", []), "candles_count": len(offline_data.get("candles", [])), "source": "Offline Fixture", "provider": "Offline Fixture", "is_failover": False}

        now_t = time.time()
        cache_key = f"{canon}_{interval}"
        cached = self._chart_cache.get(cache_key)
        if cached and (now_t - cached[0] < 60):
            return {
                "symbol": canon,
                "interval": interval,
                "candles": cached[1],
                "candles_count": len(cached[1]),
                "source": cached[2],
                "provider": cached[2],
                "is_failover": False,
            }

        pref = self.config["preferences"].get(canon, {})
        primary_id = pref.get("primary", "twelvedata")
        backups = pref.get("backups", ["finnhub", "eodhd", "alphavantage"])
        candidate_ids = [primary_id] + [b for b in backups if b != primary_id]

        for p_id in candidate_ids:
            prov = self.providers.get(p_id)
            if not prov or not prov.enabled or canon not in prov.supported_assets:
                continue

            # Check timeframe compatibility
            if interval not in prov.supported_timeframes and interval not in ("1d", "1w"):
                continue

            try:
                candles = prov.fetch_candles(canon, interval=interval, limit=limit)
                if candles and len(candles) >= 10:
                    source_label = f"{prov.name} Real-Time" if p_id == primary_id else f"{prov.name} (Failover)"
                    self._chart_cache[cache_key] = (now_t, candles, source_label)
                    self._provider_status[p_id]["last_success"] = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
                    return {
                        "symbol": canon,
                        "interval": interval,
                        "candles": candles,
                        "candles_count": len(candles),
                        "source": source_label,
                        "provider": prov.name,
                        "is_failover": p_id != primary_id,
                    }
            except Exception as e:
                self._provider_status[p_id]["last_error"] = str(e)

        if cached:
            return {
                "symbol": canon,
                "interval": interval,
                "candles": cached[1],
                "candles_count": len(cached[1]),
                "source": f"{cached[2]} (Cached)",
                "provider": cached[2],
                "is_failover": False,
            }

        return {"symbol": canon, "interval": interval, "candles": [], "candles_count": 0, "source": "Unavailable", "provider": "Unavailable", "is_failover": False}

    def get_market_cards(self) -> Dict[str, Any]:
        """Retrieve live data for ONLY the two market cards: Gold and Bitcoin."""
        cards = {}
        for sym in ("XAU/USD", "BTC/USD"):
            try:
                q = self.get_quote(sym)
                cards[sym] = q.to_dict()
            except Exception as e:
                meta = SUPPORTED_ASSETS[sym]
                cards[sym] = {
                    "symbol": sym,
                    "asset_name": meta["name"],
                    "asset_type": meta["market_type"],
                    "price": None,
                    "change": None,
                    "change_percent": None,
                    "high": None,
                    "low": None,
                    "provider": "Connecting...",
                    "data_status": "OFFLINE",
                    "error": str(e),
                }
        return cards

    def get_providers_status(self) -> List[Dict[str, Any]]:
        """Return live health and configuration metadata for all 4 providers."""
        res = []
        for p_id in ("twelvedata", "finnhub", "alphavantage", "eodhd"):
            prov = self.providers[p_id]
            st = self._provider_status.get(p_id, {})
            pref_gold = self.config["preferences"]["XAU/USD"]
            pref_btc = self.config["preferences"]["BTC/USD"]

            role_parts = []
            if pref_gold.get("primary") == p_id:
                role_parts.append("Primary Gold")
            elif p_id in pref_gold.get("backups", []):
                role_parts.append("Backup Gold")

            if pref_btc.get("primary") == p_id:
                role_parts.append("Primary BTC")
            elif p_id in pref_btc.get("backups", []):
                role_parts.append("Backup BTC")

            role_label = " / ".join(role_parts) if role_parts else "Standby"

            res.append({
                "id": p_id,
                "name": prov.name,
                "enabled": prov.enabled,
                "status": st.get("status", "ONLINE" if prov.enabled else "DISABLED"),
                "role": role_label,
                "is_primary_gold": pref_gold.get("primary") == p_id,
                "is_primary_btc": pref_btc.get("primary") == p_id,
                "masked_key": mask_api_key(prov.api_key),
                "api_key_masked": mask_api_key(prov.api_key),
                "supported_assets": prov.supported_assets,
                "supported_timeframes": prov.supported_timeframes,
                "last_success": st.get("last_success") or "Ready",
                "last_error": st.get("last_error"),
            })
        return res

    def test_provider(self, provider_id: str) -> Dict[str, Any]:
        """Execute interactive test connection."""
        prov = self.providers.get(provider_id)
        if not prov:
            return {"success": False, "provider": provider_id, "message": f"Unknown provider '{provider_id}'"}
        res = prov.test_connection()
        res["provider"] = provider_id
        if res.get("success"):
            self._provider_status[provider_id]["status"] = "ONLINE"
            self._provider_status[provider_id]["last_success"] = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        else:
            self._provider_status[provider_id]["last_error"] = res.get("message")
        return res

    def update_config(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Update provider toggles, preferences, or keys safely."""
        if not isinstance(payload, dict):
            raise ValueError("Payload must be a dictionary")

        if "providers" in payload and isinstance(payload["providers"], dict):
            for p_id, p_cfg in payload["providers"].items():
                if p_id in self.config["providers"]:
                    if "enabled" in p_cfg:
                        self.config["providers"][p_id]["enabled"] = bool(p_cfg["enabled"])
                    if "api_key" in p_cfg and p_cfg["api_key"].strip() and "..." not in p_cfg["api_key"]:
                        self.config["providers"][p_id]["api_key"] = p_cfg["api_key"].strip()

        if "preferences" in payload and isinstance(payload["preferences"], dict):
            for sym, pref in payload["preferences"].items():
                canon = normalize_symbol(sym)
                if canon in self.config["preferences"]:
                    if "primary" in pref and pref["primary"] in self.providers:
                        self.config["preferences"][canon]["primary"] = pref["primary"]
                    if "backups" in pref and isinstance(pref["backups"], list):
                        self.config["preferences"][canon]["backups"] = [b for b in pref["backups"] if b in self.providers]

        self.save_config()
        self._init_providers()
        # Invalidate caches to refresh with new settings
        self._quote_cache.clear()
        self._chart_cache.clear()
        return {"success": True, "message": "Market API configuration updated successfully"}


# Global singleton instance
market_manager = MarketDataManager()
