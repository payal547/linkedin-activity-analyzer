"""
Unit tests for Chrome DevTools Protocol (CDP) session reuse, session validation,
and data provenance tracking in LinkedIn Activity Analyzer.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

# Ensure backend is on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_DIR = _PROJECT_ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from linkedin.playwright_service import (
    is_cdp_available,
    validate_session_state,
    find_authenticated_cdp_context,
    validate_cdp_session,
    scrape_linkedin_profile_activities
)
from services.analysis_storage import save_profile_successful_analysis, load_profile_successful_analysis


class MockCDPHandler(BaseHTTPRequestHandler):
    """Minimal mock HTTP handler simulating Chrome DevTools Protocol /json/version."""
    def do_GET(self):
        if self.path == "/json/version":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"Browser": "Chrome/157.0.8103.51", "webSocketDebuggerUrl": "ws://localhost:9222/devtools/browser/abc"}')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Silence HTTP server logs during test run
        pass


class TestChromeCDPIntegration(unittest.TestCase):

    @patch("linkedin.playwright_service.get_active_chrome_cdp_ws_url", return_value=None)
    @patch("urllib.request.urlopen", side_effect=Exception("Connection refused"))
    def test_cdp_available_when_port_closed(self, mock_urlopen, mock_ws):
        """When connection is refused on port 9222 and no active port file, is_cdp_available returns False."""
        self.assertFalse(is_cdp_available(port=9222, timeout=0.5))

    @patch("linkedin.playwright_service.get_active_chrome_cdp_ws_url", return_value=None)
    @patch("urllib.request.urlopen")
    def test_cdp_available_when_port_open(self, mock_urlopen, mock_ws):
        """When an endpoint responds on /json/version with status 200, is_cdp_available must return True."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        self.assertTrue(is_cdp_available(port=9222, timeout=2.0))

    @patch("linkedin.playwright_service.get_active_chrome_cdp_ws_url", return_value="ws://127.0.0.1:9222/devtools/browser/mock-uuid")
    def test_cdp_available_via_active_port_file(self, mock_ws):
        """When DevToolsActivePort provides a valid WebSocket endpoint, is_cdp_available returns True immediately."""
        self.assertTrue(is_cdp_available(port=9222))

    @patch("linkedin.playwright_service.is_cdp_available", return_value=True)
    @patch("linkedin.playwright_service.validate_cdp_session")
    def test_validate_session_state_uses_cdp_when_available(self, mock_val_cdp, mock_avail):
        """When CDP is available and valid, validate_session_state must prioritize CDP without checking files."""
        mock_val_cdp.return_value = ("VALID", True, "Authenticated session confirmed active via Chrome CDP (port 9222)")
        status, is_valid, msg = validate_session_state()

        self.assertEqual(status, "VALID")
        self.assertTrue(is_valid)
        self.assertIn("CDP", msg)
        mock_val_cdp.assert_called_once_with(port=9222)

    @patch("linkedin.playwright_service.is_cdp_available", return_value=False)
    @patch("linkedin.playwright_service.SESSION_FILE")
    def test_validate_session_state_falls_back_when_cdp_unavailable(self, mock_sess_file, mock_avail):
        """When CDP is not available and session file does not exist, returns informative MISSING state."""
        mock_sess_file.exists.return_value = False
        with patch.dict("os.environ", {}, clear=True):
            status, is_valid, msg = validate_session_state()
            self.assertEqual(status, "MISSING")
            self.assertFalse(is_valid)
            self.assertIn("Launch Chrome with --remote-debugging-port=9222", msg)

    def test_provenance_distinguishes_historical_vs_live(self):
        """Verify merging correctly tags historical MongoDB activities and new live activities."""
        slug = "test-provenance-slug"

        # 1. Existing historical activity
        hist_analysis = {
            "slug": slug,
            "total_activities": 1,
            "activities": [
                {
                    "id": "act_hist_1",
                    "date": "2026-05-15",
                    "type": "share",
                    "text": "Old historical share",
                    "storage_origin": "historical_mongodb"
                }
            ]
        }
        save_profile_successful_analysis(slug, hist_analysis)

        # 2. Save a new live scan with new activity
        new_analysis = {
            "slug": slug,
            "total_activities": 1,
            "activities": [
                {
                    "id": "act_live_1",
                    "date": "2026-11-18",
                    "type": "post",
                    "text": "New live post",
                    "storage_origin": "live_retrieval"
                }
            ]
        }
        save_profile_successful_analysis(slug, new_analysis)

        # 3. Reload and verify accumulated activities and provenance
        loaded = load_profile_successful_analysis(slug)
        self.assertIsNotNone(loaded)
        activities = loaded.get("activities", [])
        self.assertEqual(len(activities), 2)

        origins = {a.get("id"): a.get("storage_origin") for a in activities}
        self.assertEqual(origins.get("act_hist_1"), "historical_mongodb")
        self.assertEqual(origins.get("act_live_1"), "live_retrieval")
        self.assertEqual(loaded.get("historical_count"), 1)
        self.assertEqual(loaded.get("live_count"), 1)

    def test_find_authenticated_cdp_context_prioritizes_active_tab_and_cookie(self):
        """Verify find_authenticated_cdp_context picks context with active LinkedIn tab and li_at cookie."""
        mock_browser = MagicMock()
        # Context 0: Unrelated tabs, no LinkedIn cookies
        ctx0 = MagicMock()
        p0 = MagicMock()
        p0.url = "https://www.google.com/search?q=test"
        ctx0.pages = [p0]
        ctx0.cookies.return_value = []

        # Context 1: Active LinkedIn tab and valid li_at cookie
        ctx1 = MagicMock()
        p1 = MagicMock()
        p1.url = "https://www.linkedin.com/feed/"
        ctx1.pages = [p1]
        ctx1.cookies.return_value = [{"name": "li_at", "value": "valid_token", "expires": 9999999999}]

        mock_browser.contexts = [ctx0, ctx1]

        best_ctx, meta = find_authenticated_cdp_context(mock_browser)
        self.assertIs(best_ctx, ctx1)
        self.assertEqual(meta["context_index"], 1)
        self.assertTrue(meta["has_active_tab"])
        self.assertTrue(meta["has_li_at"])
        self.assertEqual(meta["active_tab_url"], "https://www.linkedin.com/feed/")

    def test_find_authenticated_cdp_context_ignores_login_authwall_tabs(self):
        """Verify pages on login or authwall are not counted as active authenticated tabs."""
        mock_browser = MagicMock()
        ctx0 = MagicMock()
        p_authwall = MagicMock()
        p_authwall.url = "https://www.linkedin.com/authwall?trk=bf"
        ctx0.pages = [p_authwall]
        ctx0.cookies.return_value = []

        mock_browser.contexts = [ctx0]

        best_ctx, meta = find_authenticated_cdp_context(mock_browser)
        self.assertIsNone(best_ctx)
        self.assertFalse(meta["has_active_tab"])
        self.assertFalse(meta["has_li_at"])

    @patch("playwright.sync_api.sync_playwright")
    @patch("linkedin.playwright_service.get_active_chrome_cdp_ws_url", return_value="ws://127.0.0.1:9222/devtools/browser/abc")
    def test_validate_cdp_session_does_not_close_browser(self, mock_ws, mock_sp):
        """validate_cdp_session must NOT call browser.close() so user Chrome is never terminated."""
        mock_p_instance = MagicMock()
        mock_sp.return_value.__enter__.return_value = mock_p_instance

        mock_browser = MagicMock()
        mock_p_instance.chromium.connect_over_cdp.return_value = mock_browser

        ctx = MagicMock()
        ctx.pages = [MagicMock(url="https://www.linkedin.com/feed/")]
        ctx.cookies.return_value = [{"name": "li_at", "value": "token", "expires": 9999999999}]
        mock_browser.contexts = [ctx]

        status, is_valid, msg = validate_cdp_session(port=9222)
        self.assertEqual(status, "VALID")
        self.assertTrue(is_valid)
        self.assertIn("Authenticated session confirmed active", msg)
        # Critical assertion: browser.close() must NEVER be called on CDP connection
        mock_browser.close.assert_not_called()

    @patch("linkedin.playwright_service.validate_session_state", return_value=("MISSING", False, "Session missing"))
    def test_scrape_fails_fast_when_unauthenticated_without_guest_fallback(self, mock_sess):
        """When not authenticated, scrape_linkedin_profile_activities must NOT fall back to guest mode."""
        res = scrape_linkedin_profile_activities("https://www.linkedin.com/in/test-slug", target_name="Test")
        success, activities, msg = res[0], res[1], res[2]
        self.assertFalse(success)
        self.assertEqual(activities, [])
        self.assertEqual(res.status_type, "AUTH_REQUIRED")
        self.assertEqual(res.retrieval_completeness, "UNAVAILABLE")
        self.assertIn("authentication is required", msg.lower())


if __name__ == "__main__":
    unittest.main()
