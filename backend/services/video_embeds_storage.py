"""Store user-provided official LinkedIn embeds separately from activity records."""

from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit, urlunsplit

from db.mongo import get_db


class _IframeSourceParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sources = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "iframe":
            source = dict(attrs).get("src")
            if source:
                self.sources.append(source)


def canonical_linkedin_post_url(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ValueError("A LinkedIn post URL is required.")
    parsed = urlsplit(value.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme.lower() != "https" or host not in {"linkedin.com", "www.linkedin.com"} or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("The post URL must use HTTPS on linkedin.com.")
    if not re.search(r"/(?:posts|feed/update|pulse|videos)/", parsed.path, re.IGNORECASE):
        raise ValueError("The URL does not look like a LinkedIn post link.")
    return urlunsplit(("https", "www.linkedin.com", parsed.path.rstrip("/"), parsed.query, ""))


def parse_official_embed_src(embed_code):
    if not isinstance(embed_code, str) or not embed_code.strip() or len(embed_code) > 12000:
        raise ValueError("Paste the official LinkedIn embed code or its iframe URL.")
    value = embed_code.strip()
    candidates = []
    if value.lower().startswith("https://"):
        candidates.append(value)
    else:
        parser = _IframeSourceParser()
        parser.feed(value)
        candidates.extend(parser.sources)

    for candidate in candidates:
        try:
            parsed = urlsplit(candidate.strip())
            host = (parsed.hostname or "").lower()
            if (
                parsed.scheme.lower() == "https"
                and host in {"linkedin.com", "www.linkedin.com"}
                and not parsed.username
                and not parsed.password
                and parsed.port in (None, 443)
                and re.match(r"^/embed/feed/update/[^/]+", parsed.path, re.IGNORECASE)
            ):
                return urlunsplit(("https", "www.linkedin.com", parsed.path, parsed.query, ""))
        except ValueError:
            continue
    raise ValueError("No valid LinkedIn feed embed URL was found. Use LinkedIn’s official Embed this post code for a public post.")


def _embed_key(profile_slug, post_url):
    return sha256(f"{profile_slug}|{post_url}".encode("utf-8")).hexdigest()


def save_video_embed(profile_slug, post_url, embed_code):
    slug = str(profile_slug or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,127}", slug):
        raise ValueError("Select a saved profile before adding an embed.")
    canonical_post = canonical_linkedin_post_url(post_url)
    embed_src = parse_official_embed_src(embed_code)
    db = get_db()
    collection = db["activity_video_embeds"]
    collection.create_index([("profile_slug", 1), ("post_url", 1)], unique=True)
    collection.update_one(
        {"profile_slug": slug, "post_url": canonical_post},
        {"$set": {
            "embed_id": _embed_key(slug, canonical_post),
            "profile_slug": slug,
            "post_url": canonical_post,
            "embed_src": embed_src,
            "updated_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )
    return {"profile_slug": slug, "post_url": canonical_post, "embed_src": embed_src}


def load_video_embed_map(collection=None):
    target = collection if collection is not None else get_db()["activity_video_embeds"]
    documents = target.find(
        {}, {"_id": 0, "profile_slug": 1, "post_url": 1, "embed_src": 1}
    )
    result = {}
    for document in documents:
        slug = str(document.get("profile_slug") or "").strip().lower()
        post_url = document.get("post_url")
        embed_src = document.get("embed_src")
        if slug and post_url and embed_src:
            result[(slug, post_url)] = embed_src
    return result
