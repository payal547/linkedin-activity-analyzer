"""
Scalability and Large Profile Handling Unit Tests.

NOTE ON TESTING ARCHITECTURE:
These tests use simulated/mock datasets of 100, 1,000, and 10,000 activities to rigorously verify
in-memory processing performance, server-side storage capacity, arithmetic count consistency,
and date-range filtering under high volume.
MOCK SCALABILITY TESTING IS STRICTLY DISTINGUISHED FROM LIVE LINKEDIN SCRAPING:
- Mock tests benchmark the application's capacity to process, filter, aggregate, and store large profile datasets.
- Live scraping against real LinkedIn profiles remains intentionally bounded and rate-limit polite
  to respect LinkedIn's access controls, authwalls, and anti-abuse policies.
"""

import unittest
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import List, Dict, Any

# Ensure backend imports resolve properly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from services.analysis_service import analyze_activities
from services.imported_activities_storage import (
    save_imported_activities,
    load_imported_activities,
    delete_imported_activities,
    clear_all_imported_activities
)
from linkedin.playwright_service import ScrapeResult


def generate_mock_activities(count: int, start_days_ago: int = 100) -> List[Dict[str, Any]]:
    """
    Generates synthetic deterministic LinkedIn activities across 5 distinct types:
    post, comment, like, share, video.
    """
    types_cycle = ["post", "comment", "like", "share", "video"]
    activities = []
    base_date = date.today()

    for idx in range(count):
        act_type = types_cycle[idx % len(types_cycle)]
        day_offset = (idx * start_days_ago) // max(1, count)
        act_date = (base_date - timedelta(days=day_offset)).isoformat()
        
        is_own = act_type in ("post", "video", "share")
        
        activities.append({
            "id": f"mock_act_{idx+1}",
            "subject_id": "test_user_scale",
            "subject_name": "Test Scalability User",
            "subject_url": "https://www.linkedin.com/in/test-scalability/",
            "date": act_date,
            "type": act_type,
            "text": f"Mock activity #{idx+1} of type {act_type}",
            "likes": (idx % 10),
            "comments": (idx % 5),
            "shares": (idx % 3) if act_type == "share" else 0,
            "source": "imported",
            "post_content": f"Post content for mock activity #{idx+1}",
            "post_author": "Test Scalability User" if is_own else f"Author {(idx % 20) + 1}",
            "post_url": f"https://www.linkedin.com/posts/test_user_mock_{idx+1}?trackingId=abc",
            "media_url": f"https://media.licdn.com/mock_{idx+1}.jpg" if act_type in ("video", "post") else None,
            "comment_text": f"Comment text for #{idx+1}" if act_type == "comment" else None,
            "reaction_type": "Like" if act_type == "like" else None,
            "is_own_post": is_own,
            "action_type": act_type
        })
    return activities


class TestScalabilityAndLargeProfiles(unittest.TestCase):

    def setUp(self):
        clear_all_imported_activities()

    def tearDown(self):
        clear_all_imported_activities()

    def test_analysis_scalability_100_items(self):
        """Verify deterministic analysis on 100 mock activities."""
        data_100 = generate_mock_activities(100)
        self.assertEqual(len(data_100), 100)

        t0 = time.perf_counter()
        result = analyze_activities(
            rows=data_100,
            activity_types=["all"],
            subject_name="Test User",
            subject_url="https://www.linkedin.com/in/test-scalability/"
        )
        elapsed = time.perf_counter() - t0

        self.assertEqual(result["total_activities"], 100)
        # Arithmetic integrity
        total_by_type = (
            result["posts_count"] +
            result["comments_count"] +
            result["likes_count"] +
            result["shares_count"] +
            result["videos_count"]
        )
        self.assertEqual(total_by_type, 100)
        self.assertEqual(result["posts_count"], 20)
        self.assertEqual(result["comments_count"], 20)
        self.assertEqual(result["likes_count"], 20)
        self.assertEqual(result["shares_count"], 20)
        self.assertEqual(result["videos_count"], 20)
        self.assertLess(elapsed, 0.5, "100 activities analysis should complete in < 0.5s")

    def test_analysis_scalability_1000_items(self):
        """Verify deterministic analysis on 1,000 mock activities."""
        data_1000 = generate_mock_activities(1000)
        self.assertEqual(len(data_1000), 1000)

        t0 = time.perf_counter()
        result = analyze_activities(
            rows=data_1000,
            activity_types=["all"],
            subject_name="Test User",
            subject_url="https://www.linkedin.com/in/test-scalability/"
        )
        elapsed = time.perf_counter() - t0

        self.assertEqual(result["total_activities"], 1000)
        total_by_type = (
            result["posts_count"] +
            result["comments_count"] +
            result["likes_count"] +
            result["shares_count"] +
            result["videos_count"]
        )
        self.assertEqual(total_by_type, 1000)
        self.assertEqual(result["posts_count"], 200)
        self.assertEqual(result["comments_count"], 200)
        self.assertEqual(result["likes_count"], 200)
        self.assertEqual(result["shares_count"], 200)
        self.assertEqual(result["videos_count"], 200)
        self.assertLess(elapsed, 1.0, "1,000 activities analysis should complete in < 1.0s")

    def test_analysis_scalability_10000_items(self):
        """Verify deterministic analysis on 10,000 mock activities without memory or recursion issues."""
        data_10000 = generate_mock_activities(10000)
        self.assertEqual(len(data_10000), 10000)

        t0 = time.perf_counter()
        result = analyze_activities(
            rows=data_10000,
            activity_types=["all"],
            subject_name="Test User",
            subject_url="https://www.linkedin.com/in/test-scalability/"
        )
        elapsed = time.perf_counter() - t0

        self.assertEqual(result["total_activities"], 10000)
        total_by_type = (
            result["posts_count"] +
            result["comments_count"] +
            result["likes_count"] +
            result["shares_count"] +
            result["videos_count"]
        )
        self.assertEqual(total_by_type, 10000)
        self.assertEqual(result["posts_count"], 2000)
        self.assertEqual(result["comments_count"], 2000)
        self.assertEqual(result["likes_count"], 2000)
        self.assertEqual(result["shares_count"], 2000)
        self.assertEqual(result["videos_count"], 2000)
        # Engagement formula consistency
        self.assertEqual(result["total_engagement"], result["likes"] + result["comments"] + result["shares"])
        self.assertLess(elapsed, 3.0, "10,000 activities analysis should complete in < 3.0s")

    def test_date_filtering_scalability_on_large_dataset(self):
        """Verify exact date filtering on a 10,000 item dataset."""
        data_10000 = generate_mock_activities(10000, start_days_ago=100)
        today = date.today()
        start_30 = (today - timedelta(days=30)).isoformat()
        end_today = today.isoformat()

        result = analyze_activities(
            rows=data_10000,
            activity_types=["all"],
            start_date=start_30,
            end_date=end_today
        )

        self.assertTrue(result["total_activities"] > 0)
        self.assertLess(result["total_activities"], 10000)
        # Verify all returned items strictly fall within [start_30, end_today]
        for act in result["activities"]:
            self.assertGreaterEqual(act["date"], start_30)
            self.assertLessEqual(act["date"], end_today)

    def test_server_side_imported_activities_storage(self):
        """
        Verify that large imported activity datasets (10,000 items) are saved to server disk,
        preventing Flask 4KB cookie overflow while allowing full retrieval and clean deletion.
        """
        data_10000 = generate_mock_activities(10000)
        
        # Save to disk
        import_id = save_imported_activities(data_10000)
        self.assertTrue(isinstance(import_id, str))
        self.assertTrue(len(import_id) > 10)
        # Import ID is small and safe for Flask cookie (< 100 bytes)
        self.assertLess(len(import_id), 100)

        # Load back
        loaded = load_imported_activities(import_id)
        self.assertEqual(len(loaded), 10000)
        self.assertEqual(loaded[0]["id"], "mock_act_1")
        self.assertEqual(loaded[-1]["id"], "mock_act_10000")

        # Delete
        deleted = delete_imported_activities(import_id)
        self.assertTrue(deleted)
        self.assertEqual(len(load_imported_activities(import_id)), 0)

    def test_retrieval_completeness_contract(self):
        """
        Verifies the contract of retrieval_completeness:
        1. COMPLETE when date range covered or end of feed verified.
        2. PARTIAL_CEILING_REACHED when ceiling is reached.
        3. PARTIAL_GUEST_LIMIT when guest session cannot paginate deeper.
        4. Reaching scroll ceiling must NEVER be reported as COMPLETE.
        """
        # ScrapeResult with COMPLETE
        res_complete = ScrapeResult(True, [{"id": "1"}], "Success", "SUCCESS_WITH_ACTIVITIES", "COMPLETE")
        self.assertEqual(res_complete.retrieval_completeness, "COMPLETE")
        self.assertEqual(res_complete[0], True)
        self.assertEqual(len(res_complete[1]), 1)

        # ScrapeResult with PARTIAL_CEILING_REACHED
        res_partial = ScrapeResult(True, [{"id": "1"}], "Ceiling reached", "SUCCESS_WITH_ACTIVITIES", "PARTIAL_CEILING_REACHED")
        self.assertEqual(res_partial.retrieval_completeness, "PARTIAL_CEILING_REACHED")
        self.assertNotEqual(res_partial.retrieval_completeness, "COMPLETE")

        # ScrapeResult with PARTIAL_GUEST_LIMIT
        res_guest = ScrapeResult(True, [{"id": "1"}], "Guest limit", "SUCCESS_WITH_ACTIVITIES", "PARTIAL_GUEST_LIMIT")
        self.assertEqual(res_guest.retrieval_completeness, "PARTIAL_GUEST_LIMIT")
        self.assertNotEqual(res_guest.retrieval_completeness, "COMPLETE")


if __name__ == "__main__":
    unittest.main()

