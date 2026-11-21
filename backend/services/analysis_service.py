"""Deterministic activity analysis service."""

import re
from collections import Counter
from datetime import date, timedelta
from typing import List, Dict, Any, Optional, Set

from services.sentiment_service import enrich_activities_list, compute_sentiment_summary


def _normalize_row_kind(row: Dict[str, Any]) -> str:
    """Accurately extracts and standardizes activity kind from raw row dictionary."""
    act_type = (row.get("action_type") or row.get("activity_type") or row.get("type") or "").strip().lower()
    act_type = act_type[:-1] if act_type.endswith("s") else act_type

    if act_type in ("comment", "comments"):
        return "comment"
    if act_type in ("like", "likes", "reaction", "reactions", "reactions/like", "reactions/likes"):
        return "like"
    if act_type in ("share", "shares", "repost", "reposts"):
        return "share"
    if act_type in ("video", "videos"):
        return "video"
    if row.get("video_url") or row.get("video_thumbnail") or row.get("is_video"):
        if act_type not in ("comment", "like", "reaction", "share", "repost"):
            return "video"
    if act_type in ("post", "posts"):
        return "post"
    # Secondary check on reaction_type and comment_text
    if row.get("reaction_type"):
        return "like"
    if row.get("comment_text") and not row.get("post_content"):
        return "comment"
    return act_type or "post"


def _matches_selected_types(kind: str, selected_types: Set[str], row: Dict[str, Any]) -> bool:
    """
    Strict matching between activity kind and selected filter types.
    Ensures comments only return comments, posts only return posts, etc.
    Video filter matches original videos, video likes, video comments, and video shares.
    """
    if "all" in selected_types or not selected_types:
        return True

    is_vid = bool(
        kind == "video" or
        row.get("is_video") or
        row.get("video_url") or
        row.get("video_thumbnail") or
        (row.get("video_status") and row.get("video_status") != "No video")
    )

    for sel in selected_types:
        norm_sel = sel.strip().lower()
        norm_sel = norm_sel[:-1] if norm_sel.endswith("s") else norm_sel

        if norm_sel == "all":
            return True
        if norm_sel == "comment" and kind == "comment":
            return True
        if norm_sel in ("like", "reaction") and kind in ("like", "reaction"):
            return True
        if norm_sel in ("share", "repost") and kind in ("share", "repost"):
            return True
        if norm_sel == "video" and is_vid:
            return True
        if norm_sel == "post" and kind == "post":
            return True

    return False


def analyze_activities(
    rows: List[Dict[str, Any]],
    activity_types: List[str],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    subject_name: str = "Selected Connection",
    data_source: str = "real_linkedin",
    subject_url: Optional[str] = None,
    headline: Optional[str] = None,
    retrieval_completeness: Optional[str] = None
) -> Dict[str, Any]:
    selected_types = {value.strip().lower() for value in activity_types if value.strip()}
    has_all = "all" in selected_types or not selected_types

    def _safe_date(d_val: Optional[str], prefer_dmy: bool = True) -> Optional[date]:
        if not d_val:
            return None
        d_clean = str(d_val).strip()
        # 1. Direct YYYY-MM-DD (also supports YYYY/MM/DD and YYYY.MM.DD)
        m_iso = re.search(r'(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})', d_clean)
        if m_iso:
            try:
                return date(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)))
            except (ValueError, TypeError):
                pass

        # 2. Slash/Dash/Dot format MM/DD/YYYY or DD/MM/YYYY
        m_slash = re.search(r'(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})', d_clean)
        if m_slash:
            p1, p2, yr = int(m_slash.group(1)), int(m_slash.group(2)), int(m_slash.group(3))
            # Unambiguous DD/MM/YYYY (first number > 12, second <= 12)
            if p1 > 12 >= p2:
                try:
                    return date(yr, p2, p1)
                except (ValueError, TypeError):
                    pass
            # Unambiguous MM/DD/YYYY (second number > 12, first <= 12)
            elif p2 > 12 >= p1:
                try:
                    return date(yr, p1, p2)
                except (ValueError, TypeError):
                    pass
            # Ambiguous MM/DD/YYYY vs DD/MM/YYYY (both <= 12)
            elif p1 <= 12 and p2 <= 12:
                if prefer_dmy:
                    try:
                        return date(yr, p2, p1)
                    except (ValueError, TypeError):
                        try:
                            return date(yr, p1, p2)
                        except (ValueError, TypeError):
                            pass
                else:
                    try:
                        return date(yr, p1, p2)
                    except (ValueError, TypeError):
                        try:
                            return date(yr, p2, p1)
                        except (ValueError, TypeError):
                            pass

        # 3. Month name formats: '18 Oct 2026', 'October 18, 2026', 'Oct 18, 2026'
        months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
        m_mon1 = re.search(r'(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{4})', d_clean, re.IGNORECASE)
        if m_mon1:
            day, mon_str, yr = int(m_mon1.group(1)), m_mon1.group(2).lower()[:3], int(m_mon1.group(3))
            if mon_str in months:
                try:
                    return date(yr, months.index(mon_str) + 1, day)
                except ValueError:
                    pass

        m_mon2 = re.search(r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})', d_clean, re.IGNORECASE)
        if m_mon2:
            mon_str, day, yr = m_mon2.group(1).lower()[:3], int(m_mon2.group(2)), int(m_mon2.group(3))
            if mon_str in months:
                try:
                    return date(yr, months.index(mon_str) + 1, day)
                except ValueError:
                    pass

        return None

    start = _safe_date(start_date, prefer_dmy=True)
    end = _safe_date(end_date, prefer_dmy=True)
    if start and end and start > end:
        alt_s = _safe_date(start_date, prefer_dmy=False)
        alt_e = _safe_date(end_date, prefer_dmy=False)
        if alt_s and alt_e and alt_s <= alt_e:
            start, end = alt_s, alt_e
    filtered = []

    for row in rows:
        raw_date = (row.get("date") or row.get("activity_date") or "").strip()
        kind = _normalize_row_kind(row)

        activity_date = _safe_date(raw_date)
        if activity_date is None:
            if start or end:
                # Excluded because date is unknown and filter is active
                continue
            activity_date = date.today()

        if not _matches_selected_types(kind, selected_types, row):
            continue

        if (start and activity_date < start) or (end and activity_date > end):
            continue
        filtered.append((activity_date, kind, row))

    totals = Counter(kind for _, kind, _ in filtered)
    likes = sum(_number(row.get("likes")) for _, _, row in filtered)
    comments = sum(_number(row.get("comments")) for _, _, row in filtered)
    shares = sum(_number(row.get("shares")) for _, _, row in filtered)
    engagement = likes + comments + shares

    # Specific category counts (exact additive breakdown without double-counting)
    posts_count = sum(1 for _, kind, _ in filtered if kind == "post")
    comments_count = sum(1 for _, kind, _ in filtered if kind == "comment")
    likes_count = sum(1 for _, kind, _ in filtered if kind in ("like", "reaction"))
    shares_count = sum(1 for _, kind, _ in filtered if kind in ("share", "repost"))
    videos_count = sum(
        1 for _, kind, r in filtered
        if (kind == "video" or r.get("is_video") or r.get("video_url") or r.get("video_thumbnail") or (r.get("video_status") and r.get("video_status") != "No video"))
    )

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

        p_auth_photo = row.get("post_author_photo") or None
        p_auth_headline = row.get("post_author_headline") or None

        is_vid = bool(
            row.get("is_video") or
            kind == "video" or
            row.get("video_url") or
            row.get("video_thumbnail") or
            (row.get("video_status") and row.get("video_status") != "No video")
        )
        v_url = row.get("video_url") or None
        v_thumb = row.get("video_thumbnail") or None
        v_stat = row.get("video_status")
        if not v_stat:
            if v_url:
                v_stat = "Video stream extracted"
            elif is_vid and v_thumb:
                v_stat = "Video detected (Stream protected by LinkedIn, permalink available)"
            elif is_vid:
                v_stat = "Video detected (LinkedIn auth required for playback)"
            else:
                v_stat = "No video"

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
            "post_author_photo": p_auth_photo,
            "post_author_headline": p_auth_headline,
            "post_url": p_url,
            "media_url": m_url or (v_thumb if is_vid else ""),
            "comment_text": c_text,
            "reaction_type": r_type,
            "is_own_post": is_own,
            "action_type": act_type,
            "subject_name": row.get("subject_name") or subject_name,
            "subject_url": row.get("subject_url") or subject_url,
            # Explicit Aliases for actor & original post distinction (Section 5 requirements)
            "actor": row.get("actor") or row.get("subject_name") or subject_name,
            "original_post_author": row.get("original_post_author") or p_author,
            "activity_type": row.get("activity_type") or act_type,
            "activity_date": row.get("activity_date") or activity_date.isoformat(),
            "activity_text": row.get("activity_text") or (c_text if kind == "comment" else act_text),
            "original_post_text": row.get("original_post_text") or p_content,
            "original_post_url": row.get("original_post_url") or p_url,
            # Video Attributes
            "is_video": is_vid,
            "video_url": v_url,
            "video_mime_type": row.get("video_mime_type") or None,
            "video_thumbnail": v_thumb,
            "video_status": v_stat
        })

    # AI Sentiment & Tone Analysis layer on top of actual retrieved activities
    enrich_activities_list(activities_list)
    sentiment_summary = compute_sentiment_summary(activities_list)

    empty_msg = ""
    if not filtered:
        if not has_all and selected_types:
            norm_types = [t.rstrip('s').lower() for t in selected_types]
            if norm_types == ["video"]:
                empty_msg = f"No video activities found for {subject_name} in the selected period."
            else:
                filter_names = ", ".join(sorted(selected_types))
                empty_msg = f"No {filter_names} activities found for {subject_name} in the selected period."
        else:
            empty_msg = f"No activities found for {subject_name} in the selected period."

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
        "sentiment_summary": sentiment_summary,
        "retrieval_completeness": retrieval_completeness or "COMPLETE",
        "empty_message": empty_msg,
        "filtered_activity_types": list(selected_types) if (not has_all and selected_types) else ["all"],
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
