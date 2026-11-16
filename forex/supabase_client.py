"""Supabase Cloud Database Client for Rushi The Trader Observation Terminal.

Provides seamless zero-dependency PostgreSQL persistence for:
1. 1-Device License Links & Device Bindings (forex_licenses)
2. Automatic Signal Engine - Trade Tracker (forex_journal / signal trades)
3. Paper Observation Journal - Simulated Positions (forex_journal)

Standard library only (urllib.request, json, os, threading).
Compatible with Python 3.8+ and Vercel serverless environment.
Zero em dashes in this file.
"""

import os
import json
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone
import threading
from typing import Dict, Any, List, Optional

SUPABASE_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "supabase_config.json")
TMP_SUPABASE_CONFIG_FILE = "/tmp/forex_supabase_config.json"

_LOCK = threading.Lock()
_CONFIG_CACHE: Optional[Dict[str, Any]] = None

DEFAULT_SQL_SCHEMA = """-- Supabase SQL Schema for Rushi The Trader Terminal
-- Run this in your Supabase SQL Editor (Dashboard -> SQL Editor -> New Query)

-- 1. Table for 1-Device License Links & Device Bindings
CREATE TABLE IF NOT EXISTS forex_licenses (
    key TEXT PRIMARY KEY,
    client_name TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    bound_device_id TEXT,
    bound_hw_hash TEXT,
    bound_at TIMESTAMPTZ,
    device_model TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    note TEXT DEFAULT ''
);

-- 2. Table for Automatic Signal Engine Trades & Paper Observation Journal
CREATE TABLE IF NOT EXISTS forex_journal (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    simulated_fill NUMERIC NOT NULL,
    stop_price NUMERIC NOT NULL,
    target_price NUMERIC NOT NULL,
    status TEXT NOT NULL,
    pnl_usd NUMERIC DEFAULT 0,
    timestamp TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    closed_at TEXT,
    exit_price NUMERIC,
    confluence_grade TEXT,
    notes TEXT DEFAULT '',
    is_auto BOOLEAN DEFAULT FALSE
);

-- Enable Row Level Security (RLS) & Allow Service/Anon Access
ALTER TABLE forex_licenses ENABLE ROW LEVEL SECURITY;
ALTER TABLE forex_journal ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'forex_licenses' AND policyname = 'Allow public read-write for terminal'
    ) THEN
        CREATE POLICY "Allow public read-write for terminal" ON forex_licenses
            FOR ALL USING (true) WITH CHECK (true);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'forex_journal' AND policyname = 'Allow public read-write for journal'
    ) THEN
        CREATE POLICY "Allow public read-write for journal" ON forex_journal
            FOR ALL USING (true) WITH CHECK (true);
    END IF;
END $$;
"""


def get_default_config() -> Dict[str, Any]:
    """Default Supabase configuration from environment or project URL."""
    url = os.environ.get("SUPABASE_URL", "https://jyqumcnykzmsiodbokj.supabase.co").strip().rstrip("/")
    key = os.environ.get("SUPABASE_KEY", os.environ.get("SUPABASE_ANON_KEY", os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""))).strip()
    return {
        "url": url,
        "key": key,
        "enabled": bool(url and key),
        "updated_at": datetime.now(timezone.utc).isoformat()
    }


def load_supabase_config() -> Dict[str, Any]:
    """Loads Supabase configuration with priority: environment -> /tmp -> local file."""
    global _CONFIG_CACHE
    with _LOCK:
        if _CONFIG_CACHE is not None:
            return dict(_CONFIG_CACHE)

        # 1. Environment variables have top priority
        env_url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        env_key = os.environ.get("SUPABASE_KEY", os.environ.get("SUPABASE_ANON_KEY", os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""))).strip()
        if env_url and env_key:
            cfg = {
                "url": env_url,
                "key": env_key,
                "enabled": True,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            _CONFIG_CACHE = cfg
            return dict(cfg)

        # 2. Check local config file
        if os.path.exists(SUPABASE_CONFIG_FILE):
            try:
                with open(SUPABASE_CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and data.get("url"):
                        _CONFIG_CACHE = data
                        return dict(data)
            except Exception:
                pass

        # 3. Check /tmp on serverless environments
        if os.path.exists(TMP_SUPABASE_CONFIG_FILE):
            try:
                with open(TMP_SUPABASE_CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and data.get("url"):
                        _CONFIG_CACHE = data
                        return dict(data)
            except Exception:
                pass

        default_cfg = get_default_config()
        _CONFIG_CACHE = default_cfg
        return dict(default_cfg)


def save_supabase_config(url: str, key: str, enabled: bool = True) -> Dict[str, Any]:
    """Persists Supabase credentials safely to disk and memory."""
    global _CONFIG_CACHE
    clean_url = (url or "").strip().rstrip("/")
    clean_key = (key or "").strip()
    cfg = {
        "url": clean_url,
        "key": clean_key,
        "enabled": bool(enabled and clean_url and clean_key),
        "updated_at": datetime.now(timezone.utc).isoformat()
    }
    with _LOCK:
        _CONFIG_CACHE = dict(cfg)

        # Save to local file
        try:
            with open(SUPABASE_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass

        # Save to /tmp
        try:
            with open(TMP_SUPABASE_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass

    return dict(cfg)


def is_supabase_configured() -> bool:
    """Returns True if valid Supabase URL and Key are present and enabled."""
    cfg = load_supabase_config()
    return bool(cfg.get("enabled") and cfg.get("url") and cfg.get("key"))


def _request_supabase(
    endpoint: str,
    method: str = "GET",
    payload: Optional[Any] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 6.0
) -> Dict[str, Any]:
    """Internal HTTP helper using standard urllib.request."""
    cfg = load_supabase_config()
    url = cfg.get("url", "")
    key = cfg.get("key", "")
    if not url or not key:
        return {"success": False, "status_code": 0, "error": "Supabase credentials not configured"}

    full_url = f"{url}/rest/v1/{endpoint.lstrip('/')}"
    req_headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if headers:
        req_headers.update(headers)

    body_bytes = None
    if payload is not None:
        body_bytes = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(full_url, data=body_bytes, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            content = resp.read().decode("utf-8")
            data = None
            if content:
                try:
                    data = json.loads(content)
                except Exception:
                    data = content
            return {"success": 200 <= status < 300, "status_code": status, "data": data}
    except urllib.error.HTTPError as e:
        err_msg = ""
        try:
            err_msg = e.read().decode("utf-8")
        except Exception:
            err_msg = str(e)
        return {"success": False, "status_code": e.code, "error": err_msg}
    except Exception as e:
        return {"success": False, "status_code": 0, "error": str(e)}


def test_supabase_connection() -> Dict[str, Any]:
    """Tests connectivity to Supabase and verifies required tables exist."""
    if not is_supabase_configured():
        return {
            "success": False,
            "connected": False,
            "message": "Supabase credentials not configured. Please enter URL and Key."
        }

    # Test reading forex_licenses table
    res_lic = _request_supabase("forex_licenses?select=key&limit=1", method="GET")
    # Test reading forex_journal table
    res_jrn = _request_supabase("forex_journal?select=id&limit=1", method="GET")

    lic_table_ok = res_lic.get("success", False)
    jrn_table_ok = res_jrn.get("success", False)

    if lic_table_ok and jrn_table_ok:
        return {
            "success": True,
            "connected": True,
            "message": "Supabase Connection Successful! Tables forex_licenses and forex_journal are active.",
            "tables": {"forex_licenses": True, "forex_journal": True}
        }
    elif res_lic.get("status_code") == 404 or res_jrn.get("status_code") == 404:
        return {
            "success": False,
            "connected": True,
            "message": "Supabase Connected, but tables are missing. Please run the SQL schema in Supabase SQL Editor.",
            "tables": {"forex_licenses": lic_table_ok, "forex_journal": jrn_table_ok},
            "need_schema": True
        }
    else:
        err = res_lic.get("error") or res_jrn.get("error") or "Failed to connect to Supabase"
        return {
            "success": False,
            "connected": False,
            "message": f"Connection Error: {err}",
            "tables": {"forex_licenses": False, "forex_journal": False}
        }


# =====================================================================
# 1. 1-Device License Data Sync
# =====================================================================

def fetch_licenses_from_supabase() -> Optional[Dict[str, Any]]:
    """Fetches all licenses from Supabase forex_licenses table."""
    if not is_supabase_configured():
        return None
    res = _request_supabase("forex_licenses?select=*", method="GET")
    if not res.get("success"):
        return None

    rows = res.get("data")
    if not isinstance(rows, list):
        return None

    result = {}
    for row in rows:
        key = row.get("key")
        if key:
            result[key] = {
                "key": key,
                "client_name": row.get("client_name", "Client"),
                "created_at": row.get("created_at"),
                "expires_at": row.get("expires_at"),
                "bound_device_id": row.get("bound_device_id"),
                "bound_hw_hash": row.get("bound_hw_hash"),
                "bound_at": row.get("bound_at"),
                "device_model": row.get("device_model"),
                "is_active": row.get("is_active", True),
                "note": row.get("note", "")
            }
    return result


def upsert_license_to_supabase(item: Dict[str, Any]) -> bool:
    """Inserts or updates a license item in Supabase."""
    if not is_supabase_configured():
        return False
    payload = {
        "key": item.get("key"),
        "client_name": item.get("client_name", "Client"),
        "created_at": item.get("created_at"),
        "expires_at": item.get("expires_at"),
        "bound_device_id": item.get("bound_device_id"),
        "bound_hw_hash": item.get("bound_hw_hash"),
        "bound_at": item.get("bound_at"),
        "device_model": item.get("device_model"),
        "is_active": item.get("is_active", True),
        "note": item.get("note", "")
    }
    headers = {"Prefer": "resolution=merge-duplicates"}
    res = _request_supabase("forex_licenses", method="POST", payload=payload, headers=headers)
    return bool(res.get("success"))


def delete_license_from_supabase(key: str) -> bool:
    """Deletes a license from Supabase forex_licenses."""
    if not is_supabase_configured():
        return False
    param = urllib.parse.quote(key)
    res = _request_supabase(f"forex_licenses?key=eq.{param}", method="DELETE")
    return bool(res.get("success"))


# =====================================================================
# 2. Automatic Signal Trades & Paper Observation Journal Sync
# =====================================================================

def fetch_journal_from_supabase() -> Optional[List[Dict[str, Any]]]:
    """Fetches all journal and trade tracker entries from Supabase."""
    if not is_supabase_configured():
        return None
    res = _request_supabase("forex_journal?select=*&order=created_at.desc", method="GET")
    if not res.get("success"):
        return None

    rows = res.get("data")
    if not isinstance(rows, list):
        return None

    entries = []
    for r in rows:
        entries.append({
            "id": r.get("id"),
            "symbol": r.get("symbol"),
            "side": r.get("side"),
            "simulated_fill": float(r.get("simulated_fill", 0.0)),
            "stop_price": float(r.get("stop_price", 0.0)),
            "target_price": float(r.get("target_price", 0.0)),
            "status": r.get("status"),
            "pnl_usd": float(r.get("pnl_usd", 0.0)),
            "timestamp": r.get("timestamp") or r.get("created_at"),
            "closed_at": r.get("closed_at"),
            "exit_price": float(r["exit_price"]) if r.get("exit_price") is not None else None,
            "confluence_grade": r.get("confluence_grade", ""),
            "notes": r.get("notes", ""),
            "is_auto": r.get("is_auto", False)
        })
    return entries


def upsert_journal_to_supabase(entry: Dict[str, Any]) -> bool:
    """Inserts or updates a journal or auto-signal trade in Supabase."""
    if not is_supabase_configured():
        return False
    payload = {
        "id": entry.get("id"),
        "symbol": entry.get("symbol", "XAU/USD"),
        "side": entry.get("side", "BUY"),
        "simulated_fill": float(entry.get("simulated_fill", 0.0)),
        "stop_price": float(entry.get("stop_price", 0.0)),
        "target_price": float(entry.get("target_price", 0.0)),
        "status": entry.get("status", "OPEN"),
        "pnl_usd": float(entry.get("pnl_usd", 0.0)),
        "timestamp": entry.get("timestamp") or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "closed_at": entry.get("closed_at"),
        "exit_price": float(entry["exit_price"]) if entry.get("exit_price") is not None else None,
        "confluence_grade": entry.get("confluence_grade", ""),
        "notes": entry.get("notes", ""),
        "is_auto": bool(entry.get("is_auto", "AUTO" in str(entry.get("id", ""))))
    }
    headers = {"Prefer": "resolution=merge-duplicates"}
    res = _request_supabase("forex_journal", method="POST", payload=payload, headers=headers)
    return bool(res.get("success"))


def clear_journal_in_supabase() -> bool:
    """Clears all journal history from Supabase."""
    if not is_supabase_configured():
        return False
    res = _request_supabase("forex_journal?id=neq.empty_filter_placeholder", method="DELETE")
    return bool(res.get("success"))
