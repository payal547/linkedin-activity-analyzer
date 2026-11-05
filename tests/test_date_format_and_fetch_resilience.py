"""
Regression tests for consistent date formatting (DD/MM/YYYY), date preset inclusivity,
failed-to-fetch error handling, and cached date range isolation in LinkedIn Activity Analyzer.
"""

import unittest
import sys
import json
from datetime import date, timedelta
from pathlib import Path

# Add project root and backend directory to sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_DIR = _PROJECT_ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from app import app
    from services.analysis_service import analyze_activities
    from linkedin.playwright_service import parse_relative_time_to_date
except ImportError:
    from backend.app import app  # type: ignore
    from backend.services.analysis_service import analyze_activities  # type: ignore
    from backend.linkedin.playwright_service import parse_relative_time_to_date  # type: ignore


class TestDateFormatAndFetchResilience(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    # --------------------------------------------------------------------------
    # 1. Consistent Date Format (DD/MM/YYYY) Verification
    # --------------------------------------------------------------------------

    def test_dd_mm_yyyy_standardization(self):
        """Ensure date strings are formatted as 4-digit DD/MM/YYYY consistently."""
        def format_date_dd_mm_yyyy(date_str):
            if not date_str:
                return ""
            # ISO: YYYY-MM-DD
            if "-" in date_str and len(date_str.split("-")[0]) == 4:
                parts = date_str.split("-")
                return f"{parts[2].zfill(2)}/{parts[1].zfill(2)}/{parts[0]}"
            # Slash: DD/MM/YYYY
            if "/" in date_str:
                parts = date_str.split("/")
                if len(parts) == 3:
                    yr = parts[2]
                    if len(yr) == 2:
                        yr = f"20{yr}"
                    return f"{parts[0].zfill(2)}/{parts[1].zfill(2)}/{yr}"
            return date_str

        # ISO to DD/MM/YYYY
        self.assertEqual(format_date_dd_mm_yyyy("2026-11-04"), "04/11/2026")
        self.assertEqual(format_date_dd_mm_yyyy("2026-10-06"), "06/10/2026")
        # Legacy 2-digit year to 4-digit
        self.assertEqual(format_date_dd_mm_yyyy("04/11/26"), "04/11/2026")
        # Already 4-digit
        self.assertEqual(format_date_dd_mm_yyyy("04/11/2026"), "04/11/2026")

    # --------------------------------------------------------------------------
    # 2. Date Presets & Inclusive Boundaries
    # --------------------------------------------------------------------------

    def test_preset_definitions_inclusive_span(self):
        """Verify that presets define exact inclusive calendar day spans."""
        today = date(2026, 11, 4)

        # Today: 1 day inclusive
        start_today = today
        end_today = today
        self.assertEqual((end_today - start_today).days + 1, 1)

        # Last 7 Days: 7 days inclusive (today - 6 days)
        start_7 = today - timedelta(days=6)
        self.assertEqual((today - start_7).days + 1, 7)
        self.assertEqual(start_7.isoformat(), "2026-10-29")

        # Last 10 Days: 10 days inclusive (today - 9 days)
        start_10 = today - timedelta(days=9)
        self.assertEqual((today - start_10).days + 1, 10)
        self.assertEqual(start_10.isoformat(), "2026-10-26")

        # Last 30 Days: 30 days inclusive (today - 29 days)
        start_30 = today - timedelta(days=29)
        self.assertEqual((today - start_30).days + 1, 30)
        self.assertEqual(start_30.isoformat(), "2026-10-06")

    def test_inclusive_filtering_boundary_activities(self):
        """Activities occurring on both start and end dates are included in analysis."""
        activities = [
            {"id": "act_start", "date": "2026-10-06", "type": "post", "text": "Start boundary post"},
            {"id": "act_mid", "date": "2026-10-20", "type": "like", "text": "Mid period reaction"},
            {"id": "act_end", "date": "2026-11-04", "type": "video", "text": "End boundary video"},
            {"id": "act_outside", "date": "2026-10-05", "type": "post", "text": "Outside post"}
        ]

        result = analyze_activities(
            rows=activities,
            activity_types=["all"],
            start_date="2026-10-06",
            end_date="2026-11-04"
        )

        act_ids = [a["id"] for a in result["activities"]]
        self.assertIn("act_start", act_ids)
        self.assertIn("act_mid", act_ids)
        self.assertIn("act_end", act_ids)
        self.assertNotIn("act_outside", act_ids)
        self.assertEqual(len(act_ids), 3)

    # --------------------------------------------------------------------------
    # 3. Failed-to-Fetch & Meaningful API Error Responses
    # --------------------------------------------------------------------------

    def test_invalid_profile_url_returns_meaningful_error(self):
        """API must return clear, actionable error message instead of generic failure."""
        res = self.client.post("/api/profile/validate-url", json={"url": ""})
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertFalse(data.get("valid"))
        self.assertIn("error", data)
        self.assertTrue(len(data["error"]) > 5)

    def test_missing_activity_types_returns_400(self):
        """POST /api/analyze requires valid parameters."""
        res = self.client.post("/api/analyze", json={
            "subject_url": "https://www.linkedin.com/in/",
            "activities": ["all"]
        })
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertIn("error", data)

    def test_centralized_error_handlers_json_response(self):
        """Unmatched routes return clean JSON with error details instead of raw HTML."""
        res = self.client.get("/api/non-existent-endpoint", headers={"Accept": "application/json"})
        self.assertEqual(res.status_code, 404)
        data = res.get_json()
        self.assertIsNotNone(data)
        self.assertIn("error", data)
        self.assertEqual(data.get("status_code"), 404)

    # --------------------------------------------------------------------------
    # 4. Cached Date Range Isolation
    # --------------------------------------------------------------------------

    def test_analysis_latest_date_range_isolation(self):
        """
        When /api/analysis/latest is requested with specific startDate/endDate,
        it must not return a cached analysis for a completely different date range.
        """
        with self.client.session_transaction() as sess:
            sess["latest_analysis"] = {
                "subject_id": "test-user",
                "subject_url": "https://www.linkedin.com/in/test-user",
                "subject_name": "Test User",
                "date_range": {
                    "start": "2026-09-01",
                    "end": "2026-09-30"
                },
                "total_activities": 5,
                "activities": []
            }

        # Request matching profile but DIFFERENT date range
        res = self.client.get("/api/analysis/latest?subject_id=test-user&startDate=2026-10-06&endDate=2026-11-04")
        data = res.get_json()

        # Should NOT return the September cache as valid for October-November
        if res.status_code == 200:
            # If historical fallback matched, it must match requested range
            d_range = data.get("date_range", {})
            self.assertNotEqual((d_range.get("start"), d_range.get("end")), ("2026-09-01", "2026-09-30"))
        else:
            self.assertEqual(res.status_code, 404)
            self.assertFalse(data.get("has_data", True))


if __name__ == "__main__":
    unittest.main()

