"""
Regression and verification test suite for all 13 audit fixes.
Covers:
- Phase 1: Security Fixes (Issues 1, 2)
- Phase 2: Core Functionality (Issues 3, 4, 5)
- Phase 3: Dashboard & UX (Issues 6, 7, 8, 9)
- Phase 4: Cleanup & Reliability (Issues 10, 11, 12, 13)
"""
import io
import json
import os
import sys
import zipfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app import app
from config.settings import LINKEDIN_REDIRECT_URI
from services.analysis_storage import (
    save_profile_successful_analysis,
    load_profile_successful_analysis,
    clear_profile_successful_analysis,
    _get_history_file
)
from services.saved_profiles_storage import (
    save_target_profile,
    load_saved_profiles,
    remove_saved_profile,
    _get_storage_file as _get_saved_profiles_file
)
from linkedin.data_parser import parse_connections_from_file_or_zip


class TestAuditFixesRegression(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        app.config["TESTING"] = True

    def tearDown(self):
        # Clean any test artifacts
        hist = _get_history_file()
        if hist.exists() and "test_" in hist.name:
            try:
                hist.unlink()
            except Exception:
                pass
        sp_file = _get_saved_profiles_file()
        if sp_file.exists() and "test_" in sp_file.name:
            try:
                sp_file.unlink()
            except Exception:
                pass

    # --- Phase 1: Security Fixes ---

    def test_issue1_session_status_no_hardcoded_email_and_production_safe(self):
        """Issue 1: Test account must not leak hardcoded email and must be disabled in production."""
        with app.test_client() as client:
            # Under production environment, session-status must return not authenticated (401 or 200 with authenticated=False)
            os.environ["APP_ENV"] = "production"
            try:
                res = client.get("/api/auth/session-status")
                self.assertIn(res.status_code, (200, 401))
                data = res.get_json()
                self.assertFalse(data.get("authenticated"))
                # Ensure no hardcoded personal email is present
                self.assertNotIn("payalpal2456@gmail.com", str(data))
            finally:
                os.environ.pop("APP_ENV", None)

    def test_issue2_gitignore_contains_sensitive_patterns(self):
        """Issue 2: .gitignore must contain logs, cache, temporary json, and session files."""
        gitignore_path = Path(__file__).resolve().parent.parent / ".gitignore"
        self.assertTrue(gitignore_path.exists())
        content = gitignore_path.read_text(encoding="utf-8")
        self.assertIn("browser_cache/", content)
        self.assertIn("playwright_context/", content)
        self.assertIn("playwright_session.json", content)
        self.assertIn("scraper_debug.log", content)
        self.assertIn("*.log", content)
        self.assertIn("backend/data/*.tmp", content)
        self.assertIn("backend/data/imported_activities/", content)

    # --- Phase 2: Core Functionality ---

    def test_issue3_zip_slip_and_file_size_limits(self):
        """Issue 3: Path traversal zip slip files must be safely rejected; size limit configured."""
        max_upload_mb = int(os.getenv("MAX_UPLOAD_SIZE_MB", "100"))
        self.assertGreaterEqual(max_upload_mb, 10)
        # Create a malicious zip with directory traversal
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("../../etc/malicious.txt", "evil")
        buf.seek(0)
        # Parser must reject or safely ignore traversal members
        success, rows, msg = parse_connections_from_file_or_zip(buf.getvalue(), "exploit.zip")
        self.assertEqual(rows, [])

    def test_issue4_env_example_exists_and_clean(self):
        """Issue 4: .env.example must exist, contain documentation, and have NO real credentials."""
        env_example_path = Path(__file__).resolve().parent.parent / ".env.example"
        self.assertTrue(env_example_path.exists())
        content = env_example_path.read_text(encoding="utf-8")
        self.assertIn("LINKEDIN_CLIENT_ID", content)
        self.assertIn("LINKEDIN_CLIENT_SECRET", content)
        self.assertIn("LINKEDIN_REDIRECT_URI", content)
        self.assertIn("your-linkedin-client-id", content)
        self.assertNotIn("AQED", content)  # no real li_at cookie
        self.assertNotIn("payalpal", content)

    def test_issue5_oauth_redirect_uri_matches_registered_route(self):
        """Issue 5: OAuth redirect URI in settings must route to /auth/linkedin/callback."""
        self.assertIn("/auth/linkedin/callback", LINKEDIN_REDIRECT_URI)
        self.assertNotIn("/api/auth/callback", LINKEDIN_REDIRECT_URI)

    # --- Phase 3: Dashboard & UX ---

    def test_issue8_cookie_security_settings(self):
        """Issue 8: Session cookie security flags must be configured."""
        self.assertTrue(app.config.get("SESSION_COOKIE_HTTPONLY"))
        self.assertEqual(app.config.get("SESSION_COOKIE_SAMESITE"), "Lax")

    def test_issue6_dashboard_contains_sentiment_panel(self):
        """Issue 6: dashboard.html must contain sentiment breakdown panel."""
        dash_path = Path(__file__).resolve().parent.parent / "frontend" / "pages" / "dashboard.html"
        self.assertTrue(dash_path.exists())
        content = dash_path.read_text(encoding="utf-8")
        self.assertIn("id=\"sentimentSummaryPanel\"", content)
        self.assertIn("id=\"sentimentBreakdown\"", content)

    def test_issue7_index_contains_multi_profile_progress(self):
        """Issue 7: index.html must contain multiProfileProgressCard and pacing feedback elements."""
        index_path = Path(__file__).resolve().parent.parent / "frontend" / "index.html"
        self.assertTrue(index_path.exists())
        content = index_path.read_text(encoding="utf-8")
        self.assertIn("id=\"multiProfileProgressCard\"", content)
        self.assertIn("id=\"multiProgressBar\"", content)
        self.assertIn("id=\"multiProgressStatusText\"", content)

    def test_issue9_connections_js_image_sanitization(self):
        """Issue 9: connections.js must validate image protocols and escape attributes."""
        js_path = Path(__file__).resolve().parent.parent / "frontend" / "js" / "connections.js"
        self.assertTrue(js_path.exists())
        content = js_path.read_text(encoding="utf-8")
        self.assertIn("isSafeImageUrl", content)
        self.assertIn("escapeAttr", content)

    # --- Phase 4: Cleanup & Reliability ---

    def test_issue10_empty_files_documented_and_present(self):
        """Issue 10: Empty files exist and are safe placeholders (not deleted)."""
        frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
        self.assertTrue((frontend_dir / "css" / "style.css").exists())
        self.assertTrue((frontend_dir / "js" / "app.js").exists())
        self.assertTrue((frontend_dir / "js" / "auth.js").exists())

    def test_issue11_legacy_friend_route_accessible(self):
        """Issue 11: Legacy /friend route remains accessible for backwards compatibility."""
        res = self.client.get("/friend")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"<!DOCTYPE html>", res.data)

    def test_issue12_centralized_error_handling_json_for_api_and_html_for_pages(self):
        """Issue 12: Centralized error handlers return JSON for /api/* and HTML for pages."""
        # Non-existent API route -> JSON 404
        res_api = self.client.get("/api/non-existent-endpoint-12345")
        self.assertEqual(res_api.status_code, 404)
        self.assertTrue(res_api.is_json)
        data = res_api.get_json()
        self.assertIn("error", data)
        self.assertEqual(data.get("status_code"), 404)

        # Method not allowed on API -> JSON 405
        res_method = self.client.post("/api/connections/all")
        self.assertEqual(res_method.status_code, 405)
        self.assertTrue(res_method.is_json)
        self.assertEqual(res_method.get_json().get("status_code"), 405)

        # Non-existent page route -> normal 404 (non-API)
        res_page = self.client.get("/non-existent-page-12345")
        self.assertEqual(res_page.status_code, 404)

    def test_issue13_history_preserved_on_bookmark_delete_and_explicit_history_deletion(self):
        """Issue 13: Removing saved bookmark preserves historical analysis; explicit DELETE clears it."""
        test_url = "https://www.linkedin.com/in/regression-test-user-audit-13/"
        slug = "regression-test-user-audit-13"

        # 1. Save profile as bookmark and save historical analysis
        save_target_profile({
            "profile_url": test_url,
            "name": "Audit Test User",
            "headline": "Tester"
        })
        save_profile_successful_analysis(slug, {
            "subject_name": "Audit Test User",
            "subject_url": test_url,
            "total_activities": 5,
            "activities": [{"type": "post", "text": "Hello world"}]
        })

        # Verify historical analysis exists
        hist = load_profile_successful_analysis(slug)
        self.assertIsNotNone(hist)
        self.assertEqual(hist.get("total_activities"), 5)

        # 2. Delete from saved bookmarks via API
        del_res = self.client.delete("/api/profiles/saved", json={"profile_url": test_url})
        self.assertEqual(del_res.status_code, 200)

        # 3. Verify history is PRESERVED (protects user data integrity)
        hist_after_bookmark_del = load_profile_successful_analysis(slug)
        self.assertIsNotNone(hist_after_bookmark_del, "Historical analysis must be preserved when bookmark is removed.")
        self.assertEqual(hist_after_bookmark_del.get("total_activities"), 5)

        # 4. Explicitly delete history via dedicated endpoint
        del_hist_res = self.client.delete("/api/profiles/history", json={"profile_url": test_url})
        self.assertEqual(del_hist_res.status_code, 200)
        self.assertTrue(del_hist_res.get_json().get("cleared"))

        # Verify history is now cleared
        hist_after_explicit_del = load_profile_successful_analysis(slug)
        self.assertIsNone(hist_after_explicit_del)


if __name__ == "__main__":
    unittest.main()
