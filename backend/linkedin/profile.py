"""
LinkedIn profile utilities and API helpers.
"""

from typing import Dict, Any, Optional
from auth.linkedin_auth import get_linkedin_profile
from services.profile_service import format_user_profile


def fetch_and_format_profile(access_token: str) -> Optional[Dict[str, Any]]:
    """Fetches LinkedIn userinfo and returns a formatted profile dict."""
    if not access_token:
        return None
    try:
        raw = get_linkedin_profile(access_token)
        return format_user_profile(raw)
    except Exception:
        return None
