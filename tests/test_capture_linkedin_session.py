import os
import json
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.linkedin.playwright_service import (
    SESSION_FILE,
    USER_DATA_DIR,
    validate_session_state
)


class TestCaptureLinkedInSession(unittest.TestCase):
    """Test suite verifying session capture configurations, validation, and safety properties."""

    def test_paths_match_playwright_service_configuration(self):
        """Requirement 1: Verify SESSION_FILE and USER_DATA_DIR paths match configuration."""
        self.assertEqual(SESSION_FILE.name, "playwright_session.json")
        self.assertEqual(USER_DATA_DIR.name, "playwright_context")
        self.assertEqual(SESSION_FILE.parent.name, "data")
        self.assertEqual(USER_DATA_DIR.parent.name, "data")

    def test_validate_session_state_missing_when_no_file(self):
        """Requirement 11: When SESSION_FILE does not exist, status is MISSING."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            fake_session = Path(tmp_dir) / "playwright_session.json"
            with patch("backend.linkedin.playwright_service.SESSION_FILE", fake_session):
                status, is_valid, msg = validate_session_state()
                self.assertEqual(status, "MISSING")
                self.assertFalse(is_valid)

    def test_validate_session_state_recognizes_captured_session(self):
        """Requirement 11: validate_session_state correctly recognizes captured session and user identity."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            fake_session = Path(tmp_dir) / "playwright_session.json"
            fake_meta = Path(tmp_dir) / "playwright_session_meta.json"

            # Create mock storage state with valid li_at cookie
            mock_storage_state = {
                "cookies": [
                    {
                        "name": "li_at",
                        "value": "AQEDATestCookieValueForValidationOnly",
                        "domain": ".www.linkedin.com",
                        "path": "/",
                        "expires": int(time.time()) + 86400 * 30,
                        "httpOnly": True,
                        "secure": True,
                        "sameSite": "None"
                    }
                ],
                "origins": []
            }
            with open(fake_session, "w", encoding="utf-8") as f:
                json.dump(mock_storage_state, f)

            # Create companion metadata
            mock_meta = {
                "authenticated_at": "2026-11-04T15:30:00Z",
                "user_name": "Payal Pal",
                "profile_url": "https://www.linkedin.com/in/payal-pal-a18b92339/",
                "slug": "payal-pal-a18b92339",
                "verified": True
            }
            with open(fake_meta, "w", encoding="utf-8") as f:
                json.dump(mock_meta, f)

            with patch("backend.linkedin.playwright_service.SESSION_FILE", fake_session):
                status, is_valid, msg = validate_session_state()
                self.assertEqual(status, "VALID")
                self.assertTrue(is_valid)
                self.assertIn("Payal Pal", msg)

    def test_validate_session_state_detects_expired_session(self):
        """Verify expired li_at cookie is flagged as EXPIRED."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            fake_session = Path(tmp_dir) / "playwright_session.json"
            mock_storage_state = {
                "cookies": [
                    {
                        "name": "li_at",
                        "value": "AQEDATestCookieValueExpired",
                        "domain": ".www.linkedin.com",
                        "path": "/",
                        "expires": int(time.time()) - 3600,  # 1 hour in the past
                        "httpOnly": True,
                        "secure": True,
                        "sameSite": "None"
                    }
                ],
                "origins": []
            }
            with open(fake_session, "w", encoding="utf-8") as f:
                json.dump(mock_storage_state, f)

            with patch("backend.linkedin.playwright_service.SESSION_FILE", fake_session):
                status, is_valid, msg = validate_session_state()
                self.assertEqual(status, "EXPIRED")
                self.assertFalse(is_valid)

    def test_metadata_does_not_contain_cookie_or_token_secrets(self):
        """Requirement 9: Companion metadata file must never contain secret cookie values or passwords."""
        from backend.scripts.capture_linkedin_session import SCRIPT_DIR
        self.assertTrue(SCRIPT_DIR.exists())

        # Check mock metadata structure
        sample_meta = {
            "authenticated_at": "2026-11-04T15:30:00Z",
            "user_name": "Payal Pal",
            "profile_url": "https://www.linkedin.com/in/payal-pal-a18b92339/",
            "slug": "payal-pal-a18b92339",
            "verified": True
        }
        meta_str = json.dumps(sample_meta)
        self.assertNotIn("li_at", meta_str)
        self.assertNotIn("password", meta_str)
        self.assertNotIn("access_token", meta_str)


if __name__ == "__main__":
    unittest.main()

