"""
Recognises what kind of LinkedIn link the user pasted and, where the link itself
names the person, which profile it points to.

Works offline (no LinkedIn requests). Links that do not name the author, such as
feed/update URNs, lnkd.in short links and articles, are marked needs_lookup so the
caller can resolve them with an authenticated browser session.
"""

import re
import urllib.parse
from typing import Any, Dict, Optional

try:
    from services.saved_profiles_storage import parse_linkedin_url
except ImportError:
    from backend.services.saved_profiles_storage import parse_linkedin_url

# First LinkedIn or lnkd.in link inside pasted text ("Check this: https://...").
# The host must really be linkedin.com / lnkd.in: "not-linkedin.com", "linkedin.com.evil.com"
# and "evil.com/linkedin.com/in/x" must not match.
_LINK_IN_TEXT = re.compile(
    r'(?:(?<![\w./-])https?://|(?<![\w./-]))'
    r'(?:[a-z0-9-]+\.)*(?:linkedin\.com|lnkd\.in)(?![\w.-])'
    r'(?:[/?#][^\s<>"\'()]*)?',
    re.IGNORECASE,
)

# LinkedIn custom profile URLs use letters, digits and hyphens only, so in
# /posts/<vanity>_<title>-activity-<id> everything before the first "_" is the author.
_POST_AUTHOR = re.compile(r'^/posts/([a-z0-9-]{3,100})_', re.IGNORECASE)

PROFILE_EXAMPLE = "https://www.linkedin.com/in/username"

_NOT_A_PERSON = (
    ("/company/", "company", "This is a company page. Paste a person's profile link instead, for example " + PROFILE_EXAMPLE + "."),
    ("/showcase/", "company", "This is a company showcase page. Paste a person's profile link instead, for example " + PROFILE_EXAMPLE + "."),
    ("/school/", "school", "This is a school page. Paste a person's profile link instead, for example " + PROFILE_EXAMPLE + "."),
    ("/groups/", "group", "This is a LinkedIn group. Paste a person's profile link instead, for example " + PROFILE_EXAMPLE + "."),
    ("/jobs/", "job", "This is a job listing. Paste a person's profile link instead, for example " + PROFILE_EXAMPLE + "."),
    ("/search/", "search", "This is a search results link. Open the person's profile and paste that link instead."),
    ("/newsletters/", "newsletter", "This is a newsletter page. Paste the author's profile link instead, for example " + PROFILE_EXAMPLE + "."),
    ("/events/", "event", "This is an event page. Paste a person's profile link instead, for example " + PROFILE_EXAMPLE + "."),
)

LINK_TYPE_LABELS = {
    "profile": "Profile link",
    "mobile_profile": "Mobile profile link",
    "username": "Profile username",
    "post": "Post link",
    "activity": "Post / activity link",
    "article": "Article link",
    "short_link": "Short link (lnkd.in)",
}


def _result(kind: str, *, slug: Optional[str] = None, url: Optional[str] = None,
            needs_lookup: bool = False, error: Optional[str] = None) -> Dict[str, Any]:
    return {
        "valid": error is None,
        "link_type": kind,
        "link_type_label": LINK_TYPE_LABELS.get(kind, kind.replace("_", " ").title()),
        "slug": slug,
        "profile_url": f"https://www.linkedin.com/in/{slug}/" if slug else None,
        "lookup_url": url,
        "needs_lookup": needs_lookup,
        "error": error,
    }


def extract_link(text: str) -> Optional[str]:
    """Returns the first LinkedIn/lnkd.in link found in pasted text, with a scheme."""
    if not text:
        return None
    m = _LINK_IN_TEXT.search(str(text))
    if not m:
        return None
    link = m.group(0).rstrip(".,;:!?")
    if not link.lower().startswith(("http://", "https://")):
        link = "https://" + link
    return link


def classify_linkedin_link(text: str) -> Dict[str, Any]:
    """
    Classifies pasted input. Returns a dict with:
      valid, link_type, link_type_label, slug, profile_url,
      needs_lookup (author must be resolved in a browser), lookup_url, error.
    """
    raw = (text or "").strip()
    if not raw:
        return _result("empty", error="Please enter a LinkedIn profile URL.")

    link = extract_link(raw)
    if not link:
        # No link at all: accept a bare username, otherwise reject.
        ok, err, slug, _ = parse_linkedin_url(raw)
        if ok and slug:
            return _result("username", slug=slug)
        return _result("other", error="Invalid URL. Please enter a valid LinkedIn member profile URL (e.g., " + PROFILE_EXAMPLE + ").")

    parts = urllib.parse.urlsplit(link)
    host = (parts.hostname or "").lower()
    path = urllib.parse.unquote(parts.path or "/")
    path_l = path.lower()

    if host == "lnkd.in" or host.endswith(".lnkd.in"):
        if path.strip("/"):
            return _result("short_link", url=link, needs_lookup=True)
        return _result("short_link", error="This short link is incomplete. Paste the full link you were given.")

    for marker, kind, message in _NOT_A_PERSON:
        if marker in path_l or path_l.rstrip("/") == marker.rstrip("/"):
            return _result(kind, error=message)

    # Profile links, including the mobile site (/mwlite/in/...) and /in/<slug>/details/...
    if re.search(r'(?:^|/)in/[^/]+', path_l):
        ok, err, slug, _ = parse_linkedin_url(link)
        if ok and slug:
            return _result("mobile_profile" if "/mwlite/" in path_l else "profile", slug=slug)
        return _result("profile", error=err)

    if re.search(r'(?:^|/)in/?$', path_l):
        return _result("profile", error="Incomplete LinkedIn profile URL. Must include a profile username or slug (e.g., " + PROFILE_EXAMPLE + ").")

    m = _POST_AUTHOR.match(path)
    if m:
        ok, _, slug, _ = parse_linkedin_url(m.group(1))
        if ok and slug:
            return _result("post", slug=slug, url=link)

    if path_l.startswith(("/posts/", "/feed/update/", "/embed/feed/update/")):
        return _result("activity", url=link, needs_lookup=True)

    if path_l.startswith("/pulse/"):
        return _result("article", url=link, needs_lookup=True)

    if path_l.strip("/") in ("", "feed", "home"):
        return _result("other", error="This is the LinkedIn home feed, not a profile. Open the person's profile and paste that link.")

    return _result("other", error="This LinkedIn link does not point to a person. Paste a profile link, for example " + PROFILE_EXAMPLE + ".")
