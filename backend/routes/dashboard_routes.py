import logging

from flask import Blueprint, jsonify, request, session
from services.analysis_storage import load_latest_analysis
from services import dashboard_analytics_service as analytics

dashboard_bp = Blueprint("dashboard", __name__)
logger = logging.getLogger(__name__)


def _run_read(operation):
    try:
        return jsonify(operation())
    except Exception:
        logger.exception("MongoDB dashboard read failed")
        return jsonify({"error": "Analytics data is temporarily unavailable from MongoDB.", "available": False}), 503


@dashboard_bp.route("/api/dashboard/summary", methods=["GET"])
def summary():
    """Returns dashboard summary state."""
    profile = session.get("linkedin_profile")
    analysis = session.get("latest_analysis") or load_latest_analysis()
    return jsonify({
        "connected": bool(profile),
        "profile": profile,
        "latest_analysis": analysis
    })


@dashboard_bp.route("/api/dashboard/overview", methods=["GET"])
def overview():
    return _run_read(analytics.get_overview)


@dashboard_bp.route("/api/dashboard/profiles", methods=["GET"])
def profiles():
    return _run_read(lambda: analytics.list_profiles(
        search=request.args.get("q", ""),
        sort=request.args.get("sort", "name"),
        order=request.args.get("order", "asc"),
        page=request.args.get("page", 1, type=int) or 1,
        per_page=request.args.get("per_page", 20, type=int) or 20,
    ))


@dashboard_bp.route("/api/dashboard/profile-options", methods=["GET"])
def profile_options():
    return _run_read(lambda: {"items": analytics.list_profile_options()})


@dashboard_bp.route("/api/dashboard/profiles/<slug>", methods=["GET"])
def profile_detail(slug):
    try:
        result = analytics.get_profile(slug)
        if not result:
            return jsonify({"error": "No stored profile or analysis history was found.", "has_data": False}), 404
        history = result.get("history")
        if history:
            # This route returns profile snapshot metadata only. Activity rows are
            # served through the independently paginated content endpoint.
            history.pop("activities", None)
        result["has_data"] = True
        return jsonify(result)
    except Exception:
        logger.exception("MongoDB profile dashboard read failed")
        return jsonify({"error": "Profile analytics are temporarily unavailable from MongoDB.", "available": False}), 503


@dashboard_bp.route("/api/dashboard/profiles/<slug>/runs", methods=["GET"])
def profile_runs(slug):
    return _run_read(lambda: analytics.list_runs(
        profile_slug=slug,
        page=request.args.get("page", 1, type=int) or 1,
        per_page=request.args.get("per_page", 20, type=int) or 20,
    ))


@dashboard_bp.route("/api/dashboard/runs", methods=["GET"])
def runs():
    return _run_read(lambda: analytics.list_runs(
        page=request.args.get("page", 1, type=int) or 1,
        per_page=request.args.get("per_page", 20, type=int) or 20,
    ))


@dashboard_bp.route("/api/dashboard/content", methods=["GET"])
def content():
    return _run_read(lambda: analytics.list_content(
        profile_slug=request.args.get("profile", ""),
        kind=request.args.get("type", ""),
        search=request.args.get("q", ""),
        start_date=request.args.get("from", ""),
        end_date=request.args.get("to", ""),
        sort=request.args.get("sort", "newest"),
        page=request.args.get("page", 1, type=int) or 1,
        per_page=request.args.get("per_page", 20, type=int) or 20,
    ))


@dashboard_bp.route("/api/dashboard/trends", methods=["GET"])
def trends():
    return _run_read(lambda: analytics.get_trends(
        months=request.args.get("months", 12, type=int) or 12,
        profile_slug=request.args.get("profile", ""),
    ))

