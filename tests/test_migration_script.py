"""
Tests for Safe JSON-to-MongoDB Migration Script (Phase 3 Preparation & Safety Patch).
Covers:
1. JSON file loading & inspection (verifies latest_analysis is 1 logical document).
2. Correct mapping to collections with stable _id.
3. Missing optional fields handling.
4. Malformed JSON handling.
5. Duplicate ID detection.
6. Idempotent upsert preparation.
7. Activity and connection timestamp conflict handling.
8. Offline mode count reporting (never reports 0 when DB is offline).
9. Migration-specific rollback planning (preserves pre-existing DB records).
10. Dry-run performs zero database writes.
11. Original JSON files remain unchanged.
"""

import sys
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Set up module paths
TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
BACKEND_DIR = PROJECT_ROOT / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from scripts.migrate_json_to_mongodb import (
    JSONMigrationManager,
    compute_sha256,
    extract_doc_timestamp,
    main
)


class TestMigrationScript(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_migration_")
        self.temp_data_dir = Path(self.temp_dir) / "data"
        self.temp_data_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_json_file_loading_and_latest_analysis_singleton(self):
        """Verifies loading and inspection of JSON source files; latest_analysis shows 1 document, not key count."""
        profiles = [{"id": "user1", "name": "User 1", "profile_url": "https://linkedin.com/in/user1"}]
        with open(self.temp_data_dir / "saved_target_profiles.json", "w", encoding="utf-8") as f:
            json.dump(profiles, f)

        # Dictionary with 26 keys
        latest_data = {f"key_{i}": f"val_{i}" for i in range(26)}
        with open(self.temp_data_dir / "latest_analysis.json", "w", encoding="utf-8") as f:
            json.dump(latest_data, f)

        manager = JSONMigrationManager(data_dir=self.temp_data_dir, dry_run=True)
        inspection = manager.inspect_raw_files()

        self.assertTrue(inspection["saved_profiles"]["exists"])
        self.assertEqual(inspection["saved_profiles"]["record_count"], 1)

        # Confirm latest_analysis is counted as 1 singleton document, NOT 26
        self.assertTrue(inspection["latest_analysis"]["exists"])
        self.assertEqual(inspection["latest_analysis"]["record_count"], 1)
        self.assertEqual(inspection["latest_analysis"]["raw_type"], "singleton_object")

    def test_02_correct_mapping_to_collections(self):
        """Verifies each source JSON file maps to its correct collection with stable _id."""
        with open(self.temp_data_dir / "saved_target_profiles.json", "w", encoding="utf-8") as f:
            json.dump([{"id": "slug1", "name": "Name 1"}], f)
        with open(self.temp_data_dir / "activities.json", "w", encoding="utf-8") as f:
            json.dump([{"id": "act_101", "type": "post", "text": "Post content"}], f)
        with open(self.temp_data_dir / "latest_analysis.json", "w", encoding="utf-8") as f:
            json.dump({"subject_name": "Name 1", "total_activities": 5}, f)
        with open(self.temp_data_dir / "profile_analysis_history.json", "w", encoding="utf-8") as f:
            json.dump({"slug1": {"subject_name": "Name 1", "total_activities": 5}}, f)
        with open(self.temp_data_dir / "connections.json", "w", encoding="utf-8") as f:
            json.dump([{"id": "conn_1", "name": "Connection 1"}], f)

        manager = JSONMigrationManager(data_dir=self.temp_data_dir, dry_run=True)
        res = manager.prepare_and_validate_records()
        prepared = res["prepared_records"]

        # Verify collection mapping & stable _id
        self.assertEqual(len(prepared["saved_profiles"]), 1)
        self.assertEqual(prepared["saved_profiles"][0]["_id"], "slug1")

        self.assertEqual(len(prepared["activities"]), 1)
        self.assertEqual(prepared["activities"][0]["_id"], "act_101")

        self.assertEqual(len(prepared["latest_analysis"]), 1)
        self.assertEqual(prepared["latest_analysis"][0]["_id"], "latest_analysis_singleton")

        self.assertEqual(len(prepared["profile_analysis_history"]), 1)
        self.assertEqual(prepared["profile_analysis_history"][0]["_id"], "slug1")

        self.assertEqual(len(prepared["connections"]), 1)
        self.assertEqual(prepared["connections"][0]["_id"], "conn_1")

    def test_03_missing_optional_fields_handling(self):
        """Verifies safe handling of records with missing or null optional fields."""
        profiles = [
            {"id": "user_no_pic", "profile_url": "https://linkedin.com/in/user_no_pic", "picture": None},
            {"id": "user_minimal"}
        ]
        with open(self.temp_data_dir / "saved_target_profiles.json", "w", encoding="utf-8") as f:
            json.dump(profiles, f)

        activities = [
            {"id": "act_no_sentiment", "type": "post", "date": "2026-11-01"}
        ]
        with open(self.temp_data_dir / "activities.json", "w", encoding="utf-8") as f:
            json.dump(activities, f)

        manager = JSONMigrationManager(data_dir=self.temp_data_dir, dry_run=True)
        res = manager.prepare_and_validate_records()

        self.assertEqual(len(res["prepared_records"]["saved_profiles"]), 2)
        self.assertEqual(len(res["prepared_records"]["activities"]), 1)
        self.assertEqual(len(res["malformed"]["saved_profiles"]), 0)
        self.assertEqual(len(res["malformed"]["activities"]), 0)

    def test_04_malformed_json_handling(self):
        """Verifies corrupted entries and invalid items are caught in malformed list without crashing."""
        profiles = [
            "invalid_string_entry",  # Not an object
            {"name": "No ID or URL"}  # Missing both id and URL
        ]
        with open(self.temp_data_dir / "saved_target_profiles.json", "w", encoding="utf-8") as f:
            json.dump(profiles, f)

        activities = [
            {"type": "like"}  # Missing id
        ]
        with open(self.temp_data_dir / "activities.json", "w", encoding="utf-8") as f:
            json.dump(activities, f)

        manager = JSONMigrationManager(data_dir=self.temp_data_dir, dry_run=True)
        res = manager.prepare_and_validate_records()

        self.assertEqual(len(res["prepared_records"]["saved_profiles"]), 0)
        self.assertEqual(len(res["malformed"]["saved_profiles"]), 2)
        self.assertEqual(len(res["malformed"]["activities"]), 1)

    def test_05_duplicate_id_detection(self):
        """Verifies duplicate record IDs in source files are flagged."""
        profiles = [
            {"id": "duplicate_slug", "name": "First User"},
            {"id": "duplicate_slug", "name": "Second User with same slug"}
        ]
        with open(self.temp_data_dir / "saved_target_profiles.json", "w", encoding="utf-8") as f:
            json.dump(profiles, f)

        manager = JSONMigrationManager(data_dir=self.temp_data_dir, dry_run=True)
        res = manager.prepare_and_validate_records()

        self.assertIn("duplicate_slug", res["duplicates"]["saved_profiles"])

    def test_06_profile_timestamp_conflict_detection(self):
        """Verifies profile timestamp conflict detection when DB has newer data."""
        doc = {"id": "prof1", "last_analyzed": "2026-10-01T00:00:00"}
        with open(self.temp_data_dir / "saved_target_profiles.json", "w", encoding="utf-8") as f:
            json.dump([doc], f)

        mock_db = MagicMock()
        mock_coll = MagicMock()
        mock_db.__getitem__.return_value = mock_coll
        mock_db.name = "test_db"
        mock_coll.count_documents.return_value = 1
        mock_coll.find.return_value = [
            {"_id": "prof1", "last_analyzed": "2026-11-01T00:00:00"}
        ]

        manager = JSONMigrationManager(data_dir=self.temp_data_dir, dry_run=True, overwrite_newer=False)
        plan = manager.plan_dry_run(mock_db)

        sp_plan = plan["collections"]["saved_profiles"]
        self.assertEqual(sp_plan["projected_updates"], 1)
        self.assertEqual(sp_plan["timestamp_conflicts"], 1)

    def test_07_activity_and_connection_timestamp_conflict_handling(self):
        """Verifies timestamp conflict handling for activities (date/activity_date) and connections (connected_on)."""
        act_doc = {"id": "act_1", "activity_date": "2026-10-01", "type": "post"}
        with open(self.temp_data_dir / "activities.json", "w", encoding="utf-8") as f:
            json.dump([act_doc], f)

        conn_doc = {"id": "conn_1", "name": "Jane", "connected_on": "2025-01-01"}
        with open(self.temp_data_dir / "connections.json", "w", encoding="utf-8") as f:
            json.dump([conn_doc], f)

        # Mock DB where DB has NEWER activity and connection
        mock_db = MagicMock()
        mock_coll = MagicMock()
        mock_db.__getitem__.return_value = mock_coll
        mock_db.name = "test_db"
        mock_coll.count_documents.return_value = 1

        def mock_find(query, proj):
            # Return newer dates in DB
            return [
                {"_id": "act_1", "activity_date": "2026-11-01", "date": "2026-11-01"},
                {"_id": "conn_1", "connected_on": "2026-05-01"}
            ]
        mock_coll.find = mock_find

        manager = JSONMigrationManager(data_dir=self.temp_data_dir, dry_run=True, overwrite_newer=False)
        plan = manager.plan_dry_run(mock_db)

        # Both activity and connection must register conflict because DB is newer
        self.assertEqual(plan["collections"]["activities"]["timestamp_conflicts"], 1)
        self.assertEqual(plan["collections"]["connections"]["timestamp_conflicts"], 1)

    def test_08_offline_count_reporting_never_assumes_zero(self):
        """Verifies that offline dry-run never reports Current DB = 0 or assumes empty database."""
        with open(self.temp_data_dir / "saved_target_profiles.json", "w", encoding="utf-8") as f:
            json.dump([{"id": "prof1", "name": "User 1"}], f)

        manager = JSONMigrationManager(data_dir=self.temp_data_dir, dry_run=True)
        # Call plan_dry_run with db=None (offline mode)
        plan = manager.plan_dry_run(db=None)

        self.assertFalse(plan["db_connected"])
        sp = plan["collections"]["saved_profiles"]
        # current_mongo_count must be None, NOT 0!
        self.assertIsNone(sp["current_mongo_count"])
        self.assertIsNone(sp["projected_updates"])
        self.assertIsNone(sp["timestamp_conflicts"])
        self.assertIsNone(sp["projected_final_count"])
        self.assertEqual(sp["projected_inserts"], 1)

    def test_09_migration_specific_rollback_preserves_pre_existing_data(self):
        """Verifies that rollback selectively removes only batch-inserted docs and restores updated docs, keeping pre-existing data intact."""
        batch_id = "test_batch_20261103"
        rollback_data = {
            "batch_id": batch_id,
            "inserted_ids": {
                "saved_profiles": ["migrated_new_user"]
            },
            "updated_snapshots": {
                "saved_profiles": [{"_id": "existing_user", "name": "Pre-Migration Name", "headline": "Old Headline"}]
            }
        }
        manifest_file = Path(self.temp_dir) / f"rollback_manifest_{batch_id}.json"
        with open(manifest_file, "w", encoding="utf-8") as f:
            json.dump(rollback_data, f)

        mock_db = MagicMock()
        mock_coll = MagicMock()
        mock_db.__getitem__.return_value = mock_coll
        mock_db.name = "test_db"
        mock_coll.delete_many.return_value.deleted_count = 1
        mock_coll.count_documents.return_value = 1

        res = JSONMigrationManager.rollback_batch(batch_id, mock_db, backup_root=Path(self.temp_dir))

        # Confirm delete_many was called strictly with $in filter for migrated_new_user, NOT delete_many({})
        mock_coll.delete_many.assert_called_with({"_id": {"$in": ["migrated_new_user"]}})

        # Confirm replace_one was called to restore the updated document's pre-migration state
        mock_coll.replace_one.assert_called_with(
            {"_id": "existing_user"},
            {"_id": "existing_user", "name": "Pre-Migration Name", "headline": "Old Headline"},
            upsert=True
        )

    def test_10_dry_run_performs_zero_database_writes(self):
        """Strictly verifies that dry-run mode never calls write operations on MongoDB."""
        with open(self.temp_data_dir / "saved_target_profiles.json", "w", encoding="utf-8") as f:
            json.dump([{"id": "slug1", "name": "User 1"}], f)

        mock_db = MagicMock()
        mock_coll = MagicMock()
        mock_db.__getitem__.return_value = mock_coll
        mock_db.name = "test_db"
        mock_coll.count_documents.return_value = 0
        mock_coll.find.return_value = []

        manager = JSONMigrationManager(data_dir=self.temp_data_dir, dry_run=True)
        manager.plan_dry_run(mock_db)

        # Assert zero write operations were invoked
        mock_coll.update_one.assert_not_called()
        mock_coll.update_many.assert_not_called()
        mock_coll.insert_one.assert_not_called()
        mock_coll.insert_many.assert_not_called()
        mock_coll.delete_one.assert_not_called()
        mock_coll.delete_many.assert_not_called()
        mock_coll.replace_one.assert_not_called()
        mock_coll.drop.assert_not_called()

    def test_11_original_json_files_remain_unchanged(self):
        """Verifies that running dry-run and cold backup never mutates original JSON files."""
        real_data_dir = BACKEND_DIR / "data"
        json_files = list(real_data_dir.glob("*.json"))

        # Calculate initial hashes
        initial_hashes = {f.name: compute_sha256(f) for f in json_files}

        # Run dry run on actual data
        manager = JSONMigrationManager(data_dir=real_data_dir, dry_run=True)
        _ = manager.inspect_raw_files()
        _ = manager.prepare_and_validate_records()

        # Run backup to temporary folder
        backup_root = Path(self.temp_dir) / "backups"
        manager.create_cold_backup(backup_root=backup_root)

        # Re-check hashes of original files
        for f in json_files:
            current_hash = compute_sha256(f)
            self.assertEqual(
                current_hash,
                initial_hashes[f.name],
                f"File {f.name} was modified! Hash changed from {initial_hashes[f.name]} to {current_hash}"
            )

    def test_12_execute_without_confirmation_aborts_safely(self):
        """Verifies that --execute without confirmation aborts and never calls execute_live_migration."""
        with patch("sys.argv", ["migrate_json_to_mongodb.py", "--execute", "--data-dir", str(self.temp_data_dir)]), \
             patch("sys.stdin.isatty", return_value=False), \
             patch("sys.exit") as mock_exit, \
             patch.object(JSONMigrationManager, "execute_live_migration") as mock_exec, \
             patch.object(JSONMigrationManager, "create_cold_backup") as mock_backup:

            main()
            mock_exit.assert_called_with(0)
            mock_exec.assert_not_called()
            mock_backup.assert_not_called()

    def test_13_execute_with_confirm_flag_reaches_live_migration(self):
        """Verifies that --execute --confirm reaches the actual execution path."""
        with patch("sys.argv", ["migrate_json_to_mongodb.py", "--execute", "--confirm", "--data-dir", str(self.temp_data_dir)]), \
             patch("scripts.migrate_json_to_mongodb.check_connection", return_value=(True, "Connected")), \
             patch("scripts.migrate_json_to_mongodb.get_db") as mock_get_db, \
             patch.object(JSONMigrationManager, "create_cold_backup", return_value=(Path(self.temp_dir), Path(self.temp_dir) / "manifest.json", "b123")), \
             patch.object(JSONMigrationManager, "execute_live_migration") as mock_exec:

            mock_db = MagicMock()
            mock_get_db.return_value = mock_db
            mock_exec.return_value = {"status": "ok"}

            main()
            mock_exec.assert_called_once()
            self.assertEqual(mock_exec.call_args[0][0], mock_db)

    def test_14_execute_with_interactive_yes_reaches_live_migration(self):
        """Verifies that interactive 'yes' prompt confirms live migration execution."""
        with patch("sys.argv", ["migrate_json_to_mongodb.py", "--execute", "--data-dir", str(self.temp_data_dir)]), \
             patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", return_value="yes"), \
             patch("scripts.migrate_json_to_mongodb.check_connection", return_value=(True, "Connected")), \
             patch("scripts.migrate_json_to_mongodb.get_db") as mock_get_db, \
             patch.object(JSONMigrationManager, "create_cold_backup", return_value=(Path(self.temp_dir), Path(self.temp_dir) / "manifest.json", "b123")), \
             patch.object(JSONMigrationManager, "execute_live_migration") as mock_exec:

            mock_db = MagicMock()
            mock_get_db.return_value = mock_db
            mock_exec.return_value = {"status": "ok"}

            main()
            mock_exec.assert_called_once()

    def test_15_execute_with_interactive_no_aborts_safely(self):
        """Verifies that interactive 'no' prompt aborts without calling execute_live_migration."""
        with patch("sys.argv", ["migrate_json_to_mongodb.py", "--execute", "--data-dir", str(self.temp_data_dir)]), \
             patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", return_value="no"), \
             patch("sys.exit") as mock_exit, \
             patch.object(JSONMigrationManager, "execute_live_migration") as mock_exec:

            main()
            mock_exit.assert_called_with(0)
            mock_exec.assert_not_called()


if __name__ == "__main__":
    unittest.main()
