"""Deterministic activity analysis service."""

import re
from collections import Counter
from datetime import date, timedelta
from typing import List, Dict, Any, Optional

from services.sentiment_service import enrich_activities_list, compute_sentiment_summary


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
    # If "all" in selected_types, match everything
    has_all = "all" in selected_types

    def _safe_date(d_val: Optional[str]) -> Optional[date]:
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
            # Default to standard LinkedIn export US format MM/DD/YYYY; if invalid day for month, fallback to DD/MM/YYYY
            elif p1 <= 12 and p2 <= 12:
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

    start = _safe_date(start_date)
    end = _safe_date(end_date)
    filtered = []

    for row in rows:
        raw_date = (row.get("date") or "").strip()
        raw_type = (row.get("type") or "").strip().lower()
        raw_type = raw_type[:-1] if raw_type.endswith("s") else raw_type
        if not raw_type:
            continue

        activity_date = _safe_date(raw_date)
        if activity_date is None:
            if start or end:
                # Excluded because date is unknown and filter is active
                continue
            activity_date = date.today()

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

    # Specific category counts (exact additive breakdown without double-counting)
    posts_count = sum(1 for _, kind, _ in filtered if kind == "post")
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

        p_auth_photo = row.get("post_author_photo") or None
        p_auth_headline = row.get("post_author_headline") or None

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
            "media_url": m_url,
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
            "video_url": row.get("video_url") or None,
            "video_thumbnail": row.get("video_thumbnail") or None,
            "video_status": row.get("video_status") or ("Video stream extracted" if row.get("video_url") else ("Video detected (LinkedIn auth required for playback)" if (kind == "video" or row.get("video_thumbnail")) else "No video"))
        })

    # AI Sentiment & Tone Analysis layer on top of actual retrieved activities
    enrich_activities_list(activities_list)
    sentiment_summary = compute_sentiment_summary(activities_list)

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
