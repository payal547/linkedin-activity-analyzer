"""
Server-Side Storage Service for User-Imported LinkedIn Activity Archives.
Stores imported CSV/ZIP activity records in MongoDB Local with JSON dual-write/fallback.
Prevents browser cookie overflow (> 4093 bytes) and session corruption.
"""

import json
import logging
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "imports"
TEST_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "test_imports"


def _is_testing() -> bool:
    try:
        from flask import current_app
        if current_app and current_app.config.get("TESTING"):
            return True
    except Exception:
        pass
    return "unittest" in sys.modules or "pytest" in sys.modules or os.getenv("TESTING") == "1"


def _get_imports_dir() -> Path:
    target_dir = TEST_DATA_DIR if _is_testing() else DATA_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir


def _get_mongo_collection():
    try:
        from db.mongo import get_db, sanitize_doc
    except ImportError:
        try:
            from backend.db.mongo import get_db, sanitize_doc
        except ImportError:
            return None, None
    try:
        db = get_db()
        return db["imported_activities"], sanitize_doc
    except Exception as e:
        logger.warning("Could not obtain MongoDB collection imported_activities: %s", e)
        return None, None


def save_imported_activities(activities: List[Dict[str, Any]], import_id: Optional[str] = None) -> str:
    """
    Saves imported activity records to MongoDB Local with JSON file dual-write.
    Returns the unique import_id.
    """
    if not import_id:
        import_id = uuid.uuid4().hex

    now_iso = datetime.now().isoformat()

    # 1. Primary write to MongoDB
    coll, _ = _get_mongo_collection()
    if coll is not None:
        try:
            doc = {
                "import_id": import_id,
                "activities": activities,
                "count": len(activities) if isinstance(activities, list) else 0,
                "created_at": now_iso
            }
            coll.update_one({"import_id": import_id}, {"$set": doc}, upsert=True)
        except Exception as e:
            logger.warning("MongoDB save_imported_activities failed for %s: %s", import_id, e)

    # 2. Dual-write to JSON file in imports directory
    directory = _get_imports_dir()
    file_path = directory / f"import_{import_id}.json"
    temp_file = file_path.with_suffix(".tmp")
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(activities, f, indent=2, ensure_ascii=False)
        temp_file.replace(file_path)
    except Exception as e:
        if temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass
        logger.warning("JSON dual-write failed in save_imported_activities: %s", e)

    return import_id


def load_imported_activities(import_id: str) -> List[Dict[str, Any]]:
    """
    Loads imported activity records from MongoDB Local by import_id with fallback to JSON.
    """
    if not import_id:
        return []

    clean_id = os.path.basename(str(import_id).strip())

    # 1. MongoDB primary query
    coll, sanitize_fn = _get_mongo_collection()
    if coll is not None and sanitize_fn is not None:
        try:
            doc = coll.find_one({"import_id": clean_id})
            if doc and "activities" in doc:
                return sanitize_fn(doc["activities"])
        except Exception as e:
            logger.warning("MongoDB load_imported_activities failed for %s: %s", clean_id, e)

    # 2. Disk fallback
    directory = _get_imports_dir()
    file_path = directory / f"import_{clean_id}.json"
    if file_path.exists():
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception:
            return []

    return []


def delete_imported_activities(import_id: str) -> bool:
    """Deletes temporary imported records from MongoDB and disk."""
    if not import_id:
        return False

    clean_id = os.path.basename(str(import_id).strip())
    mongo_deleted = False

    # 1. Remove from MongoDB
    coll, _ = _get_mongo_collection()
    if coll is not None:
        try:
            res = coll.delete_one({"import_id": clean_id})
            mongo_deleted = (res.deleted_count > 0)
        except Exception as e:
            logger.warning("MongoDB delete_imported_activities failed for %s: %s", clean_id, e)

    # 2. Remove file from disk
    directory = _get_imports_dir()
    file_path = directory / f"import_{clean_id}.json"
    file_deleted = False
    if file_path.exists():
        try:
            file_path.unlink()
            file_deleted = True
        except Exception:
            pass

    return mongo_deleted or file_deleted


def clear_all_imported_activities() -> None:
    """Cleans up all imported records from MongoDB and imports directory."""
    coll, _ = _get_mongo_collection()
    if coll is not None:
        try:
            coll.delete_many({})
        except Exception as e:
            logger.warning("MongoDB clear_all_imported_activities failed: %s", e)

    directory = _get_imports_dir()
    if directory.exists():
        for item in directory.glob("import_*.json"):
            try:
                item.unlink()
            except Exception:
                pass
