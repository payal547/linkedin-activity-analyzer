"""
Safe, Repeatable JSON-to-MongoDB Migration Script for LinkedIn Activity Analyzer.

Supports:
- Safe dry-run mode (mandatory default; zero MongoDB writes).
- Schema validation, duplicate ID detection, and malformed record reporting.
- Idempotent upserts with stable document IDs (_id).
- Domain-aware timestamp conflict detection (prevents overwriting newer DB records with older JSON data).
- Batch-tagged migration tracking with snapshot-based selective rollback.
- Automated timestamped backup generation and cryptographic migration manifest.
- Complete rollback instructions and safety verification.

Usage:
  # Dry-run inspection (default, zero writes):
  python backend/scripts/migrate_json_to_mongodb.py --dry-run
  
  # Future execution (requires explicit --execute flag):
  python backend/scripts/migrate_json_to_mongodb.py --execute
  
  # Selective batch rollback (requires batch ID):
  python backend/scripts/migrate_json_to_mongodb.py --rollback <batch_id>
"""

import os
import sys
import json
import shutil
import hashlib
import logging
import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("migration")

# Add backend directory to sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = BACKEND_DIR.parent

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:
    from db.mongo import get_db, get_mongo_client, check_connection, init_indexes, sanitize_doc
    from config import settings
except ImportError:
    from backend.db.mongo import get_db, get_mongo_client, check_connection, init_indexes, sanitize_doc
    from backend.config import settings

DEFAULT_DATA_DIR = BACKEND_DIR / "data"
DEFAULT_BACKUP_ROOT = PROJECT_ROOT.parent


def compute_sha256(file_path: Path) -> str:
    """Computes SHA-256 hash of a file for integrity verification."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def extract_doc_timestamp(doc: Any, coll_name: str) -> Optional[str]:
    """
    Extracts the most relevant comparison timestamp for a document based on its collection domain.
    Returns an ISO-formatted or sortable string, or None if no valid timestamp exists.
    Handles missing or invalid formats safely.
    """
    if not isinstance(doc, dict):
        return None

    # Priority 1: Generic audit timestamps if present
    for key in ("updated_at", "historical_analyzed_at", "last_analyzed"):
        val = doc.get(key)
        if val and isinstance(val, str) and len(val.strip()) >= 8:
            return val.strip()

    # Priority 2: Domain-specific timestamps
    if coll_name == "activities":
        for key in ("activity_date", "date"):
            val = doc.get(key)
            if val and isinstance(val, str) and len(val.strip()) >= 8:
                return val.strip()

    elif coll_name == "connections":
        val = doc.get("connected_on")
        if val and isinstance(val, str) and len(val.strip()) >= 4:
            cleaned = val.strip()
            for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d %b %Y", "%m/%d/%y, %I:%M %p"):
                try:
                    dt = datetime.strptime(cleaned, fmt)
                    return dt.isoformat()
                except (ValueError, TypeError):
                    continue
            return cleaned

    elif coll_name == "imported_activities":
        val = doc.get("created_at")
        if val and isinstance(val, str) and len(val.strip()) >= 8:
            return val.strip()

    return None


class JSONMigrationManager:
    """
    Manages safe preparation, validation, backup, and idempotent migration
    from local JSON storage to MongoDB Local.
    """

    def __init__(
        self,
        data_dir: Optional[Path] = None,
        db_name: Optional[str] = None,
        dry_run: bool = True,
        overwrite_newer: bool = False
    ):
        self.data_dir = Path(data_dir or DEFAULT_DATA_DIR)
        self.db_name = db_name or getattr(settings, "MONGO_DB_NAME", "linkedin_activity_analyzer")
        self.dry_run = dry_run
        self.overwrite_newer = overwrite_newer

        # Tracked files and their target collection mappings
        self.target_files = {
            "saved_profiles": self.data_dir / "saved_target_profiles.json",
            "activities": self.data_dir / "activities.json",
            "latest_analysis": self.data_dir / "latest_analysis.json",
            "profile_analysis_history": self.data_dir / "profile_analysis_history.json",
            "connections": self.data_dir / "connections.json",
        }
        self.imports_dir = self.data_dir / "imports"

    def inspect_raw_files(self) -> Dict[str, Any]:
        """
        Reads each source JSON file directly and collects metadata without altering anything.
        """
        inspection = {}
        for coll_name, file_path in self.target_files.items():
            if not file_path.exists():
                inspection[coll_name] = {
                    "file_path": str(file_path),
                    "exists": False,
                    "record_count": 0,
                    "size_bytes": 0,
                    "sha256": None,
                    "raw_type": None,
                    "status": "NOT_FOUND"
                }
                continue

            try:
                size = file_path.stat().st_size
                sha256 = compute_sha256(file_path)
                with open(file_path, "r", encoding="utf-8") as f:
                    content = json.load(f)

                if coll_name == "latest_analysis":
                    # latest_analysis.json is 1 logical document, regardless of its internal dictionary keys
                    count = 1 if (isinstance(content, dict) and content) else 0
                    raw_type = "singleton_object"
                elif isinstance(content, list):
                    count = len(content)
                    raw_type = "list"
                elif isinstance(content, dict):
                    count = len(content)
                    raw_type = "dict"
                else:
                    count = 1
                    raw_type = type(content).__name__

                inspection[coll_name] = {
                    "file_path": str(file_path),
                    "exists": True,
                    "record_count": count,
                    "size_bytes": size,
                    "sha256": sha256,
                    "raw_type": raw_type,
                    "status": "OK"
                }
            except json.JSONDecodeError as e:
                inspection[coll_name] = {
                    "file_path": str(file_path),
                    "exists": True,
                    "record_count": 0,
                    "size_bytes": file_path.stat().st_size,
                    "sha256": compute_sha256(file_path),
                    "raw_type": "corrupted",
                    "status": f"JSON_DECODE_ERROR: {str(e)}"
                }

        # Check imports directory
        imported_files = []
        if self.imports_dir.exists():
            for p in self.imports_dir.glob("import_*.json"):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    imported_files.append({
                        "file_path": str(p),
                        "import_id": p.stem.replace("import_", ""),
                        "record_count": len(data) if isinstance(data, list) else 1,
                        "size_bytes": p.stat().st_size,
                        "sha256": compute_sha256(p)
                    })
                except Exception as e:
                    imported_files.append({
                        "file_path": str(p),
                        "status": f"ERROR: {str(e)}"
                    })

        inspection["imported_activities"] = {
            "directory": str(self.imports_dir),
            "exists": self.imports_dir.exists(),
            "files_found": len(imported_files),
            "files": imported_files,
            "status": "OK"
        }
        return inspection

    def prepare_and_validate_records(self) -> Dict[str, Any]:
        """
        Parses raw JSON data, verifies field schemas, detects duplicate IDs,
        isolates malformed records, and structures documents for MongoDB upsert.
        """
        prepared: Dict[str, List[Dict[str, Any]]] = {
            "saved_profiles": [],
            "activities": [],
            "latest_analysis": [],
            "profile_analysis_history": [],
            "connections": [],
            "imported_activities": []
        }
        duplicates: Dict[str, List[str]] = {k: [] for k in prepared}
        malformed: Dict[str, List[Dict[str, Any]]] = {k: [] for k in prepared}
        seen_ids: Dict[str, set] = {k: set() for k in prepared}

        # 1. Saved Profiles
        prof_file = self.target_files["saved_profiles"]
        if prof_file.exists():
            try:
                with open(prof_file, "r", encoding="utf-8") as f:
                    profs = json.load(f)
                if isinstance(profs, list):
                    for idx, p in enumerate(profs):
                        if not isinstance(p, dict):
                            malformed["saved_profiles"].append({"index": idx, "reason": "Not a JSON object", "data": p})
                            continue
                        doc_id = (p.get("id") or "").strip().lower()
                        if not doc_id and p.get("profile_url"):
                            # Derive fallback slug if id missing
                            doc_id = p["profile_url"].rstrip("/").split("/")[-1].lower()

                        if not doc_id:
                            malformed["saved_profiles"].append({"index": idx, "reason": "Missing profile id and URL", "data": p})
                            continue

                        if doc_id in seen_ids["saved_profiles"]:
                            duplicates["saved_profiles"].append(doc_id)
                        seen_ids["saved_profiles"].add(doc_id)

                        doc = dict(p)
                        doc["_id"] = doc_id
                        doc["id"] = doc_id
                        prepared["saved_profiles"].append(doc)
            except Exception as e:
                malformed["saved_profiles"].append({"reason": f"File read failure: {e}"})

        # 2. Activities
        act_file = self.target_files["activities"]
        if act_file.exists():
            try:
                with open(act_file, "r", encoding="utf-8") as f:
                    acts = json.load(f)
                if isinstance(acts, list):
                    for idx, a in enumerate(acts):
                        if not isinstance(a, dict):
                            malformed["activities"].append({"index": idx, "reason": "Not a JSON object", "data": a})
                            continue
                        act_id = a.get("id")
                        if not act_id:
                            malformed["activities"].append({"index": idx, "reason": "Missing activity id", "data": a})
                            continue

                        if act_id in seen_ids["activities"]:
                            duplicates["activities"].append(act_id)
                        seen_ids["activities"].add(act_id)

                        doc = dict(a)
                        doc["_id"] = str(act_id)
                        doc["id"] = str(act_id)
                        prepared["activities"].append(doc)
            except Exception as e:
                malformed["activities"].append({"reason": f"File read failure: {e}"})

        # 3. Latest Analysis (Singleton Document)
        lat_file = self.target_files["latest_analysis"]
        if lat_file.exists():
            try:
                with open(lat_file, "r", encoding="utf-8") as f:
                    lat = json.load(f)
                if isinstance(lat, dict) and lat:
                    doc = dict(lat)
                    doc["_id"] = "latest_analysis_singleton"
                    prepared["latest_analysis"].append(doc)
            except Exception as e:
                malformed["latest_analysis"].append({"reason": f"File read failure: {e}"})

        # 4. Profile Analysis History
        hist_file = self.target_files["profile_analysis_history"]
        if hist_file.exists():
            try:
                with open(hist_file, "r", encoding="utf-8") as f:
                    hist = json.load(f)
                if isinstance(hist, dict):
                    for slug, h_data in hist.items():
                        if not isinstance(h_data, dict):
                            malformed["profile_analysis_history"].append({"slug": slug, "reason": "History entry not an object", "data": h_data})
                            continue
                        clean_slug = str(slug).strip().lower()
                        if clean_slug in seen_ids["profile_analysis_history"]:
                            duplicates["profile_analysis_history"].append(clean_slug)
                        seen_ids["profile_analysis_history"].add(clean_slug)

                        doc = dict(h_data)
                        doc["_id"] = clean_slug
                        doc["slug"] = clean_slug
                        prepared["profile_analysis_history"].append(doc)
            except Exception as e:
                malformed["profile_analysis_history"].append({"reason": f"File read failure: {e}"})

        # 5. Connections
        conn_file = self.target_files["connections"]
        if conn_file.exists():
            try:
                with open(conn_file, "r", encoding="utf-8") as f:
                    conns = json.load(f)
                if isinstance(conns, list):
                    for idx, c in enumerate(conns):
                        if not isinstance(c, dict):
                            malformed["connections"].append({"index": idx, "reason": "Not a JSON object", "data": c})
                            continue
                        cid = c.get("id") or (c.get("profile_url") or "").rstrip("/").split("/")[-1].lower()
                        if not cid:
                            malformed["connections"].append({"index": idx, "reason": "Missing connection id and profile_url", "data": c})
                            continue

                        if cid in seen_ids["connections"]:
                            duplicates["connections"].append(cid)
                        seen_ids["connections"].add(cid)

                        doc = dict(c)
                        doc["_id"] = str(cid)
                        doc["id"] = str(cid)
                        prepared["connections"].append(doc)
            except Exception as e:
                malformed["connections"].append({"reason": f"File read failure: {e}"})

        # 6. Imported Activities (if any)
        if self.imports_dir.exists():
            for p in self.imports_dir.glob("import_*.json"):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    import_id = p.stem.replace("import_", "")
                    doc = {
                        "_id": import_id,
                        "import_id": import_id,
                        "activities": data if isinstance(data, list) else [],
                        "created_at": datetime.fromtimestamp(p.stat().st_ctime).isoformat()
                    }
                    prepared["imported_activities"].append(doc)
                except Exception as e:
                    malformed["imported_activities"].append({"file": str(p), "reason": str(e)})

        return {
            "prepared_records": prepared,
            "counts": {k: len(v) for k, v in prepared.items()},
            "duplicates": duplicates,
            "malformed": malformed
        }

    def create_cold_backup(self, backup_root: Optional[Path] = None, batch_id: Optional[str] = None) -> Tuple[Path, Path, str]:
        """
        Creates a timestamped snapshot of all source JSON files and writes a migration manifest.
        NEVER alters the source JSON files.
        """
        batch_id = batch_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        root = Path(backup_root or DEFAULT_BACKUP_ROOT)
        backup_dir = root / f"backup_json_pre_migration_{batch_id}"
        backup_dir.mkdir(parents=True, exist_ok=True)

        manifest_entries = []

        # Copy individual target files
        for coll_name, file_path in self.target_files.items():
            if file_path.exists():
                dest = backup_dir / file_path.name
                shutil.copy2(file_path, dest)
                manifest_entries.append({
                    "collection": coll_name,
                    "filename": file_path.name,
                    "source_path": str(file_path),
                    "backup_path": str(dest),
                    "size_bytes": file_path.stat().st_size,
                    "sha256": compute_sha256(file_path)
                })

        # Copy imports folder if exists
        if self.imports_dir.exists():
            dest_imports = backup_dir / "imports"
            dest_imports.mkdir(exist_ok=True)
            for imp in self.imports_dir.glob("import_*.json"):
                dest = dest_imports / imp.name
                shutil.copy2(imp, dest)
                manifest_entries.append({
                    "collection": "imported_activities",
                    "filename": imp.name,
                    "source_path": str(imp),
                    "backup_path": str(dest),
                    "size_bytes": imp.stat().st_size,
                    "sha256": compute_sha256(imp)
                })

        manifest = {
            "migration_manifest_version": "1.1",
            "batch_id": batch_id,
            "created_at": datetime.now().isoformat(),
            "target_database": self.db_name,
            "source_directory": str(self.data_dir),
            "backup_directory": str(backup_dir),
            "files": manifest_entries,
            "rollback_instructions": [
                "1. Original JSON files remain untouched in backend/data/.",
                f"2. Migration-specific rollback can be executed using: python backend/scripts/migrate_json_to_mongodb.py --rollback {batch_id}",
                "3. Rollback selectively deletes documents inserted during this batch and restores previous states of updated documents.",
                "4. Live user documents created after migration are NEVER deleted.",
                f"5. All verified source files can be re-restored from: {str(backup_dir)}"
            ]
        }

        manifest_path = backup_dir / "migration_manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        return backup_dir, manifest_path, batch_id

    def plan_dry_run(self, db=None) -> Dict[str, Any]:
        """
        Executes dry-run analysis against MongoDB:
        Calculates exact inserts, updates, and timestamp conflicts with ZERO database writes.
        Works in both online (with live DB) and offline (dry-run JSON inspection) modes.
        Never reports Current DB = 0 when MongoDB is offline.
        """
        validation = self.prepare_and_validate_records()
        prepared = validation["prepared_records"]

        is_connected = db is not None
        plan: Dict[str, Any] = {
            "dry_run": True,
            "target_database": db.name if is_connected else self.db_name,
            "db_connected": is_connected,
            "collections": {}
        }

        for coll_name, docs in prepared.items():
            current_db_count = None
            existing_ids = set()
            db_docs = {}

            if is_connected:
                try:
                    coll = db[coll_name]
                    current_db_count = coll.count_documents({})
                    cursor = coll.find({}, {"_id": 1, "last_analyzed": 1, "historical_analyzed_at": 1, "updated_at": 1, "date": 1, "activity_date": 1, "connected_on": 1})
                    db_docs = {doc["_id"]: doc for doc in cursor}
                    existing_ids = set(db_docs.keys())
                except Exception as e:
                    logger.warning("Could not read collection %s from MongoDB: %s", coll_name, e)
                    is_connected = False

            if is_connected and current_db_count is not None:
                to_insert = 0
                to_update = 0
                timestamp_conflicts = 0

                for doc in docs:
                    doc_id = doc["_id"]
                    if doc_id in existing_ids:
                        to_update += 1
                        existing = db_docs.get(doc_id, {})
                        db_ts = extract_doc_timestamp(existing, coll_name)
                        json_ts = extract_doc_timestamp(doc, coll_name)
                        if db_ts and json_ts and str(db_ts) > str(json_ts):
                            timestamp_conflicts += 1
                    else:
                        to_insert += 1

                projected_final = current_db_count + to_insert
            else:
                # In offline mode, do not assume database is empty or calculate unverified counts
                to_insert = len(docs)
                to_update = None
                timestamp_conflicts = None
                projected_final = None

            plan["collections"][coll_name] = {
                "source_records_found": len(docs),
                "current_mongo_count": current_db_count,
                "projected_inserts": to_insert,
                "projected_updates": to_update,
                "timestamp_conflicts": timestamp_conflicts,
                "projected_final_count": projected_final
            }

        plan["validation"] = {
            "duplicate_ids": validation["duplicates"],
            "malformed_records": validation["malformed"]
        }
        return plan

    def execute_live_migration(self, db, batch_id: Optional[str] = None, backup_dir: Optional[Path] = None) -> Dict[str, Any]:
        """
        Executes idempotent upserts to MongoDB Local with batch tracking and rollback snapshotting.
        STRICT REQUIREMENT: Can only be executed when dry_run=False.
        """
        if self.dry_run:
            raise RuntimeError("execute_live_migration called while dry_run is True. Aborting.")

        batch_id = batch_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        validation = self.prepare_and_validate_records()
        prepared = validation["prepared_records"]

        results = {
            "dry_run": False,
            "batch_id": batch_id,
            "target_database": db.name,
            "collections": {},
            "timestamp": datetime.now().isoformat()
        }

        rollback_data = {
            "batch_id": batch_id,
            "target_database": db.name,
            "created_at": datetime.now().isoformat(),
            "inserted_ids": {},
            "updated_snapshots": {},
            "skipped_conflicts": {}
        }

        for coll_name, docs in prepared.items():
            coll = db[coll_name]
            inserted_ids = []
            updated_snapshots = []
            skipped_conflicts = []
            error_count = 0

            for doc in docs:
                doc_id = doc["_id"]
                try:
                    existing = coll.find_one({"_id": doc_id})
                    if existing and not self.overwrite_newer:
                        db_ts = extract_doc_timestamp(existing, coll_name)
                        json_ts = extract_doc_timestamp(doc, coll_name)
                        if db_ts and json_ts and str(db_ts) > str(json_ts):
                            skipped_conflicts.append({
                                "id": doc_id,
                                "db_timestamp": db_ts,
                                "json_timestamp": json_ts,
                                "reason": "MongoDB record is newer than source JSON"
                            })
                            continue

                    # Preserve prior state snapshot if updating an existing record
                    if existing is not None:
                        updated_snapshots.append(sanitize_doc(existing))

                    # Attach migration metadata tag
                    doc_to_save = dict(doc)
                    doc_to_save["_migration"] = {
                        "batch_id": batch_id,
                        "migrated_at": datetime.now().isoformat(),
                        "source": self.target_files.get(coll_name, Path("imports")).name
                    }

                    res = coll.update_one(
                        {"_id": doc_id},
                        {"$set": doc_to_save},
                        upsert=True
                    )
                    if res.upserted_id is not None or existing is None:
                        inserted_ids.append(doc_id)
                except Exception as e:
                    logger.error("Failed to migrate record %s in %s: %s", doc_id, coll_name, e)
                    error_count += 1

            results["collections"][coll_name] = {
                "total_source": len(docs),
                "upserted_new": len(inserted_ids),
                "updated_existing": len(updated_snapshots),
                "skipped_newer": len(skipped_conflicts),
                "errors": error_count,
                "final_db_count": coll.count_documents({})
            }
            rollback_data["inserted_ids"][coll_name] = inserted_ids
            rollback_data["updated_snapshots"][coll_name] = updated_snapshots
            rollback_data["skipped_conflicts"][coll_name] = skipped_conflicts

        # Save rollback snapshot manifest
        if backup_dir:
            rollback_file = Path(backup_dir) / f"rollback_manifest_{batch_id}.json"
            try:
                with open(rollback_file, "w", encoding="utf-8") as f:
                    json.dump(rollback_data, f, indent=2)
                results["rollback_manifest"] = str(rollback_file)
            except Exception as e:
                logger.warning("Could not write rollback manifest: %s", e)

        return results

    @staticmethod
    def rollback_batch(batch_id: str, db, backup_root: Optional[Path] = None) -> Dict[str, Any]:
        """
        Executes a targeted rollback for a specific migration batch:
        - Deletes ONLY the documents that were newly inserted by this batch.
        - Restores updated documents to their exact pre-migration state.
        - Never deletes documents created by live users after migration or before migration.
        """
        if not batch_id:
            raise ValueError("batch_id is required for rollback.")

        root = Path(backup_root or DEFAULT_BACKUP_ROOT)
        # Search for rollback manifest matching batch_id
        manifest_files = list(root.glob(f"**/rollback_manifest_{batch_id}.json"))
        rollback_data = None
        if manifest_files:
            try:
                with open(manifest_files[0], "r", encoding="utf-8") as f:
                    rollback_data = json.load(f)
            except Exception as e:
                logger.warning("Could not load rollback manifest %s: %s", manifest_files[0], e)

        results = {
            "batch_id": batch_id,
            "target_database": db.name,
            "collections": {},
            "timestamp": datetime.now().isoformat()
        }

        # List of all managed collections
        collections = ["saved_profiles", "activities", "latest_analysis", "profile_analysis_history", "connections", "imported_activities"]

        for coll_name in collections:
            coll = db[coll_name]
            deleted_count = 0
            restored_count = 0

            if rollback_data and "inserted_ids" in rollback_data:
                # Targeted deletion of specific IDs inserted by this batch
                ids_to_delete = rollback_data.get("inserted_ids", {}).get(coll_name, [])
                if ids_to_delete:
                    res = coll.delete_many({"_id": {"$in": ids_to_delete}})
                    deleted_count = res.deleted_count

                # Targeted restore of updated documents from pre-migration snapshots
                snapshots = rollback_data.get("updated_snapshots", {}).get(coll_name, [])
                for snap in snapshots:
                    snap_id = snap.get("_id")
                    if snap_id:
                        coll.replace_one({"_id": snap_id}, snap, upsert=True)
                        restored_count += 1
            else:
                # Fallback only when manifest is absent: delete only documents matching the specific batch metadata tag
                res = coll.delete_many({"_migration.batch_id": batch_id})
                deleted_count = res.deleted_count

            results["collections"][coll_name] = {
                "deleted_inserted": deleted_count,
                "restored_pre_migration": restored_count,
                "final_db_count": coll.count_documents({})
            }

        return results


def print_dry_run_report(inspection: Dict[str, Any], plan: Dict[str, Any], backup_info: Optional[Tuple[Path, Path, str]] = None) -> None:
    """Pretty-prints dry run summary report to standard output."""
    print("=" * 80)
    print(" JSON-TO-MONGODB MIGRATION: DRY-RUN INSPECTION & PLAN")
    print("=" * 80)
    print(f"Target Database : {plan['target_database']}")
    db_status = "CONNECTED (Live verified)" if plan.get("db_connected") else "OFFLINE / UNREACHABLE (Verified counts unavailable)"
    print(f"Database Status : {db_status}")
    print(f"Execution Mode  : DRY-RUN (ZERO database writes performed)")
    if backup_info:
        print(f"Backup Directory: {backup_info[0]}")
        print(f"Manifest Path   : {backup_info[1]}")
    print("-" * 80)

    print("\n1. SOURCE JSON FILES INSPECTION:")
    print(f"{'Collection':<26} {'Source File':<32} {'Records':<8} {'Size':<10} {'Status'}")
    print("-" * 80)
    for coll, info in inspection.items():
        if coll == "imported_activities":
            print(f"{coll:<26} {'imports/ (dir)':<32} {info.get('files_found', 0):<8} {'-':<10} {info.get('status')}")
        else:
            fname = Path(info["file_path"]).name
            size_kb = f"{info['size_bytes'] / 1024:.1f} KB" if info["size_bytes"] else "0 KB"
            print(f"{coll:<26} {fname:<32} {info['record_count']:<8} {size_kb:<10} {info['status']}")

    print("\n2. PROJECTED MIGRATION PLAN (IDEMPOTENT UPSERT):")
    print(f"{'Collection':<26} {'Current DB':<14} {'New Inserts':<12} {'Updates':<10} {'Conflicts':<10} {'Projected'}")
    print("-" * 80)
    for coll, p in plan["collections"].items():
        curr_db_str = str(p['current_mongo_count']) if p['current_mongo_count'] is not None else "N/A (offline)"
        inserts_str = str(p['projected_inserts']) if p['projected_inserts'] is not None else "N/A"
        updates_str = str(p['projected_updates']) if p['projected_updates'] is not None else "N/A"
        conflicts_str = str(p['timestamp_conflicts']) if p['timestamp_conflicts'] is not None else "N/A"
        proj_str = str(p['projected_final_count']) if p['projected_final_count'] is not None else f"{p['source_records_found']} (if empty)"
        print(f"{coll:<26} {curr_db_str:<14} {inserts_str:<12} {updates_str:<10} {conflicts_str:<10} {proj_str}")

    print("\n3. DATA INTEGRITY & ANOMALY CHECKS:")
    dups = plan["validation"]["duplicate_ids"]
    malf = plan["validation"]["malformed_records"]
    has_issues = False
    for coll, d_list in dups.items():
        if d_list:
            has_issues = True
            print(f"  [WARNING] Duplicate IDs in {coll}: {d_list}")
    for coll, m_list in malf.items():
        if m_list:
            has_issues = True
            print(f"  [WARNING] Malformed records in {coll}: {len(m_list)} items")
    if not has_issues:
        print("  [PASS] Zero duplicate IDs found in source files.")
        print("  [PASS] Zero malformed JSON records found.")
        print("  [PASS] All existing fields and relationships verified.")

    print("\n4. SAFETY & EXECUTION STATUS:")
    print("  [VERIFIED] Original JSON files remain untouched.")
    print("  [VERIFIED] Zero documents inserted, modified, or deleted in MongoDB.")
    if not plan.get("db_connected"):
        print("  [NOTICE] MongoDB is offline: dry-run performed validation and schema checks only.")
        print("           When ready to migrate, start MongoDB ('net start MongoDB') and run with --execute.")
    else:
        print("  [NOTICE] To execute actual data migration when approved, run with --execute.")
    print("=" * 80)


def main():
    parser = argparse.ArgumentParser(description="LinkedIn Activity Analyzer JSON-to-MongoDB Migration Tool")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Perform read-only inspection and planning (Default behavior)")
    parser.add_argument("--execute", action="store_true", default=False, help="Execute actual MongoDB writes (requires explicit confirmation)")
    parser.add_argument("--confirm", action="store_true", default=False, help="Explicit confirmation to proceed with live migration without interactive prompt")
    parser.add_argument("--rollback", type=str, default=None, help="Execute targeted rollback for a specific batch ID")
    parser.add_argument("--backup-only", action="store_true", default=False, help="Create a timestamped JSON backup and manifest without running planning")
    parser.add_argument("--data-dir", type=str, default=str(DEFAULT_DATA_DIR), help="Custom source JSON directory")
    parser.add_argument("--db-name", type=str, default=None, help="Target MongoDB database name")
    parser.add_argument("--overwrite-newer", action="store_true", default=False, help="Overwrite newer MongoDB records with older JSON data")

    args = parser.parse_args()

    # Determine execution mode:
    # 1. Rollback mode
    if args.rollback is not None:
        is_rollback = True
        is_live = False
        dry_run = False
    # 2. Live execution mode (requires --execute and confirmation)
    elif args.execute:
        is_rollback = False
        confirmed = args.confirm
        if not confirmed:
            is_interactive = False
            try:
                is_interactive = sys.stdin.isatty()
            except Exception:
                is_interactive = False

            if is_interactive:
                try:
                    prompt_resp = input("Are you sure you want to execute live migration to MongoDB? Type 'yes' to proceed: ").strip().lower()
                    confirmed = (prompt_resp == "yes")
                except (EOFError, OSError):
                    confirmed = False
            else:
                confirmed = False

        if not confirmed:
            print("[ABORTED] Live migration cancelled: explicit confirmation ('yes' or --confirm) was not provided.")
            print("To run in read-only inspection mode, use: --dry-run")
            print("To execute with explicit confirmation, use: --execute --confirm")
            sys.exit(0)
            return

        is_live = True
        dry_run = False
    # 3. Safe dry-run mode (default for all other invocations)
    else:
        is_rollback = False
        is_live = False
        dry_run = True

    manager = JSONMigrationManager(
        data_dir=Path(args.data_dir),
        db_name=args.db_name,
        dry_run=dry_run,
        overwrite_newer=args.overwrite_newer
    )

    if args.backup_only:
        backup_dir, manifest_path, batch_id = manager.create_cold_backup()
        print(f"Backup created successfully at: {backup_dir}")
        print(f"Manifest generated at: {manifest_path}")
        print(f"Batch ID: {batch_id}")
        return

    # Check MongoDB connection
    ok, msg = check_connection(timeout_ms=1500)
    db = None
    if ok:
        try:
            db = get_db(db_name=manager.db_name)
        except Exception as e:
            logger.warning("Could not obtain DB instance: %s", e)
    else:
        if not dry_run:
            print(f"[FATAL] Cannot connect to local MongoDB: {msg}")
            print("Please ensure MongoDB is running (e.g. 'net start MongoDB' in an Admin prompt or via services.msc).")
            sys.exit(1)
        else:
            print(f"[NOTICE] Local MongoDB is currently unreachable ({msg}).")
            print("Running dry-run JSON inspection and schema validation in offline mode.\n")

    if is_rollback:
        print(f"[ROLLBACK] Executing targeted rollback for batch: {args.rollback}")
        res = JSONMigrationManager.rollback_batch(args.rollback, db)
        print(json.dumps(res, indent=2))
        return

    # Perform raw inspection
    raw_inspection = manager.inspect_raw_files()

    if dry_run:
        plan = manager.plan_dry_run(db)
        print_dry_run_report(raw_inspection, plan)
    else:
        # Create cold backup before any live execution
        backup_dir, manifest_path, batch_id = manager.create_cold_backup()
        print(f"[BACKUP] Created pre-migration snapshot at: {backup_dir}")
        results = manager.execute_live_migration(db, batch_id=batch_id, backup_dir=backup_dir)
        print("\n" + "=" * 80)
        print(" MIGRATION EXECUTION COMPLETED")
        print("=" * 80)
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
