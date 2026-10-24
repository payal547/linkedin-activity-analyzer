"""
Persistent Storage Service for Saved Target Profiles.
Stores public LinkedIn profiles analyzed by the user in backend JSON storage.
Does NOT store passwords, credentials, or private authentication tokens.
Preserves profiles across application restarts.
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from flask import current_app

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PROFILES_FILE = DATA_DIR / "saved_target_profiles.json"
TEST_PROFILES_FILE = DATA_DIR / "test_saved_target_profiles.json"


def _get_storage_file() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        from flask import current_app
        if current_app and current_app.config.get("TESTING"):
            return TEST_PROFILES_FILE
    except Exception:
        pass
    try:
        import sys
        if "unittest" in sys.modules or os.getenv("TESTING") == "1":
            return TEST_PROFILES_FILE
    except Exception:
        pass
    return PROFILES_FILE


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
    if not url:
        return ""
    match = re.search(r'(?:https?:\/\/)?(?:[a-zA-Z0-9_\-\.]+\.)?linkedin\.com\/in\/([a-zA-Z0-9_\-\%]+)', url.strip(), re.IGNORECASE)
    if match:
        return match.group(1).rstrip('/').lower()
    return re.sub(r'[^a-zA-Z0-9_\-]', '', url.lower())


def load_saved_profiles() -> List[Dict[str, Any]]:
    """Loads all saved target profiles from disk sorted by last_analyzed descending."""
    file_path = _get_storage_file()
    if not file_path.exists():
        return []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                # Sort by last_analyzed descending
                return sorted(data, key=lambda x: x.get("last_analyzed") or "", reverse=True)
            return []
    except Exception:
        return []


def save_target_profile(profile_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Saves a target profile to persistent storage.
    If the profile already exists, updates last_analyzed, headline, and picture without creating a duplicate.
    If it's new, appends it as a new profile.
    Never stores passwords or credentials.
    """
    if not profile_data:
        return {}

    raw_url = profile_data.get("profile_url") or profile_data.get("url") or ""
    norm_url = normalize_profile_url(raw_url)
    slug = extract_slug(raw_url)

    if not slug:
        return {}

    now_iso = datetime.now().isoformat()
    file_path = _get_storage_file()
    profiles = load_saved_profiles()

    existing_idx = None
    for idx, p in enumerate(profiles):
        p_slug = extract_slug(p.get("profile_url", "")) or p.get("id", "").lower()
        if p_slug == slug:
            existing_idx = idx
            break

    name = profile_data.get("name") or slug.replace("-", " ").title()
    headline = profile_data.get("headline") or "LinkedIn Profile"
    picture = profile_data.get("picture") or profile_data.get("photo_url") or None

    if existing_idx is not None:
        # Update existing profile
        current = profiles[existing_idx]
        current["last_analyzed"] = now_iso
        if name and name != "LinkedIn Member":
            current["name"] = name
        if headline and headline != "LinkedIn Profile":
            current["headline"] = headline
        if picture:
            current["picture"] = picture
        current["profile_url"] = norm_url or current.get("profile_url")
        saved_entry = current
    else:
        # Create new profile entry
        saved_entry = {
            "id": slug,
            "profile_url": norm_url,
            "name": name,
            "headline": headline,
            "picture": picture,
            "last_analyzed": now_iso
        }
        profiles.insert(0, saved_entry)

    # Save to disk atomically
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

    return saved_entry


def remove_saved_profile(profile_url_or_slug: str) -> bool:
    """
    Removes a target profile from persistent storage by URL or slug.
    Returns True if removed, False if not found.
    """
    if not profile_url_or_slug:
        return False

    target_slug = extract_slug(profile_url_or_slug)
    if not target_slug:
        return False

    file_path = _get_storage_file()
    profiles = load_saved_profiles()

    initial_len = len(profiles)
    updated = [
        p for p in profiles
        if extract_slug(p.get("profile_url", "")) != target_slug and (p.get("id") or "").lower() != target_slug
    ]

    if len(updated) == initial_len:
        return False

    temp_path = file_path.with_suffix(".tmp")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(updated, f, indent=2, ensure_ascii=False)
        temp_path.replace(file_path)
        return True
    except Exception:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass
        return False
