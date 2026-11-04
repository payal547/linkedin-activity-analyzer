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

    def test_latest_analysis_endpoint_serves_historical_data_on_failed_attempt(self):
        from services.analysis_storage import save_latest_analysis, clear_latest_analysis

        payload = {
            "subject_name": "Dr. Neel Mani",
            "subject_url": "https://www.linkedin.com/in/neel-mani/",
            "total_activities": 0,
            "posts_count": 0,
            "comments_count": 0,
            "likes_count": 0,
            "shares_count": 0,
            "videos_count": 0,
            "activities": [],
            "status_type": "RATE_LIMITED_999",
            "retrieval_completeness": "BLOCKED",
            "historical_successful_analysis": {
                "subject_name": "Dr. Neel Mani",
                "total_activities": 10,
                "posts_count": 4,
                "comments_count": 3,
                "likes_count": 2,
                "shares_count": 1,
                "videos_count": 0,
                "activities": [{"id": f"act_{i}", "type": "post"} for i in range(10)],
                "date_range": {"start": "2026-07-16", "end": "2026-10-29"}
            }
        }
        save_latest_analysis(payload)

        res = self.client.get("/api/analysis/latest")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("has_data"))
        self.assertEqual(data.get("status_type"), "RATE_LIMITED_999")
        self.assertEqual(data.get("retrieval_completeness"), "BLOCKED")
        self.assertIn("historical_successful_analysis", data)
        hist = data["historical_successful_analysis"]
        self.assertEqual(hist["total_activities"], 10)
        self.assertEqual(hist["posts_count"], 4)
        self.assertEqual(len(hist["activities"]), 10)
        self.assertEqual(hist["date_range"]["start"], "2026-07-16")

        clear_latest_analysis()


if __name__ == "__main__":
    unittest.main()

