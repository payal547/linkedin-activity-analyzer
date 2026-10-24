import json
import re
from pathlib import Path
from flask import Blueprint, jsonify, request, session
from auth.token_store import get_access_token
from services.activity_service import get_activities_for_subject
from services.analysis_service import analyze_activities
from services.connections_storage import load_saved_connections, DATA_FILE
from services.saved_profiles_storage import (
    load_saved_profiles,
    save_target_profile,
    remove_saved_profile
)
from linkedin.data_parser import parse_activities_csv, parse_activities_from_file_or_zip

activity_bp = Blueprint("activity", __name__)


def resolve_linkedin_url(url: str):
    """
    Validates a LinkedIn profile URL and resolves known connection details or format clean metadata.
    Does NOT scrape or bypass LinkedIn authentication.
    """
    if not url or not url.strip():
        return False, "Please enter a LinkedIn profile URL.", None

    cleaned_url = url.strip()
    match = re.search(r'(?:https?:\/\/)?(?:www\.)?linkedin\.com\/in\/([a-zA-Z0-9_\-\%]+)', cleaned_url, re.IGNORECASE)
    if not match:
        return False, "Invalid LinkedIn profile URL. Example: https://www.linkedin.com/in/username", None

    slug = match.group(1).rstrip('/')
    norm_url = f"https://www.linkedin.com/in/{slug}"

    # 1. Check if profile matches user's saved/imported connections
    conns = load_saved_connections()
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                base_conns = json.load(f)
                seen_urls = {c.get("profile_url", "").strip().lower() for c in conns if c.get("profile_url")}
                seen_names = {c.get("name", "").strip().lower() for c in conns if c.get("name")}
                for bc in base_conns:
                    bc_url = (bc.get("profile_url") or "").strip().lower()
                    bc_name = (bc.get("name") or "").strip().lower()
                    if (bc_url and bc_url not in seen_urls) or (bc_name and bc_name not in seen_names):
                        conns.append(bc)
                        if bc_url:
                            seen_urls.add(bc_url)
                        if bc_name:
                            seen_names.add(bc_name)
        except Exception:
            pass

    for c in conns:
        c_url = (c.get("profile_url") or "").strip().lower()
        if slug.lower() in c_url or c_url.rstrip("/").endswith(f"/in/{slug.lower()}"):
            return True, None, {
                "profile_url": norm_url,
                "username": slug,
                "name": c.get("name") or slug.replace("-", " ").title(),
                "headline": c.get("headline") or "LinkedIn Connection",
                "company": c.get("company"),
                "position": c.get("position"),
                "picture": c.get("picture"),
                "source": c.get("source", "imported"),
                "subject_id": c.get("id")
            }

    # 2. Check if profile matches authenticated user's own profile
    user_prof = session.get("linkedin_profile")
    if user_prof and user_prof.get("name"):
        own_sub = str(user_prof.get("sub", "")).lower()
        if slug.lower() in own_sub:
            return True, None, {
                "profile_url": norm_url,
                "username": slug,
                "name": user_prof.get("name"),
                "headline": "Authenticated LinkedIn Account",
                "picture": user_prof.get("picture"),
                "source": "real_linkedin",
                "subject_id": "self"
            }

    # 3. Check if profile matches previously saved target profiles
    for sp in load_saved_profiles():
        sp_url = (sp.get("profile_url") or "").strip().lower()
        sp_id = (sp.get("id") or "").strip().lower()
        if slug.lower() in sp_url or slug.lower() == sp_id:
            return True, None, {
                "profile_url": norm_url,
                "username": slug,
                "name": sp.get("name") or slug.replace("-", " ").title(),
                "headline": sp.get("headline") or "LinkedIn Profile",
                "company": sp.get("company"),
                "position": sp.get("position"),
                "picture": sp.get("picture"),
                "source": "real_linkedin",
                "subject_id": f"profile_{slug}"
            }

    # 4. Derive clean human display name from URL slug
    clean_slug = re.sub(r'[-_]\d+$', '', slug)
    name = clean_slug.replace('-', ' ').replace('_', ' ').strip().title()

    return True, None, {
        "profile_url": norm_url,
        "username": slug,
        "name": name or "LinkedIn Member",
        "headline": "LinkedIn Profile",
        "company": None,
        "position": None,
        "picture": None,
        "source": "real_linkedin",
        "subject_id": f"profile_{slug}"
    }


@activity_bp.route("/api/profile/validate-url", methods=["POST"])
def validate_profile_url():
    """
    Validates any LinkedIn profile URL and prepares it for activity analysis.
    Stores the selected profile in session and permanently persists it to backend storage.
    """
    data = request.get_json(silent=True) or request.form or {}
    url = (data.get("url") or "").strip()

    valid, err_msg, profile_info = resolve_linkedin_url(url)
    if not valid:
        return jsonify({"valid": False, "error": err_msg}), 400

    session["selected_profile"] = profile_info

    # Permanently persist the target profile
    saved = save_target_profile(profile_info)

    return jsonify({
        "valid": True,
        **profile_info,
        "saved_profile": saved
    })


@activity_bp.route("/api/profiles/saved", methods=["GET"])
def get_saved_target_profiles():
    """Returns all saved target profiles persisted in backend storage."""
    profiles = load_saved_profiles()
    return jsonify({
        "profiles": profiles,
        "count": len(profiles)
    })


@activity_bp.route("/api/profiles/saved", methods=["POST"])
def save_new_target_profile():
    """Explicitly saves or updates a target profile in persistent storage."""
    data = request.get_json(silent=True) or request.form or {}
    url = (data.get("profile_url") or data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "Profile URL is required."}), 400

    valid, err_msg, profile_info = resolve_linkedin_url(url)
    if not valid:
        return jsonify({"error": err_msg}), 400

    if data.get("name"):
        profile_info["name"] = data["name"]
    if data.get("headline"):
        profile_info["headline"] = data["headline"]
    if data.get("picture"):
        profile_info["picture"] = data["picture"]

    saved = save_target_profile(profile_info)
    return jsonify({
        "success": True,
        "profile": saved
    })


@activity_bp.route("/api/profiles/saved", methods=["DELETE"])
def delete_saved_target_profile():
    """Removes a single saved target profile by URL or slug."""
    data = request.get_json(silent=True) or {}
    target = data.get("url") or data.get("profile_url") or data.get("id") or request.args.get("url") or request.args.get("id")
    if not target:
        return jsonify({"error": "Profile URL or ID is required."}), 400

    removed = remove_saved_profile(target)
    if not removed:
        return jsonify({"error": "Saved profile not found."}), 404

    return jsonify({
        "success": True,
        "message": "Target profile removed successfully.",
        "removed_target": target
    })


@activity_bp.route("/api/profile/selected", methods=["GET"])
def get_selected_profile():
    """Returns the currently selected profile for activity filter and dashboard."""
    prof = session.get("selected_profile")
    if not prof:
        return jsonify({"error": "No profile has been selected yet.", "has_profile": False}), 404
    return jsonify({
        **prof,
        "has_profile": True
    })


@activity_bp.route("/api/analyze", methods=["POST"])
def analyze():
    """
    Main workflow endpoint:
    Analyzes activities for the selected connection/profile and filters.
    Accepts JSON payload: { subject_id, subject_name, subject_url, headline, data_source, dateType, startDate, endDate, activities }
    Also accepts optional CSV or ZIP upload as a secondary fallback if user wants to import an export.
    """
    access_token = get_access_token(session.get("linkedin_session_id")) or session.get("linkedin_access_token")

    # Check if this request is a secondary/optional file upload (CSV or ZIP)
    upload = request.files.get("file")
    if upload and upload.filename:
        try:
            file_bytes = upload.stream.read(15 * 1024 * 1024)
            parsed = parse_activities_from_file_or_zip(file_bytes, upload.filename)
            if not parsed:
                return jsonify({"error": "No valid activity records found in file. Supported files: Reactions.csv, Comments.csv, Shares.csv, or LinkedIn export ZIP."}), 400

            # Convert to dicts and save in session as imported activities
            imported = [a.to_dict() for a in parsed]
            session["imported_activities"] = imported

            filters = request.form
            kinds = [kind.lower() for kind in filters.getlist("activities")]
            if not kinds or "all" in kinds:
                kinds = ["all"]
            start_date = filters.get("startDate") or None
            end_date = filters.get("endDate") or None
            subject_name = filters.get("subject_name", "Imported Activity Data")
            subject_url = filters.get("subject_url")

            result = analyze_activities(
                imported,
                kinds,
                start_date,
                end_date,
                subject_name=subject_name,
                data_source="imported",
                subject_url=subject_url
            )
            session["latest_analysis"] = result
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": f"Failed to process activity archive: {str(e)}"}), 400

    # Primary workflow: JSON payload or Form data from the application flow
    data = request.get_json(silent=True) or {}
    if not data and request.form:
        data = {
            "subject_id": request.form.get("subject_id", "self"),
            "subject_name": request.form.get("subject_name", "My Profile"),
            "subject_url": request.form.get("subject_url"),
            "headline": request.form.get("headline"),
            "data_source": request.form.get("data_source", "real_linkedin"),
            "dateType": request.form.get("dateType", "current"),
            "startDate": request.form.get("startDate"),
            "endDate": request.form.get("endDate"),
            "activities": request.form.getlist("activities")
        }

    # Pull defaults from session["selected_profile"] if present
    sess_prof = session.get("selected_profile") or {}
    subject_id = data.get("subject_id") or sess_prof.get("subject_id") or "self"
    subject_name = data.get("subject_name") or sess_prof.get("name") or session.get("linkedin_profile", {}).get("name", "LinkedIn Profile")
    subject_url = data.get("subject_url") or sess_prof.get("profile_url") or None
    headline = data.get("headline") or sess_prof.get("headline") or "LinkedIn Profile"
    data_source = data.get("data_source") or sess_prof.get("source") or "real_linkedin"

    use_sample = bool(data.get("use_sample", False))
    if use_sample:
        data_source = "sample"

    kinds = data.get("activities", [])
    if isinstance(kinds, str):
        kinds = [kinds]
    kinds = [k.lower() for k in kinds if k]
    if not kinds:
        kinds = ["all"]

    start_date = data.get("startDate") or None
    end_date = data.get("endDate") or None
    date_type = data.get("dateType", "current")

    if date_type == "current" and not start_date and not end_date:
        from datetime import date
        today_str = date.today().isoformat()
        start_date = today_str
        end_date = today_str

    if start_date and end_date and start_date > end_date:
        return jsonify({"error": "Starting date must be on or before ending date."}), 400

    # Fetch activities for subject based on data source
    status_tracker = {}
    raw_activities = get_activities_for_subject(
        subject_id=subject_id,
        subject_name=subject_name,
        data_source=data_source,
        access_token=access_token,
        session_activities=session.get("imported_activities"),
        subject_url=subject_url,
        allow_sample_fallback=(data_source == "sample"),
        start_date=start_date,
        end_date=end_date,
        activity_types=kinds,
        status_out=status_tracker
    )

    # Perform deterministic analysis
    result = analyze_activities(
        rows=raw_activities,
        activity_types=kinds,
        start_date=start_date,
        end_date=end_date,
        subject_name=subject_name,
        data_source=data_source,
        subject_url=subject_url,
        headline=headline
    )

    result["retrieval_status"] = status_tracker.get("status", "")
    if not result.get("activities"):
        result["empty_message"] = status_tracker.get("empty_message") or status_tracker.get("status") or "Actual post content could not be retrieved from LinkedIn."

    session["latest_analysis"] = result
    return jsonify(result)



@activity_bp.route("/api/analysis/latest", methods=["GET"])
def get_latest_analysis():
    """Returns the most recent analysis result for dashboard display."""
    analysis = session.get("latest_analysis")
    if not analysis:
        return jsonify({"error": "No analysis has been run yet.", "has_data": False}), 404

    return jsonify({
        **analysis,
        "has_data": True
    })


@activity_bp.route("/api/activities/add", methods=["POST"])
def add_activity():
    """Add a real post or activity directly from the user to display and analyze."""
    from datetime import date
    import uuid
    from services.activities_storage import save_activity

    data = request.get_json(silent=True) or request.form or {}
    sess_prof = session.get("selected_profile") or {}
    subject_name = data.get("subject_name") or sess_prof.get("name") or session.get("linkedin_profile", {}).get("name", "My Profile")
    subject_id = data.get("subject_id") or sess_prof.get("subject_id") or "self"
    subject_url = data.get("subject_url") or sess_prof.get("profile_url") or None

    text = (data.get("text") or data.get("post_content") or "").strip()
    post_content = (data.get("post_content") or text or "LinkedIn Activity").strip()
    act_type = (data.get("type") or data.get("action_type") or "post").strip().lower()
    act_date = (data.get("date") or date.today().isoformat()).strip()
    post_author = (data.get("post_author") or (subject_name if act_type in ("post", "video", "share") else "Original Post Author")).strip()
    post_url = (data.get("post_url") or "").strip()
    media_url = (data.get("media_url") or "").strip()
    comment_text = (data.get("comment_text") or "").strip()
    reaction_type = (data.get("reaction_type") or ("Like" if act_type == "like" else "")).strip()
    is_own_post = bool(data.get("is_own_post", act_type in ("post", "video", "share")))

    def safe_int(v):
        try:
            return max(0, int(v or 0))
        except (ValueError, TypeError):
            return 0

    new_item = {
        "id": f"act_{uuid.uuid4().hex[:8]}",
        "subject_id": subject_id,
        "subject_name": subject_name,
        "subject_url": subject_url,
        "date": act_date,
        "type": act_type,
        "text": text or post_content,
        "likes": safe_int(data.get("likes")),
        "comments": safe_int(data.get("comments")),
        "shares": safe_int(data.get("shares")),
        "source": "real_linkedin",
        # Grouped Post fields
        "post_content": post_content,
        "post_author": post_author,
        "post_url": post_url or None,
        "media_url": media_url or None,
        "comment_text": comment_text or None,
        "reaction_type": reaction_type or None,
        "is_own_post": is_own_post,
        "action_type": act_type
    }

    save_activity(new_item)

    # Re-run analysis with all activities for this subject
    all_items = get_activities_for_subject(
        subject_id=subject_id,
        subject_name=subject_name,
        data_source="real_linkedin",
        subject_url=subject_url
    )

    result = analyze_activities(
        rows=all_items,
        activity_types=["all"],
        subject_name=subject_name,
        data_source="real_linkedin",
        subject_url=subject_url,
        headline=sess_prof.get("headline", "LinkedIn Profile")
    )

    session["latest_analysis"] = result
    return jsonify({
        "message": "Activity added successfully!",
        "item": new_item,
        "analysis": result
    })


@activity_bp.route("/api/activities/<activity_id>", methods=["DELETE"])
def remove_activity(activity_id):
    """Remove a previously logged activity and refresh analysis."""
    from services.activities_storage import delete_activity
    deleted = delete_activity(activity_id)
    if not deleted:
        return jsonify({"error": "Activity not found"}), 404

    sess_prof = session.get("selected_profile") or {}
    subject_name = request.args.get("subject_name") or sess_prof.get("name") or session.get("linkedin_profile", {}).get("name", "My Profile")
    subject_id = sess_prof.get("subject_id") or "self"
    subject_url = sess_prof.get("profile_url")

    all_items = get_activities_for_subject(
        subject_id=subject_id,
        subject_name=subject_name,
        data_source="real_linkedin",
        subject_url=subject_url
    )

    result = analyze_activities(
        rows=all_items,
        activity_types=["all"],
        subject_name=subject_name,
        data_source="real_linkedin",
        subject_url=subject_url,
        headline=sess_prof.get("headline", "LinkedIn Profile")
    )

    session["latest_analysis"] = result
    return jsonify({
        "message": "Activity removed successfully",
        "analysis": result
    })


