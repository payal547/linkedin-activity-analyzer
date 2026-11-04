"""
Interactive One-Time LinkedIn Playwright Session Capture Utility.

Allows the user to authenticate manually in a visible Chromium browser window.
Captures and persists the authenticated browser storage state (cookies, session)
directly into the project's configured SESSION_FILE and USER_DATA_DIR.

Security & Safety:
- Never asks the user to enter or paste passwords or cookies in code or terminal.
- Strictly redacts all cookie values, tokens, and secrets from stdout and logs.
- Preserves all existing project databases, saved target profiles, and configurations.
"""

import os
import sys
import json
import time
import re
from pathlib import Path
from datetime import datetime, timezone

# Ensure project root and backend are in sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = BACKEND_DIR.parent

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from linkedin.playwright_service import SESSION_FILE, USER_DATA_DIR, validate_session_state
except ImportError:
    from backend.linkedin.playwright_service import SESSION_FILE, USER_DATA_DIR, validate_session_state

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


def capture_session(timeout_seconds: int = 300) -> bool:
    """
    Launches a visible Chromium window for manual LinkedIn login,
    waits for authentication, extracts identity metadata, and saves
    the storage state to SESSION_FILE.
    """
    print("\n" + "=" * 65)
    print("  LinkedIn Activity Analyzer - One-Time Session Capture")
    print("=" * 65)
    print(f"[*] Target Session File : {SESSION_FILE}")
    print(f"[*] Browser User Data Dir: {USER_DATA_DIR}")
    print("=" * 65 + "\n")

    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        print("[1/5] Launching visible Chromium browser...")
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(USER_DATA_DIR),
            headless=False,
            viewport={"width": 1280, "height": 850},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized"
            ]
        )

        page = context.pages[0] if context.pages else context.new_page()

        print("[2/5] Navigating to LinkedIn login page...")
        try:
            page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            print(f"[!] Warning during navigation: {e}")

        print("\n" + "-" * 65)
        print(">>> ACTION REQUIRED: PLEASE LOG IN TO LINKEDIN IN THE BROWSER WINDOW <<<")
        print(" - Enter your credentials directly into the open Chromium browser.")
        print(" - Complete any 2FA / verification challenges if prompted.")
        print(" - Do NOT paste any passwords or cookies into this terminal.")
        print(f" - Waiting up to {timeout_seconds} seconds for successful login...")
        print("-" * 65 + "\n")

        start_time = time.time()
        authenticated = False
        user_identity = {"name": "", "headline": "", "profile_url": "", "slug": ""}

        # Polling loop waiting for authentication
        while time.time() - start_time < timeout_seconds:
            try:
                current_url = page.url.lower()

                # Check if li_at cookie is present in context
                cookies = context.cookies()
                li_at_cookie = next((c for c in cookies if c.get("name") == "li_at" and c.get("value")), None)

                # Determine if user has reached feed or an authenticated surface
                is_on_feed = "linkedin.com/feed" in current_url
                is_on_profile = "linkedin.com/in/" in current_url and "login" not in current_url
                is_logged_in_page = is_on_feed or is_on_profile

                if li_at_cookie and (is_logged_in_page or len(cookies) > 5):
                    print(f"\n[3/5] Authentication detected! (URL: {page.url})")
                    authenticated = True
                    break

            except Exception:
                pass

            # Informative progress indicator every 5 seconds
            elapsed = int(time.time() - start_time)
            remaining = timeout_seconds - elapsed
            sys.stdout.write(f"\r[*] Waiting for login... ({elapsed}s elapsed, {remaining}s remaining)")
            sys.stdout.flush()
            time.sleep(2.0)

        print("")

        if not authenticated:
            print("\n[!] Authentication timed out or window was closed before login was confirmed.")
            context.close()
            return False

        # Verify authenticated identity
        print("[4/5] Verifying authenticated profile identity...")
        try:
            # Navigate to /in/me/ which redirects to the member's profile
            page.goto("https://www.linkedin.com/in/me/", wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)

            final_url = page.url
            user_identity["profile_url"] = final_url

            # Extract slug
            slug_match = re.search(r'linkedin\.com\/in\/([a-zA-Z0-9_\-\%]+)', final_url)
            if slug_match:
                user_identity["slug"] = slug_match.group(1).rstrip('/')

            # Extract user name from title or h1
            title = page.title()
            if "|" in title:
                raw_name = title.split("|")[0].strip()
            else:
                raw_name = title.replace("- LinkedIn", "").strip()

            if not raw_name or "linkedin" in raw_name.lower():
                try:
                    h1_elem = page.locator("h1").first
                    if h1_elem:
                        raw_name = h1_elem.inner_text().strip()
                except Exception:
                    pass

            user_identity["name"] = raw_name or user_identity["slug"] or "Authenticated Member"
            print(f"      Verified Identity : {user_identity['name']}")
            print(f"      Profile URL       : {user_identity['profile_url']}")
        except Exception as e:
            print(f"[!] Note on identity check: {e}")
            user_identity["name"] = "Authenticated Member"

        # Save storage state
        print("\n[5/5] Persisting authenticated session...")
        context.storage_state(path=str(SESSION_FILE))

        # Save companion metadata (never containing cookie secrets)
        meta_file = SESSION_FILE.parent / "playwright_session_meta.json"
        meta_data = {
            "authenticated_at": datetime.now(timezone.utc).isoformat(),
            "user_name": user_identity["name"],
            "profile_url": user_identity["profile_url"],
            "slug": user_identity["slug"],
            "verified": True
        }
        with open(meta_file, "w", encoding="utf-8") as mf:
            json.dump(meta_data, mf, indent=2)

        print(f"[*] Session state saved to: {SESSION_FILE}")
        print(f"[*] Metadata saved to     : {meta_file}")

        context.close()

    # Validate session state using playwright_service
    print("\n" + "=" * 65)
    print("  Validation Check via playwright_service.validate_session_state()")
    print("=" * 65)
    status, is_valid, msg = validate_session_state()
    print(f"  Session Status : {status}")
    print(f"  Is Valid       : {is_valid}")
    print(f"  Message        : {msg}")
    print("=" * 65)

    if is_valid and status == "VALID":
        print("\n[SUCCESS] Playwright is now configured with an authorized session!")
        print("          All future scrapes will run fully automated and headless.\n")
        return True
    else:
        print(f"\n[WARNING] Validation returned status '{status}': {msg}\n")
        return False


if __name__ == "__main__":
    success = capture_session()
    sys.exit(0 if success else 1)

