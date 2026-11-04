"""
Unit tests for Analysis Server-Side Storage and Session Cookie Size Optimization.
Verifies that:
1. Latest analysis is safely persisted server-side.
2. API responses on /api/analyze do NOT emit oversized session cookies (> 4093 bytes).
3. /api/analysis/latest can retrieve stored analysis without depending on large client-side session cookies.
4. Backward compatibility is preserved for tests setting session['latest_analysis'].
5. Dashboard summary endpoint correctly includes latest analysis.
"""

import sys
import unittest
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from app import app
from services.analysis_storage import (
    save_latest_analysis,
    load_latest_analysis,
    clear_latest_analysis,
    _get_storage_file
)


class TestAnalysisStorage(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        clear_latest_analysis()

    def tearDown(self):
        clear_latest_analysis()

    def test_save_and_load_latest_analysis(self):
        """Verify saving and retrieving analysis data server-side."""
        sample_analysis = {
            "subject_name": "Test User",
            "total_activities": 15,
            "posts_count": 5,
            "comments_count": 10,
            "activities": [
                {
                    "activity_id": f"act_{i}",
                    "text": f"This is activity number {i} with some long text to simulate real data",
                    "sentiment": "Positive",
                    "tone": "Professional"
                }
                for i in range(15)
            ]
        }

        save_latest_analysis(sample_analysis)
        loaded = load_latest_analysis()

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.get("subject_name"), "Test User")
        self.assertEqual(loaded.get("total_activities"), 15)
        self.assertEqual(len(loaded.get("activities", [])), 15)

    def test_clear_latest_analysis(self):
        """Verify clearing latest analysis removes the file."""
        save_latest_analysis({"test": "data"})
        self.assertIsNotNone(load_latest_analysis())

        clear_latest_analysis()
        self.assertIsNone(load_latest_analysis())

    def test_analyze_endpoint_cookie_size(self):
        """
        Verify /api/analyze does NOT set an oversized session cookie (> 4093 bytes).
        RFC 6265 and browsers reject cookies exceeding 4096 bytes.
        """
        # Create an analysis with multiple activities containing text and metadata
        activities_data = [
            {
                "activity_id": f"act_{i}",
                "type": "post",
                "text": f"Detailed activity text description for item {i} to verify cookie size limit. " * 3,
                "likes": i * 5,
                "comments": i * 2,
                "shares": i,
                "sentiment": "Positive",
                "tone": "Optimistic"
            }
            for i in range(15)
        ]

        # Save an activity so analyze finds it
        self.client.post("/api/activities/add", json={
            "subject_name": "Storage Test Profile",
            "type": "post",
            "text": "Detailed post content for storage test",
            "likes": 10,
            "comments": 2
        })

        payload = {
            "subject_id": "self",
            "subject_name": "Storage Test Profile",
            "data_source": "real_linkedin",
            "dateType": "all",
            "activities": ["posts", "comments"]
        }

        response = self.client.post("/api/analyze", json=payload)
        self.assertEqual(response.status_code, 200)

        # Inspect Set-Cookie header if present
        cookie_header = response.headers.get("Set-Cookie", "")
        # The entire Set-Cookie header must be well below 4093 bytes
        self.assertLess(
            len(cookie_header.encode("utf-8")),
            4093,
            f"Set-Cookie header size ({len(cookie_header.encode('utf-8'))} bytes) exceeds browser limit of 4093 bytes!"
        )

        # Verify analysis was persisted server-side
        persisted = load_latest_analysis()
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted.get("subject_name"), "Storage Test Profile")

    def test_get_latest_analysis_server_fallback(self):
        """
        Verify /api/analysis/latest succeeds via server-side storage
        even when the client has NO session cookie.
        """
        sample_result = {
            "subject_name": "Server Stored Profile",
            "total_activities": 8,
            "activities": [{"id": 1, "text": "Post 1"}]
        }
        save_latest_analysis(sample_result)

        # Fresh client without cookies
        fresh_client = app.test_client()
        res = fresh_client.get("/api/analysis/latest")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("has_data"))
        self.assertEqual(data.get("subject_name"), "Server Stored Profile")
        self.assertEqual(data.get("total_activities"), 8)

    def test_get_latest_analysis_backward_compatibility(self):
        """
        Verify that explicit session['latest_analysis'] (e.g. from existing tests)
        is still respected and prioritized.
        """
        # Server-side has one value
        save_latest_analysis({"total_activities": 99, "source": "server"})

        # Client session has another
        with self.client.session_transaction() as sess:
            sess["latest_analysis"] = {"total_activities": 42, "source": "session"}

        res = self.client.get("/api/analysis/latest")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data.get("total_activities"), 42)
        self.assertEqual(data.get("source"), "session")

    def test_dashboard_summary_with_server_analysis(self):
        """Verify /api/dashboard/summary falls back to server-side analysis."""
        save_latest_analysis({"total_activities": 7, "likes": 20})

        fresh_client = app.test_client()
        res = fresh_client.get("/api/dashboard/summary")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data.get("latest_analysis", {}).get("total_activities"), 7)


if __name__ == "__main__":
    unittest.main()

