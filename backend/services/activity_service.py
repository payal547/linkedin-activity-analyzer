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
    status_out: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """
    Retrieves activities for a chosen profile or connection.
    - Checks user-imported offline data (from LinkedIn CSV/ZIP exports).
    - Checks user-saved activities in persistent storage.
    - Checks live LinkedIn API data (if legitimate r_member_social permissions are present).
    - Uses Playwright to open the target profile and extract real post content and metrics.
    - Strictly returns only real data or empty list for real profiles.
    - Returns sample data ONLY when explicitly requested as a demo/sample.
    """
    if status_out is None:
        status_out = {}

    # 1. Check for user-imported data in session (offline CSV or ZIP export)
    if session_activities:
        s_name = (subject_name or "").strip().lower()
        s_id = (subject_id or "").strip().lower()
        s_url = (subject_url or "").strip().lower()
        matching = []
        for a in session_activities:
            a_name = (a.get("subject_name") or "").strip().lower()
            a_id = (a.get("subject_id") or "").strip().lower()
            a_url = (a.get("subject_url") or "").strip().lower()
            if s_url and a_url and (s_url in a_url or a_url in s_url):
                matching.append(a)
            elif s_id and a_id and s_id == a_id:
                matching.append(a)
            elif s_name and a_name and s_name == a_name:
                matching.append(a)
            elif not a_name and not a_id and not a_url:
                matching.append(a)
        if matching:
            status_out["status"] = f"Loaded {len(matching)} real activities from uploaded LinkedIn archive."
            return matching

    # 2. Check for user-saved activities in persistent storage
    saved = load_saved_activities(subject_name=subject_name, subject_id=subject_id, subject_url=subject_url)
    if saved:
        status_out["status"] = f"Loaded {len(saved)} real activities from verified storage."
        return saved

    # 3. Check for live LinkedIn API data
    if data_source == "real_linkedin" and access_token:
        success, live_activities, api_msg = fetch_linkedin_activities(access_token)
        if success and live_activities:
            status_out["status"] = "Loaded from LinkedIn API."
            return live_activities

    # 4. Attempt real retrieval via Playwright for target profile URL
    # Skip automatic live browser launch during automated unit test runs unless testing Playwright directly
    is_testing = False
    try:
        if current_app and current_app.config.get("TESTING"):
            is_testing = True
    except Exception:
        pass

    if subject_url and not is_testing and data_source != "sample":
        try:
            from linkedin.playwright_service import scrape_linkedin_profile_activities
            pw_success, pw_activities, pw_msg = scrape_linkedin_profile_activities(
                profile_url=subject_url,
                target_name=subject_name,
                start_date=start_date,
                end_date=end_date,
                activity_types=activity_types
            )
            status_out["status"] = pw_msg
            if pw_success and pw_activities:
                return pw_activities
            if pw_success and not pw_activities:
                status_out["empty_message"] = "No activities found for this date range"
                return []
        except Exception as e:
            status_out["status"] = f"Actual post content could not be retrieved from LinkedIn. ({str(e)})"

    # 5. If sample/demo explicitly requested
    if data_source == "sample":
        status_out["status"] = "Sample demonstration dataset."
        return _generate_sample_activities(subject_name, subject_url)

    # 6. For real profiles with no activity data legitimately accessible, return empty list
    if "status" not in status_out or not status_out["status"]:
        status_out["status"] = "Actual post content could not be retrieved from LinkedIn."
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
            "post_content": f"Excited to share our latest project progress and insights on LinkedIn! We are building scalable systems and sharing key technical takeaways.",
            "post_author": subject_name,
            "post_url": subject_url or f"https://www.linkedin.com/posts/activity-{today.strftime('%Y%m%d')}",
            "media_url": None,
            "is_own_post": True,
            "action_type": "post",
            "subject_name": subject_name
        },
        {
            "id": "act_2",
            "date": (today - timedelta(days=2)).isoformat(),
            "type": "comment",
            "text": "Great perspective on engineering best practices and scalable architecture.",
            "likes": 12,
            "comments": 2,
            "shares": 0,
            "source": "sample",
            "post_content": "Deep dive into distributed systems design and reliable microservice communication.",
            "post_author": "Engineering Director",
            "post_url": "https://www.linkedin.com/posts/engineering-distributed-systems",
            "comment_text": "Great perspective on engineering best practices and scalable architecture. Completely agree on resilience patterns.",
            "is_own_post": False,
            "action_type": "comment",
            "subject_name": subject_name
        },
        {
            "id": "act_3",
            "date": (today - timedelta(days=5)).isoformat(),
            "type": "like",
            "text": "Liked LinkedIn post on modern cloud infrastructure.",
            "likes": 48,
            "comments": 9,
            "shares": 6,
            "source": "sample",
            "post_content": "Announcing our new open-source performance benchmarking suite for web applications.",
            "post_author": "Open Source Foundation",
            "post_url": "https://www.linkedin.com/posts/open-source-benchmark",
            "reaction_type": "Like",
            "is_own_post": False,
            "action_type": "like",
            "subject_name": subject_name
        },
        {
            "id": "act_4",
            "date": (today - timedelta(days=10)).isoformat(),
            "type": "video",
            "text": "Walkthrough of performance optimization and cloud deployments.",
            "likes": 75,
            "comments": 14,
            "shares": 8,
            "source": "sample",
            "post_content": "Full video tutorial: End-to-end continuous deployment pipelines with automated regression testing.",
            "post_author": subject_name,
            "post_url": subject_url or "https://www.linkedin.com/posts/video-deployment-tutorial",
            "media_url": "https://images.unsplash.com/photo-1516321318423-f06f85e504b3?w=800",
            "is_own_post": True,
            "action_type": "video",
            "subject_name": subject_name
        }
    ]
    return sample_items

