from flask import session

_tokens = {}


def store_access_token(session_id, access_token):
    _tokens[session_id] = access_token


def get_access_token(session_id):
    if session_id and session_id in _tokens:
        return _tokens[session_id]
    try:
        return session.get("linkedin_access_token")
    except Exception:
        return None


def is_placeholder_token(token):
    """Placeholder tokens from the Playwright testing account are not OAuth tokens."""
    return isinstance(token, str) and token.startswith("token_playwright_")


def get_oauth_access_token():
    """The current session's real LinkedIn OAuth token, or None (placeholders excluded)."""
    try:
        token = get_access_token(session.get("linkedin_session_id")) or session.get("linkedin_access_token")
    except Exception:
        return None
    return None if (not token or is_placeholder_token(token)) else token


def remove_access_token(session_id):
    return _tokens.pop(session_id, None)

