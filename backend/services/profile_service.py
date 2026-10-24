from typing import Dict, Any, Optional


def format_user_profile(raw_profile: Dict[str, Any]) -> Dict[str, Any]:
    """Formats and sanitizes authenticated user profile data."""
    if not raw_profile:
        return {}

    name = raw_profile.get("name") or "LinkedIn User"
    email = raw_profile.get("email") or "Not available"
    picture = raw_profile.get("picture")

    locale = raw_profile.get("locale", {})
    country = locale.get("country", "") if isinstance(locale, dict) else ""
    language = locale.get("language", "") if isinstance(locale, dict) else ""

    return {
        "id": raw_profile.get("sub", ""),
        "name": name,
        "email": email,
        "email_verified": raw_profile.get("email_verified", False),
        "picture": picture,
        "country": country,
        "language": language,
        "source": "real_linkedin"
    }

