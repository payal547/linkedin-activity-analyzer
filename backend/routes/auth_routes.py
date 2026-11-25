import os
import secrets
from pathlib import Path

from flask import Blueprint, jsonify, redirect, request, session
from auth.token_store import store_access_token, remove_access_token
from auth.linkedin_auth import (
    get_linkedin_authorization_url,
    exchange_code_for_token,
    get_linkedin_profile
)

auth_bp = Blueprint("auth", __name__)

_BROWSER_SESSION_CACHE = {"at": 0.0, "value": None}
_BROWSER_SESSION_TTL = 20.0


def _browser_linkedin_session():
    """
    Whether the Chrome/Playwright session used for retrieval is actually logged in to
    LinkedIn. Cached briefly so page loads do not reconnect to Chrome every time.
    Returns {"valid": bool, "status": str, "message": str}.
    """
    import time
    now = time.monotonic()
    cached = _BROWSER_SESSION_CACHE["value"]
    if cached is not None and now - _BROWSER_SESSION_CACHE["at"] < _BROWSER_SESSION_TTL:
        return cached
    try:
        try:
            from linkedin.playwright_service import validate_session_state
        except ImportError:
            from backend.linkedin.playwright_service import validate_session_state
        status, valid, _detail = validate_session_state()
    except Exception:
        status, valid = "ERROR", False
    if valid:
        message = "LinkedIn session active in Chrome."
    elif status == "EXPIRED":
        message = "Chrome is open, but LinkedIn is not logged in on that Chrome profile."
    else:
        message = "Chrome is not running with remote debugging. Run backend\\scripts\\launch_chrome_with_debug.bat and log in to LinkedIn."
    value = {"valid": bool(valid), "status": status, "message": message}
    _BROWSER_SESSION_CACHE.update(at=now, value=value)
    return value


def _session_payload(name, headline, picture, email, account_type):
    """Account identity plus the real LinkedIn retrieval-session state."""
    browser = _browser_linkedin_session()
    return {
        "authenticated": True,
        "connected": browser["valid"],
        "name": name,
        "headline": headline,
        "picture": picture,
        "status_label": "LinkedIn Connected ✓" if browser["valid"] else "LinkedIn not logged in",
        "linkedin_session": browser,
        "email": email,
        "account_type": account_type,
    }


@auth_bp.route("/auth/linkedin")
def linkedin_login():
    redirect_uri = os.getenv("LINKEDIN_REDIRECT_URI", "")
    # Ensure cookie domain matches callback host if accessing via localhost vs 127.0.0.1
    host_parts = request.host.split(":")
    port_suffix = f":{host_parts[1]}" if len(host_parts) > 1 else ""
    if "127.0.0.1" in redirect_uri and host_parts[0] == "localhost":
        return redirect(f"http://127.0.0.1{port_suffix}/auth/linkedin")
    if "localhost" in redirect_uri and host_parts[0] == "127.0.0.1":
        return redirect(f"http://localhost{port_suffix}/auth/linkedin")

    state = secrets.token_urlsafe(32)
    session["oauth_state"] = state
    return redirect(get_linkedin_authorization_url(state))



@auth_bp.route("/auth/linkedin/callback")
def linkedin_callback():

    expected_state = session.pop("oauth_state", None)
    if not expected_state or not secrets.compare_digest(expected_state, request.args.get("state", "")):
        return (
            "LinkedIn authorization failed: session state mismatch.<br><br>"
            "Please make sure you are accessing the app from "
            "<a href='http://127.0.0.1:5000/login'>http://127.0.0.1:5000/login</a>",
            400
        )

    if request.args.get("error"):
        error_desc = request.args.get("error_description", "LinkedIn authorization was cancelled or not completed.")
        return f"LinkedIn authorization failed: {error_desc}. <a href='/login'>Try again</a>", 400

    code = request.args.get("code")

    if not code:
        return "LinkedIn authorization failed: no authorization code received. <a href='/login'>Try again</a>", 400

    try:
        # Exchange authorization code for access token
        token_data = exchange_code_for_token(code)

        access_token = token_data.get("access_token")

        if not access_token:
            return "LinkedIn authorization failed: no access token received. <a href='/login'>Try again</a>", 400

        # Get LinkedIn profile information
        profile = get_linkedin_profile(access_token)

        # Save profile and token in session
        session["linkedin_profile"] = profile
        session["linkedin_access_token"] = access_token
        session_id = secrets.token_urlsafe(32)
        store_access_token(session_id, access_token)
        session["linkedin_session_id"] = session_id

        # Print profile in terminal for testing
        print("\n========== LINKEDIN PROFILE ==========")
        print(profile)
        print("======================================\n")

        return redirect("/connections")

    except Exception as e:
        print("LinkedIn Error:", str(e))
        return f"LinkedIn authorization failed: {str(e)}. <a href='/login'>Try again</a>", 500


@auth_bp.route("/auth/logout", methods=["GET", "POST"])
def logout():
    session_id = session.pop("linkedin_session_id", None)
    if session_id:
        remove_access_token(session_id)
    session.pop("linkedin_profile", None)
    session.pop("linkedin_access_token", None)
    return redirect("/login")


@auth_bp.route("/api/auth/session-status", methods=["GET", "POST"])
def session_status():
    """
    Returns current LinkedIn session authentication status.
    - If user has an active authenticated session (OAuth or testing account), returns their session.
    - Never overwrites an existing legitimate session.
    - Allows authorized testing account initialization only in development/testing mode, never in production.
    """
    from flask import current_app

    # 1. Check if user already has an active session
    prof = session.get("linkedin_profile")
    if prof and isinstance(prof, dict) and prof.get("name"):
        is_test_account = (prof.get("name") == "Aniket Pal")
        return jsonify(_session_payload(
            prof.get("name"),
            prof.get("headline", "LinkedIn Member"),
            prof.get("picture") or ("/aniket_pal.jpg" if is_test_account else None),
            prof.get("email", ""),
            "Authorized Testing Account" if is_test_account else "LinkedIn Account",
        ))

    # 2. Check if manual logout was performed
    if session.get("manual_logout") and request.method == "GET":
        return jsonify({
            "authenticated": False,
            "connected": False,
            "name": None,
            "picture": None,
            "status_label": "LinkedIn Not Connected",
            "error": "LinkedIn session has been logged out."
        }), 401

    # 3. Only initialize authorized testing session if in dev/testing mode and NOT in production
    is_prod = (os.getenv("APP_ENV", "").lower() == "production" or os.getenv("FLASK_ENV", "").lower() == "production")
    allow_testing_account = not is_prod and (
        bool(current_app and current_app.config.get("TESTING")) or
        os.getenv("TESTING") == "1" or
        os.getenv("APP_ENV", "development").lower() in ("development", "test", "testing") or
        os.getenv("ENABLE_TEST_ACCOUNT", "true").lower() in ("1", "true")
    )

    photo_file = Path(__file__).resolve().parent.parent.parent / "frontend" / "aniket_pal.jpg"
    has_photo = photo_file.exists()
    email = os.getenv("LINKEDIN_EMAIL", "").strip()

    if allow_testing_account and has_photo:
        session.pop("manual_logout", None)
        aniket_profile = {
            "name": "Aniket Pal",
            "email": email,
            "sub": "linkedin_user_aniket",
            "picture": "/aniket_pal.jpg",
            "headline": "Student at Graphic Era Deemed to be University"
        }
        # No access token is stored for the testing account: it is not an OAuth login, and a
        # placeholder token only made every analysis call LinkedIn's API and wait for a 401.
        session["linkedin_profile"] = aniket_profile

        return jsonify(_session_payload(
            "Aniket Pal",
            "Student at Graphic Era Deemed to be University",
            "/aniket_pal.jpg",
            email,
            "Authorized Testing Account",
        ))

    return jsonify({
        "authenticated": False,
        "connected": False,
        "name": None,
        "picture": None,
        "status_label": "LinkedIn Not Connected",
        "error": "Please connect your LinkedIn account."
    }), 401

