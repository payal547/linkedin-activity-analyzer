import json
from pathlib import Path
from typing import List, Dict, Any

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "connections.json"


def _get_storage_file() -> Path:
    from flask import current_app
    try:
        if current_app and current_app.config.get("TESTING"):
            return Path(__file__).resolve().parent.parent / "data" / "test_connections.json"
    except Exception:
        pass
    return DATA_FILE


def _ensure_storage():
    storage_path = _get_storage_file()
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    if not storage_path.exists():
        with open(storage_path, "w", encoding="utf-8") as f:
            json.dump([], f)


def load_saved_connections() -> List[Dict[str, Any]]:
    _ensure_storage()
    storage_path = _get_storage_file()
    try:
        with open(storage_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_connection(connection: Dict[str, Any]) -> List[Dict[str, Any]]:
    _ensure_storage()
    storage_path = _get_storage_file()
    connections = load_saved_connections()
    # Check if duplicate by name or id
    existing = [c for c in connections if c.get("id") == connection.get("id") or (c.get("name", "").lower() == connection.get("name", "").lower() and connection.get("name"))]
    if not existing:
        connections.append(connection)
        with open(storage_path, "w", encoding="utf-8") as f:
            json.dump(connections, f, indent=2, ensure_ascii=False)
    return connections


def save_all_connections(new_connections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    _ensure_storage()
    storage_path = _get_storage_file()
    existing = load_saved_connections()
    existing_names = {c.get("name", "").strip().lower() for c in existing if c.get("name")}

    added = 0
    for conn in new_connections:
        name = conn.get("name", "").strip()
        if name and name.lower() not in existing_names:
            existing.append(conn)
            existing_names.add(name.lower())
            added += 1

    with open(storage_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)

    return existing


def delete_saved_connection(conn_id: str) -> List[Dict[str, Any]]:
    _ensure_storage()
    storage_path = _get_storage_file()
    connections = load_saved_connections()
    connections = [c for c in connections if c.get("id") != conn_id]
    with open(storage_path, "w", encoding="utf-8") as f:
        json.dump(connections, f, indent=2, ensure_ascii=False)
    return connections


def clear_all_connections() -> List[Dict[str, Any]]:
    _ensure_storage()
    storage_path = _get_storage_file()
    with open(storage_path, "w", encoding="utf-8") as f:
        json.dump([], f, indent=2, ensure_ascii=False)
    return []

