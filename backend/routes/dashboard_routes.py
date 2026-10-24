from flask import Blueprint, jsonify, session

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/api/dashboard/summary", methods=["GET"])
def summary():
    """Returns dashboard summary state."""
    profile = session.get("linkedin_profile")
    analysis = session.get("latest_analysis")
    return jsonify({
        "connected": bool(profile),
        "profile": profile,
        "latest_analysis": analysis
    })

