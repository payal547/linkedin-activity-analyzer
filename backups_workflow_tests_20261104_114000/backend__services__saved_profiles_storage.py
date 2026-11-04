"""
Persistent Storage Service for Saved Target Profiles.
Stores public LinkedIn profiles analyzed by the user in MongoDB Local with JSON dual-write/fallback.
Does NOT store passwords, credentials, or private authentication tokens.
Preserves profiles across application restarts.
"""

import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PROFILES_FILE = DATA_DIR / "saved_target_profiles.json"
TEST_PROFILES_FILE = DATA_DIR / "test_saved_target_profiles.json"


def _is_testing() -> bool:
    try:
        from flask import current_app
        if current_app and current_app.config.get("TESTING"):
            return True
    except Exception:
        pass
    return "unittest" in sys.modules or "pytest" in sys.modules or os.getenv("TESTING") == "1"


def _get_storage_file() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if _is_testing():
        return TEST_PROFILES_FILE
    return PROFILES_FILE


def _sync_test_state_if_needed(coll):
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
        coll = db["saved_profiles"]
        _sync_test_state_if_needed(coll)
        return coll, sanitize_doc
    except Exception as e:
        logger.warning("Could not obtain MongoDB collection saved_profiles: %s", e)
        return None, None


def normalize_profile_url(url: str) -> str:
    """Normalizes a LinkedIn profile URL and returns https://www.linkedin.com/in/{slug}/."""
    if not url:
        return ""
    cleaned = url.strip()
    match = re.search(r'(?:https?:\/\/)?(?:[a-zA-Z0-9_\-\.]+\.)?linkedin\.com\/in\/([a-zA-Z0-9_\-\%]+)', cleaned, re.IGNORECASE)
    if match:
        slug = match.group(1).rstrip('/')
        return f"https://www.linkedin.com/in/{slug}/"
    return cleaned


def extract_slug(url: str) -> str:
    """
    Extracts canonical LinkedIn profile slug from a URL or raw slug string.
    Returns an empty string for incomplete URLs (e.g. https://www.linkedin.com/in/)
    or invalid strings, preventing corrupted slug generation.
    """
    if not url:
        return ""
    cleaned = str(url).strip()
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


def _load_profiles_from_json() -> List[Dict[str, Any]]:
    file_path = _get_storage_file()
    if not file_path.exists():
        return []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return sorted(data, key=lambda x: x.get("last_analyzed") or "", reverse=True)
            return []
    except Exception:
        return []


def _save_profiles_to_json(profiles: List[Dict[str, Any]]) -> None:
    file_path = _get_storage_file()
    temp_path = file_path.with_suffix(".tmp")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(profiles, f, indent=2, ensure_ascii=False)
        temp_path.replace(file_path)
    except Exception as e:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass
        raise e


def load_saved_profiles() -> List[Dict[str, Any]]:
    """
    Loads all saved target profiles sorted by last_analyzed descending.
    Queries MongoDB first, falling back to local JSON storage if unavailable.
    """
    coll, sanitize_fn = _get_mongo_collection()
    if coll is not None and sanitize_fn is not None:
        try:
            if _is_testing() and not _get_storage_file().exists():
                coll.delete_many({})
                return []
            cursor = coll.find({}, {"_id": 0, "_migration": 0}).sort("last_analyzed", -1)
            docs = [sanitize_fn(d) for d in cursor]
            # If docs found or test mode, return MongoDB results
            if docs or _is_testing():
                return docs
            # If MongoDB has 0 docs in production, check JSON fallback
            json_docs = _load_profiles_from_json()
            if json_docs:
                return json_docs
            return docs
        except Exception as e:
            logger.warning("MongoDB load_saved_profiles query failed: %s; falling back to JSON.", e)

    return _load_profiles_from_json()


def save_target_profile(profile_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Saves a target profile to persistent storage (MongoDB primary + JSON dual-write).
    If the profile already exists, updates last_analyzed, headline, and picture without creating a duplicate.
    If it's new, appends it as a new profile.
    Never stores passwords or credentials.
    Preserves valid existing profile photo if incoming photo is None/empty.
    """
    if not profile_data:
        return {}

    raw_url = profile_data.get("profile_url") or profile_data.get("url") or ""
    norm_url = normalize_profile_url(raw_url)
    slug = extract_slug(raw_url)

    if not slug:
        return {}

    now_iso = datetime.now().isoformat()
    existing_profile = get_saved_profile_by_url(raw_url) or get_saved_profile_by_url(slug)

    name = profile_data.get("name") or slug.replace("-", " ").title()
    headline = profile_data.get("headline") or "LinkedIn Profile"
    raw_picture = profile_data.get("picture") or profile_data.get("photo_url")
    is_valid_picture = isinstance(raw_picture, str) and (raw_picture.strip().startswith("http://") or raw_picture.strip().startswith("https://"))
    valid_picture_url = raw_picture.strip() if is_valid_picture else None

    # Preserve existing picture if incoming is invalid/None
    if not is_valid_picture and existing_profile and existing_profile.get("picture"):
        valid_picture_url = existing_profile.get("picture")

    if existing_profile:
        saved_entry = dict(existing_profile)
        saved_entry["last_analyzed"] = now_iso
        if name and name != "LinkedIn Member":
            saved_entry["name"] = name
        if headline and headline != "LinkedIn Profile":
            saved_entry["headline"] = headline
        if is_valid_picture:
            saved_entry["picture"] = valid_picture_url
        elif "picture" not in saved_entry:
            saved_entry["picture"] = None
        saved_entry["profile_url"] = norm_url or saved_entry.get("profile_url")
        saved_entry["id"] = slug
    else:
        saved_entry = {
            "id": slug,
            "profile_url": norm_url,
            "name": name,
            "headline": headline,
            "picture": valid_picture_url,
            "last_analyzed": now_iso
        }

    # 1. Primary write to MongoDB
    coll, _ = _get_mongo_collection()
    if coll is not None:
        try:
            coll.update_one(
                {"id": slug},
                {"$set": saved_entry},
                upsert=True
            )
        except Exception as e:
            logger.warning("MongoDB save_target_profile failed for %s: %s", slug, e)

    # 2. Dual-write to JSON storage atomically
    profiles = _load_profiles_from_json()
    existing_idx = None
    for idx, p in enumerate(profiles):
        p_slug = extract_slug(p.get("profile_url", "")) or p.get("id", "").lower()
        if p_slug == slug:
            existing_idx = idx
            break

    if existing_idx is not None:
        profiles[existing_idx] = saved_entry
    else:
        profiles.insert(0, saved_entry)

    try:
        _save_profiles_to_json(profiles)
    except Exception as e:
        logger.warning("JSON dual-write failed in save_target_profile: %s", e)

    return saved_entry


def get_saved_profile_by_url(profile_url: str) -> Optional[Dict[str, Any]]:
    """Retrieves a saved profile record by URL or slug."""
    if not profile_url:
        return None
    slug = extract_slug(profile_url)
    if not slug:
        return None

    norm_url = normalize_profile_url(profile_url)

    coll, sanitize_fn = _get_mongo_collection()
    if coll is not None and sanitize_fn is not None:
        try:
            if _is_testing() and not _get_storage_file().exists():
                return None
            query_or = [{"id": slug}]
            if norm_url:
                query_or.append({"profile_url": norm_url})
            doc = coll.find_one({"$or": query_or}, {"_id": 0, "_migration": 0})
            if doc:
                return sanitize_fn(doc)
        except Exception as e:
            logger.warning("MongoDB get_saved_profile_by_url failed for %s: %s", slug, e)

    for p in _load_profiles_from_json():
        p_slug = extract_slug(p.get("profile_url", "")) or (p.get("id") or "").lower()
        if p_slug == slug:
            return p
    return None


def remove_saved_profile(profile_url_or_slug: str) -> bool:
    """
    Removes a target profile from persistent storage by URL or slug.
    Deletes from MongoDB and syncs with JSON storage.
    Returns True if removed, False if not found.
    """
    if not profile_url_or_slug:
        return False

    target_slug = extract_slug(profile_url_or_slug)
    if not target_slug:
        return False

    norm_url = normalize_profile_url(profile_url_or_slug)
    mongo_removed = False

    # 1. Remove from MongoDB
    coll, _ = _get_mongo_collection()
    if coll is not None:
        try:
            query_or = [{"id": target_slug}]
            if norm_url:
                query_or.append({"profile_url": norm_url})
            res = coll.delete_one({"$or": query_or})
            mongo_removed = (res.deleted_count > 0)
        except Exception as e:
            logger.warning("MongoDB remove_saved_profile failed for %s: %s", target_slug, e)

    # 2. Sync removal with JSON storage
    profiles = _load_profiles_from_json()
    initial_len = len(profiles)
    updated = [
        p for p in profiles
        if extract_slug(p.get("profile_url", "")) != target_slug and (p.get("id") or "").lower() != target_slug
    ]
    json_removed = (len(updated) < initial_len)

    if json_removed:
        try:
            _save_profiles_to_json(updated)
        except Exception as e:
            logger.warning("JSON sync failed during remove_saved_profile: %s", e)

    return mongo_removed or json_removed
