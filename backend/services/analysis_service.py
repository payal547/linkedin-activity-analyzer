"""Deterministic activity analysis service."""

from collections import Counter
from datetime import date
from typing import List, Dict, Any, Optional


def analyze_activities(
    rows: List[Dict[str, Any]],
    activity_types: List[str],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    subject_name: str = "Selected Connection",
    data_source: str = "real_linkedin",
    subject_url: Optional[str] = None,
    headline: Optional[str] = None
) -> Dict[str, Any]:
    selected_types = {value.strip().lower() for value in activity_types if value.strip()}
    # If "all" in selected_types, match everything
    has_all = "all" in selected_types

    start = date.fromisoformat(start_date) if start_date else None
    end = date.fromisoformat(end_date) if end_date else None
    filtered = []

    for row in rows:
        raw_date = (row.get("date") or "").strip()
        raw_type = (row.get("type") or "").strip().lower()
        raw_type = raw_type[:-1] if raw_type.endswith("s") else raw_type
        if not raw_date or not raw_type:
            continue
        try:
            activity_date = date.fromisoformat(raw_date[:10])
        except ValueError:
            continue

        if not has_all and selected_types:
            normalized_selected = {kind[:-1] if kind.endswith("s") else kind for kind in selected_types}
            if raw_type in normalized_selected:
                pass
            elif raw_type in ("like", "reaction", "reactions/like") and any(k in normalized_selected for k in ("like", "reaction", "reactions/like", "reactions/likes")):
                pass
            elif raw_type in ("post", "share") and any(k in normalized_selected for k in ("post", "posts", "share", "shares")):
                pass
            else:
                continue

        if (start and activity_date < start) or (end and activity_date > end):
            continue
        filtered.append((activity_date, raw_type, row))

    totals = Counter(kind for _, kind, _ in filtered)
    likes = sum(_number(row.get("likes")) for _, _, row in filtered)
    comments = sum(_number(row.get("comments")) for _, _, row in filtered)
    shares = sum(_number(row.get("shares")) for _, _, row in filtered)
    engagement = likes + comments + shares

    # Specific category counts
    posts_count = sum(1 for _, kind, _ in filtered if kind in ("post", "share"))
    comments_count = sum(1 for _, kind, _ in filtered if kind == "comment")
    likes_count = sum(1 for _, kind, _ in filtered if kind in ("like", "reaction"))
    shares_count = sum(1 for _, kind, _ in filtered if kind == "share")
    videos_count = sum(1 for _, kind, _ in filtered if kind == "video")

    activities_list = []
    for activity_date, kind, row in sorted(filtered, key=lambda item: item[0], reverse=True):
        act_text = (row.get("text") or "").strip()[:500]
        p_content = row.get("post_content") or act_text or "LinkedIn Post"
        p_author = row.get("post_author") or (subject_name if row.get("is_own_post", True) else "Original Post Author")
        p_url = row.get("post_url") or ""
        m_url = row.get("media_url") or ""
        c_text = row.get("comment_text") or (act_text if kind == "comment" else "")
        r_type = row.get("reaction_type") or ("Like" if kind in ("like", "reaction") else "")
        is_own = row.get("is_own_post") if row.get("is_own_post") is not None else (kind in ("post", "video", "share"))
        act_type = row.get("action_type") or kind

        activities_list.append({
            "id": row.get("id", ""),
            "date": activity_date.isoformat(),
            "type": kind,
            "text": act_text,
            "likes": _number(row.get("likes")),
            "comments": _number(row.get("comments")),
            "shares": _number(row.get("shares")),
            "source": row.get("source", data_source),
            # Actual Post & Grouped Activity Attributes
            "post_content": p_content,
            "post_author": p_author,
            "post_url": p_url,
            "media_url": m_url,
            "comment_text": c_text,
            "reaction_type": r_type,
            "is_own_post": is_own,
            "action_type": act_type,
            "subject_name": row.get("subject_name") or subject_name,
            "subject_url": row.get("subject_url") or subject_url
        })

    return {
        "subject_name": subject_name,
        "subject_url": subject_url,
        "headline": headline,
        "data_source": data_source,
        "total_activities": len(filtered),
        "by_type": dict(totals),
        "posts_count": posts_count,
        "comments_count": comments_count,
        "likes_count": likes_count,
        "shares_count": shares_count,
        "videos_count": videos_count,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "total_engagement": engagement,
        "average_engagement": round(engagement / len(filtered), 2) if filtered else 0,
        "date_range": {
            "start": min((d for d, _, _ in filtered), default=None).isoformat() if filtered else (start_date or None),
            "end": max((d for d, _, _ in filtered), default=None).isoformat() if filtered else (end_date or None),
        },
        "activities": activities_list,
    }



def _number(value):
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return 0
