"""Single-Device Link Locking & License Management Engine for RUSHI THE TRADER Terminal.

Ensures that an access link/key can only be opened on ONE mobile device.
If forwarded to another mobile/device, access is strictly BLOCKED with Device Lock Warning.
Supports:
1. Master Admin Key (unrestricted access for terminal owner).
2. Unique Client License Keys with 1-Device Hardware Binding.
3. First-open automatic device registration.
4. Device mismatch rejection (prohibits link forwarding/piracy).
5. Admin reset (for clients who genuinely switch phones).

Standard library only, Python 3.8+ compatible.
Strictly zero em dashes allowed in code or strings.
"""

from typing import Dict, List, Optional, Any
from datetime import datetime, timezone, timedelta
import os
import json
import threading
import uuid
import re


LICENSES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "licenses.json")
MASTER_ADMIN_KEY = os.environ.get("RUSHI_ADMIN_KEY", "RUSHI-ADMIN-777")

_LOCK = threading.Lock()
_CACHE: Optional[Dict[str, Any]] = None


def _get_default_registry() -> Dict[str, Any]:
    """Default license registry with Master Admin and demo client."""
    now_iso = datetime.now(timezone.utc).isoformat()
    return {
        "master_admin_key": MASTER_ADMIN_KEY,
        "licenses": {
            "RUSHI-PRO-DEMO": {
                "key": "RUSHI-PRO-DEMO",
                "client_name": "Demo VIP Trader",
                "created_at": now_iso,
                "expires_at": None,
                "bound_device_id": None,
                "bound_hw_hash": None,
                "bound_at": None,
                "device_model": None,
                "is_active": True,
                "note": "Default 1-Device Demo Key",
            }
        },
    }


def load_licenses() -> Dict[str, Any]:
    """Loads licenses from disk or returns cached/default registry."""
    global _CACHE
    with _LOCK:
        if _CACHE is not None:
            return dict(_CACHE)

        loaded_data = None
        # 1. Base from packaged LICENSES_FILE
        if os.path.exists(LICENSES_FILE):
            try:
                with open(LICENSES_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "licenses" in data:
                        loaded_data = data
            except Exception:
                pass

        # 2. Serverless /tmp overlay
        tmp_file = "/tmp/forex_licenses.json"
        if os.path.exists(tmp_file):
            try:
                with open(tmp_file, "r", encoding="utf-8") as f:
                    tmp_data = json.load(f)
                    if isinstance(tmp_data, dict) and "licenses" in tmp_data:
                        if loaded_data is None:
                            loaded_data = tmp_data
                        else:
                            loaded_data["licenses"].update(tmp_data.get("licenses", {}))
            except Exception:
                pass

        # 3. Supabase Cloud Database overlay (if configured)
        try:
            from .supabase_client import is_supabase_configured, fetch_licenses_from_supabase
            if is_supabase_configured():
                supa_lics = fetch_licenses_from_supabase()
                if supa_lics:
                    if loaded_data is None:
                        loaded_data = _get_default_registry()
                    loaded_data["licenses"].update(supa_lics)
        except Exception:
            pass

        if loaded_data is not None:
            _CACHE = loaded_data
            return dict(loaded_data)

        default_reg = _get_default_registry()
        _CACHE = default_reg
        _save_licenses_unlocked(default_reg)
        return dict(default_reg)


def _save_licenses_unlocked(data: Dict[str, Any]) -> None:
    """Internal unlocked write to disk."""
    global _CACHE
    _CACHE = dict(data)
    # 1. Try writing to repo directory
    try:
        with open(LICENSES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass
    # 2. Also write to /tmp for serverless persistence
    try:
        with open("/tmp/forex_licenses.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def save_licenses(data: Dict[str, Any]) -> None:
    """Thread-safe persist licenses registry."""
    with _LOCK:
        _save_licenses_unlocked(data)
    try:
        from .supabase_client import is_supabase_configured, upsert_license_to_supabase
        if is_supabase_configured():
            for lic in data.get("licenses", {}).values():
                threading.Thread(target=upsert_license_to_supabase, args=(lic,), daemon=True).start()
    except Exception:
        pass


def sanitize_key(key: str) -> str:
    """Normalizes key string."""
    if not key:
        return ""
    return re.sub(r"[^A-Za-z0-9\-_]", "", key.strip()).upper()


def verify_device_license(
    key: str,
    device_id: str,
    hw_hash: Optional[str] = None,
    device_model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Verifies if the given device is authorized to access the terminal using the key.
    
    1. If key is Master Admin Key -> Full access granted, no device lock.
    2. If key is Unbound -> First mobile binds permanently to this key.
    3. If key is Bound ->
       - Matches registered device -> Access GRANTED.
       - Does NOT match registered device -> Access BLOCKED (Device Mismatch Warning).
    """
    clean_key = sanitize_key(key)
    if not clean_key:
        return {
            "status": "BLOCKED",
            "reason": "EMPTY_KEY",
            "message": "कृपया तुमचा VIP ॲक्सेस की (Access Key) टाका किंवा लिंक वापरा.",
            "is_admin": False,
        }

    # 1. Master Admin Key Check
    admin_key = sanitize_key(MASTER_ADMIN_KEY)
    if clean_key == admin_key:
        return {
            "status": "GRANTED",
            "is_admin": True,
            "key": clean_key,
            "client_name": "Rushi The Trader (Owner)",
            "message": "मास्टर ॲडमिन ॲक्सेस मंजूर! (Master Admin Unlocked)",
        }

    registry = load_licenses()
    licenses = registry.get("licenses", {})

    if clean_key not in licenses:
        return {
            "status": "BLOCKED",
            "reason": "INVALID_KEY",
            "message": "अवैध ॲक्सेस की (Invalid Key)! ही लिंक अस्तित्वात नाही.",
            "is_admin": False,
        }

    lic = licenses[clean_key]

    # 2. Expiration & Active Check
    if not lic.get("is_active", True):
        return {
            "status": "BLOCKED",
            "reason": "REVOKED",
            "message": "हा ॲक्सेस लिंक रद्द (Revoked) करण्यात आला आहे.",
            "is_admin": False,
        }

    expires_at = lic.get("expires_at")
    if expires_at:
        try:
            exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > exp_dt:
                return {
                    "status": "BLOCKED",
                    "reason": "EXPIRED",
                    "message": f"हा ॲक्सेस की कालबाह्य (Expired: {expires_at[:10]}) झाला आहे. कृपया नूतनीकरण करा.",
                    "is_admin": False,
                }
        except Exception:
            pass

    clean_device_id = (device_id or "").strip()
    if not clean_device_id:
        return {
            "status": "BLOCKED",
            "reason": "NO_DEVICE_ID",
            "message": "डिव्हाइस सिग्नेचर उपलब्ध नाही. कृपया ब्राउझरमध्ये कुकीज/स्टोरेज चालू करा.",
            "is_admin": False,
        }

    bound_id = lic.get("bound_device_id")
    bound_hw = lic.get("bound_hw_hash")

    # 3. First-time Open: Bind Key to this Mobile Device!
    if bound_id is None:
        now_iso = datetime.now(timezone.utc).isoformat()
        lic["bound_device_id"] = clean_device_id
        lic["bound_hw_hash"] = hw_hash or ""
        lic["bound_at"] = now_iso
        lic["device_model"] = device_model or "Mobile Device"
        save_licenses(registry)

        return {
            "status": "GRANTED",
            "is_admin": False,
            "first_activation": True,
            "key": clean_key,
            "client_name": lic.get("client_name", "Client"),
            "bound_at": now_iso,
            "device_model": lic.get("device_model"),
            "message": "डिव्हाइस यशस्वीरीत्या रजिस्टर झाले! आता ही लिंक फक्त याच मोबाईलवर चालेल.",
        }

    # 4. Device Verification: Compare with registered device!
    is_same_device = (bound_id == clean_device_id)
    # Optional hardware fallback in case cookies were cleared on same device
    if not is_same_device and hw_hash and bound_hw and hw_hash == bound_hw:
        # Same hardware signature -> restore device_id
        lic["bound_device_id"] = clean_device_id
        save_licenses(registry)
        is_same_device = True

    if is_same_device:
        return {
            "status": "GRANTED",
            "is_admin": False,
            "first_activation": False,
            "key": clean_key,
            "client_name": lic.get("client_name", "Client"),
            "bound_at": lic.get("bound_at"),
            "device_model": lic.get("device_model"),
            "message": "डिव्हाइस व्हेरिफाय झाले. ॲक्सेस मंजूर!",
        }

    # 5. DEVICE MISMATCH -> Strict Block!
    return {
        "status": "BLOCKED",
        "reason": "DEVICE_MISMATCH",
        "key": clean_key,
        "client_name": lic.get("client_name", "Client"),
        "bound_at": lic.get("bound_at"),
        "bound_device_model": lic.get("device_model", "Other Mobile Device"),
        "registered_device": lic.get("device_model", "Other Mobile Device"),
        "message": "डिव्हाइस लॉक: हा ॲक्सेस लिंक आधीच दुसऱ्या मोबाईलवर रजिस्टर झाला आहे! एका लिंकवर फक्त १ च मोबाईल वापरता येतो. तुम्ही ही लिंक दुसऱ्याला फॉरवर्ड केल्यास ती चालणार नाही. नवीन मोबाईलसाठी Rushi The Trader शी संपर्क साधा.",
        "is_admin": False,
    }


def create_license(
    client_name: str,
    duration_days: Optional[int] = None,
    custom_key: Optional[str] = None,
    note: str = "",
) -> Dict[str, Any]:
    """Generates a new 1-Device Access Key for a client."""
    registry = load_licenses()
    licenses = registry.setdefault("licenses", {})

    safe_name = re.sub(r"[^A-Za-z0-9]", "", client_name.strip()).upper() or "USER"
    if custom_key:
        new_key = sanitize_key(custom_key)
    else:
        rand_suffix = uuid.uuid4().hex[:6].upper()
        new_key = f"RUSHI-{safe_name[:6]}-{rand_suffix}"

    now_dt = datetime.now(timezone.utc)
    expires_at = (now_dt + timedelta(days=duration_days)).isoformat() if duration_days else None

    item = {
        "key": new_key,
        "client_name": client_name.strip() or "VIP Client",
        "created_at": now_dt.isoformat(),
        "expires_at": expires_at,
        "bound_device_id": None,
        "bound_hw_hash": None,
        "bound_at": None,
        "device_model": None,
        "is_active": True,
        "note": note,
    }
    licenses[new_key] = item
    save_licenses(registry)
    return item


def reset_license_device(key: str) -> Dict[str, Any]:
    """Resets the bound device for a key so client can bind to a new phone."""
    clean_key = sanitize_key(key)
    registry = load_licenses()
    licenses = registry.get("licenses", {})

    if clean_key not in licenses:
        return {"success": False, "error": "Key not found"}

    lic = licenses[clean_key]
    lic["bound_device_id"] = None
    lic["bound_hw_hash"] = None
    lic["bound_at"] = None
    lic["device_model"] = None
    save_licenses(registry)

    return {"success": True, "key": clean_key, "message": "डिव्हाइस लॉक यशस्वीरीत्या रिसेट झाला. आता ग्राहक नवीन मोबाईलवर उघडू शकतो."}


def compute_license_status(lic: Dict[str, Any]) -> str:
    """Computes ACTIVE, EXPIRED, or REVOKED status."""
    if not lic.get("is_active", True):
        return "REVOKED"
    expires_at = lic.get("expires_at")
    if expires_at:
        try:
            exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > exp_dt:
                return "EXPIRED"
        except Exception:
            pass
    return "ACTIVE"


def revoke_license(key: str) -> Dict[str, Any]:
    """Revokes an access key."""
    clean_key = sanitize_key(key)
    registry = load_licenses()
    licenses = registry.get("licenses", {})

    if clean_key in licenses:
        licenses[clean_key]["is_active"] = False
        save_licenses(registry)
        return {"success": True, "message": "ॲक्सेस की रद्द करण्यात आली."}
    return {"success": False, "error": "Key not found"}


def delete_license(key: str) -> Dict[str, Any]:
    """Permanently deletes an access key from registry."""
    clean_key = sanitize_key(key)
    registry = load_licenses()
    licenses = registry.get("licenses", {})

    if clean_key in licenses:
        del licenses[clean_key]
        save_licenses(registry)
        try:
            from .supabase_client import is_supabase_configured, delete_license_from_supabase
            if is_supabase_configured():
                threading.Thread(target=delete_license_from_supabase, args=(clean_key,), daemon=True).start()
        except Exception:
            pass
        return {"success": True, "message": "की यशस्वीरित्या हटवली."}
    return {"success": False, "error": "Key not found"}


def list_all_licenses() -> List[Dict[str, Any]]:
    """Returns list of all active licenses for the Admin panel with computed status."""
    registry = load_licenses()
    licenses = registry.get("licenses", {})
    results = []
    for k, v in licenses.items():
        item = dict(v)
        item["status"] = compute_license_status(item)
        results.append(item)
    return results
