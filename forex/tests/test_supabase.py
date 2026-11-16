"""Unit tests for Supabase Cloud Database Client.
Ensures:
1. Config load and save work reliably.
2. Default SQL schema contains required table definitions.
3. Fallback when credentials are not configured.
4. Zero em dashes in this file.
"""

import os
import shutil
import tempfile
import unittest
from forex.supabase_client import (
    load_supabase_config,
    save_supabase_config,
    is_supabase_configured,
    test_supabase_connection,
    DEFAULT_SQL_SCHEMA,
    _request_supabase,
)
import forex.supabase_client as supa_mod


class TestSupabaseClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp()
        cls.orig_config_file = supa_mod.SUPABASE_CONFIG_FILE
        supa_mod.SUPABASE_CONFIG_FILE = os.path.join(cls.temp_dir, "test_supabase_config.json")
        supa_mod._CONFIG_CACHE = None

    @classmethod
    def tearDownClass(cls):
        supa_mod.SUPABASE_CONFIG_FILE = cls.orig_config_file
        supa_mod._CONFIG_CACHE = None
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_schema_definitions(self):
        """Verifies schema contains forex_licenses and forex_journal tables."""
        self.assertIn("CREATE TABLE IF NOT EXISTS forex_licenses", DEFAULT_SQL_SCHEMA)
        self.assertIn("CREATE TABLE IF NOT EXISTS forex_journal", DEFAULT_SQL_SCHEMA)
        self.assertIn("bound_device_id", DEFAULT_SQL_SCHEMA)
        self.assertIn("confluence_grade", DEFAULT_SQL_SCHEMA)

    def test_config_save_and_load(self):
        """Verifies saving and loading Supabase configuration."""
        save_supabase_config("https://testproject.supabase.co", "test-api-key-12345", True)
        cfg = load_supabase_config()
        self.assertEqual(cfg["url"], "https://testproject.supabase.co")
        self.assertEqual(cfg["key"], "test-api-key-12345")
        self.assertTrue(cfg["enabled"])
        self.assertTrue(is_supabase_configured())

    def test_unconfigured_fallback(self):
        """Verifies graceful handling when unconfigured."""
        supa_mod._CONFIG_CACHE = {"url": "", "key": "", "enabled": False}
        self.assertFalse(is_supabase_configured())
        res = test_supabase_connection()
        self.assertFalse(res["connected"])
        self.assertFalse(res["success"])


if __name__ == "__main__":
    unittest.main()
