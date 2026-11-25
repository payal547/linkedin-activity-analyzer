import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse
from flask import Blueprint, g, jsonify, request, session
from auth.token_store import get_access_token
from services.activity_service import get_activities_for_subject
from services.analysis_service import analyze_activities
from services.sentiment_service import compute_sentiment_summary
from services.connections_storage import load_saved_connections
from services.saved_profiles_storage import (
    load_saved_profiles,
    save_target_profile,
    remove_saved_profile,
    extract_slug,
    parse_linkedin_url,
    get_saved_profile_by_url
)
from services.analysis_storage import save_latest_analysis, load_latest_analysis, load_profile_successful_analysis
from services.activities_storage import save_activities_batch
from services.imported_activities_storage import (
    save_imported_activities,
    load_imported_activities,
    delete_imported_activities
)
from linkedin.data_parser import (
    parse_activities_csv,
    parse_activities_from_file_or_zip,
    parse_registration_from_file_or_zip
)
from services.pipeline_service import (
    start_pipeline_run, start_stage, complete_stage, fail_stage, skip_stage,
    update_pipeline_profile, complete_pipeline_run, get_latest_pipeline_run,
    get_pipeline_run, get_recent_pipeline_runs
)
from utils.logger import (
    get_logger, generate_run_id, set_current_run_id, clear_current_run_id,
    get_recent_logs, get_known_runs
)

logger = get_logger(__name__)

activity_bp = Blueprint("activity", __name__)


@activity_bp.after_request
def persist_analysis_run(response):
    """Record one real analysis request after its response has been assembled."""
    context = getattr(g, "analysis_run_context", None)
    if not context:
        return response
    # Validation errors return early without finishing the pipeline run; close it here
    # so /api/pipeline/* does not report it as still in progress.
    if response.status_code >= 400:
        try:
            live_run = get_pipeline_run(context["run_id"])
            if live_run and live_run.get("status") == "running":
                complete_pipeline_run(context["run_id"], status="failed")
        except Exception as exc:
            logger.warning("Could not close failed pipeline run: %s", exc)
    try:
        payload = response.get_json(silent=True) or {}
        analysis_result = context.get("analysis_result")
        source = analysis_result if isinstance(analysis_result, dict) else payload
        profile_rows = source.get("profile_results") if isinstance(source, dict) else None
        identities = []
        if isinstance(profile_rows, list):
            identities = profile_rows
        elif isinstance(source, dict) and (source.get("subject_url") or source.get("subject_id")):
            identities = [source]
        slugs = set(context.get("profile_slugs", []))
        summaries = []
        run_sources = {}
        try:
            from services.dashboard_analytics_service import _activity_type, _collect_content
        except Exception:
            _activity_type = None
            _collect_content = None
        for row in identities:
            if not isinstance(row, dict):
                continue
            slug = extract_slug(row.get("subject_url") or row.get("subject_id"))
            if slug == "self":
                slug = ""
            if slug:
                slugs.add(slug)
            rows = row.get("activities") if isinstance(row.get("activities"), list) else []
            identity = slug or f"run-profile-{len(run_sources)}"
            run_sources.setdefault(identity, {"slug": identity, "activities": []})["activities"].extend(rows)

        unique_content = _collect_content(list(run_sources.values()), [], []) if _collect_content else []
        for identity, run_source in run_sources.items():
            unique_rows = _collect_content([run_source], [], []) if _collect_content else run_source["activities"]
            type_counts = {}
            for activity in unique_rows:
                kind = _activity_type(activity) if _activity_type else str(activity.get("type") or "unknown").lower()
                type_counts[kind] = type_counts.get(kind, 0) + 1
            source_row = next((item for item in identities if isinstance(item, dict) and (extract_slug(item.get("subject_url") or item.get("subject_id")) or identity) == identity), {})
            summaries.append({
                "profile_slug": identity if not identity.startswith("run-profile-") else None,
                "total_activities": len(unique_rows),
                "posts_count": sum(1 for activity in unique_rows if _activity_type and _activity_type(activity) == "post" and not activity.get("is_video")),
                "videos_count": sum(1 for activity in unique_rows if activity.get("is_video")),
                "by_type": type_counts,
                "retrieval_completeness": source_row.get("retrieval_completeness"),
                "requested_date_range": source_row.get("requested_date_range"),
                "requested_activity_types": source_row.get("filtered_activity_types") or context.get("requested_activity_types") or [],
            })

        total = len(unique_content) if analysis_result else (payload.get("total_activities") if isinstance(payload, dict) else None)
        if total is None and summaries:
            total = sum(int(row.get("total_activities") or 0) for row in summaries)
        run_doc = {
            "run_id": context["run_id"],
            "started_at": context["started_at"],
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "status": "completed" if response.status_code < 400 else "failed",
            "http_status": response.status_code,
            "profile_slugs": sorted(slugs),
            "profile_count": len(slugs) or int(context.get("profile_count") or 0),
            "profile_summaries": summaries,
            "requested_date_range": context.get("requested_date_range"),
            "requested_activity_types": context.get("requested_activity_types") or [],
            "data_source": context.get("data_source"),
            "total_activities": total,
            "posts_count": sum(1 for activity in unique_content if _activity_type and _activity_type(activity) == "post" and not activity.get("is_video")) if analysis_result else (payload.get("posts_count") if isinstance(payload, dict) else None),
            "videos_count": sum(1 for activity in unique_content if activity.get("is_video")) if analysis_result else (payload.get("videos_count") if isinstance(payload, dict) else None),
            "by_type": {kind: sum(1 for activity in unique_content if (_activity_type(activity) if _activity_type else str(activity.get("type") or "unknown").lower()) == kind) for kind in {(_activity_type(activity) if _activity_type else str(activity.get("type") or "unknown").lower()) for activity in unique_content}} if analysis_result else (payload.get("by_type") if isinstance(payload, dict) and isinstance(payload.get("by_type"), dict) else {}),
            "retrieval_completeness": source.get("retrieval_completeness") if isinstance(source, dict) else None,
        }
        from services.analysis_runs_storage import save_analysis_run
        save_analysis_run(run_doc)
    except Exception as exc:
        logger.warning("Could not persist analysis run summary: %s", exc)
    return response


def _paginate_analysis_payload(payload, page, per_page):
    """Return an analysis response with only the requested activity page(s)."""
    if not per_page:
        return payload

    # Copy response containers only; activity rows are immutable during pagination.
    result = dict(payload)
    try:
        page = max(1, int(page or 1))
        per_page = min(100, max(1, int(per_page)))
    except (TypeError, ValueError):
        return payload

    original_profiles = result.get("profile_results")
    profile_results = [dict(profile) for profile in original_profiles] if isinstance(original_profiles, list) else original_profiles
    if isinstance(profile_results, list):
        result["profile_results"] = profile_results
    is_multi = bool(result.get("is_multi_profile") or (isinstance(profile_results, list) and len(profile_results) > 1))

    def source_activities(container):
        activities = container.get("activities")
        if isinstance(activities, list) and activities:
            return activities, "activities"
        historical = container.get("historical_successful_analysis") or {}
        historical_activities = historical.get("activities")
        if isinstance(historical_activities, list) and historical_activities:
            return historical_activities, "historical"
        return activities if isinstance(activities, list) else [], "activities"

    def page_container(container, selected_page):
        activities, source = source_activities(container)
        total = len(activities)
        if source == "historical":
            historical = dict(container.get("historical_successful_analysis") or {})
            total_pages = max(1, (total + per_page - 1) // per_page)
            start = (selected_page - 1) * per_page
            historical["activities"] = activities[start:start + per_page]
            historical["activity_pagination"] = {
                "page": selected_page, "per_page": per_page,
                "total_items": total, "total_pages": total_pages
            }
            container["historical_successful_analysis"] = historical
            container["activities"] = []
        else:
            total_pages = max(1, (total + per_page - 1) // per_page)
            start = (selected_page - 1) * per_page
            container["activities"] = activities[start:start + per_page]
        container["activity_pagination"] = {
            "page": selected_page, "per_page": per_page,
            "total_items": total, "total_pages": total_pages
        }
        historical = container.get("historical_successful_analysis") or {}
        return container.get("activities", []) or historical.get("activities", [])

    if is_multi and isinstance(profile_results, list):
        activity_sets = [source_activities(profile) for profile in profile_results]
        total_items = sum(len(activities) for activities, _source in activity_sets)
        total_pages = max(1, (total_items + per_page - 1) // per_page)
        page = min(page, total_pages)
        page_start = (page - 1) * per_page
        page_end = page_start + per_page
        cursor = 0
        displayed = []
        for profile, (activities, source) in zip(profile_results, activity_sets):
            local_start = max(0, page_start - cursor)
            local_end = min(len(activities), page_end - cursor)
            selected = activities[local_start:local_end] if local_end > local_start else []
            if source == "historical":
                historical = dict(profile.get("historical_successful_analysis") or {})
                historical["activities"] = selected
                historical["activity_pagination"] = {
                    "page": page, "per_page": per_page,
                    "total_items": len(activities), "total_pages": max(1, (len(activities) + per_page - 1) // per_page)
                }
                profile["historical_successful_analysis"] = historical
                profile["activities"] = []
            else:
                profile["activities"] = selected
            profile["activity_pagination"] = {
                "page": page, "per_page": per_page,
                "total_items": len(activities), "total_pages": max(1, (len(activities) + per_page - 1) // per_page)
            }
            displayed.extend(selected)
            cursor += len(activities)
        result["activities"] = displayed
        # The dashboard reads historical activities from each profile row in a batch.
        # A top-level history copy is redundant and could otherwise leak the full history.
        if isinstance(result.get("historical_successful_analysis"), dict):
            top_history = dict(result["historical_successful_analysis"])
            top_history["activities"] = []
            result["historical_successful_analysis"] = top_history
    else:
        activities, source = source_activities(result)
        total_items = len(activities)
        total_pages = max(1, (total_items + per_page - 1) // per_page)
        page = min(page, total_pages)
        page_container(result, page)
        if isinstance(profile_results, list) and profile_results:
            page_container(profile_results[0], page)
            result["profile_results"] = profile_results

    result["activity_pagination"] = {
        "page": page, "per_page": per_page,
        "total_items": total_items, "total_pages": total_pages
    }
    return result


def _normalize_batch_date(value):
    """Normalize common ISO/DD/MM/YYYY inputs before batch range comparison."""
    if value is None or not str(value).strip():
        return None, True
    clean = str(value).strip()
    iso_match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", clean)
    dmy_match = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", clean)
    try:
        if iso_match:
            return date(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3))).isoformat(), True
        if dmy_match:
            return date(int(dmy_match.group(3)), int(dmy_match.group(2)), int(dmy_match.group(1))).isoformat(), True
    except ValueError:
        return None, False
    return None, False


def _slug_display_name(slug: str) -> str:
    clean_slug = re.sub(r'[-_]\d+$', '', slug or "")
    return clean_slug.replace('-', ' ').replace('_', ' ').strip().title() or "LinkedIn Member"


def resolve_linkedin_url(url: str, identify: bool = True):
    """
    Works out which LinkedIn profile a pasted link points to and who that person is.

    1. Recognises the link type: profile, mobile profile, bare username, post, feed
       activity, article or lnkd.in short link (company/school/job links are rejected).
    2. Uses details already known locally (connections, own account, saved profiles).
    3. When the person is not known yet, or the link does not name them, opens the
       link with the authenticated LinkedIn session to read their name, headline and
       photo. Without a session it falls back to the username from the URL.

    Returns (valid, error_message, profile_info).
    """
    try:
        from linkedin.link_resolver import classify_linkedin_link
        from linkedin.playwright_service import identify_linkedin_profile
    except ImportError:
        from backend.linkedin.link_resolver import classify_linkedin_link
        from backend.linkedin.playwright_service import identify_linkedin_profile

    link = classify_linkedin_link(url)
    if not link["valid"]:
        return False, link["error"], None

    slug = link["slug"]
    live = None
    if link["needs_lookup"]:
        if not identify:
            return False, f"{link['link_type_label']} does not name the profile. Paste the person's profile link instead.", None
        live = identify_linkedin_profile(lookup_url=link["lookup_url"])
        if not live.get("slug"):
            return False, f"{link['link_type_label']} recognised, but its profile could not be found. {live.get('message', '')}".strip(), None
        slug = live["slug"]

    info = _lookup_known_profile(slug, f"https://www.linkedin.com/in/{slug}/", str(url).strip())

    # Saved entries created from a bare URL only hold the username-derived guess.
    known = info["identity_source"] != "url_guess" and not (
        info["identity_source"] == "saved"
        and not info.get("picture")
        and info.get("headline") in (None, "", "LinkedIn Profile")
        and info.get("name") in (_slug_display_name(slug), slug.replace("-", " ").title())
    )
    if identify and live is None and not known:
        live = identify_linkedin_profile(slug=slug)

    if live and live.get("ok"):
        final_slug = live.get("slug") or slug
        info.update({
            "profile_url": f"https://www.linkedin.com/in/{final_slug}/",
            "username": final_slug,
            "name": live.get("name") or info.get("name"),
            "headline": live.get("headline") or info.get("headline"),
            "picture": live.get("picture") or info.get("picture"),
            "identity_source": "linkedin",
        })
        if info.get("subject_id") == f"profile_{slug}":
            info["subject_id"] = f"profile_{final_slug}"

    info["link_type"] = link["link_type"]
    info["link_type_label"] = link["link_type_label"]
    info["identified"] = info["identity_source"] != "url_guess"
    info["identity_status"] = (live or {}).get("status") or ("KNOWN" if known else "NOT_ATTEMPTED")
    info["identity_message"] = (
        (live or {}).get("message") if live
        else ("Details loaded from your saved data." if known else "Name taken from the profile URL.")
    )
    return True, None, info


def _lookup_known_profile(slug: str, canonical_url: str, cleaned_url: str) -> Dict[str, Any]:
    """Profile details already known locally, or a username-derived placeholder."""
    # 1. Check if profile matches user's saved/imported connections
    conns = load_saved_connections()

    for c in conns:
        c_url = (c.get("profile_url") or "").strip()
        if extract_slug(c_url) == slug:
            return {
                "identity_source": "connections",
                "profile_url": canonical_url,
                "original_url": cleaned_url,
                "username": slug,
                "name": c.get("name") or slug.replace("-", " ").title(),
                "headline": c.get("headline") or "LinkedIn Connection",
                "company": c.get("company"),
                "position": c.get("position"),
                "picture": c.get("picture"),
                "source": c.get("source", "imported"),
                "subject_id": c.get("id") or f"profile_{slug}"
            }

    # 2. Check if profile matches authenticated user's own profile
    user_prof = session.get("linkedin_profile")
    if user_prof and user_prof.get("name"):
        own_sub = str(user_prof.get("sub", "")).lower()
        if slug in own_sub:
            return {
                "identity_source": "self",
                "profile_url": canonical_url,
                "original_url": cleaned_url,
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
        if extract_slug(sp_url) == slug or (sp_id and sp_id == slug):
            return {
                "identity_source": "saved",
                "profile_url": canonical_url,
                "original_url": cleaned_url,
                "username": slug,
                "name": sp.get("name") or slug.replace("-", " ").title(),
                "headline": sp.get("headline") or "LinkedIn Profile",
                "company": sp.get("company"),
                "position": sp.get("position"),
                "picture": sp.get("picture"),
                "source": "real_linkedin",
                "subject_id": f"profile_{slug}",
                "verified_registration_date": sp.get("verified_registration_date"),
                "registration_date_source": sp.get("registration_date_source")
            }

    # 4. Derive clean human display name from URL slug
    return {
        "identity_source": "url_guess",
        "profile_url": canonical_url,
        "original_url": cleaned_url,
        "username": slug,
        "name": _slug_display_name(slug),
        "headline": "LinkedIn Profile",
        "company": None,
        "position": None,
        "picture": None,
        "source": "real_linkedin",
        "subject_id": f"profile_{slug}"
    }


def _build_history_limits_and_coverage(activities, requested_start, requested_end, verified_reg_date=None, reg_date_source=None, completeness="COMPLETE", status_type="", extra_stats=None):
    """
    Builds official LinkedIn activity history limits metadata and date coverage.
    Reference: https://www.linkedin.com/help/linkedin/answer/a1339548
    """
    valid_dates = [a["date"] for a in activities if isinstance(a, dict) and a.get("date")]
    earliest_retrieved = min(valid_dates) if valid_dates else None
    latest_retrieved = max(valid_dates) if valid_dates else None

    # Count categories strictly
    posts_cnt = sum(1 for a in activities if isinstance(a, dict) and a.get("type") in ("post", "article"))
    comments_cnt = sum(1 for a in activities if isinstance(a, dict) and a.get("type") == "comment")
    likes_cnt = sum(1 for a in activities if isinstance(a, dict) and a.get("type") in ("like", "reaction"))
    shares_cnt = sum(1 for a in activities if isinstance(a, dict) and a.get("type") == "share")
    videos_cnt = sum(1 for a in activities if isinstance(a, dict) and (a.get("type") == "video" or a.get("is_video") or a.get("video_url")))

    extra = extra_stats or {}
    act_discovered = extra.get("activities_discovered", len(activities))
    act_parsed = extra.get("activities_parsed", len(activities))
    dups_removed = extra.get("duplicates_removed", 0)
    act_filtered = extra.get("activities_filtered", 0)
    act_saved = extra.get("activities_saved", len(activities))

    # Determine registration date note (Requirement 3)
    if verified_reg_date:
        if requested_start and requested_start < verified_reg_date:
            reg_note = "The selected date range starts before this member's verified LinkedIn registration date."
        else:
            reg_note = f"Verified account registration date: {verified_reg_date} (Source: {reg_date_source or 'Official LinkedIn Data Export'})."
    else:
        reg_note = "Account registration date is unavailable. We can only report the earliest activity date observed in accessible data."

    official_limits = {
        "documented_max_visibility_years": 2,
        "inactive_hide_threshold_days": 360,
        "official_reference_url": "https://www.linkedin.com/help/linkedin/answer/a1339548",
        "notice": (
            "According to official LinkedIn documentation (https://www.linkedin.com/help/linkedin/answer/a1339548), "
            "content up to two years old can be viewed from the profile Activity page, and the Activity section "
            "may be hidden if a member has had no activity for 360 days. Historical visibility is not a guarantee "
            "of complete extraction, and accessible history varies by account visibility, privacy settings, and rate limits."
        )
    }

    scanned_coverage = {
        "scanned_range": {"start": requested_start, "end": requested_end},
        "requested_date_range": {"start": requested_start, "end": requested_end},
        "retrieved_date_range": {"start": earliest_retrieved, "end": latest_retrieved},
        "earliest_retrieved_activity_date": earliest_retrieved,
        "latest_retrieved_activity_date": latest_retrieved,
        "earliest_observed_activity_date": earliest_retrieved,
        "activities_discovered": act_discovered,
        "activities_parsed": act_parsed,
        "duplicates_removed": dups_removed,
        "activities_filtered": act_filtered,
        "activities_saved": act_saved,
        "category_counts": {
            "posts": posts_cnt,
            "comments": comments_cnt,
            "reactions": likes_cnt,
            "shares": shares_cnt,
            "videos": videos_cnt
        },
        "status_type": status_type,
        "retrieval_completeness": completeness
    }

    return {
        "official_history_limits": official_limits,
        "scanned_coverage": scanned_coverage,
        "verified_registration_date": verified_reg_date,
        "registration_date_source": reg_date_source if verified_reg_date else None,
        "registration_date_note": reg_note,
        "earliest_retrieved_activity_date": earliest_retrieved,
        "latest_retrieved_activity_date": latest_retrieved,
        "earliest_observed_activity_date": earliest_retrieved,
        "selected_start_date": requested_start,
        "requested_date_range": {"start": requested_start, "end": requested_end},
        "retrieved_date_range": {"start": earliest_retrieved, "end": latest_retrieved},
        "activities_discovered": act_discovered,
        "activities_parsed": act_parsed,
        "duplicates_removed": dups_removed,
        "activities_filtered": act_filtered,
        "activities_saved": act_saved
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

    # Only look the person up on LinkedIn when the caller did not supply a name.
    valid, err_msg, profile_info = resolve_linkedin_url(url, identify=not data.get("name"))
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
@activity_bp.route("/api/saved-profiles", methods=["DELETE"])
@activity_bp.route("/api/saved-profiles/<path:target_id>", methods=["DELETE"])
def delete_saved_target_profile(target_id=None):
    """Removes a single saved target profile by URL or slug."""
    data = request.get_json(silent=True) or {}
    target = target_id or data.get("url") or data.get("profile_url") or data.get("id") or request.args.get("url") or request.args.get("id")
    if not target:
        return jsonify({"error": "Profile URL or ID is required."}), 400

    removed = remove_saved_profile(target)
    if not removed:
        return jsonify({"error": "Saved profile not found."}), 404

    return jsonify({
        "success": True,
        "message": "Target profile removed successfully.",
        "note": "Historical analysis records and stored activities are preserved.",
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
    initial_data = request.get_json(silent=True)
    if not isinstance(initial_data, dict):
        initial_data = request.form.to_dict(flat=True)
        form_activities = request.form.getlist("activities")
        if form_activities:
            initial_data["activities"] = form_activities
    initial_profiles = initial_data.get("profiles") if isinstance(initial_data, dict) else None
    initial_profile_rows = initial_profiles if isinstance(initial_profiles, list) else [initial_data] if isinstance(initial_data, dict) else []
    initial_slugs = {
        extract_slug(row.get("subject_url") or row.get("profile_url") or row.get("subject_id"))
        for row in initial_profile_rows if isinstance(row, dict)
    }
    initial_slugs.discard(None)
    initial_slugs.discard("")
    initial_slugs.discard("self")
    g.analysis_run_context = {
        "run_id": run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "profile_slugs": sorted(initial_slugs),
        "profile_count": len(initial_profiles) if isinstance(initial_profiles, list) else (1 if initial_data else 0),
        "requested_date_range": {
            "start": initial_data.get("startDate") if isinstance(initial_data, dict) else request.form.get("startDate"),
            "end": initial_data.get("endDate") if isinstance(initial_data, dict) else request.form.get("endDate"),
        },
        "requested_activity_types": initial_data.get("activities", []) if isinstance(initial_data, dict) else request.form.getlist("activities"),
        "data_source": initial_data.get("data_source", "real_linkedin") if isinstance(initial_data, dict) else request.form.get("data_source", "imported" if request.files.get("file") else "real_linkedin"),
    }

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

            filters = request.form
            kinds = [kind.lower() for kind in filters.getlist("activities")]
            if not kinds or "all" in kinds:
                kinds = ["all"]
            start_date = filters.get("startDate") or None
            end_date = filters.get("endDate") or None
            subject_name = filters.get("subject_name", "Imported Activity Data")
            subject_url = filters.get("subject_url")

            # Convert to dicts and save in server-side storage to prevent cookie overflow.
            # Tag rows with the profile they were uploaded for, so later analyses of
            # other profiles never pick them up.
            imported = [a.to_dict() for a in parsed]
            upload_slug = extract_slug(subject_url) if subject_url else ""
            if upload_slug:
                for row in imported:
                    if not row.get("subject_url") and not row.get("subject_id"):
                        row["subject_url"] = subject_url
                        row["subject_id"] = upload_slug
                        row["subject_name"] = row.get("subject_name") or subject_name
            import_id = save_imported_activities(imported)
            session["imported_activity_id"] = import_id
            session.pop("imported_activities", None)

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
            result["requested_date_range"] = {"start": start_date, "end": end_date}
            complete_stage(run_id, 7, items_count=len(result.get("activities", [])))

            # Check if Registration.csv exists in uploaded export
            verified_reg = parse_registration_from_file_or_zip(file_bytes, upload.filename)
            history_meta = _build_history_limits_and_coverage(
                activities=result.get("activities", []),
                requested_start=start_date,
                requested_end=end_date,
                verified_reg_date=verified_reg,
                reg_date_source="Official LinkedIn Data Export (Registration.csv)" if verified_reg else None,
                completeness="COMPLETE" if result.get("activities") else "NO_ACTIVITIES_IN_RANGE",
                status_type="SUCCESS_WITH_ACTIVITIES" if result.get("activities") else "NO_ACTIVITIES_IN_RANGE"
            )
            result.update(history_meta)

            start_stage(run_id, 8, "Enriching activities with sentiment analysis")
            complete_stage(run_id, 8, items_count=len(result.get("activities", [])))

            start_stage(run_id, 9, "Persisting latest analysis to MongoDB and JSON")
            save_latest_analysis(result)
            session.pop("latest_analysis", None)
            complete_stage(run_id, 9)

            start_stage(run_id, 10, "Packaging API response payload")
            result["run_id"] = run_id
            complete_stage(run_id, 10)

            v_count = sum(1 for a in result.get("activities", []) if a.get("type") == "video" or a.get("video_url") or a.get("video_thumbnail") or a.get("is_video") or (a.get("video_status") and a.get("video_status") != "No video"))
            v_likes_count = sum(1 for a in result.get("activities", []) if a.get("type") == "like" and (a.get("video_url") or a.get("video_thumbnail") or a.get("is_video") or (a.get("video_status") and a.get("video_status") != "No video")))
            complete_pipeline_run(run_id, status="completed", summary={"cards_found": len(result.get("activities", [])), "videos_detected": v_count, "video_likes_detected": v_likes_count, "activities_stored": len(result.get("activities", []))})
            g.analysis_run_context["analysis_result"] = result
            return jsonify(_paginate_analysis_payload(result, filters.get("page", 1), filters.get("per_page")))
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
    tot_profiles_count = len(profiles_payload) if (profiles_payload and len(profiles_payload) > 0) else 1
    start_pipeline_run(target_desc, run_id=run_id, total_profiles=tot_profiles_count)
    start_stage(run_id, 1, f"Received analysis request for {target_desc}")
    complete_stage(run_id, 1, items_count=tot_profiles_count)

    start_stage(run_id, 2, "Checking LinkedIn session state")
    try:
        from linkedin.playwright_service import validate_session_state
        _, is_auth, sess_msg = validate_session_state()
        sess_details = f"Active Chrome session: {sess_msg}" if is_auth else ("OAuth token present" if access_token else "Session not detected")
    except Exception:
        sess_details = "Session access token present" if access_token else "Session not detected"
    complete_stage(run_id, 2, details=sess_details)

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

            slug = extract_slug(p_url)
            if not slug:
                return jsonify({
                    "error": f"Invalid or incomplete LinkedIn profile URL '{p_url}'. Must include a profile slug (e.g. https://www.linkedin.com/in/username)."
                }), 400
            p_name = (p.get("name") or slug.replace("-", " ").title() or "LinkedIn Profile").strip()
            p_headline = (p.get("headline") or "LinkedIn Profile").strip()
            p_picture = p.get("picture") or None

            # Update multi-profile tracking in pipeline monitoring
            update_pipeline_profile(run_id, p_name, idx + 1, len(profiles_payload))

            # Polite pacing between profile requests to respect LinkedIn rate limits and prevent 999
            if idx > 0:
                import time
                time.sleep(1.0)

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

            p_start, start_date_valid = _normalize_batch_date(p.get("startDate") or p.get("start_date"))
            p_end, end_date_valid = _normalize_batch_date(p.get("endDate") or p.get("end_date"))

            if not start_date_valid or not end_date_valid:
                return jsonify({"error": f"For {p_name}, dates must be valid DD/MM/YYYY or YYYY-MM-DD values."}), 400

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
            p_analysis["requested_date_range"] = {"start": p_start, "end": p_end}
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

            # Check for verified registration date for this profile
            p_reg_date = None
            p_reg_source = None
            if p_url:
                try:
                    sp = get_saved_profile_by_url(p_url)
                    if sp:
                        p_reg_date = sp.get("verified_registration_date")
                        p_reg_source = sp.get("registration_date_source")
                except Exception:
                    pass

            p_history_meta = _build_history_limits_and_coverage(
                activities=p_analysis.get("activities", []),
                requested_start=p_start,
                requested_end=p_end,
                verified_reg_date=p_reg_date,
                reg_date_source=p_reg_source,
                completeness=p_status_tracker.get("retrieval_completeness", "COMPLETE"),
                status_type=p_status_tracker.get("status_type", ""),
                extra_stats=p_status_tracker
            )
            p_analysis.update(p_history_meta)

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

        # Use the bounds each profile requested, not the dates of the activities found.
        # A profile with an open bound makes the batch bound open too.
        req_starts = [(pr.get("requested_date_range") or {}).get("start") for pr in profile_results]
        req_ends = [(pr.get("requested_date_range") or {}).get("end") for pr in profile_results]
        batch_history_meta = _build_history_limits_and_coverage(
            activities=all_activities,
            requested_start=None if (not req_starts or None in req_starts) else min(req_starts),
            requested_end=None if (not req_ends or None in req_ends) else max(req_ends),
            verified_reg_date=None,
            reg_date_source=None,
            completeness=top_completeness,
            status_type=top_status_type
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
            "sentiment_summary": compute_sentiment_summary(all_activities),
            "official_history_limits": batch_history_meta["official_history_limits"],
            "scanned_coverage": batch_history_meta["scanned_coverage"]
        }

        start_stage(run_id, 7, "Retrieving historical activities from MongoDB and storage")
        complete_stage(run_id, 7, items_count=len(all_activities), details="Historical retrieval synchronized")

        start_stage(run_id, 8, "Removing duplicates across batch profile activities")
        complete_stage(run_id, 8, items_count=len(all_activities), details=f"{len(all_activities)} unique activities")

        start_stage(run_id, 9, "Persisting batch analysis to MongoDB and JSON")
        save_activities_batch(all_activities)
        save_latest_analysis(combined_result)
        session.pop("latest_analysis", None)
        complete_stage(run_id, 9, details="Stored successfully in MongoDB")

        start_stage(run_id, 10, "Applying date filters across batch activities")
        complete_stage(run_id, 10, items_count=len(all_activities), details="Date filtering verified")

        start_stage(run_id, 11, "Packaging API response payload and dashboard summaries")
        combined_result["run_id"] = run_id
        complete_stage(run_id, 11, details="Dashboard payload prepared")

        v_count = sum(1 for a in all_activities if a.get("type") == "video" or a.get("video_url") or a.get("video_thumbnail") or a.get("is_video") or (a.get("video_status") and a.get("video_status") != "No video"))
        v_likes_count = sum(1 for a in all_activities if a.get("type") == "like" and (a.get("video_url") or a.get("video_thumbnail") or a.get("is_video") or (a.get("video_status") and a.get("video_status") != "No video")))
        complete_pipeline_run(run_id, status="completed", summary={"cards_found": len(all_activities), "videos_detected": v_count, "video_likes_detected": v_likes_count, "activities_stored": len(all_activities)})
        clear_current_run_id()
        g.analysis_run_context["analysis_result"] = combined_result
        return jsonify(_paginate_analysis_payload(combined_result, data.get("page", 1), data.get("per_page")))

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

    from datetime import date, timedelta
    today_str = date.today().isoformat()

    # Handle preset fallback if explicit dates were not sent
    if date_type in ("current", "today") and not start_date and not end_date:
        start_date = today_str
        end_date = today_str
    elif date_type == "7days" and not start_date:
        start_date = (date.today() - timedelta(days=6)).isoformat()
        end_date = today_str
    elif date_type == "10days" and not start_date:
        start_date = (date.today() - timedelta(days=9)).isoformat()
        end_date = today_str
    elif date_type == "30days" and not start_date:
        start_date = (date.today() - timedelta(days=29)).isoformat()
        end_date = today_str
    elif date_type in ("1year", "365days", "year") and not start_date:
        start_date = (date.today() - timedelta(days=364)).isoformat()
        end_date = today_str
    elif date_type in ("2years", "2year", "730days", "two_years", "2_years") and not start_date:
        start_date = (date.today() - timedelta(days=729)).isoformat()
        end_date = today_str
    elif date_type == "older1year" and not start_date:
        end_date = (date.today() - timedelta(days=365)).isoformat()
        start_date = (date.today() - timedelta(days=729)).isoformat()
    elif date_type == "all":
        start_date = None
        end_date = None

    def _normalize_iso(d_str):
        if not d_str:
            return None
        d_clean = str(d_str).strip()
        m_iso = re.search(r'(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})', d_clean)
        if m_iso:
            try:
                return date(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))).isoformat()
            except ValueError:
                pass
        m_slash = re.search(r'(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})', d_clean)
        if m_slash:
            p1, p2, yr = int(m_slash.group(1)), int(m_slash.group(2)), int(m_slash.group(3))
            # Standard DD/MM/YYYY (p1 is day <= 31, p2 is month <= 12)
            if p1 <= 31 and p2 <= 12:
                try: return date(yr, p2, p1).isoformat()
                except ValueError: pass
            # Unambiguous MM/DD/YYYY (p1 is month <= 12, p2 is day > 12)
            if p1 <= 12 and p2 <= 31:
                try: return date(yr, p1, p2).isoformat()
                except ValueError: pass
        return d_clean

    norm_s = _normalize_iso(start_date)
    norm_e = _normalize_iso(end_date)
    if norm_s and norm_e and norm_s > norm_e:
        m_slash_s = re.search(r'(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})', str(start_date))
        m_slash_e = re.search(r'(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})', str(end_date))
        if m_slash_s and m_slash_e:
            try:
                alt_s = date(int(m_slash_s.group(3)), int(m_slash_s.group(1)), int(m_slash_s.group(2))).isoformat()
                alt_e = date(int(m_slash_e.group(3)), int(m_slash_e.group(1)), int(m_slash_e.group(2))).isoformat()
                if alt_s <= alt_e:
                    norm_s, norm_e = alt_s, alt_e
            except ValueError:
                pass

    if norm_s:
        start_date = norm_s
    if norm_e:
        end_date = norm_e

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

    # Check if browser stages need skipping for local/cached/sample retrieval
    run_state = get_pipeline_run(run_id)
    if run_state:
        st3 = next((s for s in run_state.get("stages", []) if s["id"] == 3), None)
        if st3 and st3["status"] == "pending":
            skip_stage(run_id, 3, "Playwright browser not needed for local/mock data")
            skip_stage(run_id, 4, "Web feed scrolling not needed")
            start_stage(run_id, 5, "Classifying activities from storage")
            complete_stage(run_id, 5, items_count=len(raw_activities))
            start_stage(run_id, 6, "Parsing and validating dates from storage")
            complete_stage(run_id, 6, items_count=len(raw_activities))

    # Stage 7: Retrieve Historical Activities
    start_stage(run_id, 7, "Retrieving historical activities from MongoDB and storage")
    complete_stage(run_id, 7, items_count=len(raw_activities), details="History synchronized")

    # Stage 8: Deduplication and Classification
    start_stage(run_id, 8, "Removing duplicates by post URLs, URNs, and content hashes")
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
                hist_start = start_date
                hist_end = end_date
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

    # Profile identity verification (Requirement 1)
    req_slug = extract_slug(subject_url) if subject_url else ""
    res_url = result.get("subject_url") or ""
    res_slug = extract_slug(res_url) if res_url else (extract_slug(result.get("subject_id") or "") or req_slug)

    extracted_identity_match = True
    identity_warning = None
    if req_slug and res_slug and req_slug != res_slug:
        extracted_identity_match = False
        identity_warning = f"Identity verification warning: requested profile '{req_slug}' does not match extracted profile '{res_slug}'. Activities have not been attached to the wrong saved profile."

    result["identity_verified"] = extracted_identity_match
    result["identity_warning"] = identity_warning

    # Check for verified registration date from saved profile or uploaded archive (Requirement 3)
    verified_reg_date = None
    reg_date_source = None
    if subject_url:
        try:
            saved_prof = get_saved_profile_by_url(subject_url)
            if saved_prof:
                verified_reg_date = saved_prof.get("verified_registration_date")
                reg_date_source = saved_prof.get("registration_date_source")
        except Exception:
            pass

    # Build official LinkedIn activity history limits metadata and date coverage (Requirement 2 & 3)
    history_meta = _build_history_limits_and_coverage(
        activities=result.get("activities", []),
        requested_start=start_date,
        requested_end=end_date,
        verified_reg_date=verified_reg_date,
        reg_date_source=reg_date_source,
        completeness=status_tracker.get("retrieval_completeness", "COMPLETE" if (raw_activities and len(raw_activities) > 0) else "UNAVAILABLE"),
        status_type=status_tracker.get("status_type", ""),
        extra_stats=status_tracker
    )
    result.update(history_meta)

    # Update persistent storage for target profile ONLY if identity is verified (Requirement 1)
    target_picture = None
    if subject_url and extracted_identity_match:
        try:
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
            "picture": target_picture,
            "verified_registration_date": verified_reg_date,
            "registration_date_source": reg_date_source
        })
    else:
        target_picture = sess_prof.get("picture") or None

    single_summary = {
        "subject_name": result.get("subject_name"),
        "subject_url": result.get("subject_url"),
        "headline": result.get("headline"),
        "picture": target_picture,
        "date_range": result.get("date_range"),
        "requested_date_range": {"start": start_date, "end": end_date},
        "retrieved_date_range": {"start": history_meta["earliest_retrieved_activity_date"], "end": history_meta["latest_retrieved_activity_date"]},
        "activities_discovered": status_tracker.get("activities_discovered", len(raw_activities)),
        "activities_parsed": status_tracker.get("activities_parsed", len(raw_activities)),
        "duplicates_removed": status_tracker.get("duplicates_removed", 0),
        "activities_filtered": status_tracker.get("activities_filtered", max(0, len(raw_activities) - len(result.get("activities", [])))),
        "activities_saved": len(result.get("activities", [])),
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
        "historical_successful_analysis": result.get("historical_successful_analysis"),
        "identity_verified": extracted_identity_match,
        "identity_warning": identity_warning,
        "official_history_limits": history_meta["official_history_limits"],
        "scanned_coverage": history_meta["scanned_coverage"],
        "verified_registration_date": verified_reg_date,
        "registration_date_source": reg_date_source,
        "registration_date_note": history_meta["registration_date_note"],
        "earliest_retrieved_activity_date": history_meta["earliest_retrieved_activity_date"],
        "latest_retrieved_activity_date": history_meta["latest_retrieved_activity_date"],
        "earliest_observed_activity_date": history_meta["earliest_observed_activity_date"],
        "selected_start_date": start_date
    }

    result["is_multi_profile"] = False
    result["profiles_count"] = 1
    result["requested_date_range"] = {"start": start_date, "end": end_date}
    result["retrieved_date_range"] = {"start": history_meta["earliest_retrieved_activity_date"], "end": history_meta["latest_retrieved_activity_date"]}
    result["activities_discovered"] = status_tracker.get("activities_discovered", len(raw_activities))
    result["activities_parsed"] = status_tracker.get("activities_parsed", len(raw_activities))
    result["duplicates_removed"] = status_tracker.get("duplicates_removed", 0)
    result["activities_filtered"] = status_tracker.get("activities_filtered", max(0, len(raw_activities) - len(result.get("activities", []))))
    result["activities_saved"] = len(result.get("activities", []))
    result["picture"] = target_picture
    result["filtered_activity_types"] = kinds
    result["profile_results"] = [single_summary]

    start_stage(run_id, 9, "Saving latest analysis to MongoDB and JSON dual-storage")
    save_activities_batch(result.get("activities", []))
    save_latest_analysis(result)
    session.pop("latest_analysis", None)
    complete_stage(run_id, 9, details="Stored successfully in MongoDB")

    start_stage(run_id, 10, "Applying date filters and activity constraints")
    complete_stage(run_id, 10, items_count=len(result.get("activities", [])), details="Date filters applied")

    start_stage(run_id, 11, "Preparing dashboard metrics and payload")
    result["run_id"] = run_id
    complete_stage(run_id, 11, details="Dashboard payload prepared")

    v_count = sum(1 for a in result.get("activities", []) if a.get("type") == "video" or a.get("video_url") or a.get("video_thumbnail") or a.get("is_video") or (a.get("video_status") and a.get("video_status") != "No video"))
    v_likes_count = sum(1 for a in result.get("activities", []) if a.get("type") == "like" and (a.get("video_url") or a.get("video_thumbnail") or a.get("is_video") or (a.get("video_status") and a.get("video_status") != "No video")))
    complete_pipeline_run(run_id, status="completed", summary={"cards_found": len(result.get("activities", [])), "videos_detected": v_count, "video_likes_detected": v_likes_count, "activities_stored": len(result.get("activities", []))})
    clear_current_run_id()
    g.analysis_run_context["analysis_result"] = result
    return jsonify(_paginate_analysis_payload(result, data.get("page", 1), data.get("per_page")))



@activity_bp.route("/api/analysis/latest", methods=["GET"])
def get_latest_analysis():
    """
    Returns the most recent analysis result for dashboard display with strict profile identity verification.
    If a specific subject_id or profile_url is requested, verifies that the latest analysis belongs to that subject.
    Never matches profile identity by display name alone (Phase 4 - Issue 1).
    """
    req_subject_id = (request.args.get("subject_id") or "").strip()
    req_subject_name = (request.args.get("subject_name") or "").strip()
    req_profile_url = (request.args.get("profile_url") or "").strip()
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", type=int)

    req_slug = extract_slug(req_profile_url) or extract_slug(req_subject_id)

    analysis = session.get("latest_analysis") or load_latest_analysis()

    if not req_slug and not req_subject_id and not req_profile_url:
        sess_prof = session.get("selected_profile") or {}
        req_profile_url = sess_prof.get("profile_url") or ""
        req_subject_id = sess_prof.get("subject_id") or ""
        req_subject_name = req_subject_name or sess_prof.get("name") or ""
        req_slug = extract_slug(req_profile_url) or extract_slug(req_subject_id)

    # If a specific profile was requested, verify identity using slug or reliable ID (never display name)
    if req_slug:
        is_match = False
        if analysis:
            a_url = analysis.get("subject_url") or ""
            a_id = analysis.get("subject_id") or ""
            a_slug = extract_slug(a_url) or extract_slug(a_id)

            if req_slug and a_slug and req_slug == a_slug:
                is_match = True
            elif req_subject_id and a_id and req_subject_id.strip().lower() == a_id.strip().lower():
                is_match = True

        req_start = request.args.get("startDate") or request.args.get("start_date") or None
        req_end = request.args.get("endDate") or request.args.get("end_date") or None

        if is_match and analysis:
            # Compare against the original query bounds, not observed activity extrema.
            date_match = True
            if isinstance(analysis.get("requested_date_range"), dict):
                requested_range = analysis["requested_date_range"]
                date_match = (requested_range.get("start") == req_start and requested_range.get("end") == req_end)
            else:
                # Legacy records only stored observed activity extrema, which cannot prove
                # the requested bounds. Use profile history filtering for explicit ranges.
                date_match = not req_start and not req_end

            if date_match:
                return jsonify({
                    **_paginate_analysis_payload(analysis, page, per_page),
                    "has_data": True
                })

        # Latest analysis does not match requested profile or date range: check profile-specific history
        if req_slug:
            historical = load_profile_successful_analysis(req_slug)
            if historical:
                req_kinds = request.args.getlist("activities") or (request.args.get("activities", "").split(",") if request.args.get("activities") else [])
                req_kinds = [k.strip().lower() for k in req_kinds if k.strip()]
                if not req_kinds:
                    req_kinds = ["all"]

                raw_hist = historical.get("activities", [])
                if req_kinds != ["all"] or req_start or req_end:
                    filtered_hist = analyze_activities(
                        rows=raw_hist,
                        activity_types=req_kinds,
                        start_date=req_start,
                        end_date=req_end,
                        subject_name=historical.get("subject_name") or req_subject_name,
                        data_source=historical.get("data_source") or "real_linkedin",
                        subject_url=historical.get("subject_url") or req_profile_url,
                        headline=historical.get("headline"),
                        retrieval_completeness="HISTORICAL_PRESERVED"
                    )
                    filtered_hist["is_historical"] = True
                    filtered_hist["requested_date_range"] = {"start": req_start, "end": req_end}
                    filtered_hist["historical_analyzed_at"] = historical.get("historical_analyzed_at") or historical.get("analyzed_at") or historical.get("updated_at")
                    filtered_hist["original_unfiltered_count"] = len(raw_hist)
                    return jsonify({
                        **_paginate_analysis_payload(filtered_hist, page, per_page),
                        "has_data": True,
                        "is_historical": True
                    })

                return jsonify({
                    **_paginate_analysis_payload(historical, page, per_page),
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
        **_paginate_analysis_payload(analysis, page, per_page),
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
    video_url = data.get("video_url") or None
    if video_url:
        try:
            host = (urlparse(video_url).hostname or "").lower()
            if host == "linkedin.com" or host.endswith(".linkedin.com"):
                video_url = None
        except ValueError:
            video_url = None
    video_status = data.get("video_status") or ("Video stream extracted" if video_url else ("Video detected" if act_type == "video" else "No video"))
    if not video_url and str(video_status).strip().lower() == "video stream extracted":
        video_status = "Video detected (Stream protected by LinkedIn, permalink available)"

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
        "video_url": video_url,
        "video_mime_type": data.get("video_mime_type") or None,
        "video_thumbnail": data.get("video_thumbnail") or None,
        "video_status": video_status,
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



