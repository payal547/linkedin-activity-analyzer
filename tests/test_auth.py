import unittest
from unittest.mock import patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from app import app


class TestAuth(unittest.TestCase):

    def setUp(self):
        self.client = app.test_client()

    def test_linkedin_login_redirect(self):
        # When hitting from matching host 127.0.0.1:5000
        response = self.client.get("/auth/linkedin", headers={"Host": "127.0.0.1:5000"})
        self.assertEqual(response.status_code, 302)
        location = response.headers.get("Location")
        self.assertIn("linkedin.com/oauth/v2/authorization", location)
        self.assertIn("client_id=", location)

    def test_linkedin_login_host_alignment(self):
        # When hitting localhost, redirect to 127.0.0.1 to align session cookie
        response = self.client.get("/auth/linkedin", headers={"Host": "localhost:5000"})
        self.assertEqual(response.status_code, 302)
        location = response.headers.get("Location")
        self.assertEqual(location, "http://127.0.0.1:5000/auth/linkedin")


    def test_callback_without_code(self):
        with self.client.session_transaction() as sess:
            sess["oauth_state"] = "valid_state"

        response = self.client.get("/auth/linkedin/callback?state=valid_state")
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"no authorization code", response.data)

    def test_callback_invalid_state(self):
        with self.client.session_transaction() as sess:
            sess["oauth_state"] = "state_a"

        response = self.client.get("/auth/linkedin/callback?code=abc&state=state_b")
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"session state mismatch", response.data)

    def test_logout(self):
        with self.client.session_transaction() as sess:
            sess["linkedin_profile"] = {"name": "Test"}
            sess["linkedin_access_token"] = "token123"

        response = self.client.get("/auth/logout")
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as sess:
            self.assertNotIn("linkedin_profile", sess)
            self.assertNotIn("linkedin_access_token", sess)

    def test_session_status_preserves_legitimate_oauth_session(self):
        # A real OAuth user logged in (e.g. Jane Doe)
        with self.client.session_transaction() as sess:
            sess["linkedin_profile"] = {
                "name": "Jane Doe",
                "email": "jane.doe@example.com",
                "headline": "Senior Software Architect",
                "picture": "https://media.licdn.com/dms/image/jane.jpg"
            }
            sess["linkedin_access_token"] = "real_oauth_token_jane"

        response = self.client.get("/api/auth/session-status")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data.get("authenticated"))
        self.assertEqual(data.get("name"), "Jane Doe")
        self.assertEqual(data.get("headline"), "Senior Software Architect")
        self.assertEqual(data.get("picture"), "https://media.licdn.com/dms/image/jane.jpg")
        self.assertEqual(data.get("account_type"), "LinkedIn Account")
        # Must not be overwritten by Aniket Pal
        self.assertNotEqual(data.get("name"), "Aniket Pal")

    def test_session_status_does_not_activate_in_production(self):
        # In production mode without an active session, test account must NOT auto-initialize
        with patch.dict("os.environ", {"APP_ENV": "production"}):
            with patch.dict(app.config, {"TESTING": False}):
                response = self.client.get("/api/auth/session-status")
                self.assertEqual(response.status_code, 401)
                data = response.get_json()
                self.assertFalse(data.get("authenticated"))


if __name__ == "__main__":
    unittest.main()
