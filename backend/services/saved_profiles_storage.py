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
from typing import List, Dict, Any, Optional, Tuple
import urllib.parse

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
        # STRICT PRODUCTION SAFETY: Never wipe or delete from a non-test collection/database
        db_name = getattr(coll.database, "name", "")
        if db_name != "linkedin_activity_analyzer_test":
            logger.error("SAFETY GUARD: Refusing to delete profiles from non-test database '%s'", db_name)
            return

        test_file = _get_storage_file()
        if test_file.name == PROFILES_FILE.name:
            logger.error("SAFETY GUARD: Refusing to sync test state against production file '%s'", test_file)
            return

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


def parse_linkedin_url(url: str) -> Tuple[bool, str, Optional[str], Optional[str]]:
    """
    Parses and validates a LinkedIn member profile URL.
    Handles:
    - Short URLs (e.g. https://www.linkedin.com/in/username/)
    - Subdomains (e.g. https://in.linkedin.com/in/username, uk.linkedin.com)
    - URLs with or without scheme (http/https/none)
    - URLs with long query parameters / tracking tags (?utm_source=...&utm_content=...)
    - URL-encoded characters (e.g. %2D -> -)
    - Extra trailing slashes, dots, whitespace
    - Raw profile slugs (e.g. 'satyanadella')
    
    Rejects:
    - Empty / whitespace inputs
    - Non-member URLs: company pages (/company/...), school pages (/school/...),
      post/activity URLs (/posts/..., /feed/..., /pulse/..., /learning/...),
      search URLs (/search/...)
    - Unrelated websites
    - Incomplete profile URLs (/in/, /in)
    
    Returns:
    (is_valid: bool, error_message: str, canonical_slug: Optional[str], canonical_url: Optional[str])
    """
    if not url or not str(url).strip():
        return False, "Please enter a LinkedIn profile URL.", None, None

    cleaned = str(url).strip()

    # Pre-check: decode percent encoding safely
    try:
        decoded_candidate = urllib.parse.unquote(cleaned)
    except Exception:
        decoded_candidate = cleaned

    lower_candidate = decoded_candidate.lower()

    # Detect unsupported non-profile LinkedIn sections explicitly
    if any(p in lower_candidate for p in ("linkedin.com/company", "/company/")):
        return False, "Company pages (linkedin.com/company/...) cannot be analyzed as member profiles. Please provide an individual LinkedIn member profile URL (e.g., https://www.linkedin.com/in/username).", None, None
    if any(p in lower_candidate for p in ("linkedin.com/school", "/school/")):
        return False, "School pages (linkedin.com/school/...) cannot be analyzed as member profiles. Please provide an individual LinkedIn member profile URL (e.g., https://www.linkedin.com/in/username).", None, None
    if any(p in lower_candidate for p in ("linkedin.com/posts/", "linkedin.com/feed/update/", "linkedin.com/pulse/", "/posts/", "/pulse/")):
        return False, "Post, feed, and article URLs cannot be analyzed as member profiles. Please provide an individual member profile URL (e.g., https://www.linkedin.com/in/username).", None, None
    if any(p in lower_candidate for p in ("linkedin.com/search", "/search/")):
        return False, "Search URLs cannot be analyzed as member profiles. Please provide an individual member profile URL (e.g., https://www.linkedin.com/in/username).", None, None
    if any(p in lower_candidate for p in ("linkedin.com/feed", "/feed")):
        if "/in/" not in lower_candidate:
            return False, "LinkedIn feed URLs cannot be analyzed as member profiles. Please provide an individual member profile URL (e.g., https://www.linkedin.com/in/username).", None, None

    # Handle full or scheme-less URL
    url_to_parse = cleaned
    if not url_to_parse.startswith(("http://", "https://")):
        if "linkedin.com" in lower_candidate:
            url_to_parse = "https://" + cleaned.lstrip("/")
        else:
            # Check if this is a raw slug directly (e.g., "satyanadella", "akanksha-chauhan-5095b7266")
            sanitized_slug = re.sub(r'[^a-zA-Z0-9_\-]', '', cleaned.lower())
            if sanitized_slug and sanitized_slug not in ("", "in", "http", "https"):
                canonical_slug = sanitized_slug
                canonical_url = f"https://www.linkedin.com/in/{canonical_slug}/"
                return True, "", canonical_slug, canonical_url
            return False, "Invalid LinkedIn profile URL. Example: https://www.linkedin.com/in/username", None, None

    try:
        parsed = urllib.parse.urlsplit(url_to_parse)
    except Exception:
        return False, "Malformed LinkedIn URL.", None, None

    hostname = (parsed.hostname or "").lower()
    if not (hostname == "linkedin.com" or hostname.endswith(".linkedin.com")):
        return False, "Invalid URL. Please enter a valid LinkedIn member profile URL (e.g., https://www.linkedin.com/in/username).", None, None

    path = parsed.path or ""

    # Check for /in/ section
    match = re.search(r'(?:^|\/)in\/([a-zA-Z0-9_\-\%]+)', path, re.IGNORECASE)
    if not match:
        if path.rstrip('/') in ("/in", "") or path.endswith("/in"):
            return False, "Incomplete LinkedIn profile URL. Must include a profile username or slug (e.g., https://www.linkedin.com/in/username).", None, None
        return False, "Invalid LinkedIn profile URL. Example: https://www.linkedin.com/in/username", None, None

    raw_slug = match.group(1).rstrip('/')
    try:
        unquoted_slug = urllib.parse.unquote(raw_slug).strip()
    except Exception:
        unquoted_slug = raw_slug.strip()

    # Normalize slug: remove any residual punctuation and lowercase
    sanitized_slug = re.sub(r'[^a-zA-Z0-9_\-]', '', unquoted_slug)
    if not sanitized_slug or sanitized_slug.lower() in ("", "in"):
        return False, "Incomplete LinkedIn profile URL. Must include a profile username or slug (e.g., https://www.linkedin.com/in/username).", None, None

    canonical_slug = sanitized_slug.lower()
    canonical_url = f"https://www.linkedin.com/in/{canonical_slug}/"

    return True, "", canonical_slug, canonical_url


def normalize_profile_url(url: str) -> str:
    """Normalizes a LinkedIn profile URL and returns https://www.linkedin.com/in/{slug}/."""
    if not url:
        return ""
    valid, _, _, canonical = parse_linkedin_url(str(url))
    if valid and canonical:
        return canonical
    cleaned = str(url).strip()
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
    valid, _, slug, _ = parse_linkedin_url(str(url))
    if valid and slug:
        return slug
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

    orig_url = profile_data.get("original_url") or (raw_url if raw_url and raw_url != norm_url else None)
    verified_reg_date = profile_data.get("verified_registration_date")
    reg_date_source = profile_data.get("registration_date_source")

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
        if orig_url:
            saved_entry["original_url"] = orig_url
        if verified_reg_date:
            saved_entry["verified_registration_date"] = verified_reg_date
            saved_entry["registration_date_source"] = reg_date_source or "Official LinkedIn Data Export"
    else:
        saved_entry = {
            "id": slug,
            "profile_url": norm_url,
            "original_url": orig_url or norm_url,
            "name": name,
            "headline": headline,
            "picture": valid_picture_url,
            "last_analyzed": now_iso,
            "verified_registration_date": verified_reg_date,
            "registration_date_source": reg_date_source if verified_reg_date else None
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
