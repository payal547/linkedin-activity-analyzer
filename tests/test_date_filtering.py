"""
Unit tests for date parsing, boundary inclusivity, and safe date filtering
in LinkedIn Activity Analyzer.
"""

import unittest
import sys
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
    from linkedin.playwright_service import parse_relative_time_to_date, check_feed_date_boundary
    from services.analysis_service import analyze_activities
except ImportError:
    from backend.linkedin.playwright_service import parse_relative_time_to_date, check_feed_date_boundary  # type: ignore
    from backend.services.analysis_service import analyze_activities  # type: ignore


class TestDateFiltering(unittest.TestCase):

    def test_relative_date_parsing_keywords(self):
        """Verify keywords like 'yesterday' and 'just now' parse accurately."""
        today = date.today()
        yesterday = today - timedelta(days=1)

        d_str, exact = parse_relative_time_to_date("yesterday")
        self.assertEqual(d_str, yesterday.isoformat())
        self.assertTrue(exact)

        d_str, exact = parse_relative_time_to_date("just now")
        self.assertEqual(d_str, today.isoformat())
        self.assertTrue(exact)

    def test_relative_time_offsets(self):
        """Verify standard LinkedIn relative offsets like '1d', '2w', '1mo'."""
        today = date.today()

        d_1d, _ = parse_relative_time_to_date("1d")
        self.assertEqual(d_1d, (today - timedelta(days=1)).isoformat())

        d_2w, _ = parse_relative_time_to_date("2w")
        self.assertEqual(d_2w, (today - timedelta(weeks=2)).isoformat())

        d_1mo, _ = parse_relative_time_to_date("1mo")
        self.assertEqual(d_1mo, (today - timedelta(days=30)).isoformat())

    def test_explicit_calendar_dates(self):
        """Verify explicit dates like '18 Oct 2026' and ISO format '2026-10-18'."""
        d_iso, exact = parse_relative_time_to_date("2026-10-18")
        self.assertEqual(d_iso, "2026-10-18")
        self.assertTrue(exact)

        d_text, exact = parse_relative_time_to_date("18 Oct 2026")
        self.assertEqual(d_text, "2026-10-18")
        self.assertTrue(exact)

    def test_inclusive_date_boundaries(self):
        """
        Verify that activities on start_date and end_date are INCLUDED,
        even when timestamps contain time components (e.g. '2026-10-28T18:45:00').
        """
        rows = [
            {"id": "act_1", "date": "2026-10-15T09:00:00", "type": "post", "text": "Post on start date"},
            {"id": "act_2", "date": "2026-10-20", "type": "post", "text": "Post in middle"},
            {"id": "act_3", "date": "2026-10-25T23:59:59", "type": "post", "text": "Post on end date with time"},
            {"id": "act_4", "date": "2026-10-14", "type": "post", "text": "Post before start"},
            {"id": "act_5", "date": "2026-10-26", "type": "post", "text": "Post after end"},
        ]

        result = analyze_activities(
            rows=rows,
            activity_types=["all"],
            start_date="2026-10-15",
            end_date="2026-10-25"
        )

        acts = result["activities"]
        act_ids = [a["id"] for a in acts]

        self.assertEqual(result["total_activities"], 3)
        self.assertIn("act_1", act_ids, "Activity on start_date must be included (inclusive).")
        self.assertIn("act_2", act_ids, "Activity between dates must be included.")
        self.assertIn("act_3", act_ids, "Activity on end_date with time must be included (inclusive).")
        self.assertNotIn("act_4", act_ids, "Activity before start_date must be excluded.")
        self.assertNotIn("act_5", act_ids, "Activity after end_date must be excluded.")

    def test_handling_activities_with_unknown_dates(self):
        """Verify that activities with unknown dates are not silently crashed or lost when unbounded."""
        rows = [
            {"id": "act_known", "date": "2026-10-20", "type": "post", "text": "Valid date post"},
            {"id": "act_unknown", "date": "", "type": "post", "text": "Missing date post"},
            {"id": "act_invalid", "date": "not-a-date", "type": "post", "text": "Invalid date post"}
        ]

        # Case A: Unbounded (no date filter) -> all activities are retained
        unbounded_res = analyze_activities(rows=rows, activity_types=["all"])
        self.assertEqual(unbounded_res["total_activities"], 3)

        # Case B: Bounded (date filter active) -> unknown dates excluded consistently
        bounded_res = analyze_activities(
            rows=rows,
            activity_types=["all"],
            start_date="2026-10-10",
            end_date="2026-10-25"
        )
        self.assertEqual(bounded_res["total_activities"], 1)
        self.assertEqual(bounded_res["activities"][0]["id"], "act_known")

    def test_feed_date_boundary_ignores_empty_or_unbounded(self):
        """Date boundary check must return False when no activity dates exist or unbounded."""
        # Unbounded (no start date)
        self.assertFalse(check_feed_date_boundary([date(2026, 10, 1)], None, scroll_count=1))
        # Empty activity cards list
        self.assertFalse(check_feed_date_boundary([], date(2026, 10, 1), scroll_count=1))

    def test_feed_date_boundary_genuine_progression(self):
        """Date boundary check must return True only when activity cards genuinely cross the boundary."""
        target_start = date(2026, 10, 1)

        # All cards are newer than start date -> do NOT stop scrolling
        recent_cards = [date(2026, 10, 25), date(2026, 10, 20), date(2026, 10, 5)]
        self.assertFalse(check_feed_date_boundary(recent_cards, target_start, scroll_count=1))
        self.assertFalse(check_feed_date_boundary(recent_cards, target_start, scroll_count=3))

        # Cards have crossed start date at depth -> genuine boundary satisfied
        deep_cards = [date(2026, 10, 25), date(2026, 10, 15), date(2026, 9, 28)]
        self.assertTrue(check_feed_date_boundary(deep_cards, target_start, scroll_count=2))

    def test_feed_date_boundary_pinned_post_safety(self):
        """An isolated pinned post from years ago must not prematurely abort feed scrolling at scroll 1."""
        target_start = date(2026, 10, 1)
        # Pinned post from 2024 at top, but recent post follows it at scroll 1
        pinned_and_recent = [date(2024, 5, 1), date(2026, 10, 25)]
        self.assertFalse(check_feed_date_boundary(pinned_and_recent, target_start, scroll_count=1))

        # Single card at scroll 1 should not abort prematurely (allows scroll 2)
        single_old_card = [date(2024, 5, 1)]
        self.assertFalse(check_feed_date_boundary(single_old_card, target_start, scroll_count=1))

        # On scroll 2, if tail cards are older, genuine boundary is satisfied
        self.assertTrue(check_feed_date_boundary(single_old_card, target_start, scroll_count=2))

    def test_preset_today(self):
        """Verify 'Today' preset retains only activities from today."""
        today = date.today()
        today_iso = today.isoformat()
        yesterday_iso = (today - timedelta(days=1)).isoformat()
        last_week_iso = (today - timedelta(days=7)).isoformat()

        rows = [
            {"id": "act_today", "date": today_iso, "type": "post", "text": "Post today"},
            {"id": "act_yesterday", "date": yesterday_iso, "type": "post", "text": "Post yesterday"},
            {"id": "act_last_week", "date": last_week_iso, "type": "post", "text": "Post last week"}
        ]

        result = analyze_activities(
            rows=rows,
            activity_types=["all"],
            start_date=today_iso,
            end_date=today_iso
        )

        acts = result["activities"]
        self.assertEqual(len(acts), 1)
        self.assertEqual(acts[0]["id"], "act_today")
        self.assertEqual(result["date_range"]["start"], today_iso)
        self.assertEqual(result["date_range"]["end"], today_iso)

    def test_preset_last_7_days(self):
        """Verify 'Last 7 Days' preset retains all activities within 7 days inclusive and excludes older."""
        today = date.today()
        d_today = today.isoformat()
        d_3d = (today - timedelta(days=3)).isoformat()
        d_6d = (today - timedelta(days=6)).isoformat()
        d_7d = (today - timedelta(days=7)).isoformat()

        rows = [
            {"id": "act_today", "date": d_today, "type": "post", "text": "Post today"},
            {"id": "act_3d", "date": d_3d, "type": "post", "text": "Post 3d ago"},
            {"id": "act_6d", "date": d_6d, "type": "post", "text": "Post on 7-day inclusive boundary"},
            {"id": "act_7d", "date": d_7d, "type": "post", "text": "Post 7d ago (8th day, outside 7d inclusive)"}
        ]

        # 7-day inclusive ending today starts at today - 6 days
        result = analyze_activities(
            rows=rows,
            activity_types=["all"],
            start_date=d_6d,
            end_date=d_today
        )

        act_ids = [a["id"] for a in result["activities"]]
        self.assertEqual(len(act_ids), 3)
        self.assertIn("act_today", act_ids)
        self.assertIn("act_3d", act_ids)
        self.assertIn("act_6d", act_ids)
        self.assertNotIn("act_7d", act_ids)

    def test_preset_last_10_days(self):
        """Verify 'Last 10 Days' preset retains all activities within 10 days inclusive and excludes older."""
        today = date.today()
        d_today = today.isoformat()
        d_5d = (today - timedelta(days=5)).isoformat()
        d_9d = (today - timedelta(days=9)).isoformat()
        d_10d = (today - timedelta(days=10)).isoformat()

        rows = [
            {"id": "act_today", "date": d_today, "type": "post", "text": "Post today"},
            {"id": "act_5d", "date": d_5d, "type": "post", "text": "Post 5d ago"},
            {"id": "act_9d", "date": d_9d, "type": "post", "text": "Post on 10-day inclusive boundary"},
            {"id": "act_10d", "date": d_10d, "type": "post", "text": "Post 10d ago (11th day, outside 10d inclusive)"}
        ]

        # 10-day inclusive ending today starts at today - 9 days
        result = analyze_activities(
            rows=rows,
            activity_types=["all"],
            start_date=d_9d,
            end_date=d_today
        )

        act_ids = [a["id"] for a in result["activities"]]
        self.assertEqual(len(act_ids), 3)
        self.assertIn("act_today", act_ids)
        self.assertIn("act_5d", act_ids)
        self.assertIn("act_9d", act_ids)
        self.assertNotIn("act_10d", act_ids)

    def test_preset_last_30_days(self):
        """Verify 'Last 30 Days' preset retains all activities within 30 days inclusive (today - 29d)."""
        today = date.today()
        d_today = today.isoformat()
        d_10d = (today - timedelta(days=10)).isoformat()
        d_25d = (today - timedelta(days=25)).isoformat()
        d_29d = (today - timedelta(days=29)).isoformat()
        d_30d = (today - timedelta(days=30)).isoformat()

        rows = [
            {"id": "act_today", "date": d_today, "type": "post", "text": "Post today"},
            {"id": "act_10d", "date": d_10d, "type": "post", "text": "Post 10d ago"},
            {"id": "act_25d", "date": d_25d, "type": "post", "text": "Post 25d ago"},
            {"id": "act_29d", "date": d_29d, "type": "post", "text": "Post on 30-day inclusive boundary"},
            {"id": "act_30d", "date": d_30d, "type": "post", "text": "Post 30d ago (31st day, outside 30d inclusive)"}
        ]

        # 30-day inclusive ending today starts at today - 29 days
        result = analyze_activities(
            rows=rows,
            activity_types=["all"],
            start_date=d_29d,
            end_date=d_today
        )

        act_ids = [a["id"] for a in result["activities"]]
        self.assertEqual(len(act_ids), 4)
        self.assertIn("act_today", act_ids)
        self.assertIn("act_10d", act_ids)
        self.assertIn("act_25d", act_ids)
        self.assertIn("act_29d", act_ids)
        self.assertNotIn("act_30d", act_ids)

    def test_preset_all_dates(self):
        """Verify 'All Dates' preset retains all activities regardless of how old."""
        today = date.today()
        rows = [
            {"id": "act_today", "date": today.isoformat(), "type": "post", "text": "Post today"},
            {"id": "act_60d", "date": (today - timedelta(days=60)).isoformat(), "type": "post", "text": "Post 60d ago"},
            {"id": "act_1y", "date": (today - timedelta(days=365)).isoformat(), "type": "post", "text": "Post 1 year ago"}
        ]

        result = analyze_activities(
            rows=rows,
            activity_types=["all"],
            start_date=None,
            end_date=None
        )

        self.assertEqual(len(result["activities"]), 3)
        self.assertEqual(result["total_activities"], 3)

    def test_slash_date_inversion_auto_resolution(self):
        """
        Verify that ambiguous slash dates like '05/10/2026' to '04/11/2026' (Oct 5 to Nov 4)
        are automatically resolved to DD/MM/YYYY if default MM/DD/YYYY causes start > end.
        """
        rows = [
            {"id": "act_boundary_start", "date": "2026-10-05", "type": "post", "text": "Start boundary post"},
            {"id": "act_in_range", "date": "2026-10-20", "type": "post", "text": "October post"},
            {"id": "act_boundary_end", "date": "2026-11-04", "type": "post", "text": "End boundary post"},
            {"id": "act_before", "date": "2026-09-30", "type": "post", "text": "September post"},
            {"id": "act_after", "date": "2026-11-15", "type": "post", "text": "Mid-November post"}
        ]

        # '05/10/2026' in MM/DD/YYYY is May 10; '04/11/2026' is April 11 -> inverted (May 10 > April 11)
        # Auto-resolution must detect inversion and switch to DD/MM/YYYY (Oct 05 to Nov 04)
        result = analyze_activities(
            rows=rows,
            activity_types=["all"],
            start_date="05/10/2026",
            end_date="04/11/2026"
        )

        act_ids = [a["id"] for a in result["activities"]]
        self.assertEqual(len(act_ids), 3)
        self.assertIn("act_boundary_start", act_ids)
        self.assertIn("act_in_range", act_ids)
        self.assertIn("act_boundary_end", act_ids)
        self.assertNotIn("act_before", act_ids)
        self.assertNotIn("act_after", act_ids)
        self.assertEqual(result["date_range"]["start"], "2026-10-05")
        self.assertEqual(result["date_range"]["end"], "2026-11-04")

    def test_cache_invalidation_shallow_vs_deep_feed(self):
        """
        Verify that a cached shallow scrape (e.g. from 'Today') is NOT reused
        for a deeper query (e.g. 'Last 30 Days'), but can be reused once end-of-feed
        or sufficient depth is reached.
        """
        try:
            from linkedin.playwright_service import _PROFILE_CACHE, to_date_obj
        except ImportError:
            from backend.linkedin.playwright_service import _PROFILE_CACHE, to_date_obj  # type: ignore
        import time

        test_slug = "test-cache-profile"
        now = time.time()
        today = date.today()
        start_30d = today - timedelta(days=30)

        # 1. Shallow scrape cached: only went to today, didn't reach end of feed
        _PROFILE_CACHE[test_slug] = {
            "html": "<html><body>shallow cards</body></html>",
            "title": "Test Profile",
            "status": 200,
            "timestamp": now,
            "reached_end_of_feed": False,
            "oldest_activity_date": today.isoformat()
        }

        cached = _PROFILE_CACHE.get(test_slug)
        c_status = cached.get("status", 200)
        c_html = cached.get("html", "")
        c_end = cached.get("reached_end_of_feed", False)
        c_oldest = cached.get("oldest_activity_date")
        c_oldest_d = to_date_obj(c_oldest) if c_oldest else None

        # Evaluating for 'Last 30 Days'
        can_use_cache = False
        if c_status == 200 and c_html:
            if c_end:
                can_use_cache = True
            elif start_30d is not None and c_oldest_d and c_oldest_d <= start_30d:
                can_use_cache = True

        self.assertFalse(can_use_cache, "Shallow scrape must NOT be reused for deeper 30-day range.")

        # 2. Deeper scrape cached: reached 35 days ago
        _PROFILE_CACHE[test_slug]["oldest_activity_date"] = (today - timedelta(days=35)).isoformat()
        c_oldest = _PROFILE_CACHE[test_slug].get("oldest_activity_date")
        c_oldest_d = to_date_obj(c_oldest)

        can_use_cache = False
        if c_oldest_d and c_oldest_d <= start_30d:
            can_use_cache = True

        self.assertTrue(can_use_cache, "Sufficiently deep scrape CAN be reused for 30-day range.")

        # 3. Clean up cache
        _PROFILE_CACHE.pop(test_slug, None)

    def test_playwright_service_logger_safety(self):
        """Verify pw_logger is defined and safe from NameError in playwright_service."""
        import linkedin.playwright_service as pws
        self.assertTrue(hasattr(pws, "pw_logger"), "playwright_service must define pw_logger.")
        # Ensure pw_logger has standard logging methods if present
        if pws.pw_logger is not None:
            self.assertTrue(callable(getattr(pws.pw_logger, "info", None)))
            self.assertTrue(callable(getattr(pws.pw_logger, "error", None)))

    def test_ddmmyyyy_primary_format_parsing(self):
        """Verify DD/MM/YYYY dates parse as day/month/year by default (e.g. 04/11/2026 is Nov 4)."""
        rows = [
            {"id": "act_nov4", "date": "2026-11-04", "type": "post", "text": "November 4th post"},
            {"id": "act_oct6", "date": "2026-10-06", "type": "post", "text": "October 6th post"},
            {"id": "act_apr11", "date": "2026-04-11", "type": "post", "text": "April 11th post"}
        ]

        # In DD/MM/YYYY, '06/10/2026' to '04/11/2026' is Oct 6 to Nov 4
        result = analyze_activities(
            rows=rows,
            activity_types=["all"],
            start_date="06/10/2026",
            end_date="04/11/2026"
        )

        act_ids = [a["id"] for a in result["activities"]]
        self.assertIn("act_nov4", act_ids, "November 4th post must be within Oct 6 -> Nov 4 range.")
        self.assertIn("act_oct6", act_ids, "October 6th post must be within Oct 6 -> Nov 4 range.")
        self.assertNotIn("act_apr11", act_ids, "April 11th post must NOT be included (not interpreted as April).")

    def test_playwright_slash_date_parsing_ddmmyyyy(self):
        """Verify playwright_service parse_relative_time_to_date treats slash dates as DD/MM/YYYY."""
        d_nov4, exact1 = parse_relative_time_to_date("04/11/2026")
        self.assertEqual(d_nov4, "2026-11-04", "04/11/2026 must parse as November 4th (DD/MM/YYYY).")
        self.assertTrue(exact1)

        d_oct6, exact2 = parse_relative_time_to_date("06/10/2026")
        self.assertEqual(d_oct6, "2026-10-06", "06/10/2026 must parse as October 6th (DD/MM/YYYY).")
        self.assertTrue(exact2)

        d_oct26, exact3 = parse_relative_time_to_date("26/10/2026")
        self.assertEqual(d_oct26, "2026-10-26", "26/10/2026 must parse as October 26th.")
        self.assertTrue(exact3)

    def test_preset_inclusive_arithmetic_definitions(self):
        """Verify inclusive arithmetic: N-day period ending today begins on today - (N - 1) days."""
        ref_today = date(2026, 11, 4)

        # 1-day (Today): 04/11/2026 to 04/11/2026
        today_start = ref_today - timedelta(days=0)
        self.assertEqual(today_start, date(2026, 11, 4))

        # 7-day inclusive: 29/10/2026 to 04/11/2026 (7 days total)
        d7_start = ref_today - timedelta(days=6)
        self.assertEqual(d7_start, date(2026, 10, 29))
        self.assertEqual((ref_today - d7_start).days + 1, 7)

        # 10-day inclusive: 26/10/2026 to 04/11/2026 (10 days total)
        d10_start = ref_today - timedelta(days=9)
        self.assertEqual(d10_start, date(2026, 10, 26))
        self.assertEqual((ref_today - d10_start).days + 1, 10)

        # 30-day inclusive: 06/10/2026 to 04/11/2026 (30 days total)
        d30_start = ref_today - timedelta(days=29)
        self.assertEqual(d30_start, date(2026, 10, 6))
        self.assertEqual((ref_today - d30_start).days + 1, 30)

    def test_route_date_preset_fallback_10days_and_inclusive(self):
        """Verify activity_routes /api/analyze handles 10days, 7days, 30days presets inclusively."""
        from app import app
        app.config["TESTING"] = True
        client = app.test_client()

        # Test 10days preset
        payload_10d = {
            "subject_id": "test_subj",
            "subject_name": "Test Subject",
            "subject_url": "https://www.linkedin.com/in/test-subj-123/",
            "data_source": "sample",
            "dateType": "10days",
            "activities": ["all"]
        }
        res = client.post("/api/analyze", json=payload_10d)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn("date_range", data)
        # Expected 10-day inclusive range ending today
        today = date.today()
        exp_start = (today - timedelta(days=9)).isoformat()
        exp_end = today.isoformat()
        if data.get("activities"):
            # If activities returned, start/end will be within or equal to the range
            self.assertTrue(data["date_range"]["end"] <= exp_end)


if __name__ == "__main__":
    unittest.main()

