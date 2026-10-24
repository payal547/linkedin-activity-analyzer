import uuid
from flask import Blueprint, session, jsonify, request
from auth.token_store import get_access_token
from linkedin.connections import fetch_linkedin_connections
from linkedin.data_parser import parse_connections_csv, parse_connections_from_file_or_zip
from services.profile_service import format_user_profile
from services.connections_storage import (
    load_saved_connections,
    save_connection,
    save_all_connections,
    delete_saved_connection,
    clear_all_connections
)

connection_bp = Blueprint("connection", __name__)


@connection_bp.route("/api/profile")
def profile():
    raw_profile = session.get("linkedin_profile")

    if not raw_profile:
        return jsonify({
            "error": "LinkedIn account is not connected",
            "connected": False
        }), 401

    formatted = format_user_profile(raw_profile)
    return jsonify({
        **formatted,
        "connected": True
    })


@connection_bp.route("/api/connections")
def connections():
    raw_profile = session.get("linkedin_profile")
    api_available = False
    api_connections = []
    api_message = ""
    self_profile = None

    if raw_profile:
        access_token = get_access_token(session.get("linkedin_session_id"))
        if not access_token:
            access_token = session.get("linkedin_access_token")
        api_available, api_connections, api_message = fetch_linkedin_connections(access_token)
        self_profile = {
            "id": raw_profile.get("sub", "self"),
            "name": raw_profile.get("name", "My Profile"),
            "headline": "Authenticated LinkedIn Account",
            "picture": raw_profile.get("picture"),
            "source": "real_linkedin",
            "is_self": True
        }

    # Load user's real saved/imported connections from storage
    saved_connections = load_saved_connections()

    # Merge any live API connections if returned
    all_real_connections = []
    seen_names = set()

    for conn in api_connections:
        name = conn.get("name", "").strip()
        if name and name.lower() not in seen_names:
            all_real_connections.append(conn)
            seen_names.add(name.lower())

    for conn in saved_connections:
        name = conn.get("name", "").strip()
        if name and name.lower() not in seen_names:
            all_real_connections.append(conn)
            seen_names.add(name.lower())

    return jsonify({
        "connected": bool(raw_profile),
        "self_profile": self_profile,
        "connections": all_real_connections,
        "real_connections": all_real_connections,
        "saved_connections": saved_connections,
        "sample_connections": [],  # Strictly no sample/fake friends
        "api_available": api_available,
        "api_restricted": not api_available if raw_profile else False,
        "api_message": api_message
    })


@connection_bp.route("/api/connections/add", methods=["POST"])
def add_connection():
    """Add a real connection manually with name, headline, and profile URL."""
    data = request.get_json(silent=True) or request.form or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Please provide the connection's full name."}), 400

    headline = (data.get("headline") or "LinkedIn Connection").strip()
    profile_url = (data.get("profile_url") or "").strip()

    new_conn = {
        "id": f"conn_{uuid.uuid4().hex[:8]}",
        "name": name,
        "headline": headline,
        "profile_url": profile_url,
        "picture": None,
        "source": "real_linkedin"
    }

    updated = save_connection(new_conn)
    return jsonify({
        "message": f"Added real connection: {name}",
        "connection": new_conn,
        "connections": updated
    })


@connection_bp.route("/api/connections/<conn_id>", methods=["DELETE"])
def remove_connection(conn_id):
    """Remove a saved connection."""
    updated = delete_saved_connection(conn_id)
    return jsonify({
        "message": "Connection removed successfully.",
        "connections": updated
    })


@connection_bp.route("/api/connections/import", methods=["POST"])
def import_connections():
    """Imports real connections from user's official LinkedIn Connections.csv or complete export ZIP."""
    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify({"error": "Please select a Connections.csv or LinkedIn export ZIP file."}), 400

    try:
        file_bytes = upload.stream.read(15 * 1024 * 1024)
        success, friends, message = parse_connections_from_file_or_zip(file_bytes, upload.filename)
        if not success or not friends:
            return jsonify({
                "error": message or "No connection records found in file. Ensure you uploaded Connections.csv or your official LinkedIn export ZIP."
            }), 400

        serialized = [f.to_dict() for f in friends]
        saved = save_all_connections(serialized)
        session["imported_connections"] = serialized

        return jsonify({
            "message": message or f"Successfully loaded {len(serialized)} real connections from your LinkedIn export!",
            "connections": saved,
            "count": len(serialized)
        })
    except Exception as e:
        return jsonify({"error": f"Failed to parse connections file: {str(e)}"}), 400


@connection_bp.route("/api/connections/clear", methods=["POST", "DELETE"])
def clear_connections():
    """Clear all saved/imported connections."""
    cleared = clear_all_connections()
    session.pop("imported_connections", None)
    return jsonify({
        "message": "All saved connections have been cleared.",
        "connections": cleared
    })


@connection_bp.route("/api/connections/bulk-add", methods=["POST"])
def bulk_add_connections():
    """Bulk adds real connections by pasting names directly."""
    if not session.get("linkedin_profile"):
        return jsonify({"error": "Please connect your LinkedIn account first."}), 401

    data = request.get_json(silent=True) or {}
    names_input = data.get("names", "")
    if isinstance(names_input, str):
        raw_names = [line.strip() for line in names_input.splitlines() if line.strip()]
    elif isinstance(names_input, list):
        raw_names = [str(n).strip() for n in names_input if str(n).strip()]
    else:
        raw_names = []

    if not raw_names:
        return jsonify({"error": "Please provide at least one connection name."}), 400

    new_conns = []
    for name in raw_names:
        if name:
            new_conns.append({
                "id": f"conn_{uuid.uuid4().hex[:8]}",
                "name": name,
                "headline": "LinkedIn Connection",
                "profile_url": "",
                "picture": None,
                "source": "real_linkedin"
            })

    saved = save_all_connections(new_conns)
    return jsonify({
        "message": f"Successfully added {len(new_conns)} real connection(s)!",
        "count": len(new_conns),
        "connections": saved
    })
