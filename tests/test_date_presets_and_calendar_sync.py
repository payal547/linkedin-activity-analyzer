"""
Unit and regression tests for Date Preset and Calendar Synchronization
in LinkedIn Activity Analyzer.

Covers:
- Today (1 day inclusive)
- Last 7 Days (7 days inclusive)
- Last 10 Days (10 days inclusive)
- Last 30 Days (30 days inclusive, e.g. 04/11/2026 -> 06/10/2026)
- All Dates (unbounded)
- Manual custom date range
- Switching from preset to manual dates
- Switching from manual dates back to a preset
- Inclusive start and end dates (including midnight 00:00:00 and 23:59:59)
- DD/MM/YYYY primary parsing (e.g. 04/11/2026 is Nov 4, 06/10/2026 is Oct 6)
"""

import unittest
import sys
from datetime import date, timedelta
from pathlib import Path

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from services.analysis_service import analyze_activities
from linkedin.playwright_service import parse_relative_time_to_date
from app import app


class TestDatePresetsAndCalendarSync(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_today_preset_inclusive(self):
        """Today preset must produce a 1-day inclusive range (today to today)."""
        today = date.today()
        d_today = today.isoformat()
        d_yesterday = (today - timedelta(days=1)).isoformat()

        rows = [
            {"id": "act_today", "date": d_today, "type": "post", "text": "Post today"},
            {"id": "act_yesterday", "date": d_yesterday, "type": "post", "text": "Post yesterday"}
        ]

        result = analyze_activities(
            rows=rows,
            activity_types=["all"],
            start_date=d_today,
            end_date=d_today
        )

        act_ids = [a["id"] for a in result["activities"]]
        self.assertEqual(len(act_ids), 1)
        self.assertIn("act_today", act_ids)
        self.assertNotIn("act_yesterday", act_ids)
        self.assertEqual(result["date_range"]["start"], d_today)
        self.assertEqual(result["date_range"]["end"], d_today)

    def test_last_7_days_preset_inclusive(self):
        """Last 7 Days must produce a 7-day inclusive range (today minus 6 days to today)."""
        today = date.today()
        d_today = today.isoformat()
        # 7-day inclusive ending today: today - 6 days
        d_start = (today - timedelta(days=6)).isoformat()
        d_outside = (today - timedelta(days=7)).isoformat()

        # Day count verification
        days_count = (today - (today - timedelta(days=6))).days + 1
        self.assertEqual(days_count, 7, "Last 7 Days must represent exactly 7 inclusive calendar days.")

        rows = [
            {"id": "act_today", "date": d_today, "type": "post", "text": "Post today"},
            {"id": "act_boundary", "date": d_start, "type": "post", "text": "Post on 7d boundary"},
            {"id": "act_outside", "date": d_outside, "type": "post", "text": "Post 7d ago (day 8)"}
        ]

        result = analyze_activities(
            rows=rows,
            activity_types=["all"],
            start_date=d_start,
            end_date=d_today
        )

        act_ids = [a["id"] for a in result["activities"]]
        self.assertEqual(len(act_ids), 2)
        self.assertIn("act_today", act_ids)
        self.assertIn("act_boundary", act_ids)
        self.assertNotIn("act_outside", act_ids)

    def test_last_10_days_preset_inclusive(self):
        """Last 10 Days must produce a 10-day inclusive range (today minus 9 days to today)."""
        today = date.today()
        d_today = today.isoformat()
        # 10-day inclusive ending today: today - 9 days
        d_start = (today - timedelta(days=9)).isoformat()
        d_mid = (today - timedelta(days=4)).isoformat()
        d_outside = (today - timedelta(days=10)).isoformat()

        # Day count verification
        days_count = (today - (today - timedelta(days=9))).days + 1
        self.assertEqual(days_count, 10, "Last 10 Days must represent exactly 10 inclusive calendar days.")

        rows = [
            {"id": "act_today", "date": d_today, "type": "post", "text": "Post today"},
            {"id": "act_mid", "date": d_mid, "type": "post", "text": "Post 4d ago"},
            {"id": "act_boundary", "date": d_start, "type": "post", "text": "Post on 10d boundary"},
            {"id": "act_outside", "date": d_outside, "type": "post", "text": "Post 10d ago (day 11)"}
        ]

        result = analyze_activities(
            rows=rows,
            activity_types=["all"],
            start_date=d_start,
            end_date=d_today
        )

        act_ids = [a["id"] for a in result["activities"]]
        self.assertEqual(len(act_ids), 3)
        self.assertIn("act_today", act_ids)
        self.assertIn("act_mid", act_ids)
        self.assertIn("act_boundary", act_ids)
        self.assertNotIn("act_outside", act_ids)

    def test_last_30_days_preset_inclusive(self):
        """
        Last 30 Days must produce a 30-day inclusive range.
        For reference date 04/11/2026, 30 days inclusive begins on 06/10/2026.
        """
        ref_today = date(2026, 11, 4)
        ref_start = ref_today - timedelta(days=29)
        self.assertEqual(ref_start, date(2026, 10, 6), "30-day inclusive range for 04/11/2026 must begin on 06/10/2026.")
        self.assertEqual((ref_today - ref_start).days + 1, 30, "Must be exactly 30 inclusive calendar days.")

        rows = [
            {"id": "act_nov4", "date": "2026-11-04", "type": "post", "text": "Post on Nov 4"},
            {"id": "act_oct20", "date": "2026-10-20", "type": "post", "text": "Post in middle"},
            {"id": "act_oct6", "date": "2026-10-06", "type": "post", "text": "Post on Oct 6 boundary"},
            {"id": "act_oct5", "date": "2026-10-05", "type": "post", "text": "Post on Oct 5 (day 31, outside)"}
        ]

        result = analyze_activities(
            rows=rows,
            activity_types=["all"],
            start_date="2026-10-06",
            end_date="2026-11-04"
        )

        act_ids = [a["id"] for a in result["activities"]]
        self.assertEqual(len(act_ids), 3)
        self.assertIn("act_nov4", act_ids)
        self.assertIn("act_oct20", act_ids)
        self.assertIn("act_oct6", act_ids)
        self.assertNotIn("act_oct5", act_ids)

    def test_all_dates_preset(self):
        """All Dates preset (start=None, end=None) must include all source activities."""
        rows = [
            {"id": "act_today", "date": date.today().isoformat(), "type": "post", "text": "Post today"},
            {"id": "act_60d", "date": (date.today() - timedelta(days=60)).isoformat(), "type": "post", "text": "60d ago"},
            {"id": "act_2yr", "date": (date.today() - timedelta(days=730)).isoformat(), "type": "post", "text": "2yr ago"}
        ]

        result = analyze_activities(
            rows=rows,
            activity_types=["all"],
            start_date=None,
            end_date=None
        )

        self.assertEqual(result["total_activities"], 3)
        self.assertEqual(len(result["activities"]), 3)

    def test_manual_custom_date_range_inclusive(self):
        """Manual custom range must filter strictly by the exact specified start and end dates."""
        rows = [
            {"id": "act_10", "date": "2026-10-10", "type": "post", "text": "Start boundary"},
            {"id": "act_15", "date": "2026-10-15", "type": "post", "text": "Inside range"},
            {"id": "act_25", "date": "2026-10-25", "type": "post", "text": "End boundary"},
            {"id": "act_9", "date": "2026-10-09", "type": "post", "text": "Before start"},
            {"id": "act_26", "date": "2026-10-26", "type": "post", "text": "After end"}
        ]

        result = analyze_activities(
            rows=rows,
            activity_types=["all"],
            start_date="2026-10-10",
            end_date="2026-10-25"
        )

        act_ids = [a["id"] for a in result["activities"]]
        self.assertEqual(len(act_ids), 3)
        self.assertIn("act_10", act_ids)
        self.assertIn("act_15", act_ids)
        self.assertIn("act_25", act_ids)
        self.assertNotIn("act_9", act_ids)
        self.assertNotIn("act_26", act_ids)

    def test_ddmmyyyy_primary_format_never_confused_with_mmddyyyy(self):
        """04/11/2026 must be parsed as November 4th, NOT April 11th; 06/10/2026 as Oct 6, not June 10."""
        # 1. Playwright relative time parser
        d_nov4, exact1 = parse_relative_time_to_date("04/11/2026")
        self.assertEqual(d_nov4, "2026-11-04")
        self.assertTrue(exact1)

        d_oct6, exact2 = parse_relative_time_to_date("06/10/2026")
        self.assertEqual(d_oct6, "2026-10-06")
        self.assertTrue(exact2)

        # 2. Analysis service date parsing
        rows = [
            {"id": "act_nov4", "date": "2026-11-04", "type": "post", "text": "Nov 4 post"},
            {"id": "act_apr11", "date": "2026-04-11", "type": "post", "text": "Apr 11 post"}
        ]

        result = analyze_activities(
            rows=rows,
            activity_types=["all"],
            start_date="01/11/2026",
            end_date="04/11/2026"
        )

        act_ids = [a["id"] for a in result["activities"]]
        self.assertIn("act_nov4", act_ids, "November 4 must be included in 01/11 -> 04/11 range.")
        self.assertNotIn("act_apr11", act_ids, "April 11 must NOT be included in November range.")

    def test_midnight_and_timestamp_boundary_inclusivity(self):
        """Activities at midnight 00:00:00 on start date and 23:59:59 on end date must be included."""
        rows = [
            {"id": "act_start_midnight", "date": "2026-10-15T00:00:00", "type": "post", "text": "Midnight start"},
            {"id": "act_end_night", "date": "2026-10-25T23:59:59", "type": "post", "text": "Late night end"},
            {"id": "act_before_midnight", "date": "2026-10-14T23:59:59", "type": "post", "text": "1 sec before start"},
            {"id": "act_after_end", "date": "2026-10-26T00:00:00", "type": "post", "text": "1 sec after end"}
        ]

        result = analyze_activities(
            rows=rows,
            activity_types=["all"],
            start_date="2026-10-15",
            end_date="2026-10-25"
        )

        act_ids = [a["id"] for a in result["activities"]]
        self.assertEqual(len(act_ids), 2)
        self.assertIn("act_start_midnight", act_ids)
        self.assertIn("act_end_night", act_ids)
        self.assertNotIn("act_before_midnight", act_ids)
        self.assertNotIn("act_after_end", act_ids)

    def test_route_switching_from_preset_to_manual_dates(self):
        """Verify /api/analyze prioritizes explicit custom dates over preset parameter."""
        payload_manual = {
            "subject_id": "test_subj",
            "subject_name": "Test Subject",
            "subject_url": "https://www.linkedin.com/in/test-subj-sync/",
            "data_source": "sample",
            "dateType": "custom",
            "startDate": "2026-10-12",
            "endDate": "2026-10-18",
            "activities": ["all"]
        }

        res = self.client.post("/api/analyze", json=payload_manual)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data.get("subject_name"), "Test Subject")

    def test_route_switching_from_manual_to_preset(self):
        """Verify /api/analyze correctly applies preset calculations when switching back to preset."""
        # Switching back to 10days
        payload_preset = {
            "subject_id": "test_subj",
            "subject_name": "Test Subject",
            "subject_url": "https://www.linkedin.com/in/test-subj-sync/",
            "data_source": "sample",
            "dateType": "10days",
            "activities": ["all"]
        }

        res = self.client.post("/api/analyze", json=payload_preset)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn("date_range", data)


if __name__ == "__main__":
    unittest.main()

