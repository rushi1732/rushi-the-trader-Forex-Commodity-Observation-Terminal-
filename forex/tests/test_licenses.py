"""Unit tests for the Single-Device License & Anti-Piracy Security System.
Ensures:
1. Master admin bypass works on any device.
2. 1st device binding locks device UUID and hardware hash.
3. 2nd device access is strictly rejected with DEVICE_MISMATCH.
4. Admin device reset allows binding a new device.
5. Revoked keys are rejected.
6. Delete license permanently removes key.
7. List all licenses returns computed status (ACTIVE, EXPIRED, REVOKED).
8. Test data does not pollute production licenses.json file.
9. Zero em dashes in this file.
"""

import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
import forex.licenses as lic_mod


class TestLicenses(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp()
        cls.orig_licenses_file = lic_mod.LICENSES_FILE
        lic_mod.LICENSES_FILE = os.path.join(cls.temp_dir, "test_licenses.json")
        lic_mod._CACHE = None
        # Initialize isolated registry
        lic_mod.save_licenses(lic_mod._get_default_registry())

    @classmethod
    def tearDownClass(cls):
        lic_mod.LICENSES_FILE = cls.orig_licenses_file
        lic_mod._CACHE = None
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_master_admin_bypass(self):
        """Master admin key should be granted on any device without binding."""
        res1 = lic_mod.verify_device_license(lic_mod.MASTER_ADMIN_KEY, "admin-phone-uuid-1", "hw-hash-1", "Admin iPhone")
        self.assertEqual(res1["status"], "GRANTED")
        self.assertTrue(res1["is_admin"])

        res2 = lic_mod.verify_device_license(lic_mod.MASTER_ADMIN_KEY, "admin-laptop-uuid-2", "hw-hash-2", "Admin Laptop")
        self.assertEqual(res2["status"], "GRANTED")
        self.assertTrue(res2["is_admin"])

    def test_single_device_binding_and_mismatch(self):
        """Key should bind to device 1 and reject device 2 with DEVICE_MISMATCH."""
        item = lic_mod.create_license(client_name="Test Student", duration_days=30, custom_key="TEST-STUDENT-KEY-01")
        key = item["key"]

        # First activation on Device 1
        res_dev1 = lic_mod.verify_device_license(key, "device-uuid-phone-a", "hw-a", "OnePlus 12")
        self.assertEqual(res_dev1["status"], "GRANTED")
        self.assertTrue(res_dev1.get("first_activation", False))

        # Subsequent access from Device 1
        res_dev1_repeat = lic_mod.verify_device_license(key, "device-uuid-phone-a", "hw-a", "OnePlus 12")
        self.assertEqual(res_dev1_repeat["status"], "GRANTED")

        # Unauthorized access from Device 2 (friend's phone)
        res_dev2 = lic_mod.verify_device_license(key, "device-uuid-phone-b", "hw-b", "Samsung S24")
        self.assertEqual(res_dev2["status"], "BLOCKED")
        self.assertEqual(res_dev2["reason"], "DEVICE_MISMATCH")
        self.assertIn("OnePlus 12", res_dev2.get("registered_device", ""))

    def test_device_reset_allows_new_device(self):
        """Resetting device allows a client with a new phone to bind successfully."""
        item = lic_mod.create_license(client_name="New Phone User", duration_days=15, custom_key="TEST-PHONE-SWAP-01")
        key = item["key"]

        # First activation on old phone
        res_old = lic_mod.verify_device_license(key, "old-iphone-11", "hw-old", "iPhone 11")
        self.assertEqual(res_old["status"], "GRANTED")

        # New phone attempted - blocked
        res_new_blocked = lic_mod.verify_device_license(key, "new-iphone-16", "hw-new", "iPhone 16 Pro")
        self.assertEqual(res_new_blocked["status"], "BLOCKED")
        self.assertEqual(res_new_blocked["reason"], "DEVICE_MISMATCH")

        # Admin resets device
        reset_res = lic_mod.reset_license_device(key)
        self.assertTrue(reset_res["success"])

        # New phone attempted - granted and bound
        res_new_granted = lic_mod.verify_device_license(key, "new-iphone-16", "hw-new", "iPhone 16 Pro")
        self.assertEqual(res_new_granted["status"], "GRANTED")
        self.assertTrue(res_new_granted.get("first_activation", False))

    def test_revoked_key(self):
        """Revoked keys cannot be accessed on any device."""
        item = lic_mod.create_license(client_name="Revoked Client", duration_days=7, custom_key="TEST-REVOKE-KEY-01")
        key = item["key"]

        # Revoke the key
        revoke_res = lic_mod.revoke_license(key)
        self.assertTrue(revoke_res["success"])

        # Verify access attempt is blocked
        res = lic_mod.verify_device_license(key, "any-device", "any-hw", "Any Phone")
        self.assertEqual(res["status"], "BLOCKED")
        self.assertEqual(res["reason"], "REVOKED")

    def test_delete_license(self):
        """Deleted keys are completely purged from the registry."""
        item = lic_mod.create_license(client_name="Temporary User", duration_days=1, custom_key="TEST-DELETE-KEY-01")
        key = item["key"]

        del_res = lic_mod.delete_license(key)
        self.assertTrue(del_res["success"])

        # Should now be invalid
        res = lic_mod.verify_device_license(key, "device-1", "hw-1", "Phone")
        self.assertEqual(res["status"], "BLOCKED")
        self.assertEqual(res["reason"], "INVALID_KEY")

    def test_list_all_licenses_has_status(self):
        """All items in list_all_licenses should include computed status string."""
        lics = lic_mod.list_all_licenses()
        self.assertGreater(len(lics), 0)
        for lic in lics:
            self.assertIn("status", lic)
            self.assertIn(lic["status"], ["ACTIVE", "EXPIRED", "REVOKED"])

    def test_invalid_key(self):
        """Random invalid keys are rejected."""
        res = lic_mod.verify_device_license("NON-EXISTENT-KEY-9999", "dev-1", "hw-1", "Phone")
        self.assertEqual(res["status"], "BLOCKED")
        self.assertEqual(res["reason"], "INVALID_KEY")

    def test_signed_vip_key_lifecycle(self):
        """HMAC signed VIP keys can self-validate statelessly across cold lambda starts."""
        signed_key = lic_mod.sign_key("RAHUL", 30)
        self.assertTrue(signed_key.startswith("VIP-RAHUL-30-"))

        # Valid signature verification
        info = lic_mod.verify_signed_key(signed_key)
        self.assertIsNotNone(info)
        self.assertEqual(info["client_name"], "RAHUL")
        self.assertEqual(info["duration_days"], 30)

        # Tampered signature detection
        tampered_key = signed_key[:-2] + "99"
        self.assertIsNone(lic_mod.verify_signed_key(tampered_key))

        # First activation on Device A
        res_open = lic_mod.verify_device_license(signed_key, "dev-phone-rahul", "hw-rahul", "Samsung Galaxy S24")
        self.assertEqual(res_open["status"], "GRANTED")
        token = res_open.get("device_token")
        self.assertTrue(bool(token))

        # Device token verification
        self.assertTrue(lic_mod.verify_device_token(signed_key, "dev-phone-rahul", token))
        self.assertFalse(lic_mod.verify_device_token(signed_key, "dev-pirate-phone", token))

        # Reopen / Refresh on Device A with device token succeeds
        res_refresh = lic_mod.verify_device_license(
            signed_key, "dev-phone-rahul", "hw-rahul", "Samsung Galaxy S24", device_token=token
        )
        self.assertEqual(res_refresh["status"], "GRANTED")

        # Second phone tries to access -> Rejected with DEVICE_MISMATCH
        res_pirate = lic_mod.verify_device_license(
            signed_key, "dev-pirate-phone", "hw-pirate", "iPhone 15 Pro", device_token=None
        )
        self.assertEqual(res_pirate["status"], "BLOCKED")
        self.assertEqual(res_pirate["reason"], "DEVICE_MISMATCH")


if __name__ == "__main__":
    unittest.main()
