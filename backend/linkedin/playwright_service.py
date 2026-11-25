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
from contextlib import contextmanager
from urllib.parse import unquote, urlparse
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
    with 3-element tuple unpacking, while providing typed status_type, retrieval_completeness,
    and granular activity tracking attributes.
    """
    def __new__(
        cls,
        success: bool,
        activities: List[Dict[str, Any]],
        message: str,
        status_type: str = "",
        retrieval_completeness: str = "INCOMPLETE",
        activities_discovered: int = 0,
        activities_parsed: int = 0,
        duplicates_removed: int = 0,
        activities_filtered: int = 0,
        activities_saved: int = 0,
        earliest_retrieved_date: Optional[str] = None,
        latest_retrieved_date: Optional[str] = None,
        requested_date_range: Optional[Dict[str, Any]] = None,
        retrieved_date_range: Optional[Dict[str, Any]] = None
    ):
        instance = super().__new__(cls, (success, activities, message))
        instance.success = success
        instance.activities = activities
        instance.message = message
        instance.status_type = status_type
        instance.retrieval_completeness = retrieval_completeness
        instance.activities_discovered = activities_discovered
        instance.activities_parsed = activities_parsed
        instance.duplicates_removed = duplicates_removed
        instance.activities_filtered = activities_filtered
        instance.activities_saved = activities_saved
        instance.earliest_retrieved_date = earliest_retrieved_date
        instance.latest_retrieved_date = latest_retrieved_date
        instance.requested_date_range = requested_date_range or {}
        instance.retrieved_date_range = retrieved_date_range or {}
        return instance


class ExtractionResult(tuple):
    """
    Subclass of 3-tuple (section_count, total_elements, extracted_activities) for 100% backward compatibility
    with callers unpacking 3 items, while providing parsed_count and duplicates_count.
    """
    def __new__(cls, section_count: int, total_elements: int, extracted_activities: List[Dict[str, Any]], parsed_count: int = 0, duplicates_count: int = 0):
        obj = super().__new__(cls, (section_count, total_elements, extracted_activities))
        obj.section_count = section_count
        obj.total_elements = total_elements
        obj.extracted_activities = extracted_activities
        obj.parsed_count = parsed_count
        obj.duplicates_count = duplicates_count
        return obj


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


def get_active_chrome_cdp_ws_url(port: int = 9222) -> Optional[str]:
    """
    Discovers the active Google Chrome DevTools Protocol WebSocket URL from DevToolsActivePort or /json/version.
    Complies with Chrome 144+/157 User-Approved Remote Debugging architecture.
    Returns: WebSocket URL (e.g. 'ws://127.0.0.1:9222/devtools/browser/<guid>') or None.
    NEVER logs or exposes the exact browser GUID externally.
    """
    candidate_dirs = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidate_dirs.append(Path(local_app_data) / "Google" / "Chrome" / "User Data")
    user_prof = os.environ.get("USERPROFILE")
    if user_prof:
        candidate_dirs.append(Path(user_prof) / "AppData" / "Local" / "Google" / "Chrome" / "User Data")
    try:
        candidate_dirs.append(Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "User Data")
    except Exception:
        pass

    for u_dir in candidate_dirs:
        active_port_file = u_dir / "DevToolsActivePort"
        if active_port_file.exists():
            try:
                lines = active_port_file.read_text(encoding="utf-8", errors="ignore").splitlines()
                if len(lines) >= 2:
                    p_str = lines[0].strip()
                    browser_path = lines[1].strip()
                    if p_str.isdigit() and browser_path.startswith("/devtools/browser/"):
                        # Verify port is listening locally
                        import socket
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.settimeout(0.8)
                        result = sock.connect_ex(("127.0.0.1", int(p_str)))
                        sock.close()
                        if result == 0:
                            return f"ws://127.0.0.1:{p_str}{browser_path}"
            except Exception:
                pass

    # Direct /json/version check (works when Chrome is launched with --remote-debugging-port)
    ports_to_try = [port]
    env_port = os.environ.get("CHROME_CDP_PORT")
    if env_port and env_port.isdigit() and int(env_port) not in ports_to_try:
        ports_to_try.insert(0, int(env_port))
    if 9222 not in ports_to_try:
        ports_to_try.append(9222)

    for p in ports_to_try:
        for host in ("127.0.0.1", "localhost"):
            try:
                import urllib.request
                import json as _json
                req = urllib.request.Request(f"http://{host}:{p}/json/version", headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=1.0) as resp:
                    if resp.status == 200:
                        v_data = _json.loads(resp.read().decode("utf-8"))
                        ws_url = v_data.get("webSocketDebuggerUrl")
                        if ws_url and ws_url.startswith("ws://"):
                            return ws_url
                        return f"http://{host}:{p}"
            except Exception:
                continue

    return None


def is_cdp_available(port: int = 9222, timeout: float = 1.0) -> bool:
    """Checks whether Google Chrome is actively running with remote debugging enabled."""
    ws_url = get_active_chrome_cdp_ws_url(port=port)
    if ws_url:
        return True
    for host in ("127.0.0.1", "localhost"):
        try:
            import urllib.request
            with urllib.request.urlopen(f"http://{host}:{port}/json/version", timeout=timeout) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            continue
    return False


def _detect_chrome_profiles_hint() -> str:
    """
    Inspects Chrome's Local State to detect configured user profiles and accounts.
    Provides helpful, actionable guidance if the user has multiple profiles.
    NEVER logs or exposes passwords or private tokens.
    """
    candidate_dirs = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidate_dirs.append(Path(local_app_data) / "Google" / "Chrome" / "User Data")
    user_prof = os.environ.get("USERPROFILE")
    if user_prof:
        candidate_dirs.append(Path(user_prof) / "AppData" / "Local" / "Google" / "Chrome" / "User Data")
    try:
        candidate_dirs.append(Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "User Data")
    except Exception:
        pass

    for u_dir in candidate_dirs:
        local_state_file = u_dir / "Local State"
        if local_state_file.exists():
            try:
                data = json.loads(local_state_file.read_text(encoding="utf-8", errors="ignore"))
                info_cache = data.get("profile", {}).get("info_cache", {})
                last_used = data.get("profile", {}).get("last_used", "Default")
                if len(info_cache) > 1:
                    profiles_list = []
                    for prof_dir, p_info in info_cache.items():
                        prof_name = p_info.get("name") or prof_dir
                        user_name = p_info.get("user_name") or ""
                        is_active = (prof_dir == last_used)
                        tag = " (active)" if is_active else ""
                        if user_name:
                            profiles_list.append(f"{prof_dir} ['{prof_name}' - {user_name}]{tag}")
                        else:
                            profiles_list.append(f"{prof_dir} ['{prof_name}']{tag}")
                    profiles_str = ", ".join(profiles_list)
                    other_dirs = [p for p in info_cache.keys() if p != last_used]
                    hint = f"Chrome profiles detected: {profiles_str}."
                    if other_dirs:
                        hint += f" If your LinkedIn is logged in under another profile (e.g. {other_dirs[0]}), launch Chrome with: chrome.exe --remote-debugging-port=9222 --profile-directory=\"{other_dirs[0]}\""
                    return hint
            except Exception:
                pass
    return ""


def find_authenticated_cdp_context(browser) -> Tuple[Optional[Any], Dict[str, Any]]:
    """
    Inspects all contexts, existing open tabs, and browser-level CDP cookie stores
    in the CDP-connected Chrome browser to find the authenticated LinkedIn session
    without modifying, reloading, or navigating away from existing tabs.
    Returns: (authenticated_context, metadata_dict)
    """
    contexts = getattr(browser, "contexts", [])
    meta = {
        "context_index": -1,
        "total_contexts": len(contexts),
        "has_active_tab": False,
        "active_tab_url": None,
        "active_tab_title": None,
        "has_li_at": False,
        "cookie_source": None,
        "total_tabs": sum(len(getattr(c, "pages", [])) for c in contexts),
        "tab_urls": []
    }
    if not contexts:
        return None, meta

    # Collect tab URLs across all contexts for diagnostics
    for idx, ctx in enumerate(contexts):
        for p in getattr(ctx, "pages", []):
            try:
                p_url = getattr(p, "url", "") or ""
                p_title = ""
                try:
                    p_title = p.title()
                except Exception:
                    pass
                meta["tab_urls"].append({"context": idx, "url": p_url, "title": p_title})
            except Exception:
                pass

    best_context = None

    # Step 1: Check existing open tabs across all contexts for active LinkedIn tabs
    for idx, ctx in enumerate(contexts):
        pages = getattr(ctx, "pages", [])
        for p in pages:
            try:
                p_url = (getattr(p, "url", "") or "").lower()
                if "linkedin.com" in p_url:
                    if not any(x in p_url for x in ("login", "signup", "authwall", "checkpoint", "uas/login")):
                        meta["context_index"] = idx
                        meta["has_active_tab"] = True
                        meta["active_tab_url"] = getattr(p, "url", None)
                        try:
                            meta["active_tab_title"] = p.title()
                        except Exception:
                            pass
                        best_context = ctx
                        break
            except Exception:
                pass
        if meta["has_active_tab"]:
            break

    # Step 2: Query cookies using browser-level CDP session (bypasses page origin isolation)
    browser_has_li_at = False
    try:
        if hasattr(browser, "new_browser_cdp_session"):
            cdp_b = browser.new_browser_cdp_session()
            # 2a. Storage.getCookies returns all cookies in the browser profile
            try:
                storage_res = cdp_b.send("Storage.getCookies")
                raw_cookies = storage_res.get("cookies") if isinstance(storage_res, dict) else []
                if isinstance(raw_cookies, list):
                    for c in raw_cookies:
                        if isinstance(c, dict) and c.get("name") == "li_at" and c.get("value"):
                            dom = (c.get("domain") or "").lower()
                            if "linkedin.com" in dom or not dom:
                                exp = c.get("expires", -1)
                                if exp <= 0 or exp > time.time():
                                    browser_has_li_at = True
                                    meta["cookie_source"] = "cdp_browser_storage"
                                    break
            except Exception as es:
                if pw_logger:
                    pw_logger.debug("Storage.getCookies CDP: %s", es)

            # 2b. Network.getAllCookies at browser level
            if not browser_has_li_at:
                try:
                    net_res = cdp_b.send("Network.getAllCookies")
                    raw_net = net_res.get("cookies") if isinstance(net_res, dict) else []
                    if isinstance(raw_net, list):
                        for c in raw_net:
                            if isinstance(c, dict) and c.get("name") == "li_at" and c.get("value"):
                                dom = (c.get("domain") or "").lower()
                                if "linkedin.com" in dom or not dom:
                                    exp = c.get("expires", -1)
                                    if exp <= 0 or exp > time.time():
                                        browser_has_li_at = True
                                        meta["cookie_source"] = "cdp_browser_network"
                                        break
                except Exception as en:
                    if pw_logger:
                        pw_logger.debug("Network.getAllCookies CDP: %s", en)
    except Exception as ec:
        if pw_logger:
            pw_logger.debug("new_browser_cdp_session check: %s", ec)

    if browser_has_li_at:
        meta["has_li_at"] = True
        if best_context is None and contexts:
            best_context = contexts[0]
            meta["context_index"] = 0

    # Step 3: Check context and page-level CDP sessions if browser-level didn't find li_at
    if not meta["has_li_at"]:
        for idx, ctx in enumerate(contexts):
            pages = getattr(ctx, "pages", [])
            for p in pages:
                try:
                    if hasattr(ctx, "new_cdp_session"):
                        p_cdp = ctx.new_cdp_session(p)
                        p_res = p_cdp.send("Network.getCookies", {
                            "urls": ["https://www.linkedin.com", "https://linkedin.com", "https://www.linkedin.com/feed/"]
                        })
                        p_cookies = p_res.get("cookies") if isinstance(p_res, dict) else []
                        if isinstance(p_cookies, list):
                            for c in p_cookies:
                                if isinstance(c, dict) and c.get("name") == "li_at" and c.get("value"):
                                    dom = (c.get("domain") or "").lower()
                                    if "linkedin.com" in dom or not dom:
                                        exp = c.get("expires", -1)
                                        if exp <= 0 or exp > time.time():
                                            meta["has_li_at"] = True
                                            meta["cookie_source"] = f"cdp_page_ctx_{idx}"
                                            best_context = ctx
                                            meta["context_index"] = idx
                                            break
                            if meta["has_li_at"]:
                                break
                except Exception:
                    pass
            if meta["has_li_at"]:
                break

    # Step 4: Fallback to ctx.cookies() (for unit tests / mock contexts)
    if not meta["has_li_at"]:
        for idx, ctx in enumerate(contexts):
            try:
                cookies = []
                try:
                    cookies = ctx.cookies(["https://www.linkedin.com", "https://linkedin.com"])
                except Exception:
                    pass
                if not cookies:
                    try:
                        cookies = ctx.cookies()
                    except Exception:
                        pass
                if isinstance(cookies, list):
                    li_at_cookie = next(
                        (c for c in cookies if isinstance(c, dict) and c.get("name") == "li_at" and c.get("value") and ("linkedin.com" in (c.get("domain") or "").lower() or not c.get("domain"))),
                        None
                    )
                    if not li_at_cookie:
                        li_at_cookie = next((c for c in cookies if isinstance(c, dict) and c.get("name") == "li_at" and c.get("value")), None)
                    if li_at_cookie:
                        exp = li_at_cookie.get("expires", -1)
                        if exp <= 0 or exp > time.time():
                            meta["has_li_at"] = True
                            meta["cookie_source"] = f"ctx_cookies_{idx}"
                            best_context = ctx
                            meta["context_index"] = idx
                            break
            except Exception:
                pass

    if not (meta["has_active_tab"] or meta["has_li_at"]):
        best_context = None
    elif best_context is None and contexts:
        best_context = contexts[0]
        meta["context_index"] = 0

    return best_context, meta


def validate_cdp_session(port: int = 9222) -> Tuple[str, bool, str]:
    """
    Validates whether the active Chrome instance (via CDP) has a live authenticated LinkedIn session.
    Directly inspects existing Chrome contexts, cookies, and open tabs without navigating away
    from or closing any existing user tabs.
    Returns: (status: 'VALID' | 'EXPIRED' | 'MISSING' | 'ERROR', is_valid: bool, message: str)
    """
    ws_url = get_active_chrome_cdp_ws_url(port=port) or f"http://127.0.0.1:{port}"
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            try:
                browser = p.chromium.connect_over_cdp(ws_url, timeout=12000)
            except Exception as ce:
                return "MISSING", False, f"Could not connect to Chrome CDP on {ws_url}: {ce}"

            auth_ctx, meta = find_authenticated_cdp_context(browser)

            # NOTE: We do NOT call browser.close() because that closes the user's running Chrome browser!

            if auth_ctx is not None and (meta.get("has_li_at") or meta.get("has_active_tab")):
                details = []
                if meta.get("has_li_at"):
                    details.append(f"session cookie verified ({meta.get('cookie_source') or 'CDP'})")
                if meta.get("has_active_tab"):
                    tab_label = meta.get("active_tab_url") or "active tab"
                    details.append(f"active LinkedIn tab open [{tab_label}]")
                detail_str = ", ".join(details)
                return "VALID", True, f"Authenticated session confirmed active via existing Chrome session (CDP, context #{meta['context_index']}, {detail_str})"
            elif meta.get("total_contexts", 0) > 0:
                profile_hint = _detect_chrome_profiles_hint()
                tab_summary = ""
                if meta.get("tab_urls"):
                    urls_short = [t.get("url", "")[:45] for t in meta.get("tab_urls")[:3] if t.get("url")]
                    if urls_short:
                        tab_summary = f" (open tabs: {', '.join(urls_short)})"
                msg = f"LinkedIn is not currently logged in on the active Chrome profile (found {meta.get('total_contexts')} context(s), {meta.get('total_tabs')} tab(s){tab_summary})."
                if profile_hint:
                    msg += f" {profile_hint}"
                else:
                    msg += " Please log into LinkedIn in this Chrome window and retry."
                return "EXPIRED", False, msg
            else:
                return "MISSING", False, "No active browser context found in Chrome CDP."
    except Exception as e:
        return "MISSING", False, f"CDP connection check failed: {str(e)}"


def validate_session_state() -> Tuple[str, bool, str]:
    """
    Validates whether an authenticated session is available and valid.
    1. First checks Chrome DevTools Protocol (CDP) on port 9222 for live Chrome session reuse.
    2. Falls back to SESSION_FILE or LINKEDIN_LI_AT in .env.
    NEVER logs passwords or cookie values.
    Returns: (status: 'VALID' | 'EXPIRED' | 'MISSING', is_valid: bool, message: str)
    """
    # 1. Primary: Chrome DevTools Protocol (CDP) reuse
    if is_cdp_available(port=9222):
        cdp_status, cdp_valid, cdp_msg = validate_cdp_session(port=9222)
        if cdp_valid:
            return cdp_status, cdp_valid, cdp_msg
        if cdp_status in ("EXPIRED", "ERROR"):
            return cdp_status, cdp_valid, cdp_msg

    # 2. Secondary: Storage state file or environment variable
    li_at_env = os.getenv("LINKEDIN_LI_AT") or os.getenv("LI_AT")
    if li_at_env and not SESSION_FILE.exists():
        try:
            SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
            storage_data = {
                "cookies": [
                    {
                        "name": "li_at",
                        "value": li_at_env.strip(),
                        "domain": ".linkedin.com",
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
        return "MISSING", False, "LinkedIn authentication/session is unavailable. Launch Chrome with --remote-debugging-port=9222 to connect to your existing session."

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
    # Strip visually-hidden elements first to avoid duplicate text from screen-reader spans
    cleaned = re.sub(r'<span[^>]*class="[^"]*visually-hidden[^"]*"[^>]*>.*?</span>', '', raw_html, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', ' ', cleaned)
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
        r'<video\b|class="[^"]*(?:feedshare-video|video-js|vjs-tech|feed-shared-linkedin-video|update-components-linkedin-video|artdeco-video|video-thumbnail|update-components-video|feed-shared-video|feedshare-video__preview|feedshare-video__player|feedshare-video-container|profile-activity-card__video|artdeco-video-player|video-player)[^"]*"|data-sources=|data-stream-url=|urn:li:video:|aria-label="Play video"|aria-label="Watch video"|aria-label="Play LinkedIn Video"|class="[^"]*(?:play-button|vjs-play-control|vjs-big-play-button)[^"]*"|data-control-name="play_video"|<iframe[^>]+(?:youtube|vimeo)',
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

    # Chromium can resolve a playable source at runtime without reflecting it
    # into the serialized src attribute. The extraction page annotates only
    # browser-provided direct media sources; never infer a stream from a post
    # permalink or use blob/MSE URLs.
    if not video_url:
        runtime_src_m = re.search(r'data-analyzer-playback-src="([^"]+)"', html_chunk, re.IGNORECASE)
        if runtime_src_m:
            candidate = html.unescape(runtime_src_m.group(1)).strip()
            runtime_type_m = re.search(r'data-analyzer-playback-type="([^"]+)"', html_chunk, re.IGNORECASE)
            runtime_type = html.unescape(runtime_type_m.group(1)).split(";", 1)[0].strip().lower() if runtime_type_m else ""
            try:
                parsed_candidate = urlparse(candidate)
                host = (parsed_candidate.hostname or "").lower()
                suffix = os.path.splitext(parsed_candidate.path.lower())[1]
                supported = {
                    ".mp4": "video/mp4", ".m4v": "video/mp4",
                    ".webm": "video/webm", ".ogg": "video/ogg", ".ogv": "video/ogg",
                }
                candidate_mime = runtime_type if runtime_type in set(supported.values()) else supported.get(suffix, "")
                is_linkedin_page = host == "linkedin.com" or host.endswith(".linkedin.com")
                if (parsed_candidate.scheme == "https" and not is_linkedin_page and candidate_mime
                        and not candidate.lower().startswith(("blob:", "data:"))):
                    video_url = candidate
                    has_video = True
            except (TypeError, ValueError):
                pass

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

    # A LinkedIn post/video page is a permalink, never a media stream. Keep it
    # only in post_url; the frontend must never receive it as video_url.
    if video_url:
        try:
            parsed_video_url = urlparse(video_url)
            video_host = (parsed_video_url.hostname or "").lower()
            if video_host == "linkedin.com" or video_host.endswith(".linkedin.com"):
                video_url = None
        except ValueError:
            video_url = None

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


def _infer_video_mime_type(video_url: Optional[str], html_chunk: str = "") -> Optional[str]:
    if not video_url:
        return None
    runtime_type_m = re.search(r'data-analyzer-playback-type="([^"]+)"', html_chunk, re.IGNORECASE)
    source_type_m = re.search(r'<(?:source|video)\b[^>]*\btype="([^"]+)"', html_chunk, re.IGNORECASE)
    type_match = runtime_type_m or source_type_m
    runtime_type = html.unescape(type_match.group(1)).split(";", 1)[0].strip().lower() if type_match else ""
    supported = {"video/mp4", "video/webm", "video/ogg"}
    if runtime_type in supported:
        return runtime_type
    suffix = os.path.splitext(urlparse(video_url).path.lower())[1]
    return {
        ".mp4": "video/mp4", ".m4v": "video/mp4",
        ".webm": "video/webm", ".ogg": "video/ogg", ".ogv": "video/ogg",
    }.get(suffix)


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
                real_title_name = re.sub(r'(?:[’\']s\s+Activity|^Activity\s*\|\s*)', '', real_title_name, flags=re.IGNORECASE).strip()
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
    parsed_count = 0
    duplicates_count = 0

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

                parsed_count += 1
                dedup_key = f"comment_{iso_date}_{comment_text[:40]}"
                if dedup_key in seen_keys:
                    duplicates_count += 1
                    continue
                seen_keys.add(dedup_key)

                act_uid = f"act_cmt_{clean_slug}_{abs(hash(comment_text[:60] + str(iso_date)))}"
                extracted_activities.append({
                    "id": act_uid,
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
                    "storage_origin": "live_retrieval",
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
                    "video_mime_type": _infer_video_mime_type(video_url, chunk),
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
                parsed_count += 1
                dedup_key = clean_post_url or (post_text[:60] + action_type)
                if dedup_key in seen_keys:
                    duplicates_count += 1
                    i += 1
                    continue
                seen_keys.add(dedup_key)

                act_uid = f"act_post_{clean_slug}_{abs(hash((clean_post_url or post_text[:60]) + action_type))}"
                act_headline_desc = f"{action_desc}: \"{post_text[:120]}\"" if post_text else action_desc
                original_author = card_author or (display_name if is_own else "LinkedIn Author")

                extracted_activities.append({
                    "id": act_uid,
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
                    "storage_origin": "live_retrieval",
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
                    "video_mime_type": _infer_video_mime_type(video_url, combined_chunk),
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
    # 3. Authenticated / Modern Feed Update Processing
    # ----------------------------------------------------
    if len(extracted_activities) == 0 and any(k in page_html for k in ("feed-shared-update-v2", "urn:li:activity", "feed-full-update", "profile-creator-shared-feed-update__container", "update-components-actor", "recent-activity")):
        # Support modern LinkedIn DOM: <li class="profile-creator-shared-feed-update__container">,
        # <div data-view-name="feed-full-update">, <div class="feed-shared-update-v2">,
        # <li/div data-urn="urn:li:activity:...">, <li class="artdeco-list__item">
        feed_cards = []
        candidate_split_patterns = [
            r'<(?:li|div)[^>]*class="[^"]*profile-creator-shared-feed-update__container[^"]*"[^>]*>',
            r'<(?:div|li)[^>]*data-view-name="feed-full-update"[^>]*>',
            r'<(?:div|li)[^>]*class="[^"]*(?:^|\s)feed-shared-update-v2(?:\s|")[^>]*>',
            r'<(?:div|li)[^>]*data-urn="urn:li:(?:activity|ugcPost|share):[^"]*"[^>]*>',
            r'<li[^>]*class="[^"]*artdeco-list__item[^"]*"[^>]*>'
        ]
        for pat in candidate_split_patterns:
            chunks = re.split(pat, page_html)
            if len(chunks) > 1:
                feed_cards = chunks
                break

        if len(feed_cards) > 1:
            section_count = max(section_count, 1)

        for f_chunk in feed_cards[1:]:
            total_elements += 1
            card_html = f_chunk

            # Context bar (e.g., "X reposted this", "X likes this")
            context_m = re.search(r'class="[^"]*(?:feed-shared-update-v2__context-bar|update-components-header|feed-shared-header)[^"]*"[^>]*>(.*?)(?:</div>|</section>|</header>)', card_html, re.DOTALL)
            if not context_m:
                context_m = re.search(r'class="[^"]*update-components-header__text-view[^"]*"[^>]*>(.*?)</span>', card_html, re.DOTALL)
            context_text = _clean_text(context_m.group(1)) if context_m else ""
            c_lower = context_text.lower()

            # Actor & original author
            actor_m = re.search(r'class="[^"]*(?:update-components-actor__name|feed-shared-actor__name|profile-creator-shared-feed-update__actor-name)[^"]*"[^>]*>(.*?)(?:</div>|</span>|</h4>|</h3>)', card_html, re.DOTALL)
            if not actor_m:
                actor_m = re.search(r'<span[^>]*class="[^"]*update-components-actor__title[^"]*"[^>]*>(.*?)</span>', card_html, re.DOTALL)
            card_author = _clean_text(actor_m.group(1)) if actor_m else ""

            author_hd_m = re.search(r'class="[^"]*(?:update-components-actor__description|feed-shared-actor__description)[^"]*"[^>]*>(.*?)(?:</div>|</span>|</p>)', card_html, re.DOTALL)
            card_author_headline = _clean_text(author_hd_m.group(1)) if author_hd_m else ""

            author_img_m = re.search(r'class="[^"]*(?:update-components-actor__avatar-image|feed-shared-actor__avatar-image)[^"]*"[^>]*src="([^"]+)"', card_html)
            if not author_img_m:
                author_img_m = re.search(r'class="[^"]*(?:update-components-actor__avatar-image|feed-shared-actor__avatar-image)[^"]*"[^>]*data-delayed-url="([^"]+)"', card_html)
            card_author_photo = html.unescape(author_img_m.group(1)) if author_img_m else None

            # Date
            rel_date = ""
            time_attr_m = re.search(r'<time[^>]*datetime="([^"]+)"', card_html)
            if time_attr_m:
                rel_date = time_attr_m.group(1).strip()
            if not rel_date:
                time_tag_m = re.search(r'<time[^>]*>(.*?)</time>', card_html, re.DOTALL)
                if time_tag_m:
                    rel_date = _clean_text(time_tag_m.group(1))
            if not rel_date:
                date_m = re.search(r'class="[^"]*(?:update-components-actor__sub-description|feed-shared-actor__sub-description)[^"]*"[^>]*>(.*?)(?:</div>|</span>|</p>)', card_html, re.DOTALL)
                if date_m:
                    rel_date = _clean_text(date_m.group(1))

            # Clean trailing middot or "Edited"
            if rel_date:
                rel_date = re.sub(r'[•·]\s*(?:edited)?.*$', '', rel_date, flags=re.I).strip()

            # Content / description
            post_text = ""
            for pat in [
                r'class="[^"]*(?:feed-shared-update-v2__description|update-components-text|feed-shared-text|feed-shared-inline-show-more-text)[^"]*"[^>]*>(.*?)</div>',
                r'<span[^>]*class="[^"]*break-words[^"]*"[^>]*>(.*?)</span>',
                r'class="[^"]*see-more-text[^"]*"[^>]*>(.*?)</div>'
            ]:
                t_m = re.search(pat, card_html, re.DOTALL)
                if t_m:
                    cand = _clean_text(t_m.group(1))
                    if len(cand) > len(post_text):
                        post_text = cand

            # Post URL
            post_url = None
            urn_m = re.search(r'urn:li:(?:activity|ugcPost|share):(\d+)', card_html)
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
            img_m = re.search(r'class="[^"]*(?:feed-shared-image|update-components-image|feed-shared-article__image)[^"]*"[^>]*src="([^"]+)"', card_html)
            if not img_m:
                img_m = re.search(r'class="[^"]*(?:feed-shared-image|update-components-image|feed-shared-article__image)[^"]*"[^>]*data-delayed-url="([^"]+)"', card_html)
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
                if not rx_count_m:
                    rx_count_m = re.search(r'class="[^"]*social-details-social-counts__reactions[^"]*"[^>]*aria-label="([0-9,]+)', card_html)
                if rx_count_m:
                    try:
                        likes_count = int(rx_count_m.group(1).replace(',', ''))
                    except ValueError:
                        pass

            cm_m = re.search(r'data-num-comments="(\d+)"', card_html)
            comments_count = int(cm_m.group(1)) if cm_m else 0
            if comments_count == 0:
                cm_count_m = re.search(r'social-details-social-counts__comments[^>]*>([0-9,]+)', card_html)
                if not cm_count_m:
                    cm_count_m = re.search(r'aria-label="([0-9,]+)\s+comment', card_html)
                if cm_count_m:
                    try:
                        comments_count = int(cm_count_m.group(1).replace(',', ''))
                    except ValueError:
                        pass

            iso_date, is_exact = parse_relative_time_to_date(rel_date)
            parsed_count += 1
            urn_id = urn_m.group(1) if urn_m else None
            dedup_key = f"{action_type}_{urn_id}" if urn_id else (clean_post_url or (post_text[:60] + action_type))
            if dedup_key in seen_keys:
                duplicates_count += 1
                continue
            seen_keys.add(dedup_key)

            act_uid = f"act_urn_{urn_id}_{action_type}" if urn_id else f"act_feed_{clean_slug}_{abs(hash((clean_post_url or post_text[:60]) + action_type))}"
            act_headline_desc = f"{action_desc}: \"{post_text[:120]}\"" if post_text else action_desc
            original_author = card_author or (display_name if is_own else "LinkedIn Author")

            extracted_activities.append({
                "id": act_uid,
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
                "storage_origin": "live_retrieval",
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
                "video_mime_type": _infer_video_mime_type(video_url, card_html),
                "video_thumbnail": video_thumbnail,
                "video_status": video_status,
                "is_video": is_video,
                "comment_text": post_text if action_type == "comment" else None,
                "reaction_type": "Like" if action_type == "like" else None,
                "is_own_post": is_own,
                "action_type": action_type
            })

    return ExtractionResult(section_count, total_elements, extracted_activities, parsed_count, duplicates_count)


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

    def make_res(
        success: bool,
        acts: List[Dict[str, Any]],
        msg: str,
        st_type: str,
        completeness: str = "INCOMPLETE",
        discovered: int = 0,
        parsed: int = 0,
        duplicates: int = 0,
        filtered_out: int = 0,
        earliest_date: Optional[str] = None,
        latest_date: Optional[str] = None
    ) -> ScrapeResult:
        req_range = {"start": start_date, "end": end_date}
        ret_range = {"start": earliest_date, "end": latest_date}
        if status_out is not None:
            status_out["status_type"] = st_type
            status_out["status"] = msg
            status_out["empty_message"] = msg
            status_out["retrieval_completeness"] = completeness
            status_out["activities_discovered"] = discovered
            status_out["activities_parsed"] = parsed
            status_out["duplicates_removed"] = duplicates
            status_out["activities_filtered"] = filtered_out
            status_out["activities_saved"] = len(acts)
            status_out["earliest_retrieved_activity_date"] = earliest_date
            status_out["latest_retrieved_activity_date"] = latest_date
            status_out["requested_date_range"] = req_range
            status_out["retrieved_date_range"] = ret_range
        return ScrapeResult(
            success=success,
            activities=acts,
            message=msg,
            status_type=st_type,
            retrieval_completeness=completeness,
            activities_discovered=discovered,
            activities_parsed=parsed,
            duplicates_removed=duplicates,
            activities_filtered=filtered_out,
            activities_saved=len(acts),
            earliest_retrieved_date=earliest_date,
            latest_retrieved_date=latest_date,
            requested_date_range=req_range,
            retrieved_date_range=ret_range
        )

    slug = extract_slug_from_url(profile_url)
    if not slug:
        return make_res(False, [], "Invalid LinkedIn profile URL.", "INVALID_URL", "INVALID")

    start_date_obj = to_date_obj(start_date)
    end_date_obj = to_date_obj(end_date)
    days_span = (date.today() - start_date_obj).days if start_date_obj else 730

    # Adaptive historical retrieval depth (supports up to 2-year history, matching LinkedIn's history visibility)
    is_deep_history = (start_date is None) or (days_span >= 60)
    is_year_history = (start_date is None) or (days_span >= 180)
    is_two_year_history = (start_date is None) or (days_span >= 365)

    has_explicit_activity_ceiling = bool(max_activities and max_activities > 0)
    if has_explicit_activity_ceiling:
        effective_max_activities = max_activities
    else:
        # Avoid arbitrary fixed limits (200, 400, 600, 1500) that silently truncate high-activity profiles.
        # Set an ultra-high safety threshold (default 50,000) strictly to prevent infinite loops.
        effective_max_activities = int(os.getenv("LINKEDIN_MAX_ACTIVITIES_UNBOUNDED", "50000"))

    if max_scrolls and max_scrolls > 0:
        effective_max_scrolls = max_scrolls
    else:
        base_scrolls = int(os.getenv("LINKEDIN_MAX_SCROLLS", "15"))
        if is_two_year_history:
            effective_max_scrolls = max(base_scrolls, int(os.getenv("LINKEDIN_TWO_YEAR_MAX_SCROLLS", "1000")))
        elif is_year_history:
            effective_max_scrolls = max(base_scrolls, int(os.getenv("LINKEDIN_YEAR_MAX_SCROLLS", "600")))
        elif is_deep_history:
            effective_max_scrolls = max(base_scrolls, int(os.getenv("LINKEDIN_DEEP_MAX_SCROLLS", "400")))
        elif days_span <= 7 and days_span > 0:
            effective_max_scrolls = max(base_scrolls, 60)
        else:
            effective_max_scrolls = max(base_scrolls, 200)

    # Check authenticated session status
    sess_status, is_auth, sess_msg = validate_session_state()
    dbg(f"[DEBUG] Authenticated session confirmed: {'YES' if is_auth else 'NO'} ({sess_status}: {sess_msg})")

    # If session is unavailable, show a clear authentication-required status
    # instead of silently falling back to Public Guest Mode (which leads to HTTP 999 rate limit blocks).
    if not is_auth:
        dbg(f"[DEBUG] Retrieval failure reason: (A) Authentication required ({sess_status}: {sess_msg})")
        if active_run_id:
            fail_stage(active_run_id, 2, f"Authentication required: {sess_msg}")
        if sess_status == "EXPIRED":
            if sess_msg and "expired" in sess_msg.lower():
                msg = sess_msg
            else:
                msg = f"LinkedIn session in your Chrome browser is expired or logged out: {sess_msg}" if sess_msg else "LinkedIn session in your Chrome browser is expired or logged out. Please log in to LinkedIn in Chrome and retry."
            st_type = "SESSION_EXPIRED"
        else:
            if sess_msg and "authentication is required" in sess_msg.lower():
                msg = sess_msg
            else:
                msg = f"LinkedIn authentication is required to access profile activity: {sess_msg}" if sess_msg else "LinkedIn authentication is required to access profile activity. Please ensure your Google Chrome browser is open, logged into LinkedIn, and running with remote debugging enabled (--remote-debugging-port=9222)."
            st_type = "AUTH_REQUIRED"
        return make_res(False, [], msg, st_type, "UNAVAILABLE")

    # Use targeted sub-tab activity feed when authenticated
    target_profile_url = f"https://www.linkedin.com/in/{slug}/recent-activity/all/"
    if activity_types and "all" not in activity_types:
        norm_types = [str(t).rstrip('s').lower() for t in activity_types if t]
        if norm_types == ["post"]:
            target_profile_url = f"https://www.linkedin.com/in/{slug}/recent-activity/posts/"
        elif norm_types == ["comment"]:
            target_profile_url = f"https://www.linkedin.com/in/{slug}/recent-activity/comments/"
        elif norm_types in (["like"], ["reaction"]):
            target_profile_url = f"https://www.linkedin.com/in/{slug}/recent-activity/reactions/"
        elif norm_types in (["video"], ["media"]):
            target_profile_url = f"https://www.linkedin.com/in/{slug}/recent-activity/videos/"
    dbg(f"[DEBUG] requested URL: {profile_url}")
    dbg(f"[DEBUG] normalized URL: {target_profile_url}")

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
                ws_endpoint = get_active_chrome_cdp_ws_url()
                use_cdp = bool(ws_endpoint) or is_cdp_available(port=9222)
                with sync_playwright() as p:
                    if use_cdp:
                        cdp_target = ws_endpoint or "http://127.0.0.1:9222"
                        dbg("[DEBUG] Connecting to existing Chrome session via CDP...")
                        if active_run_id:
                            start_stage(active_run_id, 1, "Connecting to existing Chrome session via CDP")
                        browser = p.chromium.connect_over_cdp(cdp_target, timeout=20000)
                        auth_context, meta = find_authenticated_cdp_context(browser)
                        context = auth_context if auth_context else (browser.contexts[0] if browser.contexts else browser.new_context())
                        dbg(f"[DEBUG] Reusing authenticated Chrome context #{meta.get('context_index', 0)} ({meta.get('total_tabs', 0)} existing tabs preserved)")
                        page = context.new_page()
                        if active_run_id:
                            complete_stage(active_run_id, 1, details=f"Connected to Chrome (context #{meta.get('context_index', 0)}, {meta.get('total_tabs', 0)} tabs preserved)")
                    else:
                        dbg("[DEBUG] Playwright launched: yes")
                        if active_run_id:
                            start_stage(active_run_id, 1, "Launching Playwright headless browser")
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
                            complete_stage(active_run_id, 1, details="Playwright Chromium launched successfully")

                    if active_run_id:
                        start_stage(active_run_id, 2, "Validating LinkedIn session state")
                        complete_stage(active_run_id, 2, details="Session validated" if is_auth else "Public guest mode")
                        start_stage(active_run_id, 3, f"Loading profile feed: {target_profile_url}")

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
                        complete_stage(active_run_id, 3, details=f"Status: {nav_status} | Title: {page_title[:40] if page_title else 'N/A'}")

                    if nav_error and not page_title:
                        dbg(f"[DEBUG] Retrieval failure reason: Navigation error: {nav_error}")
                        return make_res(False, [], "Activity could not be retrieved from LinkedIn. Network timeout or connection unreachable.", "NETWORK_TIMEOUT")

                    final_lower = final_url.lower()
                    title_lower = (page_title or "").lower()

                    # Check for HTTP 404 or Profile Not Found / Page Not Found
                    is_not_found = (nav_status == 404) or ("profile not found" in title_lower) or ("page not found" in title_lower)
                    if is_not_found and "/recent-activity/" in target_profile_url and not target_profile_url.endswith("/recent-activity/all/"):
                        try:
                            dbg(f"[DEBUG] sub-tab {target_profile_url} returned 404, falling back to recent-activity/all/ for {slug}")
                            all_resp = page.goto(f"https://www.linkedin.com/in/{slug}/recent-activity/all/", timeout=timeout_ms, wait_until="domcontentloaded")
                            if all_resp:
                                nav_status = all_resp.status
                            final_url = page.url
                            page_title = page.title()
                            final_lower = final_url.lower()
                            title_lower = (page_title or "").lower()
                            is_not_found = (nav_status == 404) or ("profile not found" in title_lower) or ("page not found" in title_lower)
                        except Exception as fb_err:
                            dbg(f"[DEBUG] recent-activity/all/ fallback failed: {fb_err}")
                    if is_not_found and "/recent-activity/" in target_profile_url:
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
                        start_stage(active_run_id, 4, f"Extracting activities from feed (limit: {effective_max_scrolls} scrolls)")

                    try:
                        page.wait_for_timeout(400)
                        while scroll_count < effective_max_scrolls:
                            scroll_count += 1

                            # Count loaded cards
                            current_card_count = page.evaluate("""() => {
                                return document.querySelectorAll('.profile-activity-card, .feed-shared-update-v2, .tab__panel .comment, .feed-shared-update-v2__control-menu, [data-view-name="feed-full-update"], [data-urn*="urn:li:activity"], .profile-creator-shared-feed-update__container, li.profile-creator-shared-feed-update__container').length;
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
                            # Adaptive scroll pause: quick for early cards, gentle scaling for deep history to avoid rate limits
                            if scroll_count <= 5:
                                scroll_pause = 350
                            elif scroll_count <= 20:
                                scroll_pause = 500
                            elif scroll_count <= 35:
                                scroll_pause = 750
                            elif scroll_count <= 60:
                                scroll_pause = 950
                            else:
                                scroll_pause = 1100
                            page.wait_for_timeout(scroll_pause)

                            # Check for mid-scroll authwall, checkpoint, or rate limit redirection
                            cur_url = (page.url or "").lower()
                            if any(x in cur_url for x in ("authwall", "checkpoint", "login", "signup")):
                                dbg(f"[DEBUG] Mid-scroll authwall or checkpoint redirection detected: {page.url} at scroll {scroll_count}")
                                break

                            # Click "Show more results" button if present
                            try:
                                load_more = page.query_selector("button.feed-load-more-button, button:has-text('Show more results'), button:has-text('Load more')")
                                if load_more and load_more.is_visible():
                                    load_more.click()
                                    page.wait_for_timeout(600)
                            except Exception:
                                pass

                            # Check for active loader/spinner to avoid stopping prematurely on temporary loading delays
                            is_loading_spinner = False
                            try:
                                is_loading_spinner = bool(page.evaluate("""() => {
                                    return !!document.querySelector('.artdeco-loader, .feed-shared-update-v2__loading, .feed-loading, [data-finite-scroll-loading], .scaffold-finite-scroll__loading-indicator');
                                }"""))
                            except Exception:
                                pass

                            if is_loading_spinner:
                                page.wait_for_timeout(800)

                            if new_height == last_height and not is_loading_spinner:
                                stagnant_count += 1
                                # Nudge scroll slightly to trigger IntersectionObservers
                                try:
                                    page.evaluate("window.scrollBy(0, -250);")
                                    page.wait_for_timeout(150)
                                    page.evaluate("window.scrollBy(0, 800);")
                                except Exception:
                                    pass
                                if stagnant_count >= 5:
                                    reached_end_of_feed = True
                                    dbg(f"[DEBUG] Feed end verified: page height unchanged for 5 scrolls at scroll {scroll_count}")
                                    break
                            else:
                                if new_height != last_height:
                                    stagnant_count = 0
                                    last_height = new_height

                            # Check date boundary on actual activity feed cards (throttled every 2 scrolls to optimize speed)
                            if start_date_obj and (scroll_count % 2 == 0 or stagnant_count > 0 or scroll_count >= effective_max_scrolls - 1):
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

                        page.wait_for_timeout(300)
                    except Exception as se:
                        dbg(f"[DEBUG] Scroll / pagination notice: {se}")

                    if active_run_id:
                        complete_stage(active_run_id, 4, items_count=scroll_count, details=f"Scrolled {scroll_count} times, feed reached {'end' if reached_end_of_feed else ('date boundary' if reached_start_date_boundary else 'scroll limit')}")

                    # Safely retrieve page HTML content
                    try:
                        # Read only a direct, browser-resolved HTTPS media URL
                        # already exposed by a rendered <video>. Do not play,
                        # fetch, unwrap blob URLs, inspect network traffic, or
                        # infer media URLs from LinkedIn post permalinks.
                        try:
                            page.evaluate("""() => {
                            const mimeByExt = { mp4: 'video/mp4', m4v: 'video/mp4', webm: 'video/webm', ogg: 'video/ogg', ogv: 'video/ogg' };
                            for (const video of document.querySelectorAll('video')) {
                                const source = video.currentSrc || video.querySelector('source')?.src || video.getAttribute('src') || '';
                                if (!source || !source.startsWith('https://') || source.startsWith('blob:') || source.startsWith('data:')) continue;
                                let parsed;
                                try { parsed = new URL(source); } catch (_) { continue; }
                                const host = parsed.hostname.toLowerCase();
                                if (host === 'linkedin.com' || host.endsWith('.linkedin.com')) continue;
                                const declared = (video.querySelector('source[type]')?.type || '').split(';')[0].trim().toLowerCase();
                                const ext = (parsed.pathname.match(/\\.([a-z0-9]+)$/i) || [])[1]?.toLowerCase() || '';
                                const mime = ['video/mp4', 'video/webm', 'video/ogg'].includes(declared) ? declared : (mimeByExt[ext] || '');
                                if (!mime) continue;
                                video.setAttribute('data-analyzer-playback-src', source);
                                video.setAttribute('data-analyzer-playback-type', mime);
                            }
                            }""")
                        except Exception as source_error:
                            dbg(f"[DEBUG] Rendered media source inspection skipped: {source_error}")
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

                    if not use_cdp:
                        try:
                            browser.close()
                        except Exception:
                            pass
                    browser = None

            except PlaywrightTimeoutError:
                dbg("[DEBUG] Retrieval failure reason: (F) Network timeout / unreachable")
                return make_res(False, [], "Activity could not be retrieved from LinkedIn. Network timeout or connection unreachable.", "NETWORK_TIMEOUT", "FAILED")
            except Exception as e:
                dbg(f"[DEBUG] Retrieval failure reason: (G) Playwright / browser failure: {e}")
                return make_res(False, [], "Activity could not be retrieved from LinkedIn. Browser automation error.", "AUTOMATION_ERROR", "FAILED")
            finally:
                if use_cdp:
                    if page:
                        try:
                            page.close()
                        except Exception:
                            pass
                else:
                    if page:
                        try:
                            page.close()
                        except Exception:
                            pass
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
        if not found_name:
            title_m = re.search(r'<title>(.*?)(?:\s*\|\s*LinkedIn|\s*-\s*LinkedIn)</title>', page_html, re.IGNORECASE)
            if title_m:
                t_clean = _clean_text(title_m.group(1))
                t_clean = re.sub(r'(?:[’\']s\s+Activity|^Activity\s*\|\s*)', '', t_clean, flags=re.IGNORECASE).strip()
                if t_clean and "login" not in t_clean.lower() and "profile" not in t_clean.lower():
                    found_name = t_clean

        found_photo = extract_top_card_photo(page_html)
        has_activity_sec = (
            ("activities-tab" in page_html) or
            ("profile-activity-card" in page_html) or
            ("tab__panel" in page_html) or
            ("feed-shared-update-v2" in page_html) or
            ("urn:li:activity" in page_html) or
            ("feed-full-update" in page_html) or
            ("profile-creator-shared-feed-update__container" in page_html)
        )

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
            start_stage(active_run_id, 5, "Classifying activities by type (posts, comments, reactions, videos)")

        sec_count, elem_count, extracted_raw = _extract_activities_from_public_html(
            page_html=page_html,
            target_slug=slug,
            target_name=target_name or found_name,
            target_photo=found_photo,
            target_headline=target_headline
        )

        if active_run_id:
            v_cnt = sum(1 for a in extracted_raw if a.get("type") == "video" or a.get("video_url") or a.get("video_thumbnail"))
            complete_stage(active_run_id, 5, items_count=len(extracted_raw), details=f"Classified {len(extracted_raw)} activities ({v_cnt} videos)")
            start_stage(active_run_id, 6, "Parsing and validating activity dates")

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
        if active_run_id:
            complete_stage(active_run_id, 6, items_count=len(valid_dates), details=f"Validated {len(valid_dates)} activity timestamps")
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

        parsed_cnt = getattr(extracted_raw, "parsed_count", elem_count)
        dups_cnt = getattr(extracted_raw, "duplicates_count", 0)
        filtered_out_cnt = len(extracted_raw) - len(filtered_activities)

        # Calculate retrieval completeness strictly according to contract:
        # COMPLETE: genuinely covered requested date range (reached_start_date_boundary)
        # OR feed's end has been verified (reached_end_of_feed) AND activity section was accessible.
        # Reaching scroll limit, ceiling, or encounters with an inaccessible section must NEVER mean COMPLETE.
        if hit_ceiling and has_explicit_activity_ceiling:
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
            return make_res(
                True, filtered_activities, msg, "SUCCESS_WITH_ACTIVITIES", retrieval_completeness,
                discovered=elem_count, parsed=parsed_cnt, duplicates=dups_cnt, filtered_out=filtered_out_cnt,
                earliest_date=oldest, latest_date=newest
            )

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
                display_name = target_name or found_name or slug.replace('-', ' ').title()
                return make_res(
                    True, [], f"No video activities found for {display_name} in the selected period.", "NO_VIDEO_ACTIVITIES_FOUND", retrieval_completeness,
                    discovered=elem_count, parsed=parsed_cnt, duplicates=dups_cnt, filtered_out=filtered_out_cnt,
                    earliest_date=oldest, latest_date=newest
                )

            dbg(f"[DEBUG] Retrieval failure reason: (B) No activity exists inside selected date range [{start_date} to {end_date}]")
            range_desc = f"{start_date_obj.isoformat()} to {end_date_obj.isoformat()}" if (start_date_obj and end_date_obj) else (f"from {start_date_obj.isoformat()}" if start_date_obj else f"up to {end_date_obj.isoformat()}")
            bounds_note = f" (Profile's latest activity was on {newest})" if newest else ""
            case2_completeness = "COMPLETE" if (reached_start_date_boundary or reached_end_of_feed) else retrieval_completeness
            return make_res(
                True, [], f"No activities found for selected date range ({range_desc}).{bounds_note}", "NO_ACTIVITIES_IN_RANGE", case2_completeness,
                discovered=elem_count, parsed=parsed_cnt, duplicates=dups_cnt, filtered_out=filtered_out_cnt,
                earliest_date=oldest, latest_date=newest
            )

        # Case 3: Profile HTML was retrieved, but no activities found
        # Accurately distinguish missing/inaccessible activity section from a genuinely empty activity section
        if sec_count == 0 and not has_activity_sec:
            dbg("[DEBUG] Retrieval failure reason: Activity section not present in public page DOM")
            return make_res(
                False, [], "LinkedIn activity section is not accessible for this profile. An authenticated session is required to view activities.", "ACTIVITY_SECTION_UNAVAILABLE", "UNAVAILABLE",
                discovered=elem_count, parsed=parsed_cnt, duplicates=dups_cnt, filtered_out=0
            )

        # Activity section was present in the DOM, but genuinely has 0 activity cards
        dbg("[DEBUG] Retrieval failure reason: (A) No public activity found")
        empty_completeness = "COMPLETE" if reached_end_of_feed else "INCOMPLETE"
        return make_res(
            False, [], "No public activity found for this profile on LinkedIn.", "NO_PUBLIC_ACTIVITIES", empty_completeness,
            discovered=elem_count, parsed=parsed_cnt, duplicates=dups_cnt, filtered_out=0
        )


# ---------------------------------------------------------------------------
# Profile identification: who does a pasted link belong to?
# ---------------------------------------------------------------------------

_IDENTITY_CACHE: Dict[str, Dict[str, Any]] = {}
_IDENTITY_TTL_SECONDS = 1800
_IDENTIFY_LOCK = threading.Lock()

# Logged-in and guest LinkedIn render the top card differently; try both.
_IDENTITY_DOM_SCRIPT = r"""() => {
    const root = document.querySelector('main') || document.body;
    const clean = (el) => el ? el.textContent.replace(/\s+/g, ' ').trim() : null;
    const first = (sels) => { for (const s of sels) { const el = root.querySelector(s); if (el) return el; } return null; };
    const nameEl = first(['h1.top-card-layout__title', 'h1.text-heading-xlarge', '.pv-text-details__left-panel h1', 'h1']);
    const headlineEl = first(['h2.top-card-layout__headline', '.top-card-layout__headline', '.pv-text-details__left-panel .text-body-medium', 'div.text-body-medium.break-words']);
    let photo = null;
    const img = first([
        'img.pv-top-card-profile-picture__image--show', 'img.pv-top-card-profile-picture__image',
        '.pv-top-card__photo img', 'img.profile-photo-edit__preview', 'img.top-card-layout__entity-image',
        '.top-card-layout__entity-image-container img'
    ]);
    if (img) photo = img.currentSrc || img.getAttribute('src') || img.getAttribute('data-delayed-url');
    return { name: clean(nameEl), headline: clean(headlineEl), photo: photo };
}"""

# On a post, activity or article page: the author's profile link.
_AUTHOR_LINK_SCRIPT = r"""() => {
    const sels = [
        '.update-components-actor__meta-link', '.update-components-actor__container a[href*="/in/"]',
        '.feed-shared-actor__container-link', '.base-main-feed-card a[data-tracking-control-name*="actor"]',
        'a[data-tracking-control-name*="feed-actor"]', 'a[data-tracking-control-name*="author"]',
        '.author-info a[href*="/in/"]', 'article a[href*="/in/"]', 'main a[href*="/in/"]'
    ];
    for (const s of sels) {
        for (const a of document.querySelectorAll(s)) {
            const href = a.href || a.getAttribute('href') || '';
            if (/linkedin\.com\/in\/[^/?#]+/i.test(href) || /^\/in\/[^/?#]+/i.test(href)) return href;
        }
    }
    return null;
}"""


def _clean_profile_title(title: str) -> Optional[str]:
    """'(3) Satya Nadella | LinkedIn' or 'Satya Nadella - Microsoft | LinkedIn' -> 'Satya Nadella'."""
    t = html.unescape(title or "").strip()
    t = re.sub(r'^\(\d+\+?\)\s*', '', t)
    t = re.sub(r'\s*\|\s*LinkedIn\s*$', '', t, flags=re.IGNORECASE)
    t = t.split(" - ")[0].strip()
    if not t or t.lower() in ("linkedin", "sign up", "sign in", "log in", "page not found", "profile not found"):
        return None
    return t


def _meta_content(page_html: str, key: str) -> Optional[str]:
    for pattern in (
        r'<meta[^>]+(?:property|name)=["\']' + re.escape(key) + r'["\'][^>]+content=["\']([^"\']*)["\']',
        r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']' + re.escape(key) + r'["\']',
    ):
        m = re.search(pattern, page_html or "", re.IGNORECASE)
        if m:
            value = html.unescape(m.group(1)).strip()
            if value:
                return value
    return None


def parse_profile_identity(page_html: str, page_title: str = "", final_url: str = "",
                           dom: Optional[Dict[str, Any]] = None) -> Dict[str, Optional[str]]:
    """
    Builds {slug, name, headline, picture} for a loaded profile page.
    `dom` holds values read from the live page (preferred); the HTML is the fallback.
    """
    dom = dom or {}
    page_html = page_html or ""

    name = (dom.get("name") or "").strip() or None
    if not name:
        h1 = re.search(r'<h1[^>]*>(.*?)</h1>', page_html, re.DOTALL | re.IGNORECASE)
        name = _clean_text(h1.group(1)) if h1 else None
    if not name:
        name = _clean_profile_title(_meta_content(page_html, "og:title") or "") or _clean_profile_title(page_title)

    headline = (dom.get("headline") or "").strip() or None
    if not headline:
        hd = re.search(r'<h2[^>]*top-card-layout__headline[^>]*>(.*?)</h2>', page_html, re.DOTALL | re.IGNORECASE)
        headline = _clean_text(hd.group(1)) if hd else None
    if not headline:
        # Guest description: "Chairman and CEO at Microsoft · Experience: Microsoft · Location: ..."
        desc = _meta_content(page_html, "og:description") or _meta_content(page_html, "description") or ""
        head = desc.split(" · ")[0].strip()
        if head and not head.lower().startswith(("experience:", "education:", "location:", "view ")) and len(head) <= 220:
            headline = head

    picture = dom.get("photo") or extract_top_card_photo(page_html)
    if not picture:
        og_image = _meta_content(page_html, "og:image") or ""
        if "profile-displayphoto" in og_image or "profile-framedphoto" in og_image:
            picture = og_image
    if not (isinstance(picture, str) and picture.startswith(("http://", "https://"))):
        picture = None

    return {
        "slug": extract_slug_from_url(final_url) if final_url else None,
        "name": name,
        "headline": headline,
        "picture": picture,
    }


@contextmanager
def _authenticated_linkedin_page(connect_timeout_ms: int = 10000):
    """
    Opens one new tab in the user's logged-in Chrome (CDP) or, failing that, a headless
    browser with the saved session. Closes only that tab; never the user's browser.
    """
    from playwright.sync_api import sync_playwright
    ws_endpoint = get_active_chrome_cdp_ws_url()
    use_cdp = bool(ws_endpoint) or is_cdp_available(port=9222)
    with sync_playwright() as p:
        browser = None
        page = None
        try:
            if use_cdp:
                browser = p.chromium.connect_over_cdp(ws_endpoint or "http://127.0.0.1:9222", timeout=connect_timeout_ms)
                context, _ = find_authenticated_cdp_context(browser)
                if context is None:
                    context = browser.contexts[0] if browser.contexts else browser.new_context()
            else:
                browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
                ctx_kwargs: Dict[str, Any] = {
                    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    "locale": "en-US",
                    "viewport": {"width": 1440, "height": 900},
                }
                if SESSION_FILE.exists():
                    ctx_kwargs["storage_state"] = str(SESSION_FILE)
                context = browser.new_context(**ctx_kwargs)
            page = context.new_page()
            yield page
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass
            if browser is not None and not use_cdp:
                try:
                    browser.close()
                except Exception:
                    pass


def identify_linkedin_profile(slug: Optional[str] = None, lookup_url: Optional[str] = None,
                              timeout_ms: int = 15000) -> Dict[str, Any]:
    """
    Opens a LinkedIn link with the authenticated session and reports whose profile it is.
    - slug: a known profile username; the profile page is opened directly.
    - lookup_url: a post, activity, article or lnkd.in link; its author is found first.
    Never uses guest mode (guest requests are what trigger HTTP 999).

    Returns {ok, status, message, slug, profile_url, name, headline, picture}.
    status: IDENTIFIED | AUTH_REQUIRED | RATE_LIMITED_999 | PROFILE_NOT_FOUND |
            AUTHOR_NOT_FOUND | BLOCKED | TIMEOUT | ERROR
    """
    def out(ok: bool, status: str, message: str, **fields) -> Dict[str, Any]:
        res = {"ok": ok, "status": status, "message": message, "slug": None, "profile_url": None,
               "name": None, "headline": None, "picture": None}
        res.update(fields)
        if res.get("slug"):
            res["profile_url"] = f"https://www.linkedin.com/in/{res['slug']}/"
        return res

    if not slug and not lookup_url:
        return out(False, "ERROR", "Nothing to identify.")

    if slug:
        cached = _IDENTITY_CACHE.get(slug)
        if cached and time.time() - cached.get("_ts", 0) < _IDENTITY_TTL_SECONDS:
            return {k: v for k, v in cached.items() if k != "_ts"}

    _, is_auth, _ = validate_session_state()
    if not is_auth:
        return out(False, "AUTH_REQUIRED",
                   "Open Chrome with LinkedIn logged in (remote debugging on port 9222) to fetch the profile's name and photo.",
                   slug=slug)

    if is_rate_limited(slug or "__identify__"):
        return out(False, "RATE_LIMITED_999", "LinkedIn is temporarily limiting requests. Try again in a few minutes.", slug=slug)

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    except Exception:
        PlaywrightTimeoutError = TimeoutError  # type: ignore

    def blocked(url: str) -> bool:
        u = (url or "").lower()
        return any(x in u for x in ("/authwall", "/checkpoint", "/login", "/signup", "/uas/"))

    try:
        with _IDENTIFY_LOCK, _authenticated_linkedin_page() as page:
            if lookup_url:
                resp = page.goto(lookup_url, timeout=timeout_ms, wait_until="domcontentloaded")
                if resp is not None and resp.status == 999:
                    set_rate_limit("__identify__")
                    return out(False, "RATE_LIMITED_999", "LinkedIn is temporarily limiting requests. Try again in a few minutes.")
                if blocked(page.url):
                    return out(False, "BLOCKED", "LinkedIn asked for a login or security check. Make sure you are logged in to LinkedIn in Chrome.")
                slug = extract_slug_from_url(page.url)
                if not slug:
                    try:
                        page.wait_for_selector('a[href*="/in/"]', timeout=5000)
                    except Exception:
                        pass
                    author_href = page.evaluate(_AUTHOR_LINK_SCRIPT)
                    if author_href and author_href.startswith("/"):
                        author_href = "https://www.linkedin.com" + author_href
                    slug = extract_slug_from_url(author_href or "")
                slug = unquote(slug).lower() if slug else None
                if not slug:
                    return out(False, "AUTHOR_NOT_FOUND",
                               "Couldn't find whose post this is. Open the author's profile and paste that link instead.")

            resp = page.goto(f"https://www.linkedin.com/in/{slug}/", timeout=timeout_ms, wait_until="domcontentloaded")
            status_code = resp.status if resp is not None else None
            if status_code == 999:
                set_rate_limit(slug)
                return out(False, "RATE_LIMITED_999", "LinkedIn is temporarily limiting requests. Try again in a few minutes.", slug=slug)
            if blocked(page.url):
                return out(False, "BLOCKED", "LinkedIn asked for a login or security check. Make sure you are logged in to LinkedIn in Chrome.", slug=slug)
            try:
                page.wait_for_selector("h1", timeout=6000)
            except Exception:
                pass
            title = page.title() or ""
            if status_code == 404 or re.search(r'(page|profile) not found', title, re.IGNORECASE) or "/404" in (page.url or ""):
                return out(False, "PROFILE_NOT_FOUND", "No LinkedIn profile exists at this link. Check the username.", slug=slug)

            try:
                dom = page.evaluate(_IDENTITY_DOM_SCRIPT)
            except Exception:
                dom = {}
            identity = parse_profile_identity(page.content(), title, page.url, dom if isinstance(dom, dict) else {})
    except PlaywrightTimeoutError:
        return out(False, "TIMEOUT", "LinkedIn took too long to respond. Try again.", slug=slug)
    except Exception as exc:
        if pw_logger:
            pw_logger.warning("Profile identification failed for %s: %s", slug or lookup_url, exc)
        return out(False, "ERROR", "Couldn't open LinkedIn to identify this profile.", slug=slug)

    final_slug = unquote(identity.get("slug") or slug or "").lower() or None
    slug = (slug or "").lower() or None
    if not identity.get("name"):
        return out(False, "ERROR", "The profile page opened, but its name could not be read.", slug=final_slug)

    result = out(True, "IDENTIFIED", f"Identified {identity['name']} on LinkedIn.", slug=final_slug,
                 name=identity["name"], headline=identity.get("headline"), picture=identity.get("picture"))
    _IDENTITY_CACHE[final_slug] = {**result, "_ts": time.time()}
    if slug and slug != final_slug:
        _IDENTITY_CACHE[slug] = _IDENTITY_CACHE[final_slug]
    return result
