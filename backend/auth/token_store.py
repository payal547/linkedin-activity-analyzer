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


def remove_access_token(session_id):
    return _tokens.pop(session_id, None)

