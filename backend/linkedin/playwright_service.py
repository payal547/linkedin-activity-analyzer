"""
Playwright LinkedIn Profile Activity Retrieval Service.
Retrieves real public activities (Posts, Reposts/Shares, Comments/Replies, Reactions/Likes, Videos)
for target LinkedIn profiles using an authorized session (when available) or public stealth view.
Strictly adheres to honest error reporting: distinguishes authwall, checkpoint, session expiry,
timeout, browser errors, and date-range mismatches without fabricating fake data.
"""

import os
import re
import html
import json
import time
import threading
from urllib.parse import unquote
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Tuple, List, Dict, Any, Optional
from dotenv import load_dotenv

try:
    from backend.services.pipeline_service import start_stage, complete_stage, fail_stage
    from backend.utils.logger import get_logger, get_current_run_id
except ImportError:
    try:
        from services.pipeline_service import start_stage, complete_stage, fail_stage
        from utils.logger import get_logger, get_current_run_id
    except ImportError:
        def start_stage(*args, **kwargs): pass
        def complete_stage(*args, **kwargs): pass
        def fail_stage(*args, **kwargs): pass
        def get_current_run_id(): return None
        get_logger = None

pw_logger = get_logger("playwright_service") if get_logger else None

load_dotenv()

SESSION_FILE = Path(__file__).resolve().parent.parent / "data" / "playwright_session.json"
USER_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "playwright_context"


class ScrapeResult(tuple):
    """
    Subclass of tuple (success, activities, message) for 100% backward-compatibility
    with 3-element tuple unpacking, while providing typed status_type and retrieval_completeness attributes.
    """
    def __new__(cls, success: bool, activities: List[Dict[str, Any]], message: str, status_type: str = "", retrieval_completeness: str = "INCOMPLETE"):
        instance = super().__new__(cls, (success, activities, message))
        instance.success = success
        instance.activities = activities
        instance.message = message
        instance.status_type = status_type
        instance.retrieval_completeness = retrieval_completeness
        return instance


# In-memory HTML cache for polite rate-limit handling (5 minute TTL)
_PROFILE_CACHE: Dict[str, Dict[str, Any]] = {}

# Active rate-limit cooldown timestamps to prevent repeated requests after HTTP 999
_RATE_LIMIT_COOLDOWN: Dict[str, float] = {}

# Per-canonical-profile locks to prevent duplicate simultaneous scraping
_PROFILE_LOCKS: Dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _get_profile_lock(slug: str) -> threading.Lock:
    with _LOCKS_GUARD:
        if slug not in _PROFILE_LOCKS:
            _PROFILE_LOCKS[slug] = threading.Lock()
        return _PROFILE_LOCKS[slug]


def clear_rate_limit_cooldown() -> None:
    """Clears all active rate limit cooldowns (used in test suite)."""
    _RATE_LIMIT_COOLDOWN.clear()


def is_rate_limited(slug: str) -> bool:
    """Checks whether a rate-limit cooldown is currently active for this slug or globally."""
    now_ts = time.time()
    return bool(_RATE_LIMIT_COOLDOWN.get(slug, 0) > now_ts or _RATE_LIMIT_COOLDOWN.get("__global__", 0) > now_ts)


def set_rate_limit(slug: str, duration: float = 300.0) -> None:
    """Sets a rate-limit cooldown for the target slug and globally."""
    now_ts = time.time()
    _RATE_LIMIT_COOLDOWN[slug] = now_ts + duration
    _RATE_LIMIT_COOLDOWN["__global__"] = now_ts + 60.0


def clear_profile_cache() -> None:
    """Clear in-memory profile HTML cache and rate-limit cooldowns (used for testing or explicit invalidation)."""
    _PROFILE_CACHE.clear()
    _RATE_LIMIT_COOLDOWN.clear()


def validate_session_state() -> Tuple[str, bool, str]:
    """
    Validates whether an authenticated session for Aniket Pal is available and valid.
    Checks SESSION_FILE or LINKEDIN_LI_AT in .env.
    Returns: (status: 'VALID' | 'EXPIRED' | 'MISSING', is_valid: bool, message: str)
    NEVER logs passwords or cookie values.
    """
    li_at_env = os.getenv("LINKEDIN_LI_AT") or os.getenv("LI_AT")
    if li_at_env and not SESSION_FILE.exists():
        try:
            SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
            storage_data = {
                "cookies": [
                    {
                        "name": "li_at",
                        "value": li_at_env.strip(),
                        "domain": ".www.linkedin.com",
                        "path": "/",
                        "expires": int(time.time()) + 86400 * 30,
                        "httpOnly": True,
                        "secure": True,
                        "sameSite": "None"
                    }
                ],
                "origins": []
            }
            with open(SESSION_FILE, "w", encoding="utf-8") as sf:
                json.dump(storage_data, sf, indent=2)
        except Exception:
            pass

    if not SESSION_FILE.exists():
        return "MISSING", False, "LinkedIn authentication/session is unavailable."

    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            cookies = data.get("cookies", [])
            li_at_cookie = next((c for c in cookies if c.get("name") == "li_at"), None)
            if not li_at_cookie:
                return "MISSING", False, "LinkedIn authentication/session is unavailable."

            expires = li_at_cookie.get("expires", -1)
            if expires > 0 and expires < time.time():
                return "EXPIRED", False, "LinkedIn session expired. Please re-authenticate."

            meta_file = SESSION_FILE.parent / "playwright_session_meta.json"
            user_label = "Aniket Pal"
            if meta_file.exists():
                try:
                    with open(meta_file, "r", encoding="utf-8") as mf:
                        mdata = json.load(mf)
                        if mdata.get("user_name"):
                            user_label = mdata.get("user_name")
                except Exception:
                    pass

            return "VALID", True, f"Authenticated session active for {user_label}"
    except Exception:
        return "MISSING", False, "LinkedIn authentication/session is unavailable."


def extract_slug_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    match = re.search(r'(?:https?:\/\/)?(?:[a-zA-Z0-9_\-\.]+\.)?linkedin\.com\/in\/([a-zA-Z0-9_\-\%]+)', url.strip(), re.IGNORECASE)
    if match:
        return match.group(1).rstrip('/')
    return None


def parse_relative_time_to_date(rel_str: str) -> Tuple[str, bool]:
    """
    Converts LinkedIn relative time strings (e.g. '1d', '2w', '3 mo', '5h', '1 yr', 'yesterday', 'Edited • 2w')
    or direct date formats ('18 Oct 2026', 'October 18, 2026', '10/18/2026', '2026-10-18') to YYYY-MM-DD.
    Returns: (iso_date: str, is_exact: bool)
    """
    today = date.today()
    if not rel_str:
        return today.isoformat(), False

    rel_clean = rel_str.strip().lower()

    # 1. Direct ISO date format (YYYY-MM-DD or YYYY/MM/DD)
    date_match = re.search(r'(\d{4}[-/]\d{2}[-/]\d{2})', rel_clean)
    if date_match:
        iso_str = date_match.group(1).replace('/', '-')
        try:
            return date.fromisoformat(iso_str).isoformat(), True
        except ValueError:
            pass

    # 2. Month name date formats
    months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    # 2a. '18 Oct 2026' or '18 October 2026'
    mon_match1 = re.search(r'(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{4})', rel_clean)
    if mon_match1:
        day = int(mon_match1.group(1))
        mon_str = mon_match1.group(2)[:3]
        year = int(mon_match1.group(3))
        if mon_str in months:
            m_idx = months.index(mon_str) + 1
            try:
                return date(year, m_idx, day).isoformat(), True
            except ValueError:
                pass

    # 2b. 'October 18, 2026' or 'Oct 18 2026'
    mon_match2 = re.search(r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})', rel_clean)
    if mon_match2:
        mon_str = mon_match2.group(1)[:3]
        day = int(mon_match2.group(2))
        year = int(mon_match2.group(3))
        if mon_str in months:
            m_idx = months.index(mon_str) + 1
            try:
                return date(year, m_idx, day).isoformat(), True
            except ValueError:
                pass

    # 3. Slash / Dash format: default to standard DD/MM/YYYY
    slash_match = re.search(r'(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})', rel_clean)
    if slash_match:
        p1 = int(slash_match.group(1))
        p2 = int(slash_match.group(2))
        yr = int(slash_match.group(3))
        # Case A: Standard DD/MM/YYYY (first is day <= 31, second is month <= 12)
        if p1 <= 31 and p2 <= 12:
            try:
                return date(yr, p2, p1).isoformat(), True
            except ValueError:
                pass
        # Case B: Unambiguous MM/DD/YYYY (first is month <= 12, second is day > 12)
        if p1 <= 12 and p2 <= 31:
            try:
                return date(yr, p1, p2).isoformat(), True
            except ValueError:
                pass

    # 4. Explicit keywords
    if "yesterday" in rel_clean:
        return (today - timedelta(days=1)).isoformat(), True
    if any(k in rel_clean for k in ("now", "moment", "second", "just now")):
        return today.isoformat(), True

    # 5. Standard LinkedIn relative times: 1d, 2w, 3mo, 5h, 1y, 1 yr, 2 yrs, 45m
    num_match = re.search(r'(\d+)\s*(month[s]?|mo|week[s]?|w|day[s]?|d|hour[s]?|hr|h|minute[s]?|min|m|year[s]?|yr[s]?|y)', rel_clean)
    if num_match:
        num = int(num_match.group(1))
        unit = num_match.group(2)
        if unit in ('h', 'hr', 'hour', 'hours', 'min', 'minute', 'minutes', 'm'):
            return today.isoformat(), True
        elif unit in ('d', 'day', 'days'):
            return (today - timedelta(days=num)).isoformat(), True
        elif unit in ('w', 'week', 'weeks'):
            return (today - timedelta(weeks=num)).isoformat(), True
        elif unit in ('mo', 'month', 'months'):
            return (today - timedelta(days=num * 30)).isoformat(), True
        elif unit in ('y', 'year', 'years', 'yr', 'yrs'):
            return (today - timedelta(days=num * 365)).isoformat(), True

    return today.isoformat(), False


def to_date_obj(d_str: Optional[str]) -> Optional[date]:
    """Helper to parse ISO date strings (YYYY-MM-DD) or fallback relative/verbal dates to date objects."""
    if not d_str:
        return None
    iso_date, _ = parse_relative_time_to_date(str(d_str))
    try:
        return date.fromisoformat(iso_date)
    except ValueError:
        return None


def check_feed_date_boundary(parsed_dates: List[date], start_date_obj: Optional[date], scroll_count: int = 1) -> bool:
    """
    Evaluates whether activity cards in the feed have genuinely progressed past the requested start_date boundary.
    Guarantees:
    - Returns False if start_date_obj is None or parsed_dates is empty.
    - Evaluates oldest date strictly from parsed activity cards.
    - Prevents premature aborts on scroll 1 when isolated pinned posts exist.
    """
    if not start_date_obj or not parsed_dates:
        return False

    oldest_activity_date = min(parsed_dates)
    if oldest_activity_date >= start_date_obj:
        return False

    feed_tail_passed = (parsed_dates[-1] < start_date_obj and len(parsed_dates) >= 2)
    feed_has_depth = len(parsed_dates) >= 3 or scroll_count > 1

    return feed_tail_passed or feed_has_depth


def _clean_text(raw_html: str) -> str:
    if not raw_html:
        return ""
    text = re.sub(r'<[^>]+>', ' ', raw_html)
    text = html.unescape(text)
    return ' '.join(text.split()).strip()


def extract_top_card_photo(page_html: str) -> Optional[str]:
    """
    Extracts profile photo strictly from the target profile's top-card/header container.
    Guarantees that photos from 'People also viewed', sidebars, recommendations, or
    unrelated page sections are NEVER extracted.
    Returns the unescaped photo URL if found in the top card, else None.
    """
    if not page_html:
        return None

    # 1. Isolate the top-card / header container
    top_card_chunk = ""
    # Try public profile top-card-layout section
    tc_m = re.search(r'<section[^>]*top-card-layout[^>]*>(.*?)</section>', page_html, re.DOTALL | re.IGNORECASE)
    if tc_m:
        top_card_chunk = tc_m.group(1)
    else:
        # Fallback to top-card entity image container
        tc_div_m = re.search(r'<div[^>]*top-card-layout__entity-image-container[^>]*>(.*?)</div>', page_html, re.DOTALL | re.IGNORECASE)
        if tc_div_m:
            top_card_chunk = tc_div_m.group(0)
        else:
            # Fallback for authenticated / modern LinkedIn DOM top card
            pv_m = re.search(r'<section[^>]*class="[^"]*(?:pv-top-card|artdeco-card)[^"]*"[^>]*>(.*?)</section>', page_html, re.DOTALL | re.IGNORECASE)
            if pv_m:
                top_card_chunk = pv_m.group(1)

    if not top_card_chunk:
        # If no explicit container was found, look for an <img> that specifically has top-card classes
        specific_img_m = re.search(
            r'<img[^>]*(?:top-card-layout__entity-image|pv-top-card-profile-picture__image|pv-top-card__photo)[^>]*>',
            page_html,
            re.DOTALL | re.IGNORECASE
        )
        if specific_img_m:
            top_card_chunk = specific_img_m.group(0)

    if not top_card_chunk:
        return None

    # 2. Look for the image tag inside the isolated top card chunk
    img_m = re.search(
        r'<img[^>]*(?:top-card-layout__entity-image|pv-top-card|profile-picture|entity-image|profile-displayphoto)[^>]*>',
        top_card_chunk,
        re.DOTALL | re.IGNORECASE
    )
    img_tag = img_m.group(0) if img_m else top_card_chunk

    # 3. Extract the photo URL from data-delayed-url or src
    photo_src_m = re.search(
        r'(?:data-delayed-url|src)=["\']([^"\']+(?:profile-displayphoto|profile-framedphoto|dms/image)[^"\']*)["\']',
        img_tag,
        re.IGNORECASE
    )
    if photo_src_m:
        photo_url = html.unescape(photo_src_m.group(1)).strip()
        # Ensure it's a valid remote image URL and not a generic data URI or SVG
        if photo_url.startswith("http://") or photo_url.startswith("https://"):
            return photo_url

    return None


def _extract_video_info(html_chunk: str, post_url: Optional[str] = None) -> Tuple[bool, Optional[str], Optional[str], str]:
    """
    Extracts video presence, video stream/embed URL, video poster/thumbnail, and status.
    Never invents fake data or URLs.
    Returns: (has_video, video_url, video_thumbnail, video_status)
    """
    has_video = bool(re.search(
        r'<video\b|class="[^"]*(?:feedshare-video|video-js|vjs-tech|feed-shared-linkedin-video|update-components-linkedin-video|artdeco-video|video-thumbnail|update-components-video|feed-shared-video|feedshare-video__preview|feedshare-video__player|feedshare-video-container|profile-activity-card__video|artdeco-video-player|video-player)[^"]*"|data-sources=|data-stream-url=|urn:li:video:|aria-label="Play video"|aria-label="Play"|aria-label="video"|class="[^"]*(?:play-button|vjs-play-control|vjs-big-play-button)[^"]*"|data-control-name="play_video"|<iframe[^>]+(?:youtube|vimeo)',
        html_chunk,
        re.IGNORECASE
    ))

    if post_url and any(v in post_url.lower() for v in ("/videos/", "urn:li:video:", "youtube.com", "youtu.be", "vimeo.com")):
        has_video = True

    video_url = None
    # 1. Direct <video src="..."> or <source src="...">
    v_src_m = re.search(r'<video[^>]*src="([^"]+)"', html_chunk, re.IGNORECASE)
    if not v_src_m:
        v_src_m = re.search(r'<source[^>]*src="([^"]+)"', html_chunk, re.IGNORECASE)
    if v_src_m:
        cand = html.unescape(v_src_m.group(1)).strip()
        if not cand.startswith("blob:"):
            video_url = cand
            has_video = True

    # 2. LinkedIn JSON data-sources attribute (e.g. data-sources='[{"src":"https://...mp4"}]')
    if not video_url:
        ds_m = re.search(r'data-sources="([^"]+)"', html_chunk)
        if ds_m:
            try:
                import json as _json
                srcs = _json.loads(html.unescape(ds_m.group(1)))
                if isinstance(srcs, list) and srcs:
                    first = srcs[0]
                    if isinstance(first, dict) and first.get("src"):
                        video_url = first["src"]
                        has_video = True
                    elif isinstance(first, str):
                        video_url = first
                        has_video = True
            except Exception:
                pass

    # 3. data-stream-url attribute
    if not video_url:
        stream_m = re.search(r'data-stream-url="([^"]+)"', html_chunk)
        if stream_m:
            video_url = html.unescape(stream_m.group(1))
            has_video = True

    # 4. External video embed links (YouTube, Vimeo, etc.)
    if not video_url:
        ext_m = re.search(r'href="(https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/|vimeo\.com/)[^"]+)"', html_chunk, re.IGNORECASE)
        if ext_m:
            video_url = html.unescape(ext_m.group(1))
            has_video = True

    # 5. Playback data attributes
    if not video_url:
        v_attr_m = re.search(r'(?:data-video-url|data-playback-url)="([^"]+)"', html_chunk, re.IGNORECASE)
        if v_attr_m:
            cand = html.unescape(v_attr_m.group(1)).strip()
            if cand and not cand.startswith("blob:"):
                video_url = cand
                has_video = True

    # Extract Video Thumbnail (Poster)
    video_thumbnail = None
    poster_m = re.search(r'<video[^>]*poster="([^"]+)"', html_chunk, re.IGNORECASE)
    if poster_m:
        video_thumbnail = html.unescape(poster_m.group(1))
        has_video = True

    if not video_thumbnail:
        vthumb_m = re.search(r'class="[^"]*(?:video-thumbnail|vjs-poster|feedshare-video__preview|update-components-video__thumbnail|feed-shared-video__thumbnail)[^"]*"[^>]*src="([^"]+)"', html_chunk, re.IGNORECASE)
        if vthumb_m:
            video_thumbnail = html.unescape(vthumb_m.group(1))
            has_video = True

    if not video_thumbnail:
        bg_m = re.search(r'class="[^"]*(?:feedshare-video|vjs-poster)[^"]*"[^>]*style="[^"]*background-image:\s*url\((?:&quot;|"|\')?([^"\'\)]+)', html_chunk, re.IGNORECASE)
        if bg_m:
            video_thumbnail = html.unescape(bg_m.group(1))
            has_video = True

    if has_video and not video_thumbnail:
        v_img_m = re.search(r'(?:data-delayed-url|src)="([^"]+(?:media\.licdn\.com|dms\/image)[^"]*)"', html_chunk, re.IGNORECASE)
        if v_img_m and "profile-displayphoto" not in v_img_m.group(1):
            video_thumbnail = html.unescape(v_img_m.group(1))

    if video_url or video_thumbnail:
        has_video = True

    # Determine honest status
    if video_url and any(domain in video_url.lower() for domain in ("youtube.com", "youtu.be", "vimeo.com")):
        video_status = "External video embed"
    elif video_url:
        video_status = "Video stream extracted"
    elif has_video and video_thumbnail:
        video_status = "Video detected (Stream protected by LinkedIn, permalink available)"
    elif has_video:
        video_status = "Video detected (LinkedIn auth required for playback)"
    else:
        video_status = "No video"

    return has_video, video_url, video_thumbnail, video_status


def _extract_activities_from_public_html(
    page_html: str,
    target_slug: str,
    target_name: Optional[str] = None,
    target_photo: Optional[str] = None,
    target_headline: Optional[str] = None
) -> Tuple[int, int, List[Dict[str, Any]]]:
    """
    Parses LinkedIn public profile DOM for activities across Posts, Comments, and Reactions.
    Properly distinguishes between the target profile actor and the original post author.
    Returns: (section_count, post_elements_count, extracted_activities)
    """
    clean_slug = re.sub(r'[-_]\d+$', '', target_slug)

    # 1. Extract Target Profile Name from DOM if not provided or just a slug
    if not target_name or target_name.lower() == clean_slug.replace('-', ' ').lower() or target_name.lower() == target_slug.lower():
        h1_m = re.search(r'<h1[^>]*top-card-layout__title[^>]*>(.*?)</h1>', page_html, re.DOTALL)
        if h1_m:
            real_name = _clean_text(h1_m.group(1))
            if real_name:
                target_name = real_name
        elif not target_name:
            title_m = re.search(r'<title>(.*?)(?:\s*\|\s*LinkedIn|\s*-\s*LinkedIn)</title>', page_html, re.IGNORECASE)
            if title_m:
                real_title_name = _clean_text(title_m.group(1))
                if real_title_name and "login" not in real_title_name.lower():
                    target_name = real_title_name

    display_name = target_name or clean_slug.replace('-', ' ').title()
    profile_url = f"https://www.linkedin.com/in/{target_slug}/"

    # 2. Extract Photo and Headline strictly from target profile top card
    if not target_photo:
        target_photo = extract_top_card_photo(page_html)
    if not target_headline:
        hd_m = re.search(r'<h2[^>]*top-card-layout__headline[^>]*>(.*?)</h2>', page_html, re.DOTALL)
        if hd_m:
            target_headline = _clean_text(hd_m.group(1))

    panels = page_html.split('<div class="tab__panel')
    section_count = len(panels) - 1 if len(panels) > 1 else 0
    if section_count == 0 and ("activities-tab" in page_html or "profile-activity-card" in page_html):
        section_count = 1

    extracted_activities: List[Dict[str, Any]] = []
    seen_keys = set()
    total_elements = 0

    # Process each tab panel (Posts, Comments, Reactions)
    for panel_chunk in panels[1:]:
        panel_html = '<div class="tab__panel' + panel_chunk
        panel_header = panel_html[:300].lower()

        is_comments_panel = 'comment' in panel_header or 'replied' in panel_html[:600]
        is_reactions_panel = 'reaction' in panel_header

        # ----------------------------------------------------
        # 1. Comments Panel Processing
        # ----------------------------------------------------
        if is_comments_panel:
            comments = panel_html.split('<div class="comment')
            for c_html in comments[1:]:
                chunk = '<div class="comment' + c_html
                total_elements += 1

                # Action description
                action_match = re.search(r'<span>\s*([^<]+replied[^<]*|[^<]+commented[^<]*)</span>', chunk, re.IGNORECASE)
                raw_action_desc = _clean_text(action_match.group(1)) if action_match else f"{display_name} commented on LinkedIn"

                # Relative date
                date_match = re.search(r'<span class="before:middot">\s*([0-9]+[a-zA-Z]+)\s*</span>', chunk)
                if not date_match:
                    date_match = re.search(r'([0-9]+[dhmwy]|mo)\b', chunk)
                rel_date = date_match.group(1).strip() if date_match else ""
                iso_date, is_exact = parse_relative_time_to_date(rel_date)

                # Comment text
                text_match = re.search(r'<div class="text-color-text text-sm font-normal">(.*?)</div>', chunk, re.DOTALL)
                comment_text = _clean_text(text_match.group(1)) if text_match else ""
                if not comment_text:
                    continue

                # Target post permalink
                link_match = re.search(r'href="(https://www\.linkedin\.com/posts/[^"]+)"', chunk)
                if not link_match:
                    link_match = re.search(r'session_redirect=(https%3A%2F%2Fwww\.linkedin\.com%2Fposts%2F[^&"]+)', chunk)
                    comment_post_url = unquote(link_match.group(1)) if link_match else None
                else:
                    comment_post_url = html.unescape(link_match.group(1))

                # Identify Original Post Author and Content
                original_author = "Original Post Author"
                original_content = "Original post content unavailable"

                # Check if action text explicitly named author (e.g. "replied to Aniket Pal's post")
                named_author_match = re.search(r'(?:replied to|commented on)\s+([^"\'<]+?)(?:\'s|\s+post|\s+thread)', raw_action_desc, re.IGNORECASE)
                if named_author_match:
                    found_auth = named_author_match.group(1).strip()
                    if found_auth and found_auth.lower() not in ("a", "an", "the", "their", "this", "someone", "another") and found_auth.lower() != display_name.lower():
                        original_author = found_auth

                # Parse author and topic from post URL slug
                if comment_post_url and (original_author == "Original Post Author" or len(original_author) <= 2):
                    post_slug_m = re.search(r'/posts/([a-zA-Z0-9\-]+?)(?:_([a-zA-Z0-9\-]+))?(?:-activity-|\?|$)', comment_post_url)
                    if post_slug_m:
                        raw_auth = post_slug_m.group(1)
                        raw_topic = post_slug_m.group(2) or ""
                        cleaned = re.sub(r'-[0-9a-zA-Z]{7,}$', '', raw_auth)
                        parsed = cleaned.replace('-', ' ').strip().title()
                        if parsed and len(parsed) > 1 and parsed.lower() != display_name.lower():
                            original_author = parsed
                        if raw_topic and raw_topic != "activity":
                            original_content = raw_topic.replace('-', ' ').strip().capitalize()

                has_video_tag, video_url, video_thumbnail, video_status = _extract_video_info(chunk, comment_post_url)
                is_video = bool(has_video_tag or video_url or video_thumbnail or (video_status and video_status != "No video"))
                target_media_type = "video" if is_video else "post"
                action_desc = f"{display_name} commented on {original_author}'s {target_media_type}" if (original_author and original_author != "Original Post Author") else f"{display_name} commented on a {target_media_type}"

                dedup_key = f"comment_{iso_date}_{comment_text[:40]}"
                if dedup_key in seen_keys:
                    continue
                seen_keys.add(dedup_key)

                extracted_activities.append({
                    "id": f"pw_comment_{len(extracted_activities)+1}",
                    "subject_id": target_slug,
                    "subject_name": display_name,
                    "actor": display_name,
                    "subject_url": profile_url,
                    "date": iso_date,
                    "activity_date": iso_date,
                    "relative_date": rel_date,
                    "has_exact_date": is_exact,
                    "type": "comment",
                    "activity_type": "comment",
                    "text": f"{action_desc}: \"{comment_text}\"",
                    "activity_text": comment_text,
                    "likes": 0,
                    "comments": 0,
                    "shares": 0,
                    "source": "real_linkedin",
                    "post_content": original_content,
                    "original_post_text": original_content,
                    "post_author": original_author,
                    "original_post_author": original_author,
                    "post_author_photo": None,
                    "post_author_headline": "Author of LinkedIn Post",
                    "post_url": comment_post_url or profile_url,
                    "original_post_url": comment_post_url or profile_url,
                    "media_url": video_thumbnail or None,
                    "video_url": video_url,
                    "video_thumbnail": video_thumbnail,
                    "video_status": video_status,
                    "is_video": is_video,
                    "comment_text": comment_text,
                    "reaction_type": None,
                    "is_own_post": False,
                    "action_type": "comment"
                })

        # ----------------------------------------------------
        # 2. Posts and Reactions Panels Processing
        # ----------------------------------------------------
        else:
            cards = panel_html.split('<div class="profile-activity-card')
            i = 1
            while i < len(cards):
                card_chunk = '<div class="profile-activity-card' + cards[i]
                total_elements += 1

                # Header action text
                header_match = re.search(r'base-card__full-link[^>]*>.*?<span class="sr-only">(.*?)</span>', card_chunk, re.DOTALL)
                action_text = _clean_text(header_match.group(1)) if header_match else ""

                # Post permalink
                link_match = re.search(r'href="(https://www\.linkedin\.com/posts/[^"]+)"', card_chunk)
                if not link_match:
                    link_match = re.search(r'session_redirect=(https%3A%2F%2Fwww\.linkedin\.com%2Fposts%2F[^&"]+)', card_chunk)
                    post_url = unquote(link_match.group(1)) if link_match else None
                else:
                    post_url = html.unescape(link_match.group(1))

                # Primary author, headline, photo, and date
                author_match = re.search(r'publisher-author-card.*?<h3[^>]*base-main-card__title[^>]*>(.*?)</h3>', card_chunk, re.DOTALL)
                card_author = _clean_text(author_match.group(1)) if author_match else ""

                author_hd_match = re.search(r'publisher-author-card.*?<h4[^>]*base-main-card__subtitle[^>]*>(.*?)</h4>', card_chunk, re.DOTALL)
                if not author_hd_match:
                    author_hd_match = re.search(r'publisher-author-card.*?<p[^>]*base-main-card__subtitle[^>]*>(.*?)</p>', card_chunk, re.DOTALL)
                card_author_headline = _clean_text(author_hd_match.group(1)) if author_hd_match else ""

                author_img_match = re.search(r'publisher-author-card.*?data-delayed-url="([^"]+)"', card_chunk)
                if not author_img_match:
                    author_img_match = re.search(r'publisher-author-card.*?<img[^>]*src="([^"]+)"', card_chunk)
                card_author_photo = html.unescape(author_img_match.group(1)) if author_img_match else None

                rel_date_match = re.search(r'base-main-card__metadata[^>]*>(.*?)</div>', card_chunk, re.DOTALL)
                rel_date = _clean_text(rel_date_match.group(1)) if rel_date_match else ""

                # Post text in card
                post_text_match = re.search(r'class="[^"]*see-more-text[^"]*"[^>]*>(.*?)</div>', card_chunk, re.DOTALL)
                post_text = _clean_text(post_text_match.group(1)) if post_text_match else ""

                # Media in card
                img_match = re.search(r'feedshare-image-high-res[^"]*"[^>]*data-delayed-url="([^"]+)"', card_chunk)
                if not img_match:
                    img_match = re.search(r'data-delayed-url="([^"]+feedshare[^"]+)"', card_chunk)
                if not img_match:
                    img_match = re.search(r'background-image:\s*url\(\'([^\']+feedshare[^\']+)\'\)', card_chunk)
                media_url = html.unescape(img_match.group(1)) if img_match else None

                # Metrics in card
                reactions_match = re.search(r'data-num-reactions="(\d+)"', card_chunk)
                likes_count = int(reactions_match.group(1)) if reactions_match else 0
                comments_match = re.search(r'data-num-comments="(\d+)"', card_chunk)
                comments_count = int(comments_match.group(1)) if comments_match else 0

                # Check if outer header card for repost or like followed by nested child
                act_lower = action_text.lower()
                is_share_or_like_header = ('reposted' in act_lower or 'liked' in act_lower or 'reacted' in act_lower or is_reactions_panel) and not post_text
                next_chunk = ""

                if is_share_or_like_header and i + 1 < len(cards):
                    next_chunk = '<div class="profile-activity-card' + cards[i+1]
                    next_author_match = re.search(r'publisher-author-card.*?<h3[^>]*base-main-card__title[^>]*>(.*?)</h3>', next_chunk, re.DOTALL)
                    if next_author_match:
                        card_author = _clean_text(next_author_match.group(1))

                    next_author_hd = re.search(r'publisher-author-card.*?<h4[^>]*base-main-card__subtitle[^>]*>(.*?)</h4>', next_chunk, re.DOTALL)
                    if not next_author_hd:
                        next_author_hd = re.search(r'publisher-author-card.*?<p[^>]*base-main-card__subtitle[^>]*>(.*?)</p>', next_chunk, re.DOTALL)
                    if next_author_hd:
                        card_author_headline = _clean_text(next_author_hd.group(1))

                    next_author_img = re.search(r'publisher-author-card.*?data-delayed-url="([^"]+)"', next_chunk)
                    if not next_author_img:
                        next_author_img = re.search(r'publisher-author-card.*?<img[^>]*src="([^"]+)"', next_chunk)
                    if next_author_img:
                        card_author_photo = html.unescape(next_author_img.group(1))

                    next_date_match = re.search(r'base-main-card__metadata[^>]*>(.*?)</div>', next_chunk, re.DOTALL)
                    if next_date_match:
                        rel_date = _clean_text(next_date_match.group(1))

                    next_text_match = re.search(r'class="[^"]*see-more-text[^"]*"[^>]*>(.*?)</div>', next_chunk, re.DOTALL)
                    if next_text_match:
                        post_text = _clean_text(next_text_match.group(1))

                    next_img = re.search(r'data-delayed-url="([^"]+feedshare[^"]+)"', next_chunk)
                    if not next_img:
                        next_img = re.search(r'background-image:\s*url\(\'([^\']+feedshare[^\']+)\'\)', next_chunk)
                    if next_img:
                        media_url = html.unescape(next_img.group(1))

                    if not post_url:
                        next_link = re.search(r'href="(https://www\.linkedin\.com/posts/[^"]+)"', next_chunk)
                        if next_link:
                            post_url = html.unescape(next_link.group(1))

                    next_rx = re.search(r'data-num-reactions="(\d+)"', next_chunk)
                    if next_rx:
                        likes_count = int(next_rx.group(1))
                    next_cm = re.search(r'data-num-comments="(\d+)"', next_chunk)
                    if next_cm:
                        comments_count = int(next_cm.group(1))

                    i += 1  # consumed nested child

                if not post_text and not post_url:
                    i += 1
                    continue

                clean_post_url = post_url.split('?')[0].split('#')[0] if post_url else None
                combined_chunk = (card_chunk + " " + next_chunk) if next_chunk else card_chunk
                has_video_tag, video_url, video_thumbnail, video_status = _extract_video_info(combined_chunk, clean_post_url)
                if not video_thumbnail and media_url and has_video_tag:
                    video_thumbnail = media_url
                if not media_url and video_thumbnail:
                    media_url = video_thumbnail
                is_video = bool(has_video_tag or video_url or video_thumbnail or (video_status and video_status != "No video"))

                target_media_type = "video" if is_video else "post"
                if 'reposted' in act_lower:
                    action_type = "share"
                    is_own = False
                    action_desc = f"{display_name} reposted {card_author}'s {target_media_type}" if card_author and card_author.lower() != display_name.lower() else f"{display_name} reposted this {target_media_type}"
                elif 'liked' in act_lower or 'reacted' in act_lower or is_reactions_panel:
                    action_type = "like"
                    is_own = False
                    action_desc = f"{display_name} reacted (Like) to {card_author}'s {target_media_type}" if card_author and card_author.lower() != display_name.lower() else f"{display_name} reacted to this {target_media_type}"
                elif is_video:
                    action_type = "video"
                    is_own = True
                    action_desc = f"{display_name} posted a video"
                else:
                    action_type = "post"
                    is_own = True
                    action_desc = f"{display_name} posted this"

                iso_date, is_exact = parse_relative_time_to_date(rel_date)
                dedup_key = clean_post_url or (post_text[:60] + action_type)
                if dedup_key in seen_keys:
                    i += 1
                    continue
                seen_keys.add(dedup_key)

                act_headline_desc = f"{action_desc}: \"{post_text[:120]}\"" if post_text else action_desc
                original_author = card_author or (display_name if is_own else "LinkedIn Author")

                extracted_activities.append({
                    "id": f"pw_act_{len(extracted_activities)+1}",
                    "subject_id": target_slug,
                    "subject_name": display_name,
                    "actor": display_name,
                    "subject_url": profile_url,
                    "date": iso_date,
                    "activity_date": iso_date,
                    "relative_date": rel_date,
                    "has_exact_date": is_exact,
                    "type": action_type,
                    "activity_type": action_type,
                    "text": act_headline_desc,
                    "activity_text": post_text or act_headline_desc,
                    "likes": likes_count,
                    "comments": comments_count,
                    "shares": 0,
                    "source": "real_linkedin",
                    "post_content": post_text or "Original post content unavailable",
                    "original_post_text": post_text or "Original post content unavailable",
                    "post_author": original_author,
                    "original_post_author": original_author,
                    "post_author_photo": card_author_photo or (target_photo if is_own else None),
                    "post_author_headline": card_author_headline or (target_headline if is_own else None),
                    "post_url": clean_post_url or profile_url,
                    "original_post_url": clean_post_url or profile_url,
                    "media_url": media_url,
                    "video_url": video_url,
                    "video_thumbnail": video_thumbnail,
                    "video_status": video_status,
                    "is_video": is_video,
                    "comment_text": None,
                    "reaction_type": "Like" if action_type == "like" else None,
                    "is_own_post": is_own,
                    "action_type": action_type
                })

                i += 1

    # ----------------------------------------------------
    # 3. Authenticated / Modern Feed Update Processing (.feed-shared-update-v2)
    # ----------------------------------------------------
    if len(extracted_activities) == 0 and ("feed-shared-update-v2" in page_html or "urn:li:activity" in page_html):
        feed_cards = re.split(r'<div[^>]*class="[^"]*(?:^|\s)feed-shared-update-v2(?:\s|")[^>]*>', page_html)
        if len(feed_cards) <= 1:
            feed_cards = re.split(r'<div[^>]*data-urn="urn:li:activity:[^"]*"[^>]*>', page_html)

        if len(feed_cards) > 1:
            section_count = max(section_count, 1)

        for f_chunk in feed_cards[1:]:
            total_elements += 1
            card_html = f_chunk

            # Context bar (e.g., "X reposted this", "X likes this")
            context_m = re.search(r'class="[^"]*(?:feed-shared-update-v2__context-bar|update-components-header)[^"]*"[^>]*>(.*?)</div>', card_html, re.DOTALL)
            context_text = _clean_text(context_m.group(1)) if context_m else ""
            c_lower = context_text.lower()

            # Actor & original author
            actor_m = re.search(r'class="[^"]*(?:update-components-actor__name|feed-shared-actor__name)[^"]*"[^>]*>(.*?)</', card_html, re.DOTALL)
            card_author = _clean_text(actor_m.group(1)) if actor_m else ""

            author_hd_m = re.search(r'class="[^"]*(?:update-components-actor__description|feed-shared-actor__description)[^"]*"[^>]*>(.*?)</', card_html, re.DOTALL)
            card_author_headline = _clean_text(author_hd_m.group(1)) if author_hd_m else ""

            author_img_m = re.search(r'class="[^"]*(?:update-components-actor__avatar-image|feed-shared-actor__avatar-image)[^"]*"[^>]*src="([^"]+)"', card_html)
            card_author_photo = html.unescape(author_img_m.group(1)) if author_img_m else None

            # Date
            date_m = re.search(r'class="[^"]*(?:update-components-actor__sub-description|feed-shared-actor__sub-description)[^"]*"[^>]*>(.*?)</', card_html, re.DOTALL)
            if not date_m:
                date_m = re.search(r'<time[^>]*>(.*?)</time>', card_html, re.DOTALL)
            rel_date = _clean_text(date_m.group(1)) if date_m else ""

            # Content / description
            text_m = re.search(r'class="[^"]*(?:feed-shared-update-v2__description|update-components-text|feed-shared-text)[^"]*"[^>]*>(.*?)</div>', card_html, re.DOTALL)
            post_text = _clean_text(text_m.group(1)) if text_m else ""

            # Post URL
            post_url = None
            urn_m = re.search(r'urn:li:activity:(\d+)', card_html)
            if urn_m:
                post_url = f"https://www.linkedin.com/feed/update/urn:li:activity:{urn_m.group(1)}/"
            else:
                link_m = re.search(r'href="(https://www\.linkedin\.com/(?:feed/update|posts)/[^"]+)"', card_html)
                if link_m:
                    post_url = html.unescape(link_m.group(1))

            if not post_text and not post_url:
                continue

            clean_post_url = post_url.split('?')[0].split('#')[0] if post_url else None

            # Media / Video
            has_video_tag, video_url, video_thumbnail, video_status = _extract_video_info(card_html, clean_post_url)
            is_video = bool(has_video_tag or video_url or video_thumbnail or (video_status and video_status != "No video"))
            img_m = re.search(r'class="[^"]*(?:feed-shared-image|update-components-image)[^"]*"[^>]*src="([^"]+)"', card_html)
            media_url = html.unescape(img_m.group(1)) if img_m else None
            if not media_url and video_thumbnail:
                media_url = video_thumbnail

            target_media_type = "video" if is_video else "post"
            # Determine action type
            if 'repost' in c_lower or 'shared' in c_lower:
                action_type = "share"
                is_own = False
                action_desc = f"{display_name} reposted {card_author}'s {target_media_type}" if card_author and card_author.lower() != display_name.lower() else f"{display_name} reposted this {target_media_type}"
            elif 'like' in c_lower or 'reacted' in c_lower:
                action_type = "like"
                is_own = False
                action_desc = f"{display_name} reacted (Like) to {card_author}'s {target_media_type}" if card_author and card_author.lower() != display_name.lower() else f"{display_name} reacted to this {target_media_type}"
            elif 'comment' in c_lower or 'replied' in c_lower:
                action_type = "comment"
                is_own = False
                action_desc = f"{display_name} commented on {card_author}'s {target_media_type}" if card_author and card_author.lower() != display_name.lower() else f"{display_name} commented on this {target_media_type}"
            elif is_video:
                action_type = "video"
                is_own = True
                action_desc = f"{display_name} posted a video"
            else:
                action_type = "post"
                is_own = True
                action_desc = f"{display_name} posted this"

            # Metrics
            rx_m = re.search(r'data-num-reactions="(\d+)"', card_html)
            likes_count = int(rx_m.group(1)) if rx_m else 0
            if likes_count == 0:
                rx_count_m = re.search(r'social-details-social-counts__reactions-count[^>]*>([0-9,]+)', card_html)
                if rx_count_m:
                    try:
                        likes_count = int(rx_count_m.group(1).replace(',', ''))
                    except ValueError:
                        pass

            cm_m = re.search(r'data-num-comments="(\d+)"', card_html)
            comments_count = int(cm_m.group(1)) if cm_m else 0
            if comments_count == 0:
                cm_count_m = re.search(r'social-details-social-counts__comments[^>]*>([0-9,]+)', card_html)
                if cm_count_m:
                    try:
                        comments_count = int(cm_count_m.group(1).replace(',', ''))
                    except ValueError:
                        pass

            iso_date, is_exact = parse_relative_time_to_date(rel_date)
            dedup_key = clean_post_url or (post_text[:60] + action_type)
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)

            act_headline_desc = f"{action_desc}: \"{post_text[:120]}\"" if post_text else action_desc
            original_author = card_author or (display_name if is_own else "LinkedIn Author")

            extracted_activities.append({
                "id": f"pw_feed_{len(extracted_activities)+1}",
                "subject_id": target_slug,
                "subject_name": display_name,
                "actor": display_name,
                "subject_url": profile_url,
                "date": iso_date,
                "activity_date": iso_date,
                "relative_date": rel_date,
                "has_exact_date": is_exact,
                "type": action_type,
                "activity_type": action_type,
                "text": act_headline_desc,
                "activity_text": post_text or act_headline_desc,
                "likes": likes_count,
                "comments": comments_count,
                "shares": 0,
                "source": "real_linkedin",
                "post_content": post_text or "Original post content unavailable",
                "original_post_text": post_text or "Original post content unavailable",
                "post_author": original_author,
                "original_post_author": original_author,
                "post_author_photo": card_author_photo or (target_photo if is_own else None),
                "post_author_headline": card_author_headline or (target_headline if is_own else None),
                "post_url": clean_post_url or profile_url,
                "original_post_url": clean_post_url or profile_url,
                "media_url": media_url,
                "video_url": video_url,
                "video_thumbnail": video_thumbnail,
                "video_status": video_status,
                "is_video": is_video,
                "comment_text": post_text if action_type == "comment" else None,
                "reaction_type": "Like" if action_type == "like" else None,
                "is_own_post": is_own,
                "action_type": action_type
            })

    return section_count, total_elements, extracted_activities


def scrape_linkedin_profile_activities(
    profile_url: str,
    target_name: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    activity_types: Optional[List[str]] = None,
    timeout_ms: int = 25000,
    target_photo: Optional[str] = None,
    target_headline: Optional[str] = None,
    status_out: Optional[Dict[str, Any]] = None,
    max_activities: Optional[int] = None,
    max_scrolls: Optional[int] = None,
    run_id: Optional[str] = None
) -> Tuple[bool, List[Dict[str, Any]], str]:
    """
    Retrieves real posts and activities for a target LinkedIn profile URL using Playwright.
    Differentiates all failure categories:
    (A) No public activity found (verified empty activity section)
    (B) No activity exists inside selected date range
    (C) LinkedIn authwall encountered
    (D) LinkedIn security checkpoint required
    (E) LinkedIn session expired / unavailable
    (F) Network timeout / connection unreachable
    (G) Playwright / browser failure
    (H) LinkedIn rate limit (HTTP 999)
    (I) Activity section missing / inaccessible on public profile
    (J) Profile not found (HTTP 404)
    Logs all diagnostic tracing without logging passwords, tokens, or cookie secrets.
    """
    active_run_id = run_id or (get_current_run_id() if callable(get_current_run_id) else None)
    log_path = Path(__file__).resolve().parent.parent / "data" / "scraper_debug.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def dbg(msg: str):
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().isoformat()}] {msg}\n")
        except Exception:
            pass
        if pw_logger:
            pw_logger.debug(msg)

    def make_res(success: bool, acts: List[Dict[str, Any]], msg: str, st_type: str, completeness: str = "INCOMPLETE") -> ScrapeResult:
        if status_out is not None:
            status_out["status_type"] = st_type
            status_out["status"] = msg
            status_out["empty_message"] = msg
            status_out["retrieval_completeness"] = completeness
        return ScrapeResult(success, acts, msg, st_type, completeness)

    slug = extract_slug_from_url(profile_url)
    if not slug:
        return make_res(False, [], "Invalid LinkedIn profile URL.", "INVALID_URL", "INVALID")

    # Configurable retrieval ceiling & scroll boundaries
    effective_max_activities = max_activities if (max_activities and max_activities > 0) else int(os.getenv("LINKEDIN_MAX_ACTIVITIES", "200"))
    effective_max_scrolls = max_scrolls if (max_scrolls and max_scrolls > 0) else int(os.getenv("LINKEDIN_MAX_SCROLLS", "15"))

    # Check authenticated session status
    sess_status, is_auth, sess_msg = validate_session_state()
    dbg(f"[DEBUG] Authenticated session confirmed: {'YES' if is_auth else 'NO'}")

    # Use dedicated activity feed when authenticated, else overview public profile
    target_profile_url = f"https://www.linkedin.com/in/{slug}/recent-activity/all/" if is_auth else f"https://www.linkedin.com/in/{slug}/"
    dbg(f"[DEBUG] requested URL: {profile_url}")
    dbg(f"[DEBUG] normalized URL: {target_profile_url}")

    if sess_status == "EXPIRED":
        dbg("[DEBUG] Retrieval failure reason: (E) Session expired or invalid")
        return make_res(False, [], "LinkedIn session expired. Please re-authenticate.", "SESSION_EXPIRED", "UNAVAILABLE")

    # Serialize concurrent scraping for the same profile slug to avoid triggering rate limit 999
    profile_lock = _get_profile_lock(slug)
    with profile_lock:
        scroll_count = 0
        hit_ceiling = False
        reached_end_of_feed = False
        reached_start_date_boundary = False
        start_date_obj = to_date_obj(start_date)
        end_date_obj = to_date_obj(end_date)

        # Check cache for freshly retrieved HTML within last 5 minutes to avoid rate-limit hammering
        now_ts = time.time()

        # 1. Rate-limit cooldown protection: prevent repeated requests after HTTP 999
        is_testing = (os.getenv("TESTING") == "1")
        if _RATE_LIMIT_COOLDOWN.get(slug, 0) > now_ts or (not is_testing and _RATE_LIMIT_COOLDOWN.get("__global__", 0) > now_ts):
            remaining = int(max(_RATE_LIMIT_COOLDOWN.get(slug, 0), 0 if is_testing else _RATE_LIMIT_COOLDOWN.get("__global__", 0)) - now_ts)
            dbg(f"[DEBUG] Rate-limit cooldown active ({remaining}s remaining). Skipping browser navigation.")
            pw_logger.info("LinkedIn rate-limit cooldown active for %s (%ds remaining). Skipping browser navigation to respect LinkedIn restrictions.", slug, remaining)
            return make_res(False, [], "LinkedIn rate limit encountered: temporarily limiting automated requests (HTTP 999). Please try again later. Your previously saved historical analysis is preserved.", "RATE_LIMITED_999", "BLOCKED")

        cached = _PROFILE_CACHE.get(slug)
        page_html = ""
        page_title = ""
        nav_status: Optional[int] = None
        nav_error: Optional[str] = None
        final_url = target_profile_url
        can_use_cache = False
        if cached and (now_ts - cached.get("timestamp", 0) < 300):
            c_status = cached.get("status", 200)
            c_html = cached.get("html", "")
            c_end = cached.get("reached_end_of_feed", False)
            c_oldest = cached.get("oldest_activity_date")
            c_oldest_d = to_date_obj(c_oldest) if c_oldest else None

            if c_status == 200 and c_html:
                if c_end:
                    can_use_cache = True
                elif start_date_obj is not None and c_oldest_d and c_oldest_d <= start_date_obj:
                    can_use_cache = True

        if can_use_cache and cached:
            page_html = cached.get("html", "")
            page_title = cached.get("title", "")
            nav_status = cached.get("status", 200)
            reached_end_of_feed = cached.get("reached_end_of_feed", False)
            dbg(f"[DEBUG] Using cached profile DOM for {slug} ({len(page_html)} bytes)")

        if not page_html:
            try:
                from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
            except Exception as pe:
                dbg(f"[DEBUG] Retrieval failure reason: (G) Playwright import error: {pe}")
                return make_res(False, [], "Activity could not be retrieved from LinkedIn. Browser automation error.", "AUTOMATION_ERROR")

            browser = None
            context = None
            try:
                dbg("[DEBUG] Playwright launched: yes")
                if active_run_id:
                    start_stage(active_run_id, 3, "Launching Playwright headless browser")
                with sync_playwright() as p:
                    browser = p.chromium.launch(
                        headless=True,
                        args=[
                            "--disable-blink-features=AutomationControlled",
                            "--no-sandbox",
                            "--disable-setuid-sandbox",
                            "--disable-dev-shm-usage",
                            "--disable-infobars"
                        ]
                    )

                    # Use genuine desktop Chrome User-Agent matching the host platform
                    desktop_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                    ctx_kwargs: Dict[str, Any] = {
                        "user_agent": desktop_ua,
                        "locale": "en-US",
                        "viewport": {"width": 1920, "height": 1080},
                        "extra_http_headers": {
                            "Accept-Language": "en-US,en;q=0.9",
                            "Referer": "https://www.google.com/"
                        }
                    }

                    if is_auth and SESSION_FILE.exists():
                        ctx_kwargs["storage_state"] = str(SESSION_FILE)

                    context = browser.new_context(**ctx_kwargs)
                    context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
                    page = context.new_page()

                    if active_run_id:
                        complete_stage(active_run_id, 3, details="Playwright Chromium launched successfully")
                        start_stage(active_run_id, 4, f"Navigating to {target_profile_url}")

                    dbg(f"[DEBUG] LinkedIn navigation started: {target_profile_url}")
                    nav_status = None
                    try:
                        resp = page.goto(target_profile_url, timeout=timeout_ms, wait_until="domcontentloaded")
                        if resp:
                            nav_status = resp.status
                    except PlaywrightTimeoutError:
                        dbg("[DEBUG] Retrieval failure reason: (F) Network timeout / unreachable")
                        return make_res(False, [], "Activity could not be retrieved from LinkedIn. Network timeout or connection unreachable.", "NETWORK_TIMEOUT")
                    except Exception as ne:
                        nav_error = str(ne)
                        dbg(f"[DEBUG] Navigation exception: {ne}")

                    dbg(f"[DEBUG] Navigation completed (status: {nav_status})")
                    try:
                        final_url = page.url
                        page_title = page.title()
                    except Exception:
                        final_url = target_profile_url
                        page_title = ""
                    dbg(f"[DEBUG] final URL after redirects: {final_url}")
                    dbg(f"[DEBUG] page title: {page_title}")
                    if active_run_id:
                        complete_stage(active_run_id, 4, details=f"Status: {nav_status} | Title: {page_title[:40] if page_title else 'N/A'}")

                    if nav_error and not page_title:
                        dbg(f"[DEBUG] Retrieval failure reason: Navigation error: {nav_error}")
                        return make_res(False, [], "Activity could not be retrieved from LinkedIn. Network timeout or connection unreachable.", "NETWORK_TIMEOUT")

                    final_lower = final_url.lower()
                    title_lower = (page_title or "").lower()

                    # Check for HTTP 404 or Profile Not Found / Page Not Found
                    is_not_found = (nav_status == 404) or ("profile not found" in title_lower) or ("page not found" in title_lower)
                    if is_not_found and target_profile_url.endswith("/recent-activity/all/"):
                        try:
                            dbg(f"[DEBUG] recent-activity/all/ returned 404, falling back to base profile URL for {slug}")
                            base_resp = page.goto(f"https://www.linkedin.com/in/{slug}/", timeout=timeout_ms, wait_until="domcontentloaded")
                            if base_resp:
                                nav_status = base_resp.status
                            final_url = page.url
                            page_title = page.title()
                            final_lower = final_url.lower()
                            title_lower = (page_title or "").lower()
                            is_not_found = (nav_status == 404) or ("profile not found" in title_lower) or ("page not found" in title_lower)
                        except Exception as fb_err:
                            dbg(f"[DEBUG] Base profile fallback check failed: {fb_err}")

                    if is_not_found:
                        dbg(f"[DEBUG] whether LinkedIn profile found: no (HTTP {nav_status} / {page_title})")
                        dbg("[DEBUG] Retrieval failure reason: Profile not found on LinkedIn")
                        dbg("[DEBUG] Actual target profile confirmed: NO")
                        return make_res(False, [], "LinkedIn profile not found. Please verify the URL.", "PROFILE_NOT_FOUND", "NOT_FOUND")

                    # Check for Private or Restricted Profile Activity Feed
                    is_restricted_early = (
                        ("profile is private" in title_lower) or
                        ("this profile is private" in title_lower)
                    )
                    if is_restricted_early:
                        dbg("[DEBUG] Retrieval failure reason: Profile activity is private or restricted by the member")
                        return make_res(False, [], "Activity for this profile is private or restricted by the member.", "ACTIVITY_FEED_RESTRICTED", "UNAVAILABLE")

                    # Check for Security Checkpoint or Challenge
                    is_checkpoint = "checkpoint" in final_lower or "challenge" in final_lower or "security check" in title_lower
                    dbg(f"[DEBUG] whether LinkedIn authwall/checkpoint appeared: {'checkpoint' if is_checkpoint else 'no'}")
                    if is_checkpoint:
                        dbg("[DEBUG] Retrieval failure reason: (D) LinkedIn checkpoint/security challenge")
                        return make_res(False, [], "Activity could not be retrieved from LinkedIn. LinkedIn security checkpoint required.", "SECURITY_CHECKPOINT", "BLOCKED")

                    # Check for Rate-limit 999: Stop immediately, set cooldown, do NOT retry LinkedIn requests
                    if nav_status == 999:
                        _RATE_LIMIT_COOLDOWN[slug] = time.time() + 300.0  # 5 min profile cooldown
                        if os.getenv("TESTING") != "1":
                            _RATE_LIMIT_COOLDOWN["__global__"] = time.time() + 60.0  # 1 min global cooldown
                        dbg("[DEBUG] Encountered HTTP 999 rate limit, stopping immediately without retry")
                        dbg("[DEBUG] Retrieval failure reason: (C) LinkedIn rate limit (HTTP 999)")
                        return make_res(False, [], "LinkedIn rate limit encountered: temporarily limiting automated requests (HTTP 999). Please try again later. Your previously saved historical analysis is preserved.", "RATE_LIMITED_999", "BLOCKED")

                    # Check for Authwall redirect: Stop immediately to avoid triggering rate limit 999
                    is_authwall = "authwall" in final_lower or "sign-in redirect" in final_lower
                    if is_authwall:
                        dbg(f"[DEBUG] whether LinkedIn authwall/checkpoint appeared: authwall ({final_url})")
                        dbg("[DEBUG] Retrieval failure reason: (C) LinkedIn authwall")
                        if not is_auth:
                            return make_res(False, [], "LinkedIn authentication/session required. Profile activity is protected by an authwall.", "AUTH_REQUIRED", "UNAVAILABLE")
                        return make_res(False, [], "Activity could not be retrieved from LinkedIn. LinkedIn authwall encountered.", "AUTH_REQUIRED", "UNAVAILABLE")

                    dbg(f"[DEBUG] whether LinkedIn authwall/checkpoint appeared: {'checkpoint' if is_checkpoint else 'no'}")

                    # Settle and bounded incremental scrolling to retrieve activities
                    scroll_count = 0
                    hit_ceiling = False
                    reached_end_of_feed = False
                    reached_start_date_boundary = False
                    last_height = 0
                    stagnant_count = 0

                    if active_run_id:
                        start_stage(active_run_id, 5, f"Scrolling feed (limit: {effective_max_scrolls} scrolls)")

                    try:
                        page.wait_for_timeout(800)
                        while scroll_count < effective_max_scrolls:
                            scroll_count += 1

                            # Count loaded cards
                            current_card_count = page.evaluate("""() => {
                                return document.querySelectorAll('.profile-activity-card, .feed-shared-update-v2, .tab__panel .comment, .feed-shared-update-v2__control-menu').length;
                            }""")

                            if current_card_count >= effective_max_activities:
                                hit_ceiling = True
                                dbg(f"[DEBUG] Reached max activities ceiling ({current_card_count} >= {effective_max_activities}) at scroll {scroll_count}")
                                break

                            # Incremental scroll down
                            new_height = page.evaluate("""() => {
                                window.scrollBy(0, 1200);
                                return document.body.scrollHeight;
                            }""")
                            page.wait_for_timeout(600)

                            # Click "Show more results" button if present
                            try:
                                load_more = page.query_selector("button.feed-load-more-button, button:has-text('Show more results'), button:has-text('Load more')")
                                if load_more and load_more.is_visible():
                                    load_more.click()
                                    page.wait_for_timeout(800)
                            except Exception:
                                pass

                            if new_height == last_height:
                                stagnant_count += 1
                                if stagnant_count >= 3:
                                    reached_end_of_feed = True
                                    dbg(f"[DEBUG] Feed end verified: page height unchanged for 3 scrolls at scroll {scroll_count}")
                                    break
                            else:
                                stagnant_count = 0
                                last_height = new_height

                            # Check date boundary strictly on actual activity feed cards
                            if start_date_obj:
                                time_texts = page.evaluate("""() => {
                                    // Target actual LinkedIn activity feed cards only
                                    const cardSelectors = [
                                        '.profile-activity-card',
                                        '.feed-shared-update-v2',
                                        '.tab__panel .comment',
                                        '[data-urn*="urn:li:activity"]',
                                        '.profile-creator-shared-feed-update__container'
                                    ];
                                    // Non-activity profile sections that must never be used for activity date boundaries
                                    const nonActivitySelector = '#experience, #education, #licenses_and_certifications, #skills, #volunteer, #publications, #honors_and_awards, #recommendations, #languages, #organizations, .experience-section, .education-section, .certifications-section, .volunteering-section';

                                    const cards = Array.from(document.querySelectorAll(cardSelectors.join(', ')));
                                    if (!cards || cards.length === 0) return [];

                                    const dateList = [];
                                    for (const card of cards) {
                                        // Ignore any card or element within Experience, Education, or other non-activity sections
                                        if (card.closest && card.closest(nonActivitySelector)) {
                                            continue;
                                        }
                                        // Look for timestamp / metadata within this confirmed activity card only
                                        const timeEl = card.querySelector('[class*="before:middot"], .before\\\\:middot, time, .base-main-card__metadata, .feed-shared-actor__sub-description, .update-components-actor__sub-description');
                                        if (timeEl) {
                                            const dtAttr = timeEl.getAttribute('datetime');
                                            const txt = (dtAttr || timeEl.innerText || timeEl.textContent || '').trim();
                                            if (txt) {
                                                dateList.push(txt);
                                            }
                                        }
                                    }
                                    return dateList;
                                }""")

                                if time_texts:
                                    parsed_dates = []
                                    for t_str in time_texts:
                                        p_iso, _ = parse_relative_time_to_date(t_str)
                                        p_d = to_date_obj(p_iso)
                                        if p_d:
                                            parsed_dates.append(p_d)

                                    if check_feed_date_boundary(parsed_dates, start_date_obj, scroll_count):
                                        reached_start_date_boundary = True
                                        oldest_card_date = min(parsed_dates)
                                        dbg(f"[DEBUG] Reached start date boundary ({oldest_card_date.isoformat()} < {start_date_obj.isoformat()}) from activity cards at scroll {scroll_count}")
                                        break

                        page.wait_for_timeout(500)
                    except Exception as se:
                        dbg(f"[DEBUG] Scroll / pagination notice: {se}")

                    if active_run_id:
                        complete_stage(active_run_id, 5, items_count=scroll_count, details=f"Scrolled {scroll_count} times, feed reached {'end' if reached_end_of_feed else ('date boundary' if reached_start_date_boundary else 'scroll limit')}")

                    # Safely retrieve page HTML content
                    try:
                        page_html = page.content()
                    except Exception as ce:
                        dbg(f"[DEBUG] Page content fetch error: {ce}")
                        page_html = ""

                    # Cache retrieved HTML only if valid 200 response and not an authwall / checkpoint / 404
                    if page_html and nav_status == 200 and not is_authwall and not is_checkpoint and not is_not_found:
                        _PROFILE_CACHE[slug] = {
                            "html": page_html,
                            "title": page_title,
                            "status": nav_status,
                            "timestamp": time.time()
                        }

                    browser.close()
                    browser = None

            except PlaywrightTimeoutError:
                dbg("[DEBUG] Retrieval failure reason: (F) Network timeout / unreachable")
                return make_res(False, [], "Activity could not be retrieved from LinkedIn. Network timeout or connection unreachable.", "NETWORK_TIMEOUT", "FAILED")
            except Exception as e:
                dbg(f"[DEBUG] Retrieval failure reason: (G) Playwright / browser failure: {e}")
                return make_res(False, [], "Activity could not be retrieved from LinkedIn. Browser automation error.", "AUTOMATION_ERROR", "FAILED")
            finally:
                if browser:
                    try:
                        browser.close()
                    except Exception:
                        pass

        if not page_html:
            st = "NETWORK_TIMEOUT" if nav_error else "AUTOMATION_ERROR"
            return make_res(False, [], "Activity could not be retrieved from LinkedIn.", st, "FAILED")

        # Verify Target Profile Presence in HTML
        html_title_m = re.search(r'<title>(.*?)</title>', page_html, re.DOTALL | re.IGNORECASE)
        effective_title = page_title or (html_title_m.group(1).strip() if html_title_m else "")
        title_lower = effective_title.lower()

        if (nav_status == 404) or ("profile not found" in title_lower) or ("page not found" in title_lower):
            dbg(f"[DEBUG] whether LinkedIn profile found: no (HTTP {nav_status} / {effective_title})")
            dbg("[DEBUG] Retrieval failure reason: Profile not found on LinkedIn")
            dbg("[DEBUG] Actual target profile confirmed: NO")
            return make_res(False, [], "LinkedIn profile not found. Please verify the URL.", "PROFILE_NOT_FOUND", "NOT_FOUND")

        # Check for Private or Restricted Profile Activity Feed
        is_restricted = (
            ("profile is private" in title_lower) or
            ("this profile is private" in page_html[:4000].lower()) or
            ("cannot see this member's activity" in page_html.lower()) or
            ("limited their visibility" in page_html.lower()) or
            ("this member's activity is private" in page_html.lower()) or
            ("only connections can view" in page_html.lower())
        )
        if is_restricted:
            dbg("[DEBUG] Retrieval failure reason: Profile activity is private or restricted by the member")
            return make_res(False, [], "Activity for this profile is private or restricted by the member.", "ACTIVITY_FEED_RESTRICTED", "UNAVAILABLE")

        name_m = re.search(r'<h1[^>]*top-card-layout__title[^>]*>(.*?)</h1>', page_html, re.DOTALL)
        found_name = _clean_text(name_m.group(1)) if name_m else None
        found_photo = extract_top_card_photo(page_html)
        has_activity_sec = ("activities-tab" in page_html) or ("profile-activity-card" in page_html) or ("tab__panel" in page_html)

        dbg(f"[DEBUG] whether target profile name was found: {'yes' if found_name else 'no'} ({found_name or 'N/A'})")
        dbg(f"[DEBUG] whether target profile photo was found: {'yes' if found_photo else 'no'}")
        dbg(f"[DEBUG] whether the activity section was found: {'yes' if has_activity_sec else 'no'}")

        # Verify that the final page is actually the intended profile
        is_intended_profile = (
            (slug.lower() in final_url.lower()) or 
            (found_name and any(part.lower() in found_name.lower() for part in slug.split('-') if len(part) > 2))
        ) and not (
            "login" in final_url.lower() or 
            "authwall" in final_url.lower() or 
            nav_status == 404 or 
            "profile not found" in title_lower or 
            "page not found" in title_lower
        )

        dbg(f"[DEBUG] Actual target profile confirmed: {'YES' if is_intended_profile else 'NO'}")

        if not is_intended_profile:
            dbg(f"[DEBUG] Verification failed: final page is not {slug}'s profile. STOP activity parsing.")
            if nav_status == 404 or "profile not found" in title_lower or "page not found" in title_lower:
                return make_res(False, [], "LinkedIn profile not found. Please verify the URL.", "PROFILE_NOT_FOUND", "NOT_FOUND")
            if "authwall" in final_url.lower() or nav_status == 999:
                return make_res(False, [], "LinkedIn rate limit encountered (HTTP 999). Please wait before retrying." if nav_status == 999 else "LinkedIn authentication/session required. Profile activity is protected by an authwall.", "RATE_LIMITED_999" if nav_status == 999 else "AUTH_REQUIRED", "BLOCKED" if nav_status == 999 else "UNAVAILABLE")
            return make_res(False, [], "Activity could not be retrieved from LinkedIn. Target profile page was not reached.", "PROFILE_UNREACHABLE", "UNAVAILABLE")

        # Parse and extract all activities from DOM
        if active_run_id:
            start_stage(active_run_id, 6, "Extracting activity cards, media, and videos from DOM")

        sec_count, elem_count, extracted_raw = _extract_activities_from_public_html(
            page_html=page_html,
            target_slug=slug,
            target_name=target_name or found_name,
            target_photo=found_photo,
            target_headline=target_headline
        )

        if active_run_id:
            v_cnt = sum(1 for a in extracted_raw if a.get("type") == "video" or a.get("video_url") or a.get("video_thumbnail"))
            complete_stage(active_run_id, 6, items_count=len(extracted_raw), details=f"Extracted {len(extracted_raw)} activities ({v_cnt} videos)")

        has_posts = any(a.get("type") == "post" for a in extracted_raw)
        has_comments = any(a.get("type") == "comment" for a in extracted_raw)
        has_reactions = any(a.get("type") in ("like", "reaction") for a in extracted_raw)

        dbg(f"[DEBUG] Activity section found: {'yes' if sec_count > 0 else 'no'}")
        dbg(f"[DEBUG] Posts tab found: {'yes' if has_posts else 'no'}")
        dbg(f"[DEBUG] Comments tab found: {'yes' if has_comments else 'no'}")
        dbg(f"[DEBUG] Reactions tab found: {'yes' if has_reactions else 'no'}")
        dbg(f"[DEBUG] Number of raw activities found: {elem_count}")
        dbg(f"[DEBUG] Number after deduplication: {len(extracted_raw)}")
        dbg(f"[DEBUG] activities retrieved before filtering = {len(extracted_raw)}")

        # Update metadata in persistent storage: persist verified top-card photo (or None) and headline
        if found_name or found_photo is not None:
            try:
                from services.saved_profiles_storage import save_target_profile
                hd_m = re.search(r'<h2[^>]*top-card-layout__headline[^>]*>(.*?)</h2>', page_html, re.DOTALL)
                hd = _clean_text(hd_m.group(1)) if hd_m else target_headline
                save_target_profile({
                    "profile_url": profile_url,
                    "name": found_name or target_name or slug.replace('-', ' ').title(),
                    "headline": hd,
                    "picture": found_photo
                })
            except Exception:
                pass

        # Safe diagnostic logging: compute newest and oldest extracted date bounds before filtering
        valid_dates = [to_date_obj(a.get("date")) for a in extracted_raw if to_date_obj(a.get("date"))]
        valid_dates = [d for d in valid_dates if d is not None]
        if valid_dates:
            newest = max(valid_dates).isoformat()
            oldest = min(valid_dates).isoformat()
            dbg(f"[DEBUG] Raw extracted activity date bounds: newest={newest}, oldest={oldest}, total={len(extracted_raw)}")
        else:
            newest = None
            oldest = None
            dbg(f"[DEBUG] Raw extracted activity date bounds: none available, total={len(extracted_raw)}")

        # Keep cache metadata synchronized with actual extracted date bounds
        if slug in _PROFILE_CACHE:
            _PROFILE_CACHE[slug]["oldest_activity_date"] = oldest
            _PROFILE_CACHE[slug]["reached_end_of_feed"] = reached_end_of_feed

        if start_date_obj or end_date_obj:
            dbg(f"[DEBUG] Date range filter applied: start={start_date_obj.isoformat() if start_date_obj else 'unbounded'}, end={end_date_obj.isoformat() if end_date_obj else 'unbounded'}")

        # Filter activities by date range and activity types
        filtered_activities: List[Dict[str, Any]] = []
        unknown_date_count = 0
        for act in extracted_raw:
            act_date_str = act.get("date")
            act_date_obj = to_date_obj(act_date_str)

            if act_date_obj is None:
                # Handle unknown or unparseable dates explicitly and consistently
                unknown_date_count += 1
                if not start_date_obj and not end_date_obj:
                    act["has_exact_date"] = False
                    filtered_activities.append(act)
                else:
                    act["has_exact_date"] = False
                continue

            # Inclusive date comparisons
            if start_date_obj and act_date_obj < start_date_obj:
                continue
            if end_date_obj and act_date_obj > end_date_obj:
                continue

            act_type = act.get("type", "post")
            is_vid = bool(
                act.get("is_video") or
                act.get("type") == "video" or
                act.get("video_url") or
                act.get("video_thumbnail") or
                (act.get("video_status") and act.get("video_status") != "No video")
            )
            if activity_types and "all" not in activity_types:
                act_norm = act_type.rstrip('s').lower()
                allowed = [k.rstrip('s').lower() for k in activity_types]
                matches = (act_norm in allowed or act_type in allowed) or ("video" in allowed and is_vid)
                if not matches:
                    continue

            filtered_activities.append(act)

        if unknown_date_count > 0:
            dbg(f"[DEBUG] Activities with unknown/unparseable dates: {unknown_date_count}")

        dbg(f"[DEBUG] activities after date filtering = {len(filtered_activities)}")

        total_raw = len(extracted_raw)
        video_activities = sum(
            1 for a in extracted_raw
            if a.get("is_video") or a.get("type") == "video" or a.get("video_url") or a.get("video_thumbnail") or (a.get("video_status") and a.get("video_status") != "No video")
        )
        video_likes = sum(
            1 for a in extracted_raw
            if a.get("type") == "like" and (a.get("is_video") or a.get("video_url") or a.get("video_thumbnail") or (a.get("video_status") and a.get("video_status") != "No video"))
        )
        if pw_logger:
            pw_logger.info(
                "Playwright extraction stats: raw_extracted=%d, video_activities=%d, video_likes=%d, date_filtered=%d, auth_session=%s",
                total_raw, video_activities, video_likes, len(filtered_activities), is_auth
            )
        dbg(f"[DEBUG] Playwright extraction stats: raw_extracted={total_raw}, video_activities={video_activities}, video_likes={video_likes}, date_filtered={len(filtered_activities)}, auth_session={is_auth}")

        # Check if the extracted data genuinely covers the start date
        if start_date_obj and valid_dates:
            min_date = min(valid_dates)
            if min_date <= start_date_obj:
                reached_start_date_boundary = True

        # Calculate retrieval completeness strictly according to contract:
        # COMPLETE: genuinely covered requested date range (reached_start_date_boundary)
        # OR feed's end has been verified (reached_end_of_feed) AND activity section was accessible.
        # Reaching scroll limit, ceiling, or encounters with an inaccessible section must NEVER mean COMPLETE.
        if hit_ceiling:
            retrieval_completeness = "PARTIAL_CEILING_REACHED"
        elif scroll_count >= effective_max_scrolls and not (reached_end_of_feed or reached_start_date_boundary):
            retrieval_completeness = "PARTIAL_CEILING_REACHED"
        elif not is_auth and not reached_end_of_feed and not reached_start_date_boundary:
            retrieval_completeness = "PARTIAL_GUEST_LIMIT"
        elif reached_start_date_boundary or reached_end_of_feed:
            retrieval_completeness = "COMPLETE"
        else:
            retrieval_completeness = "PARTIAL_CEILING_REACHED"

        # Case 1: Activities matched after filtering
        if filtered_activities:
            msg = f"Successfully retrieved {len(filtered_activities)} real LinkedIn activities."
            if retrieval_completeness == "PARTIAL_CEILING_REACHED":
                msg = f"Successfully retrieved {len(filtered_activities)} real LinkedIn activities (retrieval ceiling reached)."
            elif retrieval_completeness == "PARTIAL_GUEST_LIMIT":
                msg = f"Successfully retrieved {len(filtered_activities)} real LinkedIn activities (guest view limit reached)."
            return make_res(True, filtered_activities, msg, "SUCCESS_WITH_ACTIVITIES", retrieval_completeness)

        # Case 2: Activities exist on LinkedIn, but NONE match the date range or activity types
        if extracted_raw:
            has_video_in_raw = any(
                a.get("is_video") or a.get("type") == "video" or a.get("video_url") or a.get("video_thumbnail") or (a.get("video_status") and a.get("video_status") != "No video")
                for a in extracted_raw
            )
            is_video_filter = activity_types and ("all" not in activity_types) and any(k.rstrip('s').lower() == "video" for k in activity_types)

            if is_video_filter and not has_video_in_raw:
                dbg(f"[DEBUG] Retrieval result: No video activities found among {len(extracted_raw)} extracted activities")
                if not is_auth:
                    return make_res(False, [], "Video activities are inaccessible in public guest mode. An authenticated session is required to access member-level video interactions.", "VIDEO_ACTIVITIES_INACCESSIBLE", "UNAVAILABLE")
                return make_res(True, [], f"No video activities found for {display_name} in the selected period.", "NO_VIDEO_ACTIVITIES_FOUND", retrieval_completeness)

            dbg(f"[DEBUG] Retrieval failure reason: (B) No activity exists inside selected date range [{start_date} to {end_date}]")
            range_desc = f"{start_date_obj.isoformat()} to {end_date_obj.isoformat()}" if (start_date_obj and end_date_obj) else (f"from {start_date_obj.isoformat()}" if start_date_obj else f"up to {end_date_obj.isoformat()}")
            bounds_note = f" (Profile's latest activity was on {newest})" if newest else ""
            case2_completeness = "COMPLETE" if (reached_start_date_boundary or reached_end_of_feed) else retrieval_completeness
            return make_res(True, [], f"No activities found for selected date range ({range_desc}).{bounds_note}", "NO_ACTIVITIES_IN_RANGE", case2_completeness)

        # Case 3: Profile HTML was retrieved, but no activities found
        # Accurately distinguish missing/inaccessible activity section from a genuinely empty activity section
        if sec_count == 0 and not has_activity_sec:
            dbg("[DEBUG] Retrieval failure reason: Activity section not present in public page DOM")
            return make_res(False, [], "LinkedIn activity section is not accessible for this profile. An authenticated session is required to view activities.", "ACTIVITY_SECTION_UNAVAILABLE", "UNAVAILABLE")

        # Activity section was present in the DOM, but genuinely has 0 activity cards
        dbg("[DEBUG] Retrieval failure reason: (A) No public activity found")
        empty_completeness = "COMPLETE" if reached_end_of_feed else "INCOMPLETE"
        return make_res(False, [], "No public activity found for this profile on LinkedIn.", "NO_PUBLIC_ACTIVITIES", empty_completeness)
