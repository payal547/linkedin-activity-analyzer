import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from app import app


class TestDashboard(unittest.TestCase):

    def setUp(self):
        self.client = app.test_client()

    def test_dashboard_summary_unauthenticated(self):
        response = self.client.get("/api/dashboard/summary")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertFalse(data.get("connected"))

    def test_dashboard_summary_authenticated(self):
        with self.client.session_transaction() as sess:
            sess["linkedin_profile"] = {"name": "Test User", "email": "test@example.com"}
            sess["latest_analysis"] = {"total_activities": 12, "likes": 50}

        response = self.client.get("/api/dashboard/summary")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data.get("connected"))
        self.assertEqual(data.get("profile", {}).get("name"), "Test User")
        self.assertEqual(data.get("latest_analysis", {}).get("total_activities"), 12)


if __name__ == "__main__":
    unittest.main()

