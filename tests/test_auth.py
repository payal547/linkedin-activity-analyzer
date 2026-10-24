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


if __name__ == "__main__":
    unittest.main()
