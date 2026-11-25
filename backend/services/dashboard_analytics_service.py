"""Read-only aggregation of stored records for analytics dashboards."""

import hashlib
import json
import logging
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from db.mongo import get_db, sanitize_doc
from services.video_embeds_storage import canonical_linkedin_post_url, load_video_embed_map

logger = logging.getLogger(__name__)


def _slug(value: Any) -> str:
    text = str(value or "").strip()
    match = re.search(r"linkedin\.com/in/([^/?#]+)", text, re.I)
    if match:
        text = match.group(1)
    return re.sub(r"[^a-z0-9-]", "", text.strip("/ ").lower())


def _date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _event_key(activity: Dict[str, Any], profile_slug: str) -> str:
    activity_id = str(activity.get("id") or "").strip()
    kind = str(activity.get("action_type") or activity.get("type") or activity.get("activity_type") or "unknown").lower()
    # LinkedIn URNs are durable event identifiers. Generated positional IDs
    # are not stable between extraction runs, so deduplicate on event context.
    if re.search(r"urn:li:(?:activity|share|ugcpost):", activity_id, re.I):
        identity = f"{profile_slug}|{activity_id}"
    else:
        event_date = str(activity.get("date") or activity.get("activity_date") or "")
        event_url = str(activity.get("post_url") or activity.get("original_post_url") or "")
        event_text = re.sub(r"\s+", " ", str(activity.get("text") or activity.get("activity_text") or activity.get("comment_text") or "").strip().lower())[:300]
        # Date-only context is not a unique event key: people can like or comment
        # on multiple posts on the same day. Retain such rows by their stored ID.
        if not event_url and not event_text:
            identity = f"{profile_slug}|{kind}|{event_date}|{activity_id}"
        else:
            identity = "|".join((profile_slug, kind, event_date, event_url, event_text))
    return hashlib.sha256(identity.encode("utf-8", "replace")).hexdigest()


_ACTIVITY_KIND_ALIASES = {
    "post": "post", "posts": "post", "update": "post", "updates": "post",
    "video": "video", "videos": "video",
    "comment": "comment", "comments": "comment",
    "like": "like", "likes": "like", "reaction": "like", "reactions": "like",
    "reactions/like": "like", "reactions/likes": "like",
    "share": "share", "shares": "share", "repost": "share", "reposts": "share",
}


def _activity_type(row: Dict[str, Any]) -> str:
    """Return the event/action category, independently from attached media."""
    for field in ("action_type", "activity_type", "type"):
        value = re.sub(r"\s+", " ", str(row.get(field) or "").strip().lower())
        if value in _ACTIVITY_KIND_ALIASES:
            return _ACTIVITY_KIND_ALIASES[value]
    if row.get("reaction_type"):
        return "like"
    if row.get("comment_text"):
        return "comment"
    if _is_video_metadata(row):
        return "video"
    if row.get("is_own_post") is True and (row.get("post_content") or row.get("post_url")):
        return "post"
    return "other"


def _is_static_image_url(url: str) -> bool:
    clean = (url or "").strip().lower().split("?")[0]
    return any(clean.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".bmp")) or "dms/image" in clean or "feedshare-image" in clean or "feedshare-shrink" in clean


def _is_video_metadata(row: Dict[str, Any]) -> bool:
    v_url = row.get("video_stream_url") or row.get("video_url")
    if v_url and not _is_static_image_url(str(v_url)):
        return True
    if row.get("embed_src"):
        return True
    m_type = str(row.get("media_type") or row.get("video_mime_type") or "").strip().lower()
    if m_type.startswith("video"):
        return True
    has_video_action = any(
        _ACTIVITY_KIND_ALIASES.get(str(row.get(field) or "").strip().lower()) == "video"
        for field in ("action_type", "activity_type", "type")
    )
    if has_video_action:
        return True
    p_url = str(row.get("post_url") or row.get("original_post_url") or row.get("linkedin_post_url") or "").lower()
    if any(vp in p_url for vp in ("/videos/", "urn:li:video:", "youtube.com", "youtu.be", "vimeo.com")):
        return True
    med_url = str(row.get("media_url") or "").lower()
    if any(ext in med_url for ext in (".mp4", ".mov", ".webm", ".m4v", "videocdn", "/video/", "dms/video/")):
        return True

    explicit_video_flag = row.get("is_video")
    if isinstance(explicit_video_flag, str):
        explicit_video_flag = explicit_video_flag.strip().lower() in {"1", "true", "yes", "y"}
    status = str(row.get("video_status") or "").strip().lower()
    has_pos_status = bool(status and status != "no video" and status.startswith(("video stream", "external video", "video detected", "video found")))
    if explicit_video_flag or has_pos_status:
        return True
    return False


def _is_video(row: Dict[str, Any]) -> bool:
    return _is_video_metadata(row)


def _has_accessible_image(row: Dict[str, Any]) -> bool:
    for field in ("image_url", "photo_url", "media_url"):
        val = row.get(field)
        if isinstance(val, str) and val.strip().startswith(("http://", "https://")):
            val_clean = val.strip().lower().split("?")[0]
            if not any(val_clean.endswith(ext) for ext in (".mp4", ".mov", ".webm", ".m4v", ".ogg")):
                return True
    for field in ("images", "media_urls"):
        items = row.get(field)
        if isinstance(items, list) and items:
            for item in items:
                url = item if isinstance(item, str) else (item.get("url") or item.get("src") or item.get("media_url") if isinstance(item, dict) else "")
                if url and str(url).strip().startswith(("http://", "https://")):
                    url_clean = str(url).strip().lower().split("?")[0]
                    if not any(url_clean.endswith(ext) for ext in (".mp4", ".mov", ".webm", ".m4v", ".ogg")):
                        return True
    return False


def _is_photo(row: Dict[str, Any]) -> bool:
    """Photo is a media facet; video activities are excluded even with a poster."""
    if _is_video(row):
        return False
    media_type = str(row.get("media_type") or "").strip().lower()
    if media_type in ("image", "photo", "photos", "picture"):
        return True
    return _has_accessible_image(row)


def _records() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    profiles: List[Dict[str, Any]] = []
    histories: List[Dict[str, Any]] = []
    runs: List[Dict[str, Any]] = []
    activities: List[Dict[str, Any]] = []
    imports: List[Dict[str, Any]] = []
    mongo_ok = False

    try:
        db = get_db()
        profiles = list(db["saved_profiles"].find({}, {"_id": 0, "id": 1, "profile_url": 1, "name": 1, "headline": 1, "picture": 1}))
        activity_projection = {
            "id": 1, "type": 1, "action_type": 1, "activity_type": 1, "date": 1, "activity_date": 1,
            "text": 1, "activity_text": 1, "comment_text": 1, "post_content": 1, "original_post_text": 1,
            "post_author": 1, "original_post_author": 1, "post_author_photo": 1, "post_author_headline": 1,
            "post_url": 1, "original_post_url": 1, "linkedin_post_url": 1, "embed_src": 1,
            "video_stream_url": 1, "subject_id": 1, "subject_url": 1, "subject_name": 1,
            "actor": 1, "is_own_post": 1, "reaction_type": 1, "likes": 1, "comments": 1, "shares": 1,
            "is_video": 1, "video_url": 1, "video_thumbnail": 1, "video_status": 1, "video_mime_type": 1,
            "media_type": 1, "media_url": 1, "media_urls": 1, "image_url": 1, "photo_url": 1, "images": 1,
            "source": 1, "storage_origin": 1,
        }
        history_fields = {"_id": 0, "slug": 1, "subject_id": 1, "subject_url": 1, "subject_name": 1, "historical_analyzed_at": 1, "retrieval_completeness": 1, "date_range": 1, "total_activities": 1, "posts_count": 1, "videos_count": 1}
        history_fields.update({f"activities.{field}": value for field, value in activity_projection.items()})
        histories = list(db["profile_analysis_history"].find({}, history_fields))
        runs = list(db["analysis_runs"].find({}, {"_id": 1, "profile_slugs": 1, "completed_at": 1, "started_at": 1, "status": 1, "requested_date_range": 1, "retrieval_completeness": 1, "total_activities": 1, "posts_count": 1, "videos_count": 1, "by_type": 1, "profile_summaries": 1}).sort("completed_at", -1))
        activity_fields = {"_id": 0, **activity_projection}
        activities = list(db["activities"].find({}, activity_fields))
        import_fields = {"_id": 0, "import_id": 1, "created_at": 1}
        import_fields.update({f"activities.{field}": value for field, value in activity_projection.items()})
        imports = list(db["imported_activities"].find({}, import_fields))
        mongo_ok = True
    except Exception as e:
        logger.warning("MongoDB _records query failed (%s); using local JSON fallback.", e)

    # Fallback to local JSON files if MongoDB is unavailable or returned 0 records while JSON exists
    if not mongo_ok or (not profiles and not histories):
        data_dir = Path(__file__).resolve().parent.parent / "data"
        prof_file = data_dir / "saved_target_profiles.json"
        hist_file = data_dir / "profile_analysis_history.json"
        act_file = data_dir / "activities.json"

        if (not profiles or not mongo_ok) and prof_file.exists():
            try:
                with open(prof_file, "r", encoding="utf-8") as f:
                    p_data = json.load(f)
                    if isinstance(p_data, list):
                        profiles = p_data
            except Exception as e:
                logger.warning("Failed loading JSON saved_target_profiles: %s", e)

        if (not histories or not mongo_ok) and hist_file.exists():
            try:
                with open(hist_file, "r", encoding="utf-8") as f:
                    h_data = json.load(f)
                    if isinstance(h_data, dict):
                        histories = [{"slug": slug, **val} for slug, val in h_data.items() if isinstance(val, dict)]
                    elif isinstance(h_data, list):
                        histories = h_data
            except Exception as e:
                logger.warning("Failed loading JSON profile_analysis_history: %s", e)

        if (not activities or not mongo_ok) and act_file.exists():
            try:
                with open(act_file, "r", encoding="utf-8") as f:
                    a_data = json.load(f)
                    if isinstance(a_data, list):
                        activities = a_data
            except Exception as e:
                logger.warning("Failed loading JSON activities: %s", e)

    return tuple(sanitize_doc(items) for items in (profiles, histories, runs, activities, imports))


def _profile_slug(profile: Dict[str, Any]) -> str:
    return _slug(profile.get("id") or profile.get("slug") or profile.get("profile_url") or profile.get("subject_url") or profile.get("subject_id"))


def _collect_content(histories: Iterable[Dict[str, Any]], activities: Iterable[Dict[str, Any]], imports: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    unique: Dict[str, Dict[str, Any]] = {}

    def add(row: Dict[str, Any], fallback_slug: str = "") -> None:
        # A history document is already scoped to its canonical profile. Keep
        # embedded rows within that scope even if an old row has a conflicting
        # or stale subject field.
        slug = _slug(fallback_slug) if fallback_slug else _slug(row.get("subject_id") or row.get("subject_url"))
        item = dict(row)
        item["profile_slug"] = slug or None
        item["is_video"] = _is_video(item)
        item["type"] = _activity_type(item)
        raw_date = item.get("date") or item.get("activity_date")
        item["date"] = raw_date.isoformat() if isinstance(raw_date, (date, datetime)) else (str(raw_date).strip() or None)
        key = _event_key(item, slug or "unassigned")
        if key not in unique:
            unique[key] = item

    for history in histories:
        slug = _profile_slug(history)
        rows = history.get("activities")
        if slug and isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict):
                    add(row, slug)
    for row in activities:
        if isinstance(row, dict):
            add(row)
    for imported in imports:
        for row in imported.get("activities", []) if isinstance(imported.get("activities"), list) else []:
            if isinstance(row, dict):
                row = dict(row)
                row.setdefault("source", "imported")
                add(row)
    return list(unique.values())


def _profile_summaries(profiles: List[Dict[str, Any]], histories: List[Dict[str, Any]], runs: List[Dict[str, Any]], content: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    histories_by_slug = {_profile_slug(h): h for h in histories if _profile_slug(h)}
    profile_content: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    run_counts: Counter = Counter()
    last_runs: Dict[str, str] = {}
    for item in content or []:
        if item.get("profile_slug"):
            profile_content[item["profile_slug"]].append(item)
    for run in runs:
        when = str(run.get("completed_at") or run.get("started_at") or "")
        for slug in run.get("profile_slugs") or []:
            clean = _slug(slug)
            if clean:
                run_counts[clean] += 1
                if clean not in last_runs or when > last_runs[clean]:
                    last_runs[clean] = when

    result = []
    for profile in profiles:
        slug = _profile_slug(profile)
        history = histories_by_slug.get(slug)
        rows = profile_content.get(slug, []) if content is not None else _collect_content([history], [], []) if history else []
        history_rows_known = isinstance(history.get("activities"), list) if history else False
        last_date = history.get("historical_analyzed_at") if history else None
        result.append({
            "id": slug,
            "profile_url": profile.get("profile_url"),
            "name": profile.get("name") or slug,
            "headline": profile.get("headline"),
            "picture": profile.get("picture"),
            "has_stored_successful_history": bool(history),
            "stored_history_updated_at": last_date,
            "analysis_runs_since_tracking": run_counts.get(slug, 0),
            "last_recorded_run_at": last_runs.get(slug),
            "stored_activity_count": len(rows) if history_rows_known else None,
            "post_count": sum(1 for row in rows if _activity_type(row) == "post" and not row.get("is_video")) if history_rows_known else None,
            "video_count": sum(1 for row in rows if row.get("is_video")) if history_rows_known else None,
            "retrieval_completeness": history.get("retrieval_completeness") if history else None,
            "observed_date_range": history.get("date_range") if history else None,
        })
    return result


def _month_starts(count: int) -> List[date]:
    today = date.today()
    first = today.replace(day=1)
    result = []
    year, month = first.year, first.month
    for _ in range(count):
        result.append(date(year, month, 1))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(result))


def _trends(content: List[Dict[str, Any]], runs: List[Dict[str, Any]], count: int = 12) -> List[Dict[str, Any]]:
    count = min(36, max(1, int(count)))
    starts = _month_starts(count)
    activity_counts: Counter = Counter()
    post_counts: Counter = Counter()
    video_counts: Counter = Counter()
    run_counts: Counter = Counter()
    for row in content:
        row_date = _date(row.get("date"))
        if not row_date:
            continue
        key = f"{row_date.year:04d}-{row_date.month:02d}"
        activity_counts[key] += 1
        if _activity_type(row) == "post" and not row.get("is_video"):
            post_counts[key] += 1
        if row.get("is_video"):
            video_counts[key] += 1
    for run in runs:
        run_date = _date(run.get("completed_at") or run.get("started_at"))
        if run_date:
            run_counts[f"{run_date.year:04d}-{run_date.month:02d}"] += 1

    data = []
    for start in starts:
        key = f"{start.year:04d}-{start.month:02d}"
        observed = activity_counts[key] > 0
        data.append({
            "month": key,
            "activity_count": activity_counts[key] if observed else None,
            "post_count": post_counts[key] if observed else None,
            "video_count": video_counts[key] if observed else None,
            "analysis_run_count": run_counts[key] if run_counts[key] > 0 else None,
            "activity_coverage": "observed" if observed else "unknown",
        })
    return data


def _insights(profiles: List[Dict[str, Any]], content: List[Dict[str, Any]], trends: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    insights: List[Dict[str, str]] = []
    if content:
        types = Counter(_activity_type(row) for row in content)
        top_type, top_count = types.most_common(1)[0]
        insights.append({"kind": "activity_mix", "label": f"Most common stored activity: {top_type}", "detail": f"{top_count} matching records in the stored dataset."})
        observed_months = [m for m in trends if m["activity_coverage"] == "observed"]
        if observed_months:
            peak = max(observed_months, key=lambda m: m["activity_count"])
            insights.append({"kind": "peak_month", "label": f"Most records observed in {peak['month']}", "detail": f"{peak['activity_count']} dated activity records are stored for that month."})
        ordered_months = sorted(observed_months, key=lambda month: month["month"])
        if len(ordered_months) > 1:
            current, previous = ordered_months[-1], ordered_months[-2]
            cy, cm = map(int, current["month"].split("-"))
            py, pm = map(int, previous["month"].split("-"))
            if cy * 12 + cm - (py * 12 + pm) == 1:
                change = int(current["activity_count"]) - int(previous["activity_count"])
                insights.append({"kind": "month_comparison", "label": f"Stored records: {change:+d} month to month", "detail": f"{current['month']} compared with {previous['month']}; this compares stored records and does not imply complete month coverage."})
    stored_profiles = sum(bool(p.get("has_stored_successful_history")) for p in profiles)
    if profiles:
        insights.append({"kind": "profile_coverage", "label": "Stored profile coverage", "detail": f"{stored_profiles} of {len(profiles)} saved profiles have a preserved successful activity snapshot."})
    return insights


def get_overview() -> Dict[str, Any]:
    profiles, histories, runs, activities, imports = _records()
    content = _collect_content(histories, activities, imports)
    profile_rows = _profile_summaries(profiles, histories, runs, content)
    trends = _trends(content, runs, 12)
    observed_dates = [_date(row.get("date")) for row in content]
    observed_dates = [d for d in observed_dates if d]
    return {
        "source": "mongodb",
        "saved_profile_count": len(profiles),
        "profiles_with_stored_successful_history": sum(bool(p.get("has_stored_successful_history")) for p in profile_rows),
        "analysis_run_count_since_tracking": len(runs),
        "activity_record_count": len(content),
        "post_count": sum(1 for row in content if _activity_type(row) == "post" and not row.get("is_video")),
        "video_count": sum(1 for row in content if row.get("is_video")),
        "observed_activity_date_range": {"start": min(observed_dates).isoformat(), "end": max(observed_dates).isoformat()} if observed_dates else None,
        "analysis_runs_tracking_started_at": min((str(r.get("completed_at") or r.get("started_at")) for r in runs if r.get("completed_at") or r.get("started_at")), default=None),
        "coverage_note": "Run counts include only runs recorded since durable tracking was enabled. Months without dated stored activity are unknown, not zero.",
        "trends": trends,
        "insights": _insights(profile_rows, content, trends),
    }


def list_profiles(search: str = "", sort: str = "name", order: str = "asc", page: int = 1, per_page: int = 20) -> Dict[str, Any]:
    profiles, histories, runs, activities, imports = _records()
    rows = _profile_summaries(profiles, histories, runs, None)
    query = search.strip().lower()
    if query:
        rows = [row for row in rows if query in str(row.get("name") or "").lower() or query in str(row.get("id") or "").lower()]
    key_map = {"activity": "stored_activity_count", "posts": "post_count", "videos": "video_count", "runs": "analysis_runs_since_tracking", "recent": "last_recorded_run_at"}
    key = key_map.get(sort, "name")
    rows.sort(key=lambda row: (row.get(key) is None, str(row.get(key) or "").lower() if key in ("name", "recent") else (row.get(key) or 0)), reverse=order.lower() == "desc")
    page = max(1, int(page))
    per_page = min(500, max(1, int(per_page)))
    total = len(rows)
    return {"items": rows[(page - 1) * per_page:page * per_page], "page": page, "per_page": per_page, "total_items": total, "total_pages": max(1, (total + per_page - 1) // per_page)}


def list_profile_options() -> List[Dict[str, Any]]:
    """Small profile metadata payload for filters; does not load activity history."""
    try:
        rows = list(get_db()["saved_profiles"].find({}, {"_id": 0, "id": 1, "profile_url": 1, "name": 1}).sort("name", 1))
        if rows:
            return [{"id": _profile_slug(row), "name": row.get("name") or _profile_slug(row), "profile_url": row.get("profile_url")} for row in rows]
    except Exception as e:
        logger.warning("MongoDB list_profile_options failed (%s); using fallback.", e)

    profiles, histories, _runs, _activities, _imports = _records()
    seen: set = set()
    result = []
    for profile in profiles:
        slug = _profile_slug(profile)
        if slug and slug not in seen:
            seen.add(slug)
            result.append({"id": slug, "name": profile.get("name") or slug, "profile_url": profile.get("profile_url")})
    for history in histories:
        slug = _profile_slug(history)
        if slug and slug not in seen:
            seen.add(slug)
            result.append({"id": slug, "name": history.get("subject_name") or slug, "profile_url": history.get("subject_url")})
    result.sort(key=lambda row: str(row.get("name") or "").lower())
    return result


def list_runs(profile_slug: str = "", page: int = 1, per_page: int = 20) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    total = 0
    query = {"profile_slugs": _slug(profile_slug)} if profile_slug else {}
    page = max(1, int(page))
    per_page = min(100, max(1, int(per_page)))
    try:
        db = get_db()
        collection = db["analysis_runs"]
        total = collection.count_documents(query)
        cursor = collection.find(query, {"_id": 1, "profile_slugs": 1, "profile_summaries": 1, "started_at": 1, "completed_at": 1, "status": 1, "requested_date_range": 1, "requested_activity_types": 1, "retrieval_completeness": 1, "total_activities": 1, "posts_count": 1, "videos_count": 1, "by_type": 1, "data_source": 1}).sort("completed_at", -1).skip((page - 1) * per_page).limit(per_page)
        rows = [sanitize_doc(item) for item in cursor]
    except Exception as e:
        logger.warning("MongoDB list_runs failed (%s); returning empty runs list.", e)
        total = 0
        rows = []

    for row in rows:
        row["run_id"] = row.pop("_id", "")
        if profile_slug:
            profile_summary = next((item for item in row.get("profile_summaries", []) if _slug(item.get("profile_slug")) == _slug(profile_slug)), None)
            if profile_summary:
                row.update({key: profile_summary.get(key) for key in ("total_activities", "posts_count", "videos_count", "by_type", "retrieval_completeness")})
                row["profile_requested_date_range"] = profile_summary.get("requested_date_range")
                row["profile_requested_activity_types"] = profile_summary.get("requested_activity_types")
        row.pop("profile_summaries", None)
    return {"items": rows, "page": page, "per_page": per_page, "total_items": total, "total_pages": max(1, (total + per_page - 1) // per_page)}


def get_profile(slug: str) -> Optional[Dict[str, Any]]:
    clean = _slug(slug)
    if not clean:
        return None
    profiles, histories, _runs, _activities, _imports = _records()
    profile = next((p for p in profiles if _profile_slug(p) == clean), None)
    history = next((h for h in histories if _profile_slug(h) == clean or _slug(h.get("slug")) == clean), None)
    if not profile and not history:
        return None
    if profile is None:
        profile = {"id": clean, "profile_url": (history or {}).get("subject_url"), "name": (history or {}).get("subject_name") or clean}
    profile = sanitize_doc(profile)
    history = sanitize_doc(history) if history else None
    return {"profile": profile, "history": history}


def list_content(profile_slug: str = "", kind: str = "", search: str = "", start_date: str = "", end_date: str = "", sort: str = "newest", page: int = 1, per_page: int = 20) -> Dict[str, Any]:
    profiles, histories, _runs, activities, imports = _records()
    rows = _collect_content(histories, activities, imports)
    try:
        embed_map = load_video_embed_map(get_db()["activity_video_embeds"])
    except Exception:
        embed_map = {}
    slug = _slug(profile_slug)
    normalized_kind = kind.strip().lower()
    if normalized_kind in ("photos", "image", "images", "picture"):
        normalized_kind = "photo"
    elif normalized_kind in ("likes", "reaction", "reactions"):
        normalized_kind = "like"
    elif normalized_kind in ("comments",):
        normalized_kind = "comment"
    elif normalized_kind in ("reposts", "shares", "share"):
        normalized_kind = "repost"
    elif normalized_kind in ("posts",):
        normalized_kind = "post"
    elif normalized_kind in ("videos",):
        normalized_kind = "video"

    profile_names: Dict[str, str] = {}
    profile_pictures: Dict[str, Optional[str]] = {}
    for profile in profiles:
        profile_id = _profile_slug(profile)
        if profile_id:
            profile_names[profile_id] = str(profile.get("name") or profile_id)
            profile_pictures[profile_id] = profile.get("picture")
    for history in histories:
        profile_id = _profile_slug(history)
        if profile_id:
            profile_names.setdefault(profile_id, str(history.get("subject_name") or profile_id))

    query = search.strip().lower()
    start, end = _date(start_date), _date(end_date)
    filtered = []
    for row in rows:
        row_slug = _slug(row.get("profile_slug"))
        if slug and row_slug != slug:
            continue
        event_kind = _activity_type(row)
        is_video = _is_video(row)
        is_photo = _is_photo(row)
        if normalized_kind == "post" and event_kind != "post":
            continue
        if normalized_kind == "video" and not is_video:
            continue
        if normalized_kind == "photo" and (not is_photo or not _has_accessible_image(row)):
            continue
        if normalized_kind == "like" and event_kind != "like":
            continue
        if normalized_kind == "comment" and event_kind != "comment":
            continue
        if normalized_kind == "repost" and event_kind != "share":
            continue
        if normalized_kind == "other" and (event_kind in ("post", "video") or is_video or is_photo):
            continue
        row["profile_slug"] = row_slug or None
        row["profile_name"] = profile_names.get(row_slug, row.get("subject_name") or "Unassigned import")
        row["profile_picture"] = profile_pictures.get(row_slug)
        row["activity_kind"] = event_kind
        row["is_video"] = is_video
        row["is_photo"] = is_photo
        if is_video and not row.get("embed_src"):
            post_url = row.get("linkedin_post_url") or row.get("original_post_url") or row.get("post_url")
            try:
                row["embed_src"] = embed_map.get((row_slug, canonical_linkedin_post_url(post_url)))
            except (ValueError, TypeError):
                row["embed_src"] = None
            if not row.get("embed_src") and post_url:
                p_str = str(post_url).strip()
                urn_m = re.search(r"urn:li:(?:activity|ugcPost|share):(\d+)", p_str, re.IGNORECASE)
                act_m = re.search(r"activity-(\d+)", p_str, re.IGNORECASE)
                if urn_m:
                    row["embed_src"] = f"https://www.linkedin.com/embed/feed/update/urn:li:activity:{urn_m.group(1)}"
                elif act_m:
                    row["embed_src"] = f"https://www.linkedin.com/embed/feed/update/urn:li:activity:{act_m.group(1)}"
        search_fields = (
            "text", "activity_text", "post_content", "original_post_text", "comment_text",
            "post_author", "original_post_author", "subject_name", "actor", "reaction_type",
            "source", "video_status", "media_type", "type", "activity_type",
            "post_url", "original_post_url", "profile_name",
        )
        if query and query not in " ".join(str(row.get(field) or "") for field in search_fields).lower():
            continue
        row_date = _date(row.get("date"))
        if (start or end) and not row_date:
            continue
        if start and row_date < start or end and row_date > end:
            continue
        filtered.append(row)
    filtered.sort(key=lambda row: str(row.get("date") or ""), reverse=sort != "oldest")
    page = max(1, int(page))
    per_page = min(100, max(1, int(per_page)))
    total = len(filtered)
    selected_profile = next((profile for profile in profiles if _profile_slug(profile) == slug), None) if slug else None
    return {
        "items": filtered[(page - 1) * per_page:page * per_page],
        "page": page,
        "per_page": per_page,
        "total_items": total,
        "total_pages": max(1, (total + per_page - 1) // per_page),
        "source": "mongodb",
        "selected_profile": {"id": slug, "name": selected_profile.get("name") or slug} if selected_profile else None,
        "filters": {"profile": slug or None, "type": normalized_kind or "all", "from": start.isoformat() if start else None, "to": end.isoformat() if end else None, "sort": sort if sort in ("newest", "oldest") else "newest"},
        "coverage_note": "Only deduplicated records currently stored in MongoDB are returned; missing dates are excluded when a date filter is active. Photo and video filters are media facets; event filters use the stored activity action type.",
    }


def get_trends(months: int = 12, profile_slug: str = "") -> Dict[str, Any]:
    profiles, histories, runs, activities, imports = _records()
    content = _collect_content(histories, activities, imports)
    slug = _slug(profile_slug)
    if slug:
        content = [row for row in content if row.get("profile_slug") == slug]
        runs = [run for run in runs if slug in {_slug(item) for item in run.get("profile_slugs", [])}]
    months = min(36, max(1, int(months)))
    return {"source": "mongodb", "profile_slug": slug or None, "months": months, "items": _trends(content, runs, months), "coverage_note": "No dated activity for a month is reported as unknown, not zero; run counts are only available since durable tracking began."}
