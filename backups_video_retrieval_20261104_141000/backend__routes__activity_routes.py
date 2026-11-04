import json
import re
from pathlib import Path
from flask import Blueprint, jsonify, request, session
from auth.token_store import get_access_token
from services.activity_service import get_activities_for_subject
from services.analysis_service import analyze_activities
from services.sentiment_service import compute_sentiment_summary
from services.connections_storage import load_saved_connections
from services.saved_profiles_storage import (
    load_saved_profiles,
    save_target_profile,
    remove_saved_profile,
    extract_slug
)
from services.analysis_storage import save_latest_analysis, load_latest_analysis, load_profile_successful_analysis
from services.imported_activities_storage import (
    save_imported_activities,
    load_imported_activities,
    delete_imported_activities
)
from linkedin.data_parser import parse_activities_csv, parse_activities_from_file_or_zip
from services.pipeline_service import (
    start_pipeline_run, start_stage, complete_stage, fail_stage, skip_stage,
    complete_pipeline_run, get_latest_pipeline_run, get_pipeline_run, get_recent_pipeline_runs
)
from utils.logger import (
    get_logger, generate_run_id, set_current_run_id, clear_current_run_id,
    get_recent_logs, get_known_runs
)

logger = get_logger(__name__)

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

    for c in conns:
        c_url = (c.get("profile_url") or "").strip()
        if extract_slug(c_url) == slug.lower():
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
        sp_url = (sp.get("profile_url") or "").strip()
        sp_id = (sp.get("id") or "").strip().lower()
        if extract_slug(sp_url) == slug.lower() or (sp_id and sp_id == slug.lower()):
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


@activity_bp.route("/api/profiles/history", methods=["DELETE"])
@activity_bp.route("/api/saved-profiles/history", methods=["DELETE"])
def delete_profile_history():
    """
    Explicitly removes historical successful analysis for a profile URL or slug.
    Separate from bookmark removal to protect against accidental data loss (Phase 4 - Issue 13).
    """
    from services.analysis_storage import clear_profile_successful_analysis
    data = request.get_json(silent=True) or {}
    target = data.get("url") or data.get("profile_url") or data.get("id") or request.args.get("profile_url") or request.args.get("url")
    if not target:
        return jsonify({"error": "Profile URL or ID is required."}), 400

    cleared = clear_profile_successful_analysis(target)
    return jsonify({
        "success": True,
        "cleared": cleared,
        "profile_url": target,
        "message": "Historical analysis cleared successfully." if cleared else "No historical analysis found for this profile."
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

    # Generate or reuse Run ID for unified tracing across logging & pipeline monitoring
    req_run_id = request.headers.get("X-Run-ID") or request.args.get("run_id")
    run_id = req_run_id or generate_run_id("run")
    set_current_run_id(run_id)

    # Check if this request is a secondary/optional file upload (CSV or ZIP)
    upload = request.files.get("file")
    if upload and upload.filename:
        start_pipeline_run("Uploaded Archive", run_id=run_id)
        start_stage(run_id, 1, f"Received file upload: {upload.filename}")
        complete_stage(run_id, 1, items_count=1)
        start_stage(run_id, 2, "Checking archive file format and size")
        complete_stage(run_id, 2, details="Archive format valid")

        max_upload_mb = int(os.getenv("MAX_UPLOAD_SIZE_MB", "100"))
        max_upload_bytes = max_upload_mb * 1024 * 1024

        if request.content_length and request.content_length > max_upload_bytes:
            fail_stage(run_id, 2, f"File exceeds maximum size {max_upload_mb} MB")
            complete_pipeline_run(run_id, status="failed")
            return jsonify({"error": f"Uploaded file exceeds the maximum allowed size of {max_upload_mb} MB."}), 400

        try:
            file_bytes = upload.stream.read(max_upload_bytes + 1)
            if len(file_bytes) > max_upload_bytes:
                fail_stage(run_id, 2, f"File exceeds maximum size {max_upload_mb} MB")
                complete_pipeline_run(run_id, status="failed")
                return jsonify({"error": f"Uploaded file exceeds the maximum allowed size of {max_upload_mb} MB."}), 400

            skip_stage(run_id, 3, "Imported file source - browser launch not required")
            skip_stage(run_id, 4, "Imported file source - web navigation not required")
            skip_stage(run_id, 5, "Imported file source - feed scrolling not required")

            start_stage(run_id, 6, "Parsing activities from uploaded archive")
            parsed = parse_activities_from_file_or_zip(file_bytes, upload.filename)
            if not parsed:
                fail_stage(run_id, 6, "No valid activity records found in archive")
                complete_pipeline_run(run_id, status="failed")
                return jsonify({"error": "No valid activity records found in file. Supported files: Reactions.csv, Comments.csv, Shares.csv, or LinkedIn export ZIP."}), 400

            complete_stage(run_id, 6, items_count=len(parsed), details=f"Parsed {len(parsed)} records")

            # Convert to dicts and save in server-side storage to prevent cookie overflow
            imported = [a.to_dict() for a in parsed]
            import_id = save_imported_activities(imported)
            session["imported_activity_id"] = import_id
            session.pop("imported_activities", None)

            filters = request.form
            kinds = [kind.lower() for kind in filters.getlist("activities")]
            if not kinds or "all" in kinds:
                kinds = ["all"]
            start_date = filters.get("startDate") or None
            end_date = filters.get("endDate") or None
            subject_name = filters.get("subject_name", "Imported Activity Data")
            subject_url = filters.get("subject_url")

            start_stage(run_id, 7, "Normalizing dates and applying activity filters")
            result = analyze_activities(
                imported,
                kinds,
                start_date,
                end_date,
                subject_name=subject_name,
                data_source="imported",
                subject_url=subject_url,
                retrieval_completeness="COMPLETE"
            )
            complete_stage(run_id, 7, items_count=len(result.get("activities", [])))

            start_stage(run_id, 8, "Enriching activities with sentiment analysis")
            complete_stage(run_id, 8, items_count=len(result.get("activities", [])))

            start_stage(run_id, 9, "Persisting latest analysis to MongoDB and JSON")
            save_latest_analysis(result)
            session.pop("latest_analysis", None)
            complete_stage(run_id, 9)

            start_stage(run_id, 10, "Packaging API response payload")
            result["run_id"] = run_id
            complete_stage(run_id, 10)

            v_count = sum(1 for a in result.get("activities", []) if a.get("type") == "video" or a.get("video_url") or a.get("video_thumbnail"))
            complete_pipeline_run(run_id, status="completed", summary={"cards_found": len(result.get("activities", [])), "videos_detected": v_count, "activities_stored": len(result.get("activities", []))})
            return jsonify(result)
        except Exception as e:
            fail_stage(run_id, 6, str(e))
            complete_pipeline_run(run_id, status="failed")
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

    # Check if multiple profiles are requested
    profiles_payload = data.get("profiles")
    target_desc = "Multiple Profiles" if (profiles_payload and len(profiles_payload) > 0) else (data.get("subject_url") or data.get("subject_name") or "LinkedIn Profile")
    start_pipeline_run(target_desc, run_id=run_id)
    start_stage(run_id, 1, f"Received analysis request for {target_desc}")
    complete_stage(run_id, 1, items_count=len(profiles_payload) if profiles_payload else 1)

    start_stage(run_id, 2, "Checking LinkedIn session state")
    complete_stage(run_id, 2, details="Session access token present" if access_token else "Public guest mode")

    if profiles_payload and isinstance(profiles_payload, list) and len(profiles_payload) > 0:
        kinds = data.get("activities", [])
        if isinstance(kinds, str):
            kinds = [kinds]
        kinds = [k.lower() for k in kinds if k]
        if not kinds:
            kinds = ["all"]

        profile_results = []
        all_activities = []
        combined_by_type = {}

        for idx, p in enumerate(profiles_payload):
            p_url = (p.get("profile_url") or p.get("url") or "").strip()
            if not p_url:
                continue

            # Polite pause between profile requests to respect LinkedIn rate limits and prevent 999
            if idx > 0:
                import time
                time.sleep(5.0)

            slug = extract_slug(p_url)
            if not slug:
                return jsonify({
                    "error": f"Invalid or incomplete LinkedIn profile URL '{p_url}'. Must include a profile slug (e.g. https://www.linkedin.com/in/username)."
                }), 400
            p_name = (p.get("name") or slug.replace("-", " ").title() or "LinkedIn Profile").strip()
            p_headline = (p.get("headline") or "LinkedIn Profile").strip()
            p_picture = p.get("picture") or None

            # Preserve existing saved picture if not provided or empty in batch request
            if not p_picture or not (isinstance(p_picture, str) and (p_picture.startswith("http://") or p_picture.startswith("https://"))):
                try:
                    from services.saved_profiles_storage import get_saved_profile_by_url
                    existing_sp = get_saved_profile_by_url(p_url)
                    if existing_sp and existing_sp.get("picture"):
                        p_picture = existing_sp.get("picture")
                    else:
                        p_picture = None
                except Exception:
                    p_picture = None

            p_start = p.get("startDate") or p.get("start_date") or None
            p_end = p.get("endDate") or p.get("end_date") or None

            if p_start and p_end and p_start > p_end:
                return jsonify({"error": f"For {p_name}, Start Date must be on or before End Date."}), 400

            # Permanently update and persist in saved_target_profiles.json
            save_target_profile({
                "profile_url": p_url,
                "name": p_name,
                "headline": p_headline,
                "picture": p_picture
            })

            p_status_tracker = {}
            loaded_imported = load_imported_activities(session.get("imported_activity_id")) or session.get("imported_activities")
            p_raw_acts = get_activities_for_subject(
                subject_id=slug,
                subject_name=p_name,
                data_source="real_linkedin",
                access_token=access_token,
                session_activities=loaded_imported,
                subject_url=p_url,
                allow_sample_fallback=False,
                start_date=p_start,
                end_date=p_end,
                activity_types=kinds,
                status_out=p_status_tracker,
                target_photo=p_picture,
                target_headline=p_headline,
                max_activities=data.get("max_activities"),
                max_scrolls=data.get("max_scrolls")
            )

            # Check if saved profile picture or headline was updated during retrieval
            try:
                from services.saved_profiles_storage import get_saved_profile_by_url
                saved_prof = get_saved_profile_by_url(p_url)
                if saved_prof:
                    if "picture" in saved_prof:
                        p_picture = saved_prof.get("picture")
                    if saved_prof.get("headline"):
                        p_headline = saved_prof.get("headline")
            except Exception:
                pass

            p_analysis = analyze_activities(
                rows=p_raw_acts,
                activity_types=kinds,
                start_date=p_start,
                end_date=p_end,
                subject_name=p_name,
                data_source="real_linkedin",
                subject_url=p_url,
                headline=p_headline,
                retrieval_completeness=p_status_tracker.get("retrieval_completeness", "COMPLETE")
            )

            p_analysis["picture"] = p_picture
            p_analysis["retrieval_status"] = p_status_tracker.get("status", "")
            p_analysis["status_type"] = p_status_tracker.get("status_type", "")
            p_analysis["retrieval_completeness"] = p_status_tracker.get("retrieval_completeness", "COMPLETE")
            if not p_analysis.get("activities"):
                p_analysis["empty_message"] = p_status_tracker.get("empty_message") or p_status_tracker.get("status") or f"No activity data available for {p_name} in this date range."

            # Tag each activity with profile info
            for act in p_analysis.get("activities", []):
                act["target_profile_name"] = p_name
                act["target_profile_url"] = p_url
                act["target_profile_headline"] = p_headline
                act["target_profile_photo"] = p_picture

            profile_results.append(p_analysis)
            all_activities.extend(p_analysis.get("activities", []))

            for t, cnt in p_analysis.get("by_type", {}).items():
                combined_by_type[t] = combined_by_type.get(t, 0) + cnt

        if not profile_results:
            return jsonify({"error": "No valid profiles were provided for analysis."}), 400

        total_acts = sum(pr.get("total_activities", 0) for pr in profile_results)
        total_eng = sum(pr.get("total_engagement", 0) for pr in profile_results)
        is_multi = len(profile_results) > 1

        top_status_type = profile_results[0].get("status_type", "") if len(profile_results) == 1 else ("SUCCESS_WITH_ACTIVITIES" if total_acts > 0 else "ACTIVITY_SECTION_UNAVAILABLE")
        top_retrieval_status = profile_results[0].get("retrieval_status", "") if len(profile_results) == 1 else ("Analysis complete" if total_acts > 0 else "No activities retrieved")
        top_empty_msg = profile_results[0].get("empty_message", "") if len(profile_results) == 1 else ("No activities found for the selected profiles.")
        top_completeness = profile_results[0].get("retrieval_completeness", "COMPLETE" if total_acts > 0 else "UNAVAILABLE") if len(profile_results) == 1 else (
            "PARTIAL_CEILING_REACHED" if any(pr.get("retrieval_completeness") == "PARTIAL_CEILING_REACHED" for pr in profile_results)
            else ("PARTIAL_GUEST_LIMIT" if any(pr.get("retrieval_completeness") == "PARTIAL_GUEST_LIMIT" for pr in profile_results)
            else ("UNAVAILABLE" if all(pr.get("retrieval_completeness") in ("UNAVAILABLE", "BLOCKED", "FAILED") for pr in profile_results)
            else "COMPLETE"))
        )

        combined_result = {
            "is_multi_profile": is_multi,
            "profiles_count": len(profile_results),
            "profile_results": profile_results,
            "subject_name": profile_results[0]["subject_name"] if not is_multi else f"{len(profile_results)} Selected Profiles",
            "subject_url": profile_results[0].get("subject_url") if not is_multi else None,
            "headline": profile_results[0].get("headline") if not is_multi else "Combined Profiles Analysis",
            "picture": profile_results[0].get("picture") if not is_multi else None,
            "data_source": "real_linkedin",
            "status_type": top_status_type,
            "retrieval_status": top_retrieval_status,
            "retrieval_completeness": top_completeness,
            "empty_message": top_empty_msg,
            "total_activities": total_acts,
            "posts_count": sum(pr.get("posts_count", 0) for pr in profile_results),
            "comments_count": sum(pr.get("comments_count", 0) for pr in profile_results),
            "likes_count": sum(pr.get("likes_count", 0) for pr in profile_results),
            "shares_count": sum(pr.get("shares_count", 0) for pr in profile_results),
            "videos_count": sum(pr.get("videos_count", 0) for pr in profile_results),
            "likes": sum(pr.get("likes", 0) for pr in profile_results),
            "comments": sum(pr.get("comments", 0) for pr in profile_results),
            "shares": sum(pr.get("shares", 0) for pr in profile_results),
            "total_engagement": total_eng,
            "average_engagement": round(total_eng / max(1, total_acts), 2),
            "by_type": combined_by_type,
            "date_range": {
                "start": min((pr.get("date_range", {}).get("start") for pr in profile_results if pr.get("date_range", {}).get("start")), default=None),
                "end": max((pr.get("date_range", {}).get("end") for pr in profile_results if pr.get("date_range", {}).get("end")), default=None)
            },
            "activities": all_activities,
            "sentiment_summary": compute_sentiment_summary(all_activities)
        }

        start_stage(run_id, 7, "Aggregating and normalizing batch profile activities")
        complete_stage(run_id, 7, items_count=len(all_activities))

        start_stage(run_id, 8, "Computing combined sentiment summary")
        complete_stage(run_id, 8, items_count=len(all_activities))

        start_stage(run_id, 9, "Persisting batch analysis to MongoDB and JSON")
        save_latest_analysis(combined_result)
        session.pop("latest_analysis", None)
        complete_stage(run_id, 9)

        start_stage(run_id, 10, "Packaging API response payload")
        combined_result["run_id"] = run_id
        complete_stage(run_id, 10)

        v_count = sum(1 for a in all_activities if a.get("type") == "video" or a.get("video_url") or a.get("video_thumbnail"))
        complete_pipeline_run(run_id, status="completed", summary={"cards_found": len(all_activities), "videos_detected": v_count, "activities_stored": len(all_activities)})
        clear_current_run_id()
        return jsonify(combined_result)

    # Pull defaults from session["selected_profile"] if present
    sess_prof = session.get("selected_profile") or {}
    subject_id = data.get("subject_id") or sess_prof.get("subject_id") or "self"
    subject_name = data.get("subject_name") or sess_prof.get("name") or session.get("linkedin_profile", {}).get("name", "LinkedIn Profile")
    subject_url = data.get("subject_url") or sess_prof.get("profile_url") or None
    headline = data.get("headline") or sess_prof.get("headline") or "LinkedIn Profile"
    data_source = data.get("data_source") or sess_prof.get("source") or "real_linkedin"

    # Validate LinkedIn profile URL for single profile request
    if subject_url:
        cleaned_url = subject_url.strip()
        if cleaned_url in ("https://www.linkedin.com/in/", "https://www.linkedin.com/in", "http://www.linkedin.com/in/", "http://www.linkedin.com/in"):
            return jsonify({
                "error": "Incomplete LinkedIn profile URL. Must include profile username or slug (e.g. https://www.linkedin.com/in/username)."
            }), 400
        if "linkedin.com/in" in cleaned_url.lower():
            extracted_p_slug = extract_slug(cleaned_url)
            if not extracted_p_slug:
                return jsonify({
                    "error": "Invalid LinkedIn profile URL. Profile username or slug is missing or malformed."
                }), 400
            if not subject_id or subject_id in ("self", "none", "unknown"):
                subject_id = extracted_p_slug

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
    loaded_imported = load_imported_activities(session.get("imported_activity_id")) or session.get("imported_activities")
    raw_activities = get_activities_for_subject(
        subject_id=subject_id,
        subject_name=subject_name,
        data_source=data_source,
        access_token=access_token,
        session_activities=loaded_imported,
        subject_url=subject_url,
        allow_sample_fallback=(data_source == "sample"),
        start_date=start_date,
        end_date=end_date,
        activity_types=kinds,
        status_out=status_tracker,
        target_photo=sess_prof.get("picture"),
        target_headline=headline,
        max_activities=data.get("max_activities"),
        max_scrolls=data.get("max_scrolls"),
        run_id=run_id
    )

    # Check if stages 3, 4, 5 need skipping for local/cached/sample retrieval
    run_state = get_pipeline_run(run_id)
    if run_state:
        st3 = next((s for s in run_state.get("stages", []) if s["id"] == 3), None)
        if st3 and st3["status"] == "pending":
            skip_stage(run_id, 3, "Playwright browser not needed for local/mock data")
            skip_stage(run_id, 4, "Web navigation not needed")
            skip_stage(run_id, 5, "Feed scrolling not needed")
            start_stage(run_id, 6, "Retrieved records from local storage")
            complete_stage(run_id, 6, items_count=len(raw_activities))

    # Perform deterministic analysis
    start_stage(run_id, 7, "Applying date filtering, relative date parsing, and deduplication")
    result = analyze_activities(
        rows=raw_activities,
        activity_types=kinds,
        start_date=start_date,
        end_date=end_date,
        subject_name=subject_name,
        data_source=data_source,
        subject_url=subject_url,
        headline=headline,
        retrieval_completeness=status_tracker.get("retrieval_completeness", "COMPLETE" if (raw_activities and len(raw_activities) > 0) else "UNAVAILABLE")
    )
    complete_stage(run_id, 7, items_count=len(result.get("activities", [])))

    start_stage(run_id, 8, "Enriching activities with AI sentiment & tone scores")
    complete_stage(run_id, 8, items_count=len(result.get("activities", [])))

    result["retrieval_status"] = status_tracker.get("status", "")
    result["status_type"] = status_tracker.get("status_type", "")
    result["retrieval_completeness"] = status_tracker.get("retrieval_completeness", "COMPLETE" if (raw_activities and len(raw_activities) > 0) else "UNAVAILABLE")
    if not result.get("activities"):
        result["empty_message"] = status_tracker.get("empty_message") or status_tracker.get("status") or "Activity could not be retrieved from LinkedIn."

    # If no activities retrieved from live LinkedIn, check for preserved historical activities for this exact profile
    if not result.get("activities") and subject_url:
        p_slug = extract_slug(subject_url)
        if p_slug:
            hist_record = load_profile_successful_analysis(p_slug)
            if hist_record and hist_record.get("activities"):
                raw_hist = hist_record.get("activities", [])
                hist_start = data.get("startDate") or None
                hist_end = data.get("endDate") or None
                filtered_hist = analyze_activities(
                    rows=raw_hist,
                    activity_types=kinds,
                    start_date=hist_start,
                    end_date=hist_end,
                    subject_name=subject_name,
                    data_source="real_linkedin",
                    subject_url=subject_url,
                    headline=headline,
                    retrieval_completeness="HISTORICAL_PRESERVED"
                )
                filtered_hist["is_historical"] = True
                filtered_hist["historical_analyzed_at"] = hist_record.get("historical_analyzed_at") or hist_record.get("analyzed_at") or hist_record.get("updated_at")
                filtered_hist["original_unfiltered_count"] = len(raw_hist)
                filtered_hist["historical_date_range"] = hist_record.get("date_range")
                filtered_hist["retrieval_status"] = status_tracker.get("status", "")
                filtered_hist["status_type"] = status_tracker.get("status_type", "")
                result["historical_successful_analysis"] = filtered_hist

    # Update persistent storage for target profile
    target_picture = None
    if subject_url:
        try:
            from services.saved_profiles_storage import get_saved_profile_by_url
            saved_prof = get_saved_profile_by_url(subject_url)
            if saved_prof and saved_prof.get("picture"):
                target_picture = saved_prof.get("picture")
            elif sess_prof.get("picture"):
                target_picture = sess_prof.get("picture")
            else:
                target_picture = None
        except Exception:
            target_picture = sess_prof.get("picture") or None
        save_target_profile({
            "profile_url": subject_url,
            "name": subject_name,
            "headline": headline,
            "picture": target_picture
        })
    else:
        target_picture = sess_prof.get("picture") or None

    single_summary = {
        "subject_name": result.get("subject_name"),
        "subject_url": result.get("subject_url"),
        "headline": result.get("headline"),
        "picture": target_picture,
        "date_range": result.get("date_range"),
        "total_activities": result.get("total_activities", 0),
        "by_type": result.get("by_type", {}),
        "posts_count": result.get("posts_count", 0),
        "comments_count": result.get("comments_count", 0),
        "likes_count": result.get("likes_count", 0),
        "shares_count": result.get("shares_count", 0),
        "videos_count": result.get("videos_count", 0),
        "likes": result.get("likes", 0),
        "comments": result.get("comments", 0),
        "shares": result.get("shares", 0),
        "total_engagement": result.get("total_engagement", 0),
        "average_engagement": result.get("average_engagement", 0),
        "activities": result.get("activities", []),
        "sentiment_summary": result.get("sentiment_summary") or compute_sentiment_summary(result.get("activities", [])),
        "retrieval_status": result.get("retrieval_status", ""),
        "status_type": result.get("status_type", ""),
        "retrieval_completeness": status_tracker.get("retrieval_completeness", "COMPLETE" if (raw_activities and len(raw_activities) > 0) else "UNAVAILABLE"),
        "empty_message": result.get("empty_message", ""),
        "filtered_activity_types": kinds,
        "historical_successful_analysis": result.get("historical_successful_analysis")
    }

    result["is_multi_profile"] = False
    result["profiles_count"] = 1
    result["picture"] = target_picture
    result["filtered_activity_types"] = kinds
    result["profile_results"] = [single_summary]

    start_stage(run_id, 9, "Saving latest analysis to MongoDB and JSON dual-storage")
    save_latest_analysis(result)
    session.pop("latest_analysis", None)
    complete_stage(run_id, 9)

    start_stage(run_id, 10, "Packaging API response payload")
    result["run_id"] = run_id
    complete_stage(run_id, 10)

    v_count = sum(1 for a in result.get("activities", []) if a.get("type") == "video" or a.get("video_url") or a.get("video_thumbnail"))
    complete_pipeline_run(run_id, status="completed", summary={"cards_found": len(result.get("activities", [])), "videos_detected": v_count, "activities_stored": len(result.get("activities", []))})
    clear_current_run_id()
    return jsonify(result)



@activity_bp.route("/api/analysis/latest", methods=["GET"])
def get_latest_analysis():
    """
    Returns the most recent analysis result for dashboard display with strict profile identity verification.
    If a specific subject_id or profile_url is requested, verifies that the latest analysis belongs to that subject.
    If the latest analysis belongs to a different profile, attempts to load the requested profile's preserved
    successful analysis from profile_analysis_history. If none exists, returns 404 with has_data: False,
    never leaking another profile's data.
    """
    req_subject_id = (request.args.get("subject_id") or "").strip()
    req_profile_url = (request.args.get("profile_url") or "").strip()
    req_subject_name = (request.args.get("subject_name") or "").strip()

    req_slug = extract_slug(req_profile_url) or extract_slug(req_subject_id)

    analysis = session.get("latest_analysis") or load_latest_analysis()

    if not req_slug and not req_subject_name and not req_subject_id and not req_profile_url:
        sess_prof = session.get("selected_profile") or {}
        req_profile_url = sess_prof.get("profile_url") or ""
        req_subject_id = sess_prof.get("subject_id") or ""
        req_subject_name = sess_prof.get("name") or ""
        req_slug = extract_slug(req_profile_url) or extract_slug(req_subject_id)

    # If a specific profile was requested, verify identity
    if req_slug or req_subject_name:
        is_match = False
        if analysis:
            a_url = analysis.get("subject_url") or ""
            a_id = analysis.get("subject_id") or ""
            a_name = analysis.get("subject_name") or ""
            a_slug = extract_slug(a_url) or extract_slug(a_id)

            if req_slug and a_slug and req_slug == a_slug:
                is_match = True
            elif req_subject_name and a_name and req_subject_name.strip().lower() == a_name.strip().lower():
                is_match = True
            elif req_subject_id and a_id and req_subject_id.strip().lower() == a_id.strip().lower():
                is_match = True

        if is_match and analysis:
            return jsonify({
                **analysis,
                "has_data": True
            })

        # Latest analysis does not match requested profile: check profile-specific history
        if req_slug:
            historical = load_profile_successful_analysis(req_slug)
            if historical:
                req_kinds = request.args.getlist("activities") or (request.args.get("activities", "").split(",") if request.args.get("activities") else [])
                req_kinds = [k.strip().lower() for k in req_kinds if k.strip()]
                if not req_kinds:
                    req_kinds = ["all"]

                raw_hist = historical.get("activities", [])
                if req_kinds != ["all"]:
                    filtered_hist = analyze_activities(
                        rows=raw_hist,
                        activity_types=req_kinds,
                        subject_name=historical.get("subject_name") or req_subject_name,
                        data_source=historical.get("data_source") or "real_linkedin",
                        subject_url=historical.get("subject_url") or req_profile_url,
                        headline=historical.get("headline"),
                        retrieval_completeness="HISTORICAL_PRESERVED"
                    )
                    filtered_hist["is_historical"] = True
                    filtered_hist["historical_analyzed_at"] = historical.get("historical_analyzed_at") or historical.get("analyzed_at") or historical.get("updated_at")
                    filtered_hist["original_unfiltered_count"] = len(raw_hist)
                    return jsonify({
                        **filtered_hist,
                        "has_data": True,
                        "is_historical": True
                    })

                return jsonify({
                    **historical,
                    "has_data": True,
                    "is_historical": True
                })

        # No matching analysis found for the requested profile
        target_label = req_subject_name or req_slug or "the selected profile"
        return jsonify({
            "error": f"No analysis has been run yet for {target_label}.",
            "has_data": False,
            "subject_name": req_subject_name,
            "subject_url": req_profile_url,
            "subject_id": req_subject_id
        }), 404

    # No specific profile filter specified
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
        "video_url": data.get("video_url") or None,
        "video_thumbnail": data.get("video_thumbnail") or None,
        "video_status": data.get("video_status") or ("Video stream extracted" if data.get("video_url") else ("Video detected" if act_type == "video" else "No video")),
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

    save_latest_analysis(result)
    session.pop("latest_analysis", None)
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

    save_latest_analysis(result)
    session.pop("latest_analysis", None)
    return jsonify({
        "message": "Activity removed successfully",
        "analysis": result
    })


# --------------------------------------------------------------------------
# Pipeline Monitoring & Observability Endpoints
# --------------------------------------------------------------------------

@activity_bp.route("/api/pipeline/latest", methods=["GET"])
def get_latest_pipeline():
    """Returns the latest pipeline run monitoring data."""
    run = get_latest_pipeline_run()
    if not run:
        return jsonify({
            "status": "no_runs",
            "message": "No pipeline runs recorded yet.",
            "stages": []
        }), 200
    return jsonify(run), 200


@activity_bp.route("/api/pipeline/<run_id>", methods=["GET"])
def get_pipeline_by_id(run_id):
    """Returns pipeline run details for a specific run_id."""
    run = get_pipeline_run(run_id)
    if not run:
        return jsonify({"error": f"Pipeline run '{run_id}' not found."}), 404
    return jsonify(run), 200


@activity_bp.route("/api/pipeline/runs", methods=["GET"])
def get_recent_pipelines():
    """Returns recent pipeline runs (newest first)."""
    limit = request.args.get("limit", 10, type=int)
    runs = get_recent_pipeline_runs(limit=min(limit, 50))
    return jsonify({"runs": runs, "total": len(runs)}), 200


# --------------------------------------------------------------------------
# Persistent Logging & Log Viewer Endpoints
# --------------------------------------------------------------------------

@activity_bp.route("/api/logs", methods=["GET"])
def get_logs():
    """
    Returns recent persistent log entries with filtering options.
    Query params:
      lines: int (default 100, max 1000)
      level: ALL | INFO | WARNING | ERROR | DEBUG
      run_id: filter by specific pipeline run
      search: case-insensitive text match
    """
    lines = request.args.get("lines", 100, type=int)
    level = request.args.get("level", "ALL")
    run_id = request.args.get("run_id")
    search = request.args.get("search")

    logs = get_recent_logs(
        lines=min(lines, 1000),
        level=level,
        run_id=run_id,
        search=search
    )
    return jsonify({
        "success": True,
        "logs": logs,
        "total_lines": len(logs)
    }), 200


@activity_bp.route("/api/logs/runs", methods=["GET"])
def get_log_runs():
    """Returns summarized known run IDs from persistent logs."""
    limit = request.args.get("limit", 20, type=int)
    runs = get_known_runs(limit=min(limit, 50))
    return jsonify({"runs": runs, "total": len(runs)}), 200



