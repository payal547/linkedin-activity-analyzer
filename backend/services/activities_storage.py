"""
Persistent Storage Service for LinkedIn Activities.
Stores activity records in MongoDB Local with JSON dual-write and graceful fallback.
"""

import json
import logging
import os
import re
import sys
import uuid
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "activities.json"
TEST_DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "test_activities.json"


def _is_testing() -> bool:
    try:
        from flask import current_app
        if current_app and current_app.config.get("TESTING"):
            return True
    except Exception:
        pass
    return "unittest" in sys.modules or "pytest" in sys.modules or os.getenv("TESTING") == "1"


def _get_storage_file() -> Path:
    if _is_testing():
        return TEST_DATA_FILE
    return DATA_FILE


def _ensure_storage() -> None:
    storage_path = _get_storage_file()
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    if not storage_path.exists():
        with open(storage_path, "w", encoding="utf-8") as f:
            json.dump([], f)


def _sync_test_state_if_needed(coll) -> None:
    if _is_testing() and coll is not None:
        test_file = _get_storage_file()
        if not test_file.exists():
            try:
                coll.delete_many({})
            except Exception:
                pass


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
        coll = db["activities"]
        _sync_test_state_if_needed(coll)
        return coll, sanitize_doc
    except Exception as e:
        logger.warning("Could not obtain MongoDB collection activities: %s", e)
        return None, None


def _extract_canonical_slug(url_or_slug: Optional[str]) -> str:
    """Safely extracts canonical slug for profile-isolated queries."""
    if not url_or_slug:
        return ""
    cleaned = str(url_or_slug).strip()
    if "linkedin.com/in" in cleaned.lower():
        match = re.search(r'(?:https?:\/\/)?(?:[a-zA-Z0-9_\-\.]+\.)?linkedin\.com\/in\/([a-zA-Z0-9_\-\%]+)', cleaned, re.IGNORECASE)
        if match:
            slug = match.group(1).rstrip('/').lower()
            if slug and slug != "in":
                return slug
        return ""
    if cleaned.lower().startswith("http") or "linkedin.com" in cleaned.lower():
        return ""
    sanitized = re.sub(r'[^a-zA-Z0-9_\-]', '', cleaned.lower())
    if sanitized in ("", "in", "httpswwwlinkedincomin", "httpwwwlinkedincomin", "linkedincomin"):
        return ""
    return sanitized


def _load_activities_from_json(subject_name: Optional[str] = None, subject_id: Optional[str] = None, subject_url: Optional[str] = None) -> List[Dict[str, Any]]:
    _ensure_storage()
    storage_path = _get_storage_file()
    try:
        with open(storage_path, "r", encoding="utf-8") as f:
            activities = json.load(f)
            if not isinstance(activities, list):
                return []
            if not subject_name and not subject_id and not subject_url:
                return activities

            matched = []
            s_name = (subject_name or "").strip().lower()
            s_id = (subject_id or "").strip().lower()
            s_url = (subject_url or "").strip().lower()
            target_slug = _extract_canonical_slug(subject_url) or _extract_canonical_slug(subject_id)

            for a in activities:
                a_name = (a.get("subject_name") or "").strip().lower()
                a_id = (a.get("subject_id") or "").strip().lower()
                a_url = (a.get("subject_url") or "").strip().lower()
                a_slug = _extract_canonical_slug(a_url) or _extract_canonical_slug(a_id)

                if target_slug and a_slug:
                    if target_slug == a_slug:
                        matched.append(a)
                elif s_id and a_id and s_id == a_id:
                    matched.append(a)
                elif s_name and a_name and s_name == a_name:
                    matched.append(a)
                elif not a_name and not a_id and not a_url and not target_slug and not s_name and not s_id:
                    matched.append(a)

            return matched
    except Exception:
        return []


def _save_activities_to_json(activities: List[Dict[str, Any]]) -> None:
    _ensure_storage()
    storage_path = _get_storage_file()
    temp_path = storage_path.with_suffix(".tmp")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(activities, f, indent=2, ensure_ascii=False)
        temp_path.replace(storage_path)
    except Exception as e:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass
        raise e


def load_saved_activities(subject_name: Optional[str] = None, subject_id: Optional[str] = None, subject_url: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Loads activities from MongoDB primary storage with fallback to JSON.
    Supports filtering by subject_name, subject_id, or subject_url with strict profile isolation.
    Never matches loose /in/ URLs across different profiles.
    """
    coll, sanitize_fn = _get_mongo_collection()
    if coll is not None and sanitize_fn is not None:
        try:
            query: Dict[str, Any] = {}
            target_slug = _extract_canonical_slug(subject_url) or _extract_canonical_slug(subject_id)
            if target_slug:
                # Strictly isolate by canonical slug
                query["$or"] = [
                    {"subject_id": target_slug},
                    {"subject_id": (subject_id or "").strip()},
                    {"subject_url": {"$regex": rf"/in/{re.escape(target_slug)}(?:/|\?|$)", "$options": "i"}}
                ]
            elif subject_url:
                # URL without recognized slug: match exact URL string, never loose /in/
                query["subject_url"] = subject_url.strip()
            elif subject_id:
                query["subject_id"] = subject_id.strip()
            elif subject_name:
                query["subject_name"] = {"$regex": f"^{re.escape(subject_name.strip())}$", "$options": "i"}

            cursor = coll.find(query, {"_id": 0, "_migration": 0}).sort("date", -1)
            docs = [sanitize_fn(d) for d in cursor]
            if docs or _is_testing():
                return docs
            # If MongoDB has 0 in production, verify JSON fallback
            json_docs = _load_activities_from_json(subject_name=subject_name, subject_id=subject_id, subject_url=subject_url)
            if json_docs:
                return json_docs
            return docs
        except Exception as e:
            logger.warning("MongoDB load_saved_activities query failed: %s; falling back to JSON.", e)

    return _load_activities_from_json(subject_name=subject_name, subject_id=subject_id, subject_url=subject_url)


def save_activity(activity: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Saves a new activity to MongoDB and dual-writes to JSON storage.
    Returns the updated activity list.
    """
    if not activity:
        return load_saved_activities()

    act_copy = dict(activity)
    act_id = act_copy.get("id") or f"act_{uuid.uuid4().hex[:10]}"
    act_copy["id"] = act_id

    # 1. Primary write to MongoDB
    coll, _ = _get_mongo_collection()
    if coll is not None:
        try:
            coll.update_one({"id": act_id}, {"$set": act_copy}, upsert=True)
        except Exception as e:
            logger.warning("MongoDB save_activity failed for %s: %s", act_id, e)

    # 2. Dual-write to JSON storage (prepend newest first)
    try:
        activities = _load_activities_from_json()
        # Remove existing if updating
        activities = [a for a in activities if a.get("id") != act_id]
        activities.insert(0, act_copy)
        _save_activities_to_json(activities)
    except Exception as e:
        logger.warning("JSON dual-write failed in save_activity: %s", e)

    return load_saved_activities()


def save_activities_batch(activities: List[Dict[str, Any]]) -> int:
    """
    Saves a batch of activities to MongoDB using efficient bulk upsert,
    and atomically syncs with JSON storage.
    Preserves existing records and guarantees deduplication by stable ID.
    Returns the count of successfully processed and saved activities.
    """
    if not activities or not isinstance(activities, list):
        return 0

    valid_copies = []
    for act in activities:
        if not isinstance(act, dict):
            continue
        c = dict(act)
        act_id = c.get("id") or f"act_{uuid.uuid4().hex[:10]}"
        c["id"] = act_id
        valid_copies.append(c)

    if not valid_copies:
        return 0

    # 1. Primary write to MongoDB
    coll, _ = _get_mongo_collection()
    if coll is not None:
        try:
            try:
                from pymongo import UpdateOne
                ops = [UpdateOne({"id": a["id"]}, {"$set": a}, upsert=True) for a in valid_copies]
                if ops:
                    coll.bulk_write(ops, ordered=False)
            except ImportError:
                for a in valid_copies:
                    coll.update_one({"id": a["id"]}, {"$set": a}, upsert=True)
        except Exception as e:
            logger.warning("MongoDB bulk save_activities_batch failed: %s", e)

    # 2. Dual-write to JSON storage atomically
    try:
        current_acts = _load_activities_from_json()
        indexed = {a.get("id"): a for a in current_acts if a.get("id")}
        for a in valid_copies:
            indexed[a["id"]] = a
        merged = sorted(
            list(indexed.values()),
            key=lambda x: str(x.get("date") or ""),
            reverse=True
        )
        _save_activities_to_json(merged)
    except Exception as e:
        logger.warning("JSON dual-write failed in save_activities_batch: %s", e)

    return len(valid_copies)


def delete_activity(activity_id: str) -> bool:
    """
    Deletes an activity from MongoDB and JSON storage by ID.
    Returns True if deleted, False otherwise.
    """
    if not activity_id:
        return False

    mongo_deleted = False
    coll, _ = _get_mongo_collection()
    if coll is not None:
        try:
            res = coll.delete_one({"id": activity_id})
            mongo_deleted = (res.deleted_count > 0)
        except Exception as e:
            logger.warning("MongoDB delete_activity failed for %s: %s", activity_id, e)

    # Sync with JSON
    activities = _load_activities_from_json()
    new_list = [a for a in activities if a.get("id") != activity_id]
    json_deleted = (len(new_list) < len(activities))
    if json_deleted:
        try:
            _save_activities_to_json(new_list)
        except Exception as e:
            logger.warning("JSON sync failed during delete_activity: %s", e)

    return mongo_deleted or json_deleted


def clear_all_activities() -> List[Dict[str, Any]]:
    """Clears all activities from MongoDB and JSON storage."""
    coll, _ = _get_mongo_collection()
    if coll is not None:
        try:
            coll.delete_many({})
        except Exception as e:
            logger.warning("MongoDB clear_all_activities failed: %s", e)

    try:
        _save_activities_to_json([])
    except Exception as e:
        logger.warning("JSON clear_all_activities failed: %s", e)

    return []
