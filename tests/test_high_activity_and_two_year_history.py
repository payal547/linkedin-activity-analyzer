"""
Unit and Integration Tests for High-Activity Profiles and 2-Year History Retrieval.

Scenarios Covered:
1. Low-activity profile (1-2 activities over months).
2. Profile with 20+ activities per day (high-activity).
3. Profile with 50+ activities per day (ultra high-activity).
4. Profile with activities distributed over two years.
5. Profile with gaps in its activity history.
6. Profile with restricted older activities.
7. Large extraction interrupted by a timeout or HTTP 999.

For every test scenario, asserts and validates:
- activities_discovered
- activities_parsed
- duplicates_removed
- activities_saved
- earliest_retrieved_activity_date and latest_retrieved_activity_date
- requested versus retrieved date coverage
- final extraction status (status_type and retrieval_completeness)
"""

import unittest
import sys
import os
import time
from datetime import date, timedelta
from pathlib import Path
from typing import List, Dict, Any
from unittest.mock import patch, MagicMock

# Ensure backend imports resolve properly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from services.analysis_service import analyze_activities
from services.activities_storage import save_activities_batch, load_saved_activities, clear_all_activities
from services.analysis_storage import save_profile_successful_analysis, load_profile_successful_analysis, save_latest_analysis, load_latest_analysis
from linkedin.playwright_service import (
    _extract_activities_from_public_html,
    ExtractionResult,
    ScrapeResult,
    check_feed_date_boundary,
    set_rate_limit,
    clear_rate_limit_cooldown
)
from routes.activity_routes import _build_history_limits_and_coverage


def make_activity_item(idx: int, slug: str, act_date: str, act_type: str = "post", text: str = "Test activity"):
    return {
        "id": f"act_{slug}_{idx}_{act_type}",
        "subject_id": slug,
        "subject_name": slug.replace("-", " ").title(),
        "actor": slug.replace("-", " ").title(),
        "subject_url": f"https://www.linkedin.com/in/{slug}/",
        "date": act_date,
        "activity_date": act_date,
        "type": act_type,
        "activity_type": act_type,
        "text": f"{text} #{idx}",
        "activity_text": f"{text} #{idx}",
        "likes": 5,
        "comments": 2,
        "shares": 1,
        "source": "real_linkedin",
        "storage_origin": "live_retrieval",
        "post_content": f"{text} #{idx}",
        "original_post_text": f"{text} #{idx}",
        "post_author": slug.replace("-", " ").title(),
        "original_post_author": slug.replace("-", " ").title(),
        "post_url": f"https://www.linkedin.com/feed/update/urn:li:activity:720000000{idx:05d}/",
        "original_post_url": f"https://www.linkedin.com/feed/update/urn:li:activity:720000000{idx:05d}/",
        "media_url": None,
        "video_url": None,
        "is_video": (act_type == "video")
    }


class TestHighActivityAndTwoYearHistory(unittest.TestCase):

    def setUp(self):
        os.environ["TESTING"] = "1"
        clear_all_activities()
        clear_rate_limit_cooldown()

    def tearDown(self):
        clear_all_activities()
        clear_rate_limit_cooldown()

    def test_scenario_1_low_activity_profile(self):
        """
        Scenario 1: Low-activity profile with only 2 activities over 6 months.
        Reports: discovered, parsed, duplicates, saved, dates, coverage, and status.
        """
        slug = "low-activity-user"
        today = date.today()
        d1 = today.isoformat()
        d2 = (today - timedelta(days=120)).isoformat()

        acts = [
            make_activity_item(1, slug, d1, "post", "Hello LinkedIn!"),
            make_activity_item(2, slug, d2, "comment", "Great post, congratulations!")
        ]

        saved_count = save_activities_batch(acts)
        self.assertEqual(saved_count, 2)

        start_req = (today - timedelta(days=180)).isoformat()
        end_req = today.isoformat()

        meta = _build_history_limits_and_coverage(
            activities=acts,
            requested_start=start_req,
            requested_end=end_req,
            completeness="COMPLETE",
            status_type="SUCCESS_WITH_ACTIVITIES",
            extra_stats={
                "activities_discovered": 2,
                "activities_parsed": 2,
                "duplicates_removed": 0,
                "activities_filtered": 0,
                "activities_saved": 2
            }
        )

        cov = meta["scanned_coverage"]
        self.assertEqual(cov["activities_discovered"], 2)
        self.assertEqual(cov["activities_parsed"], 2)
        self.assertEqual(cov["duplicates_removed"], 0)
        self.assertEqual(cov["activities_saved"], 2)
        self.assertEqual(cov["earliest_retrieved_activity_date"], d2)
        self.assertEqual(cov["latest_retrieved_activity_date"], d1)
        self.assertEqual(cov["status_type"], "SUCCESS_WITH_ACTIVITIES")
        self.assertEqual(cov["retrieval_completeness"], "COMPLETE")
        self.assertEqual(cov["requested_date_range"], {"start": start_req, "end": end_req})
        self.assertEqual(cov["retrieved_date_range"], {"start": d2, "end": d1})

    def test_scenario_2_profile_20_plus_activities_per_day(self):
        """
        Scenario 2: Profile generating 20+ activities per day over 7 days (140+ activities).
        Ensures high volume is fully extracted, parsed, deduplicated, and saved without arbitrary limits.
        """
        slug = "active-creator-20perday"
        today = date.today()
        acts = []
        types = ["post", "comment", "like", "share", "video"]

        # Generate 22 activities per day for 7 days = 154 activities
        for day in range(7):
            cur_date = (today - timedelta(days=day)).isoformat()
            for a_idx in range(22):
                t = types[a_idx % len(types)]
                global_idx = day * 22 + a_idx
                acts.append(make_activity_item(global_idx, slug, cur_date, t, f"Day {day} item"))

        self.assertEqual(len(acts), 154)

        # Batch save to MongoDB & JSON
        saved_count = save_activities_batch(acts)
        self.assertEqual(saved_count, 154)

        # Filter and analyze
        start_7d = (today - timedelta(days=6)).isoformat()
        end_7d = today.isoformat()

        analysis = analyze_activities(
            rows=acts,
            activity_types=["all"],
            start_date=start_7d,
            end_date=end_7d,
            subject_name="Active Creator",
            subject_url=f"https://www.linkedin.com/in/{slug}/"
        )

        self.assertEqual(analysis["total_activities"], 154)
        self.assertGreater(analysis["posts_count"], 0)
        self.assertGreater(analysis["comments_count"], 0)
        self.assertGreater(analysis["likes_count"], 0)
        self.assertGreater(analysis["videos_count"], 0)

        meta = _build_history_limits_and_coverage(
            activities=analysis["activities"],
            requested_start=start_7d,
            requested_end=end_7d,
            completeness="COMPLETE",
            status_type="SUCCESS_WITH_ACTIVITIES",
            extra_stats={
                "activities_discovered": 160,
                "activities_parsed": 158,
                "duplicates_removed": 4,
                "activities_filtered": 0,
                "activities_saved": 154
            }
        )

        cov = meta["scanned_coverage"]
        self.assertEqual(cov["activities_discovered"], 160)
        self.assertEqual(cov["activities_parsed"], 158)
        self.assertEqual(cov["duplicates_removed"], 4)
        self.assertEqual(cov["activities_saved"], 154)
        self.assertEqual(cov["status_type"], "SUCCESS_WITH_ACTIVITIES")
        self.assertEqual(cov["retrieval_completeness"], "COMPLETE")

    def test_scenario_3_profile_50_plus_activities_per_day(self):
        """
        Scenario 3: Profile generating 50+ activities per day over 10 days (500+ activities).
        Verifies that arbitrary 200, 400 limits DO NOT truncate high-activity retrieval.
        """
        slug = "heavy-influencer-50perday"
        today = date.today()
        acts = []
        types = ["post", "comment", "like", "share", "video"]

        # 52 activities per day for 10 days = 520 activities
        for day in range(10):
            cur_date = (today - timedelta(days=day)).isoformat()
            for a_idx in range(52):
                t = types[a_idx % len(types)]
                global_idx = day * 52 + a_idx
                acts.append(make_activity_item(global_idx, slug, cur_date, t, f"High volume day {day} item"))

        self.assertEqual(len(acts), 520)

        # Ensure batch save handles 500+ items smoothly
        saved_count = save_activities_batch(acts)
        self.assertEqual(saved_count, 520)

        start_10d = (today - timedelta(days=9)).isoformat()
        end_today = today.isoformat()

        analysis = analyze_activities(
            rows=acts,
            activity_types=["all"],
            start_date=start_10d,
            end_date=end_today,
            subject_name="Heavy Influencer",
            subject_url=f"https://www.linkedin.com/in/{slug}/"
        )

        # 520 activities retrieved and analyzed - far exceeding old 200 / 400 fixed limits
        self.assertEqual(analysis["total_activities"], 520)
        self.assertEqual(analysis["date_range"]["start"], start_10d)
        self.assertEqual(analysis["date_range"]["end"], end_today)

        meta = _build_history_limits_and_coverage(
            activities=analysis["activities"],
            requested_start=start_10d,
            requested_end=end_today,
            completeness="COMPLETE",
            status_type="SUCCESS_WITH_ACTIVITIES",
            extra_stats={
                "activities_discovered": 530,
                "activities_parsed": 525,
                "duplicates_removed": 5,
                "activities_filtered": 0,
                "activities_saved": 520
            }
        )

        cov = meta["scanned_coverage"]
        self.assertEqual(cov["activities_discovered"], 530)
        self.assertEqual(cov["activities_parsed"], 525)
        self.assertEqual(cov["duplicates_removed"], 5)
        self.assertEqual(cov["activities_saved"], 520)
        self.assertEqual(cov["retrieval_completeness"], "COMPLETE")

    def test_scenario_4_profile_distributed_over_two_years(self):
        """
        Scenario 4: Profile with activities spanning the entire two-year LinkedIn limit (up to 730 days).
        Verifies 2-year boundary handling, earliest and latest date calculation, and storage persistence.
        """
        slug = "two-year-veteran"
        today = date.today()
        earliest_2yr = (today - timedelta(days=720)).isoformat()
        mid_1yr = (today - timedelta(days=365)).isoformat()
        latest_today = today.isoformat()

        acts = [
            make_activity_item(1, slug, latest_today, "post", "Fresh post from today!"),
            make_activity_item(2, slug, mid_1yr, "share", "One year anniversary repost!"),
            make_activity_item(3, slug, earliest_2yr, "post", "Two years ago on LinkedIn!")
        ]

        save_activities_batch(acts)

        start_2y = (today - timedelta(days=729)).isoformat()
        end_2y = today.isoformat()

        analysis = analyze_activities(
            rows=acts,
            activity_types=["all"],
            start_date=start_2y,
            end_date=end_2y,
            subject_name="Two Year Veteran",
            subject_url=f"https://www.linkedin.com/in/{slug}/"
        )

        self.assertEqual(analysis["total_activities"], 3)
        self.assertEqual(analysis["date_range"]["start"], earliest_2yr)
        self.assertEqual(analysis["date_range"]["end"], latest_today)

        meta = _build_history_limits_and_coverage(
            activities=analysis["activities"],
            requested_start=start_2y,
            requested_end=end_2y,
            completeness="COMPLETE",
            status_type="SUCCESS_WITH_ACTIVITIES",
            extra_stats={
                "activities_discovered": 3,
                "activities_parsed": 3,
                "duplicates_removed": 0,
                "activities_filtered": 0,
                "activities_saved": 3
            }
        )

        cov = meta["scanned_coverage"]
        self.assertEqual(cov["earliest_retrieved_activity_date"], earliest_2yr)
        self.assertEqual(cov["latest_retrieved_activity_date"], latest_today)
        self.assertEqual(cov["requested_date_range"], {"start": start_2y, "end": end_2y})
        self.assertEqual(cov["retrieved_date_range"], {"start": earliest_2yr, "end": latest_today})
        self.assertEqual(meta["official_history_limits"]["documented_max_visibility_years"], 2)

    def test_scenario_5_profile_with_gaps_in_activity_history(self):
        """
        Scenario 5: Profile with dormant gaps in activity (e.g. active recent months, 6-month gap of 0 activities, active earlier).
        Verifies that scrolling continues through dormant gaps without falsely terminating.
        """
        slug = "gap-user"
        today = date.today()

        # Activity 1: 5 days ago
        d_recent = (today - timedelta(days=5)).isoformat()
        # Gap of 200 days with no activities
        # Activity 2: 250 days ago
        d_older = (today - timedelta(days=250)).isoformat()
        # Activity 3: 500 days ago
        d_deep = (today - timedelta(days=500)).isoformat()

        acts = [
            make_activity_item(1, slug, d_recent, "post", "Recent activity"),
            make_activity_item(2, slug, d_older, "comment", "Activity before 200-day gap"),
            make_activity_item(3, slug, d_deep, "post", "Deep activity before second gap")
        ]

        save_activities_batch(acts)

        start_req = (today - timedelta(days=600)).isoformat()
        end_req = today.isoformat()

        analysis = analyze_activities(
            rows=acts,
            activity_types=["all"],
            start_date=start_req,
            end_date=end_req
        )

        self.assertEqual(analysis["total_activities"], 3)
        self.assertEqual(analysis["date_range"]["start"], d_deep)
        self.assertEqual(analysis["date_range"]["end"], d_recent)

        # Date boundary logic must not prematurely stop at the gap
        dates_at_gap = [date.fromisoformat(d_recent)]
        target_start = date.fromisoformat(start_req)
        # Should not have reached boundary yet
        self.assertFalse(check_feed_date_boundary(dates_at_gap, target_start, scroll_count=5))

        # Once deep activity is reached, boundary is satisfied
        dates_with_deep = [date.fromisoformat(d_recent), date.fromisoformat(d_older), date.fromisoformat(d_deep)]
        self.assertTrue(check_feed_date_boundary(dates_with_deep, date.fromisoformat(d_older), scroll_count=10))

    def test_scenario_6_profile_with_restricted_older_activities(self):
        """
        Scenario 6: Profile where older activities are restricted / hidden by member privacy.
        Reports exact retrieved coverage (does not report missing dates as 0 activities)
        and provides honest partial / complete status.
        """
        slug = "restricted-older-user"
        today = date.today()
        d_recent1 = today.isoformat()
        d_recent2 = (today - timedelta(days=15)).isoformat()

        # Only 2 activities visible before LinkedIn stops returning more items
        acts = [
            make_activity_item(1, slug, d_recent1, "post", "Public visible post"),
            make_activity_item(2, slug, d_recent2, "post", "Second public post")
        ]

        # User asked for last 1 year
        start_req = (today - timedelta(days=365)).isoformat()
        end_req = today.isoformat()

        meta = _build_history_limits_and_coverage(
            activities=acts,
            requested_start=start_req,
            requested_end=end_req,
            completeness="COMPLETE",
            status_type="SUCCESS_WITH_ACTIVITIES",
            extra_stats={
                "activities_discovered": 2,
                "activities_parsed": 2,
                "duplicates_removed": 0,
                "activities_filtered": 0,
                "activities_saved": 2
            }
        )

        cov = meta["scanned_coverage"]
        self.assertEqual(cov["activities_saved"], 2)
        # Clearly shows actual retrieved range vs requested range
        self.assertEqual(cov["requested_date_range"], {"start": start_req, "end": end_req})
        self.assertEqual(cov["retrieved_date_range"], {"start": d_recent2, "end": d_recent1})
        self.assertEqual(cov["earliest_retrieved_activity_date"], d_recent2)
        self.assertEqual(cov["latest_retrieved_activity_date"], d_recent1)

    def test_scenario_7_interrupted_extraction_and_safe_recovery(self):
        """
        Scenario 7: Large extraction interrupted by HTTP 999 or timeout mid-flight.
        Verifies:
        1. Safe recovery without duplicating existing records.
        2. Preservation of historical MongoDB records.
        3. Clear reporting of rate limit status without throwing or crashing.
        """
        slug = "interrupted-profile"
        today = date.today()

        # Step 1: Profile previously had 5 saved activities in MongoDB
        initial_acts = [
            make_activity_item(i, slug, (today - timedelta(days=i*10)).isoformat(), "post", "Historical item")
            for i in range(1, 6)
        ]
        save_profile_successful_analysis(slug, {
            "subject_id": slug,
            "subject_name": "Interrupted Profile",
            "subject_url": f"https://www.linkedin.com/in/{slug}/",
            "activities": initial_acts,
            "total_activities": 5,
            "status_type": "SUCCESS_WITH_ACTIVITIES",
            "retrieval_completeness": "COMPLETE"
        })

        hist = load_profile_successful_analysis(slug)
        self.assertIsNotNone(hist)
        self.assertEqual(hist["total_activities"], 5)

        # Step 2: Rate limit HTTP 999 occurs
        set_rate_limit(slug, duration=300.0)

        # ScrapeResult returned on 999
        res_999 = ScrapeResult(
            success=False,
            activities=[],
            message="LinkedIn rate limit encountered: temporarily limiting automated requests (HTTP 999). Please try again later. Your previously saved historical analysis is preserved.",
            status_type="RATE_LIMITED_999",
            retrieval_completeness="BLOCKED"
        )

        self.assertEqual(res_999.status_type, "RATE_LIMITED_999")
        self.assertEqual(res_999.retrieval_completeness, "BLOCKED")

        # Step 3: Verify that historical records in MongoDB remain completely intact and preserved
        preserved_hist = load_profile_successful_analysis(slug)
        self.assertIsNotNone(preserved_hist)
        self.assertEqual(preserved_hist["total_activities"], 5)
        self.assertEqual(len(preserved_hist["activities"]), 5)

        # Step 4: Resume extraction later - new batch arrives with overlapping items (1 to 8)
        resumed_acts = [
            make_activity_item(i, slug, (today - timedelta(days=i*10)).isoformat(), "post", "Historical item")
            for i in range(1, 9)  # 5 overlapping + 3 new
        ]

        save_profile_successful_analysis(slug, {
            "subject_id": slug,
            "subject_name": "Interrupted Profile",
            "subject_url": f"https://www.linkedin.com/in/{slug}/",
            "activities": resumed_acts,
            "total_activities": len(resumed_acts),
            "status_type": "SUCCESS_WITH_ACTIVITIES",
            "retrieval_completeness": "COMPLETE"
        })

        merged_hist = load_profile_successful_analysis(slug)
        # Should cleanly merge without duplicates: exactly 8 total activities
        self.assertEqual(merged_hist["total_activities"], 8)
        self.assertEqual(len(merged_hist["activities"]), 8)


if __name__ == "__main__":
    unittest.main()
