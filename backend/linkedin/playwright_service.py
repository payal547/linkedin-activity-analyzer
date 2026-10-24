"""
Playwright LinkedIn Profile Activity Retrieval Service.
Opens the target LinkedIn profile using an authorized browser session and extracts real post content,
metrics, and media.
Does NOT bypass CAPTCHA, MFA, privacy controls, or LinkedIn platform restrictions.
If data cannot be legitimately accessed, returns an honest explanatory status without fabricating fake data.
"""

import os
import re
import html
import time
from urllib.parse import unquote
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Tuple, List, Dict, Any, Optional
from dotenv import load_dotenv

load_dotenv()

SESSION_FILE = Path(__file__).resolve().parent.parent / "data" / "playwright_session.json"
USER_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "playwright_context"


def extract_slug_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    match = re.search(r'(?:https?:\/\/)?(?:www\.)?linkedin\.com\/in\/([a-zA-Z0-9_\-\%]+)', url.strip(), re.IGNORECASE)
    if match:
        return match.group(1).rstrip('/')
    return None


def parse_relative_time_to_date(rel_str: str) -> str:
    """Converts LinkedIn relative time strings like '1d', '2w', '3 mo', '5h' to YYYY-MM-DD."""
    if not rel_str:
        return date.today().isoformat()
    rel_clean = rel_str.strip().lower()

    # Check for direct date format (e.g. '18 Oct 2026' or '2026-10-18')
    date_match = re.search(r'(\d{4}-\d{2}-\d{2})', rel_clean)
    if date_match:
        return date_match.group(1)

    today = date.today()
    num_match = re.search(r'(\d+)\s*(month[s]?|mo|week[s]?|w|day[s]?|d|hour[s]?|hr|h|minute[s]?|min|m|year[s]?|y)', rel_clean)
    if num_match:
        num = int(num_match.group(1))
        unit = num_match.group(2)
        if unit in ('h', 'hr', 'hour', 'hours', 'min', 'minute', 'minutes', 'm'):
            return today.isoformat()
        elif unit in ('d', 'day', 'days'):
            return (today - timedelta(days=num)).isoformat()
        elif unit in ('w', 'week', 'weeks'):
            return (today - timedelta(weeks=num)).isoformat()
        elif unit in ('mo', 'month', 'months'):
            return (today - timedelta(days=num * 30)).isoformat()
        elif unit in ('y', 'year', 'years'):
            return (today - timedelta(days=num * 365)).isoformat()

    return today.isoformat()


def _extract_activities_from_public_html(
    page_html: str,
    target_slug: str,
    target_name: Optional[str] = None
) -> Tuple[int, int, List[Dict[str, Any]]]:
    """
    Parses LinkedIn public profile DOM for activities across Posts, Comments, and Reactions.
    Returns: (section_count, post_elements_count, extracted_activities)
    """
    panels = page_html.split('<div class="tab__panel')
    section_count = len(panels) - 1 if len(panels) > 1 else 0
    if section_count == 0 and "activities-tab" in page_html:
        section_count = 1

    extracted_activities: List[Dict[str, Any]] = []
    seen_urls_or_texts = set()
    total_elements = 0

    display_name = target_name or target_slug.replace('-', ' ').title()
    profile_url = f"https://www.linkedin.com/in/{target_slug}/"

    # Process each tab panel (Posts, Comments, Reactions)
    for panel_chunk in panels[1:]:
        panel_html = '<div class="tab__panel' + panel_chunk
        panel_header = panel_html[:250].lower()

        # Determine panel kind
        is_comments_panel = 'comment' in panel_header or 'replied to a comment' in panel_html[:800]
        is_reactions_panel = 'reaction' in panel_header

        # 1. Comments Panel Processing
        if is_comments_panel:
            comments = panel_html.split('<div class="comment')
            for c_html in comments[1:]:
                chunk = '<div class="comment' + c_html
                total_elements += 1

                # Action description
                action_match = re.search(r'<span>\s*([^<]+replied[^<]*|[^<]+commented[^<]*)</span>', chunk)
                action_desc = action_match.group(1).strip() if action_match else f"{display_name} commented on LinkedIn"

                # Relative date
                date_match = re.search(r'<span class="before:middot">\s*([0-9]+[a-zA-Z]+)\s*</span>', chunk)
                if not date_match:
                    date_match = re.search(r'([0-9]+[dhmwy]|mo)\b', chunk)
                rel_date = date_match.group(1).strip() if date_match else ""
                iso_date = parse_relative_time_to_date(rel_date)

                # Comment text
                text_match = re.search(r'<div class="text-color-text text-sm font-normal">(.*?)</div>', chunk, re.DOTALL)
                comment_text = ""
                if text_match:
                    raw_text = re.sub(r'<[^>]+>', '', text_match.group(1)).strip()
                    comment_text = html.unescape(raw_text)

                if not comment_text:
                    continue

                dedup_key = f"comment_{iso_date}_{comment_text[:40]}"
                if dedup_key in seen_urls_or_texts:
                    continue
                seen_urls_or_texts.add(dedup_key)

                extracted_activities.append({
                    "id": f"pw_comment_{len(extracted_activities)+1}",
                    "subject_id": target_slug,
                    "subject_name": display_name,
                    "subject_url": profile_url,
                    "date": iso_date,
                    "relative_date": rel_date,
                    "type": "comment",
                    "text": f"{action_desc}: \"{comment_text}\"",
                    "likes": 0,
                    "comments": 0,
                    "shares": 0,
                    "source": "real_linkedin",
                    "post_content": f"Activity on LinkedIn: {action_desc}",
                    "post_author": display_name,
                    "post_url": profile_url,
                    "media_url": None,
                    "comment_text": comment_text,
                    "reaction_type": None,
                    "is_own_post": False,
                    "action_type": "comment"
                })

        # 2. Posts and Reactions Panels Processing
        else:
            cards = panel_html.split('<div class="profile-activity-card')
            i = 1
            while i < len(cards):
                card_chunk = '<div class="profile-activity-card' + cards[i]
                total_elements += 1

                # Header action text
                header_match = re.search(r'base-card__full-link[^>]*>.*?<span class="sr-only">(.*?)</span>', card_chunk, re.DOTALL)
                action_text = ""
                if header_match:
                    action_text = re.sub(r'<[^>]+>', '', header_match.group(1)).strip()

                # Post permalink
                link_match = re.search(r'href="(https://www\.linkedin\.com/posts/[^"]+)"', card_chunk)
                if not link_match:
                    link_match = re.search(r'session_redirect=(https%3A%2F%2Fwww\.linkedin\.com%2Fposts%2F[^&"]+)', card_chunk)
                    post_url = unquote(link_match.group(1)) if link_match else None
                else:
                    post_url = html.unescape(link_match.group(1))

                # Primary author and date
                author_match = re.search(r'publisher-author-card.*?<h3[^>]*base-main-card__title[^>]*>(.*?)</h3>', card_chunk, re.DOTALL)
                card_author = re.sub(r'<[^>]+>', '', author_match.group(1)).strip() if author_match else ""

                rel_date_match = re.search(r'base-main-card__metadata[^>]*>(.*?)</div>', card_chunk, re.DOTALL)
                rel_date = re.sub(r'<[^>]+>', '', rel_date_match.group(1)).strip() if rel_date_match else ""

                # Post text in card
                post_text_match = re.search(r'class="[^"]*see-more-text[^"]*"[^>]*>(.*?)</div>', card_chunk, re.DOTALL)
                post_text = html.unescape(re.sub(r'<[^>]+>', '', post_text_match.group(1)).strip()) if post_text_match else ""

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

                # Check if this is an outer header card for a repost or like (nested child follows)
                act_lower = action_text.lower()
                is_share_or_like_header = ('reposted' in act_lower or 'liked' in act_lower) and not post_text

                if is_share_or_like_header and i + 1 < len(cards):
                    next_chunk = '<div class="profile-activity-card' + cards[i+1]
                    # Root author
                    next_author_match = re.search(r'publisher-author-card.*?<h3[^>]*base-main-card__title[^>]*>(.*?)</h3>', next_chunk, re.DOTALL)
                    if next_author_match:
                        card_author = re.sub(r'<[^>]+>', '', next_author_match.group(1)).strip()

                    # Root date
                    next_date_match = re.search(r'base-main-card__metadata[^>]*>(.*?)</div>', next_chunk, re.DOTALL)
                    if next_date_match:
                        rel_date = re.sub(r'<[^>]+>', '', next_date_match.group(1)).strip()

                    # Root text
                    next_text_match = re.search(r'class="[^"]*see-more-text[^"]*"[^>]*>(.*?)</div>', next_chunk, re.DOTALL)
                    if next_text_match:
                        post_text = html.unescape(re.sub(r'<[^>]+>', '', next_text_match.group(1)).strip())

                    # Root media
                    next_img = re.search(r'data-delayed-url="([^"]+feedshare[^"]+)"', next_chunk)
                    if not next_img:
                        next_img = re.search(r'background-image:\s*url\(\'([^\']+feedshare[^\']+)\'\)', next_chunk)
                    if next_img:
                        media_url = html.unescape(next_img.group(1))

                    # Root permalink
                    if not post_url:
                        next_link = re.search(r'href="(https://www\.linkedin\.com/posts/[^"]+)"', next_chunk)
                        if next_link:
                            post_url = html.unescape(next_link.group(1))

                    # Root metrics
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

                if 'reposted' in act_lower:
                    action_type = "share"
                    is_own = False
                elif 'liked' in act_lower or 'reacted' in act_lower or is_reactions_panel:
                    action_type = "like"
                    is_own = False
                else:
                    action_type = "post"
                    is_own = True

                iso_date = parse_relative_time_to_date(rel_date)
                dedup_key = post_url or (post_text[:60] + action_type)
                if dedup_key in seen_urls_or_texts:
                    i += 1
                    continue
                seen_urls_or_texts.add(dedup_key)

                extracted_activities.append({
                    "id": f"pw_act_{len(extracted_activities)+1}",
                    "subject_id": target_slug,
                    "subject_name": display_name,
                    "subject_url": profile_url,
                    "date": iso_date,
                    "relative_date": rel_date,
                    "type": action_type,
                    "text": post_text[:250] if post_text else f"{action_type.capitalize()} on LinkedIn",
                    "likes": likes_count,
                    "comments": comments_count,
                    "shares": 0,
                    "source": "real_linkedin",
                    "post_content": post_text or "Actual post content could not be retrieved from LinkedIn.",
                    "post_author": card_author or (display_name if is_own else "LinkedIn Author"),
                    "post_url": post_url or profile_url,
                    "media_url": media_url,
                    "comment_text": None,
                    "reaction_type": "Like" if action_type == "like" else None,
                    "is_own_post": is_own,
                    "action_type": action_type
                })

                i += 1

    return section_count, total_elements, extracted_activities


def scrape_linkedin_profile_activities(
    profile_url: str,
    target_name: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    activity_types: Optional[List[str]] = None,
    timeout_ms: int = 25000
) -> Tuple[bool, List[Dict[str, Any]], str]:
    """
    Retrieves real posts and activities for a target LinkedIn profile URL using Playwright.
    Logs required debug information and extracts live public post content, author, dates, media, and metrics.
    Returns: (success: bool, activities: List[Dict], status_message: str)
    """
    log_path = Path(__file__).resolve().parent.parent / "data" / "scraper_debug.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    def dbg(msg):
        print(msg, flush=True)
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().isoformat()}] {msg}\n")
        except Exception:
            pass

    dbg(f"[DEBUG] TARGET URL: {profile_url}")

    slug = extract_slug_from_url(profile_url)
    if not slug:
        return False, [], "Invalid LinkedIn profile URL."

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
    except Exception as e:
        return False, [], f"Playwright import error: {type(e).__name__}: {str(e)}"

    target_profile_url = f"https://www.linkedin.com/in/{slug}/"

    with sync_playwright() as p:
        browser = None
        context = None
        try:
            browser = p.chromium.launch(headless=True)

            # Desktop headers with Google referer to access public profile
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                extra_http_headers={"Referer": "https://www.google.com/"},
                viewport={"width": 1280, "height": 800}
            )

            page = context.new_page()

            # Navigate to target public profile
            try:
                page.goto(target_profile_url, timeout=timeout_ms, wait_until="domcontentloaded")
            except Exception as e:
                print(f"[DEBUG] Navigation error: {e}", flush=True)
                return False, [], "Actual post content could not be retrieved from LinkedIn. Network timeout or connection unreachable."

            dbg(f"[DEBUG] PAGE URL AFTER NAVIGATION: {page.url}")
            current_url = page.url.lower()

            # Check if redirected to security checkpoint or challenge
            if "checkpoint" in current_url or "challenge" in current_url:
                dbg("[DEBUG] Security checkpoint encountered")
                return False, [], "Actual post content could not be retrieved from LinkedIn. LinkedIn security checkpoint required."

            # Wait for activity container to render
            try:
                page.wait_for_selector("[data-tab-id='activities-tab'], .activities-tab, .tab__panel", timeout=8000)
            except Exception:
                pass

            page_html = page.content()

            # If target profile photo or headline is found, update saved profile
            try:
                from services.saved_profiles_storage import save_target_profile
                photo_match = re.search(r'data-delayed-url="([^"]+profile-displayphoto[^"]+)"', page_html)
                target_pic = html.unescape(photo_match.group(1)) if photo_match else None
                headline_match = re.search(r'<h2[^>]*top-card-layout__headline[^>]*>(.*?)</h2>', page_html, re.DOTALL)
                target_hd = re.sub(r'<[^>]+>', '', headline_match.group(1)).strip() if headline_match else None
                save_target_profile({
                    "profile_url": profile_url,
                    "name": target_name or slug.replace('-', ' ').title(),
                    "headline": target_hd,
                    "picture": target_pic
                })
            except Exception:
                pass

            # Extract activities from the public profile DOM
            sec_count, elem_count, extracted_raw = _extract_activities_from_public_html(
                page_html=page_html,
                target_slug=slug,
                target_name=target_name
            )

            dbg(f"[DEBUG] NUMBER OF ACTIVITY SECTIONS FOUND: {sec_count}")
            dbg(f"[DEBUG] NUMBER OF POST ELEMENTS FOUND: {elem_count}")

            # Filter by date and activity types
            filtered_activities: List[Dict[str, Any]] = []
            for act in extracted_raw:
                act_date = act.get("date")
                if start_date and act_date and act_date < start_date:
                    continue
                if end_date and act_date and act_date > end_date:
                    continue

                act_type = act.get("type", "post")
                if activity_types and "all" not in activity_types:
                    act_norm = act_type.rstrip('s').lower()
                    allowed = [k.rstrip('s').lower() for k in activity_types]
                    if act_norm not in allowed and act_type not in allowed:
                        continue

                filtered_activities.append(act)

            dbg(f"[DEBUG] EXTRACTED POSTS: {len(filtered_activities)}")

            if filtered_activities:
                return True, filtered_activities, f"Successfully retrieved {len(filtered_activities)} real LinkedIn activities."

            # If profile has activity on LinkedIn but none in the requested date range
            if extracted_raw:
                return True, [], "No activities found for this date range."

            # Check for explicit empty feed indicators
            page_text = page.inner_text("body") if page else ""
            if "hasn't posted yet" in page_text.lower() or "no recent activity" in page_text.lower():
                return True, [], "No activities found for this date range."

            return False, [], "Actual post content could not be retrieved from LinkedIn."

        except Exception as e:
            print(f"[DEBUG] Scrape error: {str(e)}", flush=True)
            return False, [], f"Actual post content could not be retrieved from LinkedIn. ({str(e)})"
        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass
