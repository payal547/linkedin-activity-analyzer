"""
Regression tests for the fixes from the overall health check (2026-11-25):
stable activity IDs, duplicate-free history merge, real LinkedIn session status,
no placeholder OAuth token, and no personal data in session messages.
"""
import json
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from app import app
from linkedin import playwright_service
from services.analysis_storage import (
    save_profile_successful_analysis, load_profile_successful_analysis, clear_profile_successful_analysis,
)

CARD_HTML = (
    "<html><body><h1 class=\"top-card-layout__title\">Neel Mani</h1>"
    "<div class=\"tab__panel\"><div class=\"profile-activity-card\">"
    "<a class=\"base-card__full-link\" href=\"https://www.linkedin.com/posts/neel-mani_research-activity-7123\">"
    "<span class=\"sr-only\">Neel Mani posted this</span></a>"
    "<div class=\"base-main-card__metadata\">2d</div>"
    "<div class=\"see-more-text\">Excited to share our latest research.</div>"
    "</div></div></body></html>"
)


class TestStableActivityIds(unittest.TestCase):
    def _ids_in_fresh_process(self):
        script = textwrap.dedent(f"""
            import json, sys
            sys.path.insert(0, {str(BACKEND)!r})
            from linkedin.playwright_service import _extract_activities_from_public_html
            _, _, acts = _extract_activities_from_public_html(page_html={CARD_HTML!r}, target_slug="neel-mani", target_name="Neel Mani")
            print(json.dumps([a["id"] for a in acts]))
        """)
        out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120, cwd=str(BACKEND))
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        return json.loads(out.stdout.strip().splitlines()[-1])

    def test_ids_are_identical_across_processes(self):
        # Python's hash() is salted per process; IDs built from it changed on every restart.
        first, second = self._ids_in_fresh_process(), self._ids_in_fresh_process()
        self.assertTrue(first)
        self.assertEqual(first, second)

    def test_stable_id_is_deterministic(self):
        self.assertEqual(playwright_service._stable_id("a", "b"), playwright_service._stable_id("a", "b"))
        self.assertNotEqual(playwright_service._stable_id("a", "b"), playwright_service._stable_id("ab", ""))


class TestHistoryMergeDeduplication(unittest.TestCase):
    slug = "merge-dedup-test"

    def tearDown(self):
        clear_profile_successful_analysis(self.slug)

    def test_same_event_with_different_ids_is_stored_once(self):
        url = f"https://www.linkedin.com/in/{self.slug}/"
        post = "https://www.linkedin.com/posts/someone_topic-activity-7001"

        def run(act_id, date):
            return {"subject_url": url, "total_activities": 1, "activities": [
                {"id": act_id, "type": "like", "date": date, "post_url": post, "text": "Reacted to a post"}]}

        save_profile_successful_analysis(self.slug, run("act_post_x_111", "2026-11-01"))
        # Same reaction re-scraped after a restart (new legacy ID) and a day later (relative date drifted).
        save_profile_successful_analysis(self.slug, run("act_post_x_999", "2026-11-02"))
        stored = load_profile_successful_analysis(self.slug)
        self.assertEqual(len(stored["activities"]), 1)
        self.assertEqual(stored["total_activities"], 1)


class TestSessionStatusReflectsChrome(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        from routes import auth_routes
        auth_routes._BROWSER_SESSION_CACHE.update(at=0.0, value=None)
        self.auth_routes = auth_routes

    def tearDown(self):
        self.auth_routes._BROWSER_SESSION_CACHE.update(at=0.0, value=None)

    def test_not_logged_in_chrome_is_not_reported_as_connected(self):
        with patch("linkedin.playwright_service.validate_session_state", return_value=("EXPIRED", False, "x")):
            with app.test_client() as client:
                data = client.get("/api/auth/session-status").get_json()
                with client.session_transaction() as sess:
                    stored_token = sess.get("linkedin_access_token")
        self.assertTrue(data["authenticated"])
        self.assertFalse(data["connected"])
        self.assertEqual(data["status_label"], "LinkedIn not logged in")
        self.assertIn("not logged in", data["linkedin_session"]["message"])
        self.assertIsNone(stored_token)

    def test_logged_in_chrome_is_connected(self):
        with patch("linkedin.playwright_service.validate_session_state", return_value=("VALID", True, "ok")):
            with app.test_client() as client:
                data = client.get("/api/auth/session-status").get_json()
        self.assertTrue(data["connected"])
        self.assertEqual(data["status_label"], "LinkedIn Connected ✓")


class TestPlaceholderTokenIsIgnored(unittest.TestCase):
    def test_placeholder_token_never_reaches_linkedin_api(self):
        from auth.token_store import get_oauth_access_token
        with app.test_request_context():
            from flask import session
            session["linkedin_access_token"] = "token_playwright_aniket"
            self.assertIsNone(get_oauth_access_token())
            session["linkedin_access_token"] = "real-oauth-token"
            self.assertEqual(get_oauth_access_token(), "real-oauth-token")


class TestSessionMessagesHaveNoPersonalData(unittest.TestCase):
    def test_profile_hint_lists_names_not_emails(self):
        local_state = {"profile": {"last_used": "Default", "info_cache": {
            "Default": {"name": "Person 1", "user_name": "someone@example.com"},
            "Profile 1": {"name": "Work", "user_name": "work@example.com"},
        }}}
        with patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "read_text", return_value=json.dumps(local_state)):
            hint = playwright_service._detect_chrome_profiles_hint()
        self.assertIn("Profile 1", hint)
        self.assertIn("Work", hint)
        self.assertNotIn("@", hint)


if __name__ == "__main__":
    unittest.main()
