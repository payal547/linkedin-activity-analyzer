import logging

from flask import Blueprint, jsonify, request, session
from services.analysis_storage import load_latest_analysis
from services import dashboard_analytics_service as analytics
from services.video_embeds_storage import canonical_linkedin_post_url, save_video_embed

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


@dashboard_bp.route("/api/dashboard/video-embeds", methods=["POST"])
def save_content_video_embed():
    payload = request.get_json(silent=True) or {}
    profile_slug = str(payload.get("profile_slug") or "").strip().lower()
    try:
        canonical_post = canonical_linkedin_post_url(payload.get("post_url"))
        page = 1
        match = None
        while page <= 100:
            result = analytics.list_content(profile_slug=profile_slug, kind="video", page=page, per_page=100)
            for row in result.get("items", []):
                candidate = row.get("linkedin_post_url") or row.get("original_post_url") or row.get("post_url")
                try:
                    if canonical_linkedin_post_url(candidate) == canonical_post:
                        match = row
                        break
                except ValueError:
                    continue
            if match or page >= result.get("total_pages", 1):
                break
            page += 1
        if not match:
            return jsonify({"error": "That LinkedIn video is not in the selected saved profile’s library."}), 404
        saved = save_video_embed(profile_slug, canonical_post, payload.get("embed_code"))
        return jsonify({"saved": True, "embed_src": saved["embed_src"]})
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except Exception:
        logger.exception("LinkedIn video embed save failed")
        return jsonify({"error": "The embed could not be saved right now."}), 503


@dashboard_bp.route("/api/dashboard/trends", methods=["GET"])
def trends():
    return _run_read(lambda: analytics.get_trends(
        months=request.args.get("months", 12, type=int) or 12,
        profile_slug=request.args.get("profile", ""),
    ))

