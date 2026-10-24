import json
from pathlib import Path
from typing import List, Dict, Any

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "activities.json"


def _get_storage_file() -> Path:
    from flask import current_app
    try:
        if current_app and current_app.config.get("TESTING"):
            return Path(__file__).resolve().parent.parent / "data" / "test_activities.json"
    except Exception:
        pass
    return DATA_FILE


def _ensure_storage():
    storage_path = _get_storage_file()
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    if not storage_path.exists():
        with open(storage_path, "w", encoding="utf-8") as f:
            json.dump([], f)


def load_saved_activities(subject_name: str = None, subject_id: str = None, subject_url: str = None) -> List[Dict[str, Any]]:
    _ensure_storage()
    storage_path = _get_storage_file()
    try:
        with open(storage_path, "r", encoding="utf-8") as f:
            activities = json.load(f)
            if not subject_name and not subject_id and not subject_url:
                return activities

            matched = []
            s_name = (subject_name or "").strip().lower()
            s_id = (subject_id or "").strip().lower()
            s_url = (subject_url or "").strip().lower()

            for a in activities:
                a_name = (a.get("subject_name") or "").strip().lower()
                a_id = (a.get("subject_id") or "").strip().lower()
                a_url = (a.get("subject_url") or "").strip().lower()

                # Match by URL, ID, or Name
                if s_url and a_url and (s_url in a_url or a_url in s_url):
                    matched.append(a)
                elif s_id and a_id and s_id == a_id:
                    matched.append(a)
                elif s_name and a_name and s_name == a_name:
                    matched.append(a)
                elif not a_name and not a_id and not a_url:
                    matched.append(a)

            return matched
    except Exception:
        return []


def save_activity(activity: Dict[str, Any]) -> List[Dict[str, Any]]:
    _ensure_storage()
    storage_path = _get_storage_file()
    activities = load_saved_activities()
    activities.insert(0, activity)  # Newest first
    with open(storage_path, "w", encoding="utf-8") as f:
        json.dump(activities, f, indent=2, ensure_ascii=False)
    return activities


def delete_activity(activity_id: str) -> bool:
    _ensure_storage()
    storage_path = _get_storage_file()
    activities = load_saved_activities()
    new_list = [a for a in activities if a.get("id") != activity_id]
    if len(new_list) != len(activities):
        with open(storage_path, "w", encoding="utf-8") as f:
            json.dump(new_list, f, indent=2, ensure_ascii=False)
        return True
    return False


def clear_all_activities() -> List[Dict[str, Any]]:
    _ensure_storage()
    storage_path = _get_storage_file()
    with open(storage_path, "w", encoding="utf-8") as f:
        json.dump([], f, indent=2, ensure_ascii=False)
    return []

