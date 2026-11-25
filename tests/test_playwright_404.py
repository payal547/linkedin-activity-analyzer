"""
Unit tests for LinkedIn profile 404 / Profile Not Found detection in Playwright service.
Verifies:
1. HTTP 404 response triggers "LinkedIn profile not found. Please verify the URL."
2. Page title containing "Profile Not Found" triggers profile-not-found error.
3. Page title containing "Page Not Found" triggers profile-not-found error.
4. Nonexistent profiles are not marked as confirmed or reported as having zero public activities.
5. Valid profile response is successfully parsed and returned.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from linkedin.playwright_service import scrape_linkedin_profile_activities
from services.activity_service import get_activities_for_subject


class TestPlaywright404Detection(unittest.TestCase):

    def setUp(self):
        # Clear any cached profile DOMs
        from linkedin.playwright_service import _PROFILE_CACHE
        _PROFILE_CACHE.clear()

    def _setup_mock_playwright(self, status_code, url, title, html_content):
        mock_p = MagicMock()
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()

        mock_p.chromium.launch.return_value = mock_browser
        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page

        mock_page.goto.return_value = MagicMock(status=status_code)
        mock_page.url = url
        mock_page.title.return_value = title
        mock_page.content.return_value = html_content

        return mock_p

    def test_http_404_detection(self):
        """HTTP 404 response must return profile-not-found error and 0 activities."""
        mock_p = self._setup_mock_playwright(
            status_code=404,
            url="https://www.linkedin.com/in/neelmani",
            title="Profile Not Found | LinkedIn",
            html_content="<html><head><title>Profile Not Found | LinkedIn</title></head><body><h1>Profile Not Found</h1></body></html>"
        )

        with patch("playwright.sync_api.sync_playwright") as mock_sp:
            mock_sp.return_value.__enter__.return_value = mock_p
            success, acts, msg = scrape_linkedin_profile_activities("https://www.linkedin.com/in/neelmani")

        self.assertFalse(success)
        self.assertEqual(acts, [])
        self.assertEqual(msg, "LinkedIn profile not found. Please verify the URL.")

    def test_profile_not_found_title_detection(self):
        """Page title containing 'Profile Not Found' must trigger error even if HTTP status is 200."""
        mock_p = self._setup_mock_playwright(
            status_code=200,
            url="https://www.linkedin.com/in/some-nonexistent-user",
            title="Profile Not Found | LinkedIn",
            html_content="<html><head><title>Profile Not Found | LinkedIn</title></head><body>This profile does not exist.</body></html>"
        )

        with patch("playwright.sync_api.sync_playwright") as mock_sp:
            mock_sp.return_value.__enter__.return_value = mock_p
            success, acts, msg = scrape_linkedin_profile_activities("https://www.linkedin.com/in/some-nonexistent-user")

        self.assertFalse(success)
        self.assertEqual(acts, [])
        self.assertEqual(msg, "LinkedIn profile not found. Please verify the URL.")

    def test_page_not_found_title_detection(self):
        """Page title containing 'Page Not Found' (case-insensitive) must trigger error."""
        mock_p = self._setup_mock_playwright(
            status_code=200,
            url="https://www.linkedin.com/in/deleted-account",
            title="Page not found | LinkedIn",
            html_content="<html><head><title>Page not found | LinkedIn</title></head><body>Page not found</body></html>"
        )

        with patch("playwright.sync_api.sync_playwright") as mock_sp:
            mock_sp.return_value.__enter__.return_value = mock_p
            success, acts, msg = scrape_linkedin_profile_activities("https://www.linkedin.com/in/deleted-account")

        self.assertFalse(success)
        self.assertEqual(acts, [])
        self.assertEqual(msg, "LinkedIn profile not found. Please verify the URL.")

    def test_valid_profile_response(self):
        """A genuine profile with activities must be parsed and returned with success=True."""
        valid_html = (
            "<html><head><title>Satya Nadella | LinkedIn</title></head><body>"
            "<h1 class=\"top-card-layout__title\">Satya Nadella</h1>"
            "<h2 class=\"top-card-layout__headline\">Chairman and CEO at Microsoft</h2>"
            "<div class=\"tab__panel\">"
            "<div class=\"profile-activity-card\">"
            "<a class=\"base-card__full-link\" href=\"https://www.linkedin.com/posts/satyanadella_ai-activity-123456/\">"
            "<span class=\"sr-only\">Satya Nadella posted this</span>"
            "</a>"
            "<div class=\"base-main-card__metadata\">2d</div>"
            "<div class=\"see-more-text\">Excited to share our AI progress.</div>"
            "</div></div></body></html>"
        )
        mock_p = self._setup_mock_playwright(
            status_code=200,
            url="https://www.linkedin.com/in/satyanadella/",
            title="Satya Nadella | LinkedIn",
            html_content=valid_html
        )

        with patch("playwright.sync_api.sync_playwright") as mock_sp:
            mock_sp.return_value.__enter__.return_value = mock_p
            success, acts, msg = scrape_linkedin_profile_activities("https://www.linkedin.com/in/satyanadella/")

        self.assertTrue(success)
        self.assertGreater(len(acts), 0)
        self.assertIn("Successfully retrieved", msg)
        self.assertEqual(acts[0]["subject_name"], "Satya Nadella")

    def test_activity_service_integration_with_404(self):
        """get_activities_for_subject must propagate the 404 message to status_out and not claim 0 public activities."""
        status_tracker = {}
        mock_p = self._setup_mock_playwright(
            status_code=404,
            url="https://www.linkedin.com/in/neelmani",
            title="Profile Not Found | LinkedIn",
            html_content="<html><head><title>Profile Not Found | LinkedIn</title></head></html>"
        )

        with patch("playwright.sync_api.sync_playwright") as mock_sp:
            mock_sp.return_value.__enter__.return_value = mock_p
            acts = get_activities_for_subject(
                subject_id="neelmani",
                subject_name="Neelmani",
                data_source="real_linkedin",
                subject_url="https://www.linkedin.com/in/neelmani",
                status_out=status_tracker
            )

        self.assertEqual(acts, [])
        self.assertEqual(status_tracker.get("status"), "LinkedIn profile not found. Please verify the URL.")
        self.assertEqual(status_tracker.get("empty_message"), "LinkedIn profile not found. Please verify the URL.")

    def test_cached_profile_nav_status_no_unbound_error(self):
        """Verify that reading from _PROFILE_CACHE never causes an UnboundLocalError on nav_status."""
        import time
        from linkedin.playwright_service import _PROFILE_CACHE

        valid_html = (
            "<html><head><title>Neel Mani, PhD - India | Professional Profile | LinkedIn</title></head><body>"
            "<h1 class=\"top-card-layout__title\">Neel Mani, PhD</h1>"
            "<h2 class=\"top-card-layout__headline\">Professor & Researcher</h2>"
            "<div class=\"tab__panel\">"
            "<div class=\"profile-activity-card\">"
            "<a class=\"base-card__full-link\" href=\"https://www.linkedin.com/posts/neel-mani_research-activity-123456/\">"
            "<span class=\"sr-only\">Neel Mani posted this</span>"
            "</a>"
            "<div class=\"base-main-card__metadata\">2d</div>"
            "<div class=\"see-more-text\">Excited to share our latest research publication.</div>"
            "</div></div></body></html>"
        )

        _PROFILE_CACHE["neel-mani"] = {
            "html": valid_html,
            "title": "Neel Mani, PhD - India | Professional Profile | LinkedIn",
            "status": 200,
            "timestamp": time.time(),
            "reached_end_of_feed": True
        }

        # Must execute cleanly without UnboundLocalError for nav_status
        success, acts, msg = scrape_linkedin_profile_activities("https://www.linkedin.com/in/neel-mani/")
        self.assertTrue(success)
        self.assertGreater(len(acts), 0)
        self.assertIn("Successfully retrieved", msg)
        self.assertGreater(len(acts), 0)

    def test_navigation_exception_safe_handling(self):
        """When page.goto raises an exception, nav_status must be safe and not report as 0 public activities."""
        mock_p = MagicMock()
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()

        mock_p.chromium.launch.return_value = mock_browser
        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page

        mock_page.goto.side_effect = Exception("net::ERR_NAME_NOT_RESOLVED")
        mock_page.url = "https://www.linkedin.com/in/unreachable-user/"
        mock_page.title.return_value = ""
        mock_page.content.return_value = ""

        with patch("playwright.sync_api.sync_playwright") as mock_sp:
            mock_sp.return_value.__enter__.return_value = mock_p
            success, acts, msg = scrape_linkedin_profile_activities("https://www.linkedin.com/in/unreachable-user/")

        self.assertFalse(success)
        self.assertEqual(acts, [])
        self.assertIn("Activity could not be retrieved from LinkedIn", msg)
        self.assertNotEqual(msg, "No public activity found for this profile on LinkedIn.")

    def test_navigation_none_response_safe_handling(self):
        """When page.goto returns None, nav_status is initialized to None without throwing UnboundLocalError."""
        mock_p = MagicMock()
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()

        mock_p.chromium.launch.return_value = mock_browser
        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page

        mock_page.goto.return_value = None
        mock_page.url = "https://www.linkedin.com/in/user-with-none-response/"
        mock_page.title.return_value = ""
        mock_page.content.return_value = ""

        with patch("playwright.sync_api.sync_playwright") as mock_sp:
            mock_sp.return_value.__enter__.return_value = mock_p
            success, acts, msg = scrape_linkedin_profile_activities("https://www.linkedin.com/in/user-with-none-response/")

        self.assertFalse(success)
        self.assertEqual(acts, [])
        self.assertIn("Activity could not be retrieved from LinkedIn", msg)


if __name__ == "__main__":
    unittest.main()
