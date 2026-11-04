"""
Unit tests for Concurrency Locking, Accurate Status Classification,
and Profile-Specific History Preservation.
"""

import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add backend directory to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = BASE_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Ensure TESTING environment is active
os.environ["TESTING"] = "1"

from services.analysis_storage import (
    save_latest_analysis,
    load_latest_analysis,
    save_profile_successful_analysis,
    load_profile_successful_analysis,
    clear_profile_history,
    clear_latest_analysis,
    extract_slug
)
from linkedin.playwright_service import (
    scrape_linkedin_profile_activities,
    _PROFILE_CACHE,
    clear_profile_cache,
    _get_profile_lock
)


class TestAnalysisConcurrencyAndStatus(unittest.TestCase):

    def setUp(self):
        clear_profile_cache()
        clear_profile_history()
        clear_latest_analysis()

    def tearDown(self):
        clear_profile_cache()
        clear_profile_history()
        clear_latest_analysis()

    # ---------------------------------------------------------
    # 1. Profile-Specific History Isolation & Preservation
    # ---------------------------------------------------------
    def test_profile_history_isolation(self):
        """Historical analyses must be strictly isolated by canonical slug."""
        data_a = {
            "subject_name": "Profile A",
            "subject_url": "https://www.linkedin.com/in/profile-a/",
            "total_activities": 5,
            "activities": [{"id": "act_a1", "text": "Post from A"}]
        }
        data_b = {
            "subject_name": "Profile B",
            "subject_url": "https://www.linkedin.com/in/profile-b/",
            "total_activities": 2,
            "activities": [{"id": "act_b1", "text": "Post from B"}]
        }

        save_profile_successful_analysis("https://www.linkedin.com/in/profile-a/", data_a)
        save_profile_successful_analysis("https://www.linkedin.com/in/profile-b/", data_b)

        hist_a = load_profile_successful_analysis("profile-a")
        hist_b = load_profile_successful_analysis("profile-b")
        hist_c = load_profile_successful_analysis("profile-c")

        self.assertIsNotNone(hist_a)
        self.assertEqual(hist_a["subject_name"], "Profile A")
        self.assertEqual(hist_a["total_activities"], 5)

        self.assertIsNotNone(hist_b)
        self.assertEqual(hist_b["subject_name"], "Profile B")
        self.assertEqual(hist_b["total_activities"], 2)

        self.assertIsNone(hist_c)

    def test_partial_slug_collision_isolation(self):
        """Saving for 'neel' must never overwrite or collide with 'neel-mani'."""
        data_neel = {
            "subject_name": "Neel Regular",
            "subject_url": "https://www.linkedin.com/in/neel/",
            "total_activities": 1,
            "activities": [{"id": "act_n"}]
        }
        data_neel_mani = {
            "subject_name": "Dr. Neel Mani",
            "subject_url": "https://www.linkedin.com/in/neel-mani/",
            "total_activities": 4,
            "activities": [{"id": "act_nm"}]
        }

        save_profile_successful_analysis("neel", data_neel)
        save_profile_successful_analysis("neel-mani", data_neel_mani)

        loaded_neel = load_profile_successful_analysis("neel")
        loaded_neel_mani = load_profile_successful_analysis("neel-mani")

        self.assertEqual(loaded_neel["subject_name"], "Neel Regular")
        self.assertEqual(loaded_neel_mani["subject_name"], "Dr. Neel Mani")
        self.assertEqual(loaded_neel_mani["total_activities"], 4)

    def test_failed_attempt_preserves_previous_successful_result(self):
        """A failed/blocked attempt with 0 activities must not erase prior successful results."""
        # 1. Store a previous successful analysis
        successful_run = {
            "subject_name": "Neel Mani, PhD",
            "subject_url": "https://www.linkedin.com/in/neel-mani/",
            "total_activities": 3,
            "date_range": {"start": "2026-06-01", "end": "2026-08-01"},
            "activities": [
                {"id": "act_1", "type": "post", "text": "Research update 1"},
                {"id": "act_2", "type": "comment", "text": "Great insights!"},
                {"id": "act_3", "type": "post", "text": "Paper published"}
            ]
        }
        save_latest_analysis(successful_run)

        # Confirm it was saved to profile history
        hist = load_profile_successful_analysis("neel-mani")
        self.assertIsNotNone(hist)
        self.assertEqual(hist["total_activities"], 3)

        # 2. Now run a failed analysis (e.g. rate-limited 999 or authwall returning 0 activities)
        failed_run = {
            "subject_name": "Neel Mani, PhD",
            "subject_url": "https://www.linkedin.com/in/neel-mani/",
            "total_activities": 0,
            "activities": [],
            "status_type": "RATE_LIMITED_999",
            "empty_message": "LinkedIn rate limit encountered (HTTP 999). Please wait before retrying."
        }
        save_latest_analysis(failed_run)

        # 3. Profile-specific history must still contain the original 3 activities
        hist_after = load_profile_successful_analysis("neel-mani")
        self.assertIsNotNone(hist_after)
        self.assertEqual(hist_after["total_activities"], 3)

        # 4. latest_analysis must preserve current failure diagnostic AND attach historical data
        latest = load_latest_analysis()
        self.assertIsNotNone(latest)
        self.assertEqual(latest["total_activities"], 0)
        self.assertEqual(latest["status_type"], "RATE_LIMITED_999")
        self.assertIn("historical_successful_analysis", latest)
        self.assertEqual(latest["historical_successful_analysis"]["total_activities"], 3)
        self.assertEqual(len(latest["historical_successful_analysis"]["activities"]), 3)

    # ---------------------------------------------------------
    # 2. Concurrency Locking & Cache Reuse
    # ---------------------------------------------------------
    def test_per_slug_locking_instances(self):
        """Locks for the same slug must be the same instance; different slugs get different locks."""
        lock1 = _get_profile_lock("neel-mani")
        lock2 = _get_profile_lock("neel-mani")
        lock3 = _get_profile_lock("satyanadella")

        self.assertIs(lock1, lock2)
        self.assertIsNot(lock1, lock3)

    def test_cached_profile_prevents_playwright_launch(self):
        """When a valid profile DOM is in cache, Playwright sync_playwright must NOT be launched."""
        valid_html = (
            "<!DOCTYPE html><html><head><title>Neel Mani, PhD | LinkedIn</title></head>"
            "<body><h1 class=\"top-card-layout__title\">Neel Mani, PhD</h1>"
            "<div class=\"tab__panel\"><div class=\"profile-activity-card\">"
            "<a class=\"base-card__full-link\" href=\"https://www.linkedin.com/posts/neel-mani_act-1\">"
            "<span class=\"sr-only\">Neel Mani posted this</span></a>"
            "<div class=\"base-main-card__metadata\">1d</div>"
            "<div class=\"see-more-text\">Cached research post.</div>"
            "</div></div></body></html>"
        )
        _PROFILE_CACHE["neel-mani"] = {
            "html": valid_html,
            "title": "Neel Mani, PhD | LinkedIn",
            "status": 200,
            "timestamp": time.time()
        }

        with patch("playwright.sync_api.sync_playwright") as mock_pw:
            status_out = {}
            res = scrape_linkedin_profile_activities(
                "https://www.linkedin.com/in/neel-mani/",
                status_out=status_out
            )
            # sync_playwright should never be called because cached DOM was used
            mock_pw.assert_not_called()
            self.assertTrue(res[0])
            self.assertGreater(len(res[1]), 0)
            self.assertEqual(res.status_type, "SUCCESS_WITH_ACTIVITIES")
            self.assertEqual(status_out.get("status_type"), "SUCCESS_WITH_ACTIVITIES")

    # ---------------------------------------------------------
    # 3. Status Classification Accuracy
    # ---------------------------------------------------------
    def test_activity_section_unavailable_when_not_in_dom(self):
        """
        When LinkedIn returns HTTP 200 for a public profile (guest view)
        but omits the activity container entirely, it must NOT report
        'No public activity found'. It must report ACTIVITY_SECTION_UNAVAILABLE.
        """
        html_without_activity = (
            "<!DOCTYPE html><html><head><title>Neel Mani, PhD | LinkedIn</title></head>"
            "<body><h1 class=\"top-card-layout__title\">Neel Mani, PhD</h1>"
            "<div class=\"about-section\"><p>Researcher in AI</p></div>"
            "</body></html>"
        )
        _PROFILE_CACHE["neel-mani"] = {
            "html": html_without_activity,
            "title": "Neel Mani, PhD | LinkedIn",
            "status": 200,
            "timestamp": time.time()
        }

        status_out = {}
        success, acts, msg = scrape_linkedin_profile_activities(
            "https://www.linkedin.com/in/neel-mani/",
            status_out=status_out
        )

        self.assertFalse(success)
        self.assertEqual(len(acts), 0)
        self.assertEqual(status_out.get("status_type"), "ACTIVITY_SECTION_UNAVAILABLE")
        self.assertIn("activity section is not accessible", msg)
        self.assertNotIn("No public activity found", msg)

    def test_no_public_activities_when_activity_section_explicitly_empty(self):
        """
        When the activity section exists in the DOM but has 0 cards,
        it should legitimately report NO_PUBLIC_ACTIVITIES.
        """
        html_with_empty_activity = (
            "<!DOCTYPE html><html><head><title>Neel Mani, PhD | LinkedIn</title></head>"
            "<body><h1 class=\"top-card-layout__title\">Neel Mani, PhD</h1>"
            "<div class=\"activities-tab\"><!-- no cards here --></div>"
            "</body></html>"
        )
        _PROFILE_CACHE["neel-mani"] = {
            "html": html_with_empty_activity,
            "title": "Neel Mani, PhD | LinkedIn",
            "status": 200,
            "timestamp": time.time()
        }

        status_out = {}
        success, acts, msg = scrape_linkedin_profile_activities(
            "https://www.linkedin.com/in/neel-mani/",
            status_out=status_out
        )

        self.assertFalse(success)
        self.assertEqual(len(acts), 0)
        self.assertEqual(status_out.get("status_type"), "NO_PUBLIC_ACTIVITIES")
        self.assertIn("No public activity found", msg)

    def test_rate_limited_999_classification(self):
        """HTTP 999 must be classified as RATE_LIMITED_999."""
        mock_p = MagicMock()
        mock_browser = MagicMock()
        mock_ctx = MagicMock()
        mock_page = MagicMock()
        mock_resp = MagicMock()

        mock_resp.status = 999
        mock_page.goto.return_value = mock_resp
        mock_page.url = "https://www.linkedin.com/in/neel-mani/"
        mock_page.title.return_value = "LinkedIn"
        mock_ctx.new_page.return_value = mock_page
        mock_browser.new_context.return_value = mock_ctx
        mock_p.chromium.launch.return_value = mock_browser

        with patch("playwright.sync_api.sync_playwright") as mock_pw, \
             patch("time.sleep"):  # skip the 6s polite sleep in tests
            mock_pw.return_value.__enter__.return_value = mock_p

            status_out = {}
            res = scrape_linkedin_profile_activities(
                "https://www.linkedin.com/in/neel-mani/",
                status_out=status_out
            )

            self.assertFalse(res[0])
            self.assertEqual(res.status_type, "RATE_LIMITED_999")
            self.assertEqual(status_out.get("status_type"), "RATE_LIMITED_999")
            self.assertIn("HTTP 999", res[2])

    def test_profile_not_found_classification(self):
        """HTTP 404 must be classified as PROFILE_NOT_FOUND."""
        mock_p = MagicMock()
        mock_browser = MagicMock()
        mock_ctx = MagicMock()
        mock_page = MagicMock()
        mock_resp = MagicMock()

        mock_resp.status = 404
        mock_page.goto.return_value = mock_resp
        mock_page.url = "https://www.linkedin.com/in/some-nonexistent-user"
        mock_page.title.return_value = "Profile Not Found | LinkedIn"
        mock_ctx.new_page.return_value = mock_page
        mock_browser.new_context.return_value = mock_ctx
        mock_p.chromium.launch.return_value = mock_browser

        with patch("playwright.sync_api.sync_playwright") as mock_pw:
            mock_pw.return_value.__enter__.return_value = mock_p

            status_out = {}
            res = scrape_linkedin_profile_activities(
                "https://www.linkedin.com/in/some-nonexistent-user",
                status_out=status_out
            )

            self.assertFalse(res[0])
            self.assertEqual(res.status_type, "PROFILE_NOT_FOUND")
            self.assertEqual(status_out.get("status_type"), "PROFILE_NOT_FOUND")
            self.assertIn("LinkedIn profile not found", res[2])

    # ---------------------------------------------------------
    # 4. Route Integration: Status & History Propagation
    # ---------------------------------------------------------
    def test_api_analyze_preserves_history_on_blocked_or_empty_attempt(self):
        """POST /api/analyze must attach historical data when current attempt yields 0 activities."""
        from app import app
        client = app.test_client()

        # Pre-populate history for neel-mani
        save_profile_successful_analysis("neel-mani", {
            "subject_name": "Neel Mani, PhD",
            "subject_url": "https://www.linkedin.com/in/neel-mani/",
            "total_activities": 2,
            "activities": [
                {"id": "act_prev1", "type": "post", "text": "Prior research"},
                {"id": "act_prev2", "type": "comment", "text": "Prior discussion"}
            ]
        })

        # Cache a DOM with no activity section (reproducing guest view for neel-mani)
        _PROFILE_CACHE["neel-mani"] = {
            "html": "<html><body><h1 class=\"top-card-layout__title\">Neel Mani, PhD</h1></body></html>",
            "title": "Neel Mani, PhD | LinkedIn",
            "status": 200,
            "timestamp": time.time()
        }

        resp = client.post("/api/analyze", json={
            "subject_url": "https://www.linkedin.com/in/neel-mani/",
            "subject_name": "Neel Mani, PhD",
            "data_source": "real_linkedin"
        })

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()

        # Current attempt had 0 activities
        self.assertEqual(data.get("total_activities"), 0)
        self.assertEqual(data.get("status_type"), "ACTIVITY_SECTION_UNAVAILABLE")
        self.assertIn("historical_successful_analysis", data)
        self.assertEqual(data["historical_successful_analysis"]["total_activities"], 2)
        self.assertEqual(len(data["historical_successful_analysis"]["activities"]), 2)

    def test_api_analyze_multi_profile_status_propagation(self):
        """POST /api/analyze with multiple profiles must propagate status_type cleanly."""
        from app import app
        client = app.test_client()

        _PROFILE_CACHE["neel-mani"] = {
            "html": "<html><body><h1 class=\"top-card-layout__title\">Neel Mani, PhD</h1></body></html>",
            "title": "Neel Mani, PhD | LinkedIn",
            "status": 200,
            "timestamp": time.time()
        }

        resp = client.post("/api/analyze", json={
            "profiles": [
                {
                    "profile_url": "https://www.linkedin.com/in/neel-mani/",
                    "name": "Neel Mani, PhD"
                }
            ],
            "activities": ["all"]
        })

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("status_type"), "ACTIVITY_SECTION_UNAVAILABLE")
        self.assertIn("profile_results", data)
        self.assertEqual(data["profile_results"][0].get("status_type"), "ACTIVITY_SECTION_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
