"""Unit tests for the Single-Device License & Anti-Piracy Security System.
Ensures:
1. Master admin bypass works on any device.
2. 1st device binding locks device UUID and hardware hash.
3. 2nd device access is strictly rejected with DEVICE_MISMATCH.
4. Admin device reset allows binding a new device.
5. Revoked keys are rejected.
6. Zero em dashes in this file.
"""

import unittest
from datetime import datetime, timedelta, timezone
from forex.licenses import (
    verify_device_license,
    create_license,
    reset_license_device,
    revoke_license,
    list_all_licenses,
    MASTER_ADMIN_KEY,
    sanitize_key,
)


class TestLicenses(unittest.TestCase):
    def test_master_admin_bypass(self):
        """Master admin key should be granted on any device without binding."""
        res1 = verify_device_license(MASTER_ADMIN_KEY, "admin-phone-uuid-1", "hw-hash-1", "Admin iPhone")
        self.assertEqual(res1["status"], "GRANTED")
        self.assertTrue(res1["is_admin"])

        res2 = verify_device_license(MASTER_ADMIN_KEY, "admin-laptop-uuid-2", "hw-hash-2", "Admin Laptop")
        self.assertEqual(res2["status"], "GRANTED")
        self.assertTrue(res2["is_admin"])

    def test_single_device_binding_and_mismatch(self):
        """Key should bind to device 1 and reject device 2 with DEVICE_MISMATCH."""
        item = create_license(client_name="Test Student", duration_days=30, custom_key="TEST-STUDENT-KEY-01")
        key = item["key"]

        # First activation on Device 1
        res_dev1 = verify_device_license(key, "device-uuid-phone-a", "hw-a", "OnePlus 12")
        self.assertEqual(res_dev1["status"], "GRANTED")
        self.assertTrue(res_dev1.get("first_activation", False))

        # Subsequent access from Device 1
        res_dev1_repeat = verify_device_license(key, "device-uuid-phone-a", "hw-a", "OnePlus 12")
        self.assertEqual(res_dev1_repeat["status"], "GRANTED")

        # Unauthorized access from Device 2 (friend's phone)
        res_dev2 = verify_device_license(key, "device-uuid-phone-b", "hw-b", "Samsung S24")
        self.assertEqual(res_dev2["status"], "BLOCKED")
        self.assertEqual(res_dev2["reason"], "DEVICE_MISMATCH")
        self.assertIn("OnePlus 12", res_dev2.get("registered_device", ""))

    def test_device_reset_allows_new_device(self):
        """Resetting device allows a client with a new phone to bind successfully."""
        item = create_license(client_name="New Phone User", duration_days=15, custom_key="TEST-PHONE-SWAP-01")
        key = item["key"]

        # First activation on old phone
        res_old = verify_device_license(key, "old-iphone-11", "hw-old", "iPhone 11")
        self.assertEqual(res_old["status"], "GRANTED")

        # New phone attempted - blocked
        res_new_blocked = verify_device_license(key, "new-iphone-16", "hw-new", "iPhone 16 Pro")
        self.assertEqual(res_new_blocked["status"], "BLOCKED")
        self.assertEqual(res_new_blocked["reason"], "DEVICE_MISMATCH")

        # Admin resets device
        reset_res = reset_license_device(key)
        self.assertTrue(reset_res["success"])

        # New phone attempted - granted and bound
        res_new_granted = verify_device_license(key, "new-iphone-16", "hw-new", "iPhone 16 Pro")
        self.assertEqual(res_new_granted["status"], "GRANTED")
        self.assertTrue(res_new_granted.get("first_activation", False))

    def test_revoked_key(self):
        """Revoked keys cannot be accessed on any device."""
        item = create_license(client_name="Revoked Client", duration_days=7, custom_key="TEST-REVOKE-KEY-01")
        key = item["key"]

        # Revoke the key
        revoke_res = revoke_license(key)
        self.assertTrue(revoke_res["success"])

        # Verify access attempt is blocked
        res = verify_device_license(key, "any-device", "any-hw", "Any Phone")
        self.assertEqual(res["status"], "BLOCKED")
        self.assertEqual(res["reason"], "REVOKED")

    def test_invalid_key(self):
        """Random invalid keys are rejected."""
        res = verify_device_license("NON-EXISTENT-KEY-9999", "dev-1", "hw-1", "Phone")
        self.assertEqual(res["status"], "BLOCKED")
        self.assertEqual(res["reason"], "INVALID_KEY")


if __name__ == "__main__":
    unittest.main()
