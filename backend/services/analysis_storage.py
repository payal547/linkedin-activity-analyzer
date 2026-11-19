"""
Persistent Storage Service for the Latest Activity Analysis and Profile-Specific History.
Stores analysis results in MongoDB Local with JSON dual-write and graceful fallback.
Prevents oversized Flask session cookies (> 4093 bytes) and ensures reliable
dashboard retrieval across page reloads and browser sessions.
Maintains isolated historical successful analyses per canonical profile slug so
failed attempts never overwrite prior successful results or mix profiles.
"""

import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ANALYSIS_FILE = DATA_DIR / "latest_analysis.json"
TEST_ANALYSIS_FILE = DATA_DIR / "test_latest_analysis.json"
HISTORY_FILE = DATA_DIR / "profile_analysis_history.json"
TEST_HISTORY_FILE = DATA_DIR / "test_profile_analysis_history.json"


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
        return TEST_ANALYSIS_FILE
    return ANALYSIS_FILE


def _get_history_file() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if _is_testing():
        return TEST_HISTORY_FILE
    return HISTORY_FILE


def _sync_test_state_if_needed(coll, file_path: Path) -> None:
    if _is_testing() and coll is not None:
        if not file_path.exists():
            try:
                coll.delete_many({})
            except Exception:
                pass


def _get_latest_analysis_coll():
    try:
        from db.mongo import get_db, sanitize_doc
    except ImportError:
        try:
            from backend.db.mongo import get_db, sanitize_doc
        except ImportError:
            return None, None
    try:
        db = get_db()
        coll = db["latest_analysis"]
        _sync_test_state_if_needed(coll, _get_storage_file())
        return coll, sanitize_doc
    except Exception as e:
        logger.warning("Could not obtain MongoDB collection latest_analysis: %s", e)
        return None, None


def _get_history_coll():
    try:
        from db.mongo import get_db, sanitize_doc
    except ImportError:
        try:
            from backend.db.mongo import get_db, sanitize_doc
        except ImportError:
            return None, None
    try:
        db = get_db()
        coll = db["profile_analysis_history"]
        _sync_test_state_if_needed(coll, _get_history_file())
        return coll, sanitize_doc
    except Exception as e:
        logger.warning("Could not obtain MongoDB collection profile_analysis_history: %s", e)
        return None, None


def extract_slug(url_or_slug: str) -> str:
    """
    Extracts canonical profile slug from URL or slug string.
    Returns an empty string for incomplete URLs (e.g. https://www.linkedin.com/in/)
    or malformed values, preventing corrupted profile history keys.
    """
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


def _load_history_from_json() -> Dict[str, Any]:
    hist_file = _get_history_file()
    if not hist_file.exists():
        return {}
    try:
        with open(hist_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_history_to_json(history: Dict[str, Any]) -> None:
    hist_file = _get_history_file()
    temp_file = hist_file.with_suffix(".tmp")
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
        temp_file.replace(hist_file)
    except Exception:
        try:
            with open(hist_file, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=2, ensure_ascii=False)
        except Exception:
            pass


def _load_latest_from_json() -> Optional[Dict[str, Any]]:
    file_path = _get_storage_file()
    if not file_path.exists():
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else None
    except Exception:
        return None


def _save_latest_to_json(analysis_data: Dict[str, Any]) -> None:
    file_path = _get_storage_file()
    temp_file = file_path.with_suffix(".tmp")
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(analysis_data, f, indent=2, ensure_ascii=False)
        temp_file.replace(file_path)
    except Exception:
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(analysis_data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass


def save_profile_successful_analysis(slug_or_url: str, analysis_data: Dict[str, Any]) -> None:
    """Stores a successful analysis record indexed strictly by the profile's canonical slug."""
    slug = extract_slug(slug_or_url)
    if not slug or not isinstance(analysis_data, dict):
        return

    clean_copy = dict(analysis_data)
    clean_copy["historical_analyzed_at"] = datetime.now().isoformat()
    clean_copy["is_historical"] = True
    clean_copy["canonical_slug"] = slug
    clean_copy["slug"] = slug

    # Ensure self-contained summary metrics are preserved
    acts = clean_copy.get("activities", [])
    if isinstance(acts, list) and len(acts) > 0:
        if not clean_copy.get("total_activities"):
            clean_copy["total_activities"] = len(acts)
        if not clean_copy.get("by_type"):
            by_t: Dict[str, int] = {}
            for a in acts:
                t = (a.get("action_type") or a.get("type") or "post").lower()
                by_t[t] = by_t.get(t, 0) + 1
            clean_copy["by_type"] = by_t

        by_type = clean_copy.get("by_type", {})
        if "posts_count" not in clean_copy:
            clean_copy["posts_count"] = by_type.get("post", 0) + by_type.get("posts", 0)
        if "comments_count" not in clean_copy:
            clean_copy["comments_count"] = by_type.get("comment", 0) + by_type.get("comments", 0)
        if "likes_count" not in clean_copy:
            clean_copy["likes_count"] = by_type.get("like", 0) + by_type.get("likes", 0)
        if "shares_count" not in clean_copy:
            clean_copy["shares_count"] = by_type.get("share", 0) + by_type.get("shares", 0)
        if "videos_count" not in clean_copy:
            clean_copy["videos_count"] = by_type.get("video", 0) + by_type.get("videos", 0)

    # Merge with previous historical activities so history accumulates up to 1+ years
    try:
        existing_entry = load_profile_successful_analysis(slug)
        if existing_entry and isinstance(existing_entry.get("activities"), list) and len(existing_entry["activities"]) > 0:
            existing_acts = existing_entry["activities"]
            new_acts = clean_copy.get("activities", [])
            merged_map = {}
            for a in existing_acts:
                if not a.get("storage_origin"):
                    a["storage_origin"] = "historical_mongodb"
                key = a.get("id") or f"{a.get('type')}_{a.get('date')}_{a.get('text', '')[:40]}"
                merged_map[key] = a
            for a in new_acts:
                if not a.get("storage_origin"):
                    a["storage_origin"] = "live_retrieval"
                key = a.get("id") or f"{a.get('type')}_{a.get('date')}_{a.get('text', '')[:40]}"
                merged_map[key] = a
            merged_acts = sorted(
                list(merged_map.values()),
                key=lambda x: str(x.get("date") or ""),
                reverse=True
            )
            clean_copy["activities"] = merged_acts
            clean_copy["total_activities"] = len(merged_acts)
            clean_copy["historical_count"] = sum(1 for a in merged_acts if a.get("storage_origin") == "historical_mongodb")
            clean_copy["live_count"] = sum(1 for a in merged_acts if a.get("storage_origin") == "live_retrieval")

            # Update by_type counts across all merged activities
            by_t = {}
            for a in merged_acts:
                t = (a.get("action_type") or a.get("type") or "post").lower()
                by_t[t] = by_t.get(t, 0) + 1
            clean_copy["by_type"] = by_t
            clean_copy["posts_count"] = by_t.get("post", 0) + by_t.get("posts", 0)
            clean_copy["comments_count"] = by_t.get("comment", 0) + by_t.get("comments", 0)
            clean_copy["likes_count"] = by_t.get("like", 0) + by_t.get("likes", 0)
            clean_copy["shares_count"] = by_t.get("share", 0) + by_t.get("shares", 0)
            clean_copy["videos_count"] = by_t.get("video", 0) + by_t.get("videos", 0)

            # Recalculate date range to cover the entire accumulated historical span
            valid_dates = [a.get("date") for a in merged_acts if a.get("date")]
            if valid_dates:
                clean_copy["date_range"] = {
                    "start": min(valid_dates),
                    "end": max(valid_dates)
                }
    except Exception as merge_err:
        logger.warning("Historical activity merge notice for %s: %s", slug, merge_err)

    # 1. Primary write to MongoDB
    coll, _ = _get_history_coll()
    if coll is not None:
        try:
            mongo_doc = dict(clean_copy)
            coll.replace_one({"slug": slug}, mongo_doc, upsert=True)
        except Exception as e:
            logger.warning("MongoDB save_profile_successful_analysis failed for %s: %s", slug, e)

    # 2. Dual-write to JSON history file
    try:
        history = _load_history_from_json()
        history[slug] = clean_copy
        _save_history_to_json(history)
    except Exception as e:
        logger.warning("JSON dual-write failed in save_profile_successful_analysis: %s", e)


def load_profile_successful_analysis(slug_or_url: str) -> Optional[Dict[str, Any]]:
    """Loads a previous successful analysis specifically for the given profile URL or slug."""
    slug = extract_slug(slug_or_url)
    if not slug:
        return None

    # 1. MongoDB primary query
    coll, sanitize_fn = _get_history_coll()
    if coll is not None and sanitize_fn is not None:
        try:
            doc = coll.find_one({"slug": slug}, {"_id": 0, "_migration": 0})
            if doc:
                return sanitize_fn(doc)
        except Exception as e:
            logger.warning("MongoDB load_profile_successful_analysis failed for %s: %s", slug, e)

    # 2. JSON fallback
    history = _load_history_from_json()
    entry = history.get(slug)
    return dict(entry) if isinstance(entry, dict) else None


def clear_profile_successful_analysis(slug_or_url: str) -> bool:
    """
    Removes a profile's historical successful analysis record from history storage.
    Returns True if an entry was removed, False otherwise.
    """
    slug = extract_slug(slug_or_url)
    if not slug:
        return False

    mongo_cleared = False
    coll, _ = _get_history_coll()
    if coll is not None:
        try:
            res = coll.delete_one({"slug": slug})
            mongo_cleared = (res.deleted_count > 0)
        except Exception as e:
            logger.warning("MongoDB clear_profile_successful_analysis failed for %s: %s", slug, e)

    # Sync with JSON
    history = _load_history_from_json()
    json_cleared = False
    if slug in history:
        del history[slug]
        _save_history_to_json(history)
        json_cleared = True

    return mongo_cleared or json_cleared


def save_latest_analysis(analysis_data: Dict[str, Any]) -> None:
    """
    Save the latest analysis result in MongoDB Local and JSON storage.
    If the analysis was successful with activities, persists it to profile-specific history.
    If the current attempt was blocked or failed, preserves previous successful results
    and attaches profile-specific historical data without overwriting history.
    """
    if not isinstance(analysis_data, dict):
        return

    total_acts = analysis_data.get("total_activities", 0)
    activities = analysis_data.get("activities", [])
    has_success_activities = (total_acts > 0) or (isinstance(activities, list) and len(activities) > 0)

    def _filter_historical_if_needed(hist_dict: Dict[str, Any]) -> Dict[str, Any]:
        req_kinds = analysis_data.get("filtered_activity_types") or ["all"]
        if req_kinds != ["all"] and hist_dict and hist_dict.get("activities"):
            try:
                from services.analysis_service import analyze_activities
                raw_h = hist_dict.get("activities", [])
                f_hist = analyze_activities(
                    rows=raw_h,
                    activity_types=req_kinds,
                    subject_name=hist_dict.get("subject_name", "LinkedIn Profile"),
                    data_source=hist_dict.get("data_source", "real_linkedin"),
                    subject_url=hist_dict.get("subject_url"),
                    headline=hist_dict.get("headline"),
                    retrieval_completeness="HISTORICAL_PRESERVED"
                )
                f_hist["is_historical"] = True
                f_hist["historical_analyzed_at"] = hist_dict.get("historical_analyzed_at") or hist_dict.get("analyzed_at") or hist_dict.get("updated_at")
                f_hist["original_unfiltered_count"] = len(raw_h)
                f_hist["historical_date_range"] = hist_dict.get("date_range")
                return f_hist
            except Exception as fe:
                logger.warning("Failed to filter historical analysis: %s", fe)
        return hist_dict

    # Update profile-specific history for successful profiles; attach history for unsuccessful ones
    prof_results = analysis_data.get("profile_results", [])
    if isinstance(prof_results, list) and prof_results:
        for pr in prof_results:
            pr_slug = extract_slug(pr.get("subject_url") or pr.get("subject_id") or "")
            if pr.get("total_activities", 0) > 0:
                if pr_slug:
                    save_profile_successful_analysis(pr_slug, pr)
            else:
                if pr_slug:
                    hist = load_profile_successful_analysis(pr_slug)
                    if hist:
                        hist = _filter_historical_if_needed(hist)
                        pr["historical_successful_analysis"] = hist
                        if "historical_successful_analysis" not in analysis_data:
                            analysis_data["historical_successful_analysis"] = hist
    else:
        p_slug = extract_slug(analysis_data.get("subject_url") or analysis_data.get("subject_id") or "")
        if has_success_activities:
            if p_slug:
                save_profile_successful_analysis(p_slug, analysis_data)
        else:
            if p_slug:
                hist = load_profile_successful_analysis(p_slug)
                if hist:
                    hist = _filter_historical_if_needed(hist)
                    analysis_data["historical_successful_analysis"] = hist

    if not has_success_activities:
        p_slug = extract_slug(analysis_data.get("subject_url") or analysis_data.get("subject_id") or "")
        if p_slug and "historical_successful_analysis" not in analysis_data:
            hist = load_profile_successful_analysis(p_slug)
            if hist:
                hist = _filter_historical_if_needed(hist)
                analysis_data["historical_successful_analysis"] = hist

    # 1. Primary write to MongoDB singleton
    coll, _ = _get_latest_analysis_coll()
    if coll is not None:
        try:
            doc = dict(analysis_data)
            doc["_id"] = "latest_analysis_singleton"
            doc["updated_at"] = datetime.now().isoformat()
            coll.replace_one({"_id": "latest_analysis_singleton"}, doc, upsert=True)
        except Exception as e:
            logger.warning("MongoDB save_latest_analysis failed: %s", e)

    # 2. Dual-write to JSON file atomically
    _save_latest_to_json(analysis_data)


def load_latest_analysis() -> Optional[Dict[str, Any]]:
    """Load the latest analysis result from MongoDB primary storage with fallback to JSON."""
    coll, sanitize_fn = _get_latest_analysis_coll()
    if coll is not None and sanitize_fn is not None:
        try:
            doc = coll.find_one({"_id": "latest_analysis_singleton"})
            if doc:
                doc.pop("_id", None)
                doc.pop("_migration", None)
                doc.pop("updated_at", None)
                return sanitize_fn(doc)
            if _is_testing():
                return None
        except Exception as e:
            logger.warning("MongoDB load_latest_analysis failed: %s; falling back to JSON.", e)

    return _load_latest_from_json()


def clear_latest_analysis() -> None:
    """Clear latest analysis from MongoDB singleton and JSON storage file."""
    coll, _ = _get_latest_analysis_coll()
    if coll is not None:
        try:
            coll.delete_one({"_id": "latest_analysis_singleton"})
        except Exception as e:
            logger.warning("MongoDB clear_latest_analysis failed: %s", e)

    file_path = _get_storage_file()
    if file_path.exists():
        try:
            file_path.unlink()
        except Exception:
            pass


def clear_profile_history() -> None:
    """Clear server-side profile history from MongoDB and JSON storage."""
    coll, _ = _get_history_coll()
    if coll is not None:
        try:
            coll.delete_many({})
        except Exception as e:
            logger.warning("MongoDB clear_profile_history failed: %s", e)

    hist_file = _get_history_file()
    if hist_file.exists():
        try:
            hist_file.unlink()
        except Exception:
            pass
