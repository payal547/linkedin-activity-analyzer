from datetime import date, timedelta
from typing import List, Dict, Any, Optional
from flask import current_app
from linkedin.activities import fetch_linkedin_activities
from services.activities_storage import load_saved_activities


def get_activities_for_subject(
    subject_id: str,
    subject_name: str,
    data_source: str,
    access_token: Optional[str] = None,
    session_activities: Optional[List[Dict[str, Any]]] = None,
    subject_url: Optional[str] = None,
    allow_sample_fallback: bool = False,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    activity_types: Optional[List[str]] = None,
    status_out: Optional[Dict[str, Any]] = None,
    target_photo: Optional[str] = None,
    target_headline: Optional[str] = None,
    max_activities: Optional[int] = None,
    max_scrolls: Optional[int] = None,
    run_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Retrieves activities for a chosen profile or connection.
    - Checks user-imported offline data (from LinkedIn CSV/ZIP exports).
    - Checks live LinkedIn API data (if legitimate r_member_social permissions are present).
    - Uses Playwright to open the target profile and extract real post content and metrics.
    - Strictly returns only real data or empty list for real profiles.
    - NEVER returns sample/demo data unless explicitly requested (data_source == 'sample').
    - If real activity cannot be retrieved, returns [] with an honest explanatory status.
    """
    if status_out is None:
        status_out = {}

    # 1. Check for user-imported data in session (offline CSV or ZIP export).
    # Imported rows are matched by canonical profile slug only. Rows with no
    # profile identity belong to the uploader's own export, so they are used
    # only for a "self" analysis and never attributed to another profile.
    if session_activities:
        try:
            from services.saved_profiles_storage import extract_slug as _slug_of
        except ImportError:
            from backend.services.saved_profiles_storage import extract_slug as _slug_of
        s_id = (subject_id or "").strip().lower()
        s_slug = _slug_of(subject_url or "") or ("" if s_id in ("", "self") else _slug_of(s_id))
        is_self_request = not s_slug and s_id in ("", "self")
        matching = []
        for a in session_activities:
            a_id = (a.get("subject_id") or "").strip().lower()
            a_slug = _slug_of(a.get("subject_url") or "") or ("" if a_id in ("", "self") else _slug_of(a_id))
            if s_slug and a_slug:
                if s_slug == a_slug:
                    matching.append(a)
            elif is_self_request and not a_slug:
                matching.append(a)
        if matching:
            status_out["status"] = f"Loaded {len(matching)} real activities from uploaded LinkedIn archive."
            status_out["status_type"] = "SUCCESS_WITH_ACTIVITIES"
            status_out["retrieval_completeness"] = "COMPLETE"
            return matching

    # Check if rate-limit cooldown is active for this profile or globally
    try:
        try:
            from linkedin.playwright_service import is_rate_limited
        except ImportError:
            from backend.linkedin.playwright_service import is_rate_limited
        try:
            from services.saved_profiles_storage import extract_slug
        except ImportError:
            from backend.services.saved_profiles_storage import extract_slug
        c_slug = extract_slug(subject_url or subject_id or "")
        if c_slug and is_rate_limited(c_slug):
            status_out["status"] = "LinkedIn is temporarily limiting automated requests. Please try again later. Your previously saved historical analysis is preserved."
            status_out["status_type"] = "RATE_LIMITED_999"
            status_out["retrieval_completeness"] = "UNAVAILABLE"
            status_out["empty_message"] = "LinkedIn is temporarily limiting automated requests. Please try again later."
            return []
    except Exception:
        pass

    # 2. Check if running under automated unit testing framework
    is_testing = False
    try:
        if current_app and current_app.config.get("TESTING"):
            is_testing = True
    except Exception:
        pass

    if is_testing:
        saved = load_saved_activities(subject_name=subject_name, subject_id=subject_id, subject_url=subject_url)
        if saved:
            status_out["status"] = f"Loaded {len(saved)} activities from verified storage."
            status_out["status_type"] = "SUCCESS_WITH_ACTIVITIES"
            status_out["retrieval_completeness"] = "COMPLETE"
            return saved

    # 3. Check for live LinkedIn API data
    if data_source == "real_linkedin" and access_token:
        success, live_activities, api_msg = fetch_linkedin_activities(access_token)
        if success and live_activities:
            status_out["status"] = "Loaded from LinkedIn API."
            status_out["status_type"] = "SUCCESS_WITH_ACTIVITIES"
            status_out["retrieval_completeness"] = "COMPLETE"
            return live_activities

    # 4. Attempt real retrieval via Playwright for target profile URL
    if subject_url and not is_testing and data_source != "sample":
        try:
            from linkedin.playwright_service import scrape_linkedin_profile_activities
            scrape_res = scrape_linkedin_profile_activities(
                profile_url=subject_url,
                target_name=subject_name,
                start_date=start_date,
                end_date=end_date,
                activity_types=activity_types,
                target_photo=target_photo,
                target_headline=target_headline,
                status_out=status_out,
                max_activities=max_activities,
                max_scrolls=max_scrolls,
                run_id=run_id
            )
            pw_success, pw_activities, pw_msg = scrape_res[0], scrape_res[1], scrape_res[2]
            pw_status_type = getattr(scrape_res, "status_type", "") or status_out.get("status_type", "")
            pw_completeness = getattr(scrape_res, "retrieval_completeness", "") or status_out.get("retrieval_completeness", "COMPLETE")
            status_out["status"] = pw_msg
            status_out["status_type"] = pw_status_type
            status_out["retrieval_completeness"] = pw_completeness
            if pw_success and pw_activities:
                return pw_activities
            if pw_success and not pw_activities:
                status_out["empty_message"] = pw_msg or "No activities found for this date range."
                return []
            if not pw_success:
                status_out["empty_message"] = pw_msg or "Activity could not be retrieved from LinkedIn."
                return []
        except Exception as e:
            status_out["status"] = f"Activity could not be retrieved from LinkedIn. ({str(e)})"
            status_out["status_type"] = "AUTOMATION_ERROR"
            status_out["empty_message"] = "Activity could not be retrieved from LinkedIn."
            status_out["retrieval_completeness"] = "PARTIAL_CEILING_REACHED"
            return []

    # 5. If sample/demo explicitly requested
    if data_source == "sample":
        status_out["status"] = "Sample demonstration dataset."
        status_out["status_type"] = "SAMPLE_DEMO"
        status_out["retrieval_completeness"] = "COMPLETE"
        return _generate_sample_activities(subject_name, subject_url)

    # 6. For real profiles with no activity data legitimately accessible, return empty list honestly
    if "status" not in status_out or not status_out["status"]:
        status_out["status"] = "Activity could not be retrieved from LinkedIn."
    if "status_type" not in status_out or not status_out["status_type"]:
        status_out["status_type"] = "ACTIVITY_SECTION_UNAVAILABLE"
    if "empty_message" not in status_out or not status_out["empty_message"]:
        status_out["empty_message"] = status_out["status"]
    return []


def _generate_sample_activities(subject_name: str, subject_url: Optional[str] = None) -> List[Dict[str, Any]]:
    today = date.today()
    sample_items = [
        {
            "id": "act_today",
            "date": today.isoformat(),
            "type": "post",
            "text": f"Excited to share our latest project progress and insights on LinkedIn! - {subject_name}",
            "likes": 24,
            "comments": 5,
            "shares": 2,
            "source": "sample",
            "post_content": f"Excited to share our latest project progress and insights on LinkedIn! - {subject_name}",
            "post_author": subject_name,
            "post_url": subject_url or "https://www.linkedin.com/",
            "media_url": None,
            "comment_text": None,
            "reaction_type": None,
            "is_own_post": True,
            "action_type": "post",
            "subject_name": subject_name,
            "subject_url": subject_url,
            "actor": subject_name,
            "original_post_author": subject_name,
            "activity_type": "post",
            "activity_date": today.isoformat(),
            "activity_text": f"Excited to share our latest project progress and insights on LinkedIn! - {subject_name}",
            "original_post_text": f"Excited to share our latest project progress and insights on LinkedIn! - {subject_name}",
            "original_post_url": subject_url or "https://www.linkedin.com/"
        }
    ]
    return sample_items
