"""
Unit and Integration Tests for MongoDB Local Connection Module (Phase 1 & Phase 2).
Verifies:
1. Local MongoDB connection & ping.
2. Target database selection (linkedin_activity_analyzer).
3. Safe, idempotent index creation across collections.
4. Fast connection timeout and failure handling.
5. Enforcement of local-only security policy (rejection of Atlas / remote URIs).
6. Document serialization sanitization (handling of ObjectId & datetime for Flask).
"""

import os
import sys
import unittest
from datetime import datetime
from bson import ObjectId
from pathlib import Path

# Ensure backend and tests can be imported cleanly
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from db.mongo import (
    get_mongo_client,
    get_db,
    check_connection,
    init_indexes,
    sanitize_doc,
    validate_local_uri,
    close_mongo_client
)


class TestMongoLocalIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Ensure fresh client
        close_mongo_client()
        cls.is_mongo_running, cls.mongo_msg = check_connection(timeout_ms=1000)

    @classmethod
    def tearDownClass(cls):
        close_mongo_client()

    def test_01_local_uri_validation(self):
        """Rejects Atlas and remote URIs, accepts local URIs."""
        # Valid local URIs
        validate_local_uri("mongodb://localhost:27017/")
        validate_local_uri("mongodb://127.0.0.1:27017/")
        validate_local_uri("mongodb://127.0.0.1:27017/linkedin_activity_analyzer")

        # Prohibited remote/Atlas URIs
        with self.assertRaises(ValueError) as ctx:
            validate_local_uri("mongodb+srv://user:pass@cluster0.mongodb.net/test")
        self.assertIn("Security Policy Violation", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            validate_local_uri("mongodb://user:pass@some-remote-cluster.mongodb.net:27017/")
        self.assertIn("Security Policy Violation", str(ctx.exception))

    def test_02_check_connection_success(self):
        """Verifies successful connection to the running local MongoDB server."""
        if not self.is_mongo_running:
            self.skipTest(f"Local MongoDB service is stopped or unreachable ({self.mongo_msg})")
        connected, msg = check_connection(timeout_ms=2000)
        self.assertTrue(connected, f"MongoDB connection check failed: {msg}")
        self.assertIn("Connected to local MongoDB", msg)

    def test_03_database_selection(self):
        """Verifies database selection for linkedin_activity_analyzer."""
        db = get_db(db_name="linkedin_activity_analyzer")
        self.assertEqual(db.name, "linkedin_activity_analyzer")

    def test_04_idempotent_index_creation(self):
        """Verifies idempotent creation of required indexes on collections."""
        if not self.is_mongo_running:
            self.skipTest(f"Local MongoDB service is stopped or unreachable ({self.mongo_msg})")
        # We test index creation on the test database or target database
        db = get_db(db_name="linkedin_activity_analyzer_test")
        first_run = init_indexes(db)
        
        # Verify required collections are configured
        self.assertIn("saved_profiles", first_run)
        self.assertIn("activities", first_run)
        self.assertIn("profile_analysis_history", first_run)
        self.assertIn("imported_activities", first_run)
        self.assertIn("connections", first_run)
        self.assertIn("latest_analysis", first_run)

        # Re-running init_indexes must succeed identically without throwing
        second_run = init_indexes(db)
        self.assertEqual(first_run, second_run)

        # Inspect indexes directly on collection
        indexes = db["saved_profiles"].index_information()
        self.assertIn("idx_profile_id_unique", indexes)
        self.assertIn("idx_profile_url_unique", indexes)
        self.assertIn("idx_last_analyzed_desc", indexes)

        # Verify imported_activities has NO TTL index (user data must be permanent)
        imp_indexes = db["imported_activities"].index_information()
        self.assertIn("idx_import_id_unique", imp_indexes)
        self.assertIn("idx_created_at_desc", imp_indexes)
        self.assertNotIn("idx_ttl_created_at", imp_indexes)
        for idx_name, idx_info in imp_indexes.items():
            self.assertNotIn("expireAfterSeconds", idx_info, f"Index {idx_name} unexpectedly has expireAfterSeconds")

        # Clean up test database
        client = get_mongo_client()
        client.drop_database("linkedin_activity_analyzer_test")

    def test_05_check_connection_failure_handling(self):
        """Verifies connection failure handling with unreachable host/port."""
        # Test connecting to an unreachable port with short timeout
        from pymongo import MongoClient
        unreachable_client = MongoClient("mongodb://127.0.0.1:27099/", serverSelectionTimeoutMS=500)
        try:
            unreachable_client.admin.command("ping")
            connected = True
        except Exception:
            connected = False
        finally:
            unreachable_client.close()

        self.assertFalse(connected, "Connection to port 27099 should have failed.")

    def test_06_sanitize_doc_for_flask(self):
        """Verifies recursive sanitization of ObjectId and datetime for JSON serialization."""
        oid = ObjectId()
        now = datetime(2026, 11, 3, 10, 30, 0)
        raw_doc = {
            "_id": oid,
            "profile_url": "https://www.linkedin.com/in/testuser/",
            "name": "Test User",
            "last_analyzed": now,
            "meta": {
                "nested_id": ObjectId(),
                "created_at": now
            },
            "tags": ["active", ObjectId()]
        }

        sanitized = sanitize_doc(raw_doc)

        # Ensure all ObjectIds are strings
        self.assertIsInstance(sanitized["_id"], str)
        self.assertEqual(sanitized["_id"], str(oid))
        self.assertIsInstance(sanitized["last_analyzed"], str)
        self.assertEqual(sanitized["last_analyzed"], "2026-11-03T10:30:00")
        self.assertIsInstance(sanitized["meta"]["nested_id"], str)
        self.assertIsInstance(sanitized["meta"]["created_at"], str)
        self.assertIsInstance(sanitized["tags"][1], str)

        # Verify json.dumps succeeds without TypeError
        import json
        dumped = json.dumps(sanitized)
        self.assertIn(str(oid), dumped)


if __name__ == "__main__":
    unittest.main()

