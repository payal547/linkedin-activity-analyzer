"""
Automated Test Suite for Filter Accuracy, Historical Analysis Isolation,
HTTP 999 Rate Limit Handling, and Media/Link Safety.
Tests run against the isolated test database (linkedin_activity_analyzer_test).
"""

import unittest
import json
import time
from datetime import date
from typing import Dict, Any, List

from backend.app import app
from backend.services.analysis_service import analyze_activities, _normalize_row_kind
from backend.services.analysis_storage import (
    save_profile_successful_analysis,
    load_profile_successful_analysis,
    save_latest_analysis,
    load_latest_analysis,
    clear_profile_successful_analysis
)
from backend.services.activities_storage import load_saved_activities
try:
    from linkedin.playwright_service import (
        clear_rate_limit_cooldown,
        clear_profile_cache,
        set_rate_limit,
        scrape_linkedin_profile_activities
    )
except ImportError:
    from backend.linkedin.playwright_service import (
        clear_rate_limit_cooldown,
        clear_profile_cache,
        set_rate_limit,
        scrape_linkedin_profile_activities
    )


class TestFilterAccuracyAndHistorical(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        cls.client = app.test_client()

    def setUp(self):
        clear_rate_limit_cooldown()
        clear_profile_cache()

    def tearDown(self):
        clear_rate_limit_cooldown()
        clear_profile_cache()

    # 1. Comments-only filter returns comments only
    def test_comments_only_filter_returns_only_comments(self):
        sample_rows = [
            {"type": "post", "text": "Post 1", "date": "2026-10-01"},
            {"type": "comment", "comment_text": "Great point!", "date": "2026-10-02"},
            {"type": "comment", "comment_text": "I agree completely", "date": "2026-10-03"},
            {"type": "like", "reaction_type": "Like", "date": "2026-10-04"},
            {"type": "share", "text": "Sharing this", "date": "2026-10-05"},
            {"type": "video", "video_url": "https://example.com/v.mp4", "date": "2026-10-06"}
        ]
        res = analyze_activities(sample_rows, ["comments"])
        self.assertEqual(res["total_activities"], 2)
        self.assertEqual(res["comments_count"], 2)
        self.assertEqual(res["posts_count"], 0)
        self.assertEqual(res["likes_count"], 0)
        self.assertEqual(res["shares_count"], 0)
        self.assertEqual(res["videos_count"], 0)
        self.assertEqual(res["by_type"], {"comment": 2})
        for act in res["activities"]:
            self.assertEqual(act["action_type"], "comment")

    # 2. Posts-only filter returns posts only (verifying shares/reposts are not included)
    def test_posts_only_filter_returns_only_posts(self):
        sample_rows = [
            {"type": "post", "text": "Post 1", "date": "2026-10-01"},
            {"type": "post", "text": "Post 2", "date": "2026-10-02"},
            {"type": "share", "text": "Shared post", "date": "2026-10-03"},
            {"type": "comment", "comment_text": "A comment", "date": "2026-10-04"}
        ]
        res = analyze_activities(sample_rows, ["posts"])
        self.assertEqual(res["total_activities"], 2)
        self.assertEqual(res["posts_count"], 2)
        self.assertEqual(res["shares_count"], 0)
        self.assertEqual(res["comments_count"], 0)
        self.assertEqual(res["by_type"], {"post": 2})
        for act in res["activities"]:
            self.assertEqual(act["action_type"], "post")

    # 3. Reactions-only filter returns reactions only
    def test_reactions_only_filter_returns_only_reactions(self):
        sample_rows = [
            {"type": "like", "reaction_type": "Like", "date": "2026-10-01"},
            {"type": "reaction", "reaction_type": "Celebrate", "date": "2026-10-02"},
            {"type": "post", "text": "A post", "date": "2026-10-03"},
            {"type": "comment", "comment_text": "A comment", "date": "2026-10-04"}
        ]
        res = analyze_activities(sample_rows, ["likes"])
        self.assertEqual(res["total_activities"], 2)
        self.assertEqual(res["likes_count"], 2)
        self.assertEqual(res["posts_count"], 0)
        self.assertEqual(res["comments_count"], 0)
        for act in res["activities"]:
            self.assertIn(act["action_type"], ("like", "reaction"))

    # 4. Reposts-only filter returns reposts only
    def test_reposts_only_filter_returns_only_reposts(self):
        sample_rows = [
            {"type": "share", "text": "Shared article 1", "date": "2026-10-01"},
            {"action_type": "repost", "text": "Shared article 2", "date": "2026-10-02"},
            {"type": "post", "text": "Original post", "date": "2026-10-03"}
        ]
        res = analyze_activities(sample_rows, ["shares"])
        self.assertEqual(res["total_activities"], 2)
        self.assertEqual(res["shares_count"], 2)
        self.assertEqual(res["posts_count"], 0)
        for act in res["activities"]:
            self.assertIn(act["action_type"], ("share", "repost"))

    # 5. Combined filters return only selected activity types
    def test_combined_filters_return_only_selected_types(self):
        sample_rows = [
            {"type": "post", "text": "Post 1", "date": "2026-10-01"},
            {"type": "comment", "comment_text": "Comment 1", "date": "2026-10-02"},
            {"type": "like", "reaction_type": "Like", "date": "2026-10-03"},
            {"type": "share", "text": "Share 1", "date": "2026-10-04"}
        ]
        res = analyze_activities(sample_rows, ["posts", "comments"])
        self.assertEqual(res["total_activities"], 2)
        self.assertEqual(res["posts_count"], 1)
        self.assertEqual(res["comments_count"], 1)
        self.assertEqual(res["likes_count"], 0)
        self.assertEqual(res["shares_count"], 0)
        self.assertEqual(res["by_type"], {"post": 1, "comment": 1})

    # 6. Metrics match filtered activity records exactly
    def test_metrics_match_filtered_activity_records(self):
        sample_rows = [
            {"type": "comment", "comment_text": "C1", "likes": 5, "comments": 2, "date": "2026-10-01"},
            {"type": "comment", "comment_text": "C2", "likes": 3, "comments": 1, "date": "2026-10-02"},
            {"type": "post", "text": "P1", "likes": 50, "comments": 20, "shares": 10, "date": "2026-10-03"}
        ]
        res = analyze_activities(sample_rows, ["comments"])
        self.assertEqual(res["total_activities"], 2)
        self.assertEqual(res["likes"], 8)
        self.assertEqual(res["comments"], 3)
        self.assertEqual(res["shares"], 0)
        self.assertEqual(res["total_engagement"], 11)
        self.assertEqual(res["average_engagement"], 5.5)

    # 7. Historical records respect current filters
    def test_historical_records_respect_current_filter(self):
        slug = "test-hist-filter-profile"
        hist_payload = {
            "subject_name": "Test Hist Member",
            "subject_url": f"https://www.linkedin.com/in/{slug}",
            "headline": "Tester",
            "date_range": {"start": "2026-09-01", "end": "2026-10-15"},
            "total_activities": 10,
            "posts_count": 1,
            "comments_count": 3,
            "likes_count": 6,
            "shares_count": 0,
            "videos_count": 0,
            "by_type": {"post": 1, "comment": 3, "like": 6},
            "activities": [
                {"type": "post", "text": "Post 1", "date": "2026-10-01"},
                {"type": "comment", "comment_text": "Comment 1", "date": "2026-10-02"},
                {"type": "comment", "comment_text": "Comment 2", "date": "2026-10-03"},
                {"type": "comment", "comment_text": "Comment 3", "date": "2026-10-04"},
                {"type": "like", "reaction_type": "Like", "date": "2026-10-05"},
                {"type": "like", "reaction_type": "Like", "date": "2026-10-06"},
                {"type": "like", "reaction_type": "Like", "date": "2026-10-07"},
                {"type": "like", "reaction_type": "Like", "date": "2026-10-08"},
                {"type": "like", "reaction_type": "Like", "date": "2026-10-09"},
                {"type": "like", "reaction_type": "Like", "date": "2026-10-10"}
            ]
        }
        # Save to history
        save_profile_successful_analysis(slug, hist_payload)

        # Query GET /api/analysis/latest with comments filter
        resp = self.client.get(f"/api/analysis/latest?subject_id={slug}&profile_url=https://www.linkedin.com/in/{slug}&activities=comments")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()

        # Must return only comments
        self.assertEqual(data["total_activities"], 3)
        self.assertEqual(data["comments_count"], 3)
        self.assertEqual(data["posts_count"], 0)
        self.assertEqual(data["likes_count"], 0)
        self.assertEqual(data["by_type"], {"comment": 3})
        self.assertEqual(len(data["activities"]), 3)
        for a in data["activities"]:
            self.assertEqual(a["action_type"], "comment")

        # Clean up
        clear_profile_successful_analysis(slug)

    # 8. Historical records remain unchanged in storage
    def test_historical_records_remain_unchanged_in_storage(self):
        slug = "test-hist-unchanged-storage"
        hist_payload = {
            "subject_name": "Storage Preservation Test",
            "subject_url": f"https://www.linkedin.com/in/{slug}",
            "total_activities": 10,
            "posts_count": 1,
            "comments_count": 3,
            "likes_count": 6,
            "activities": [
                {"type": "post", "text": "Post 1", "date": "2026-10-01"},
                {"type": "comment", "comment_text": "Comment 1", "date": "2026-10-02"},
                {"type": "comment", "comment_text": "Comment 2", "date": "2026-10-03"},
                {"type": "comment", "comment_text": "Comment 3", "date": "2026-10-04"},
                {"type": "like", "reaction_type": "Like", "date": "2026-10-05"},
                {"type": "like", "reaction_type": "Like", "date": "2026-10-06"},
                {"type": "like", "reaction_type": "Like", "date": "2026-10-07"},
                {"type": "like", "reaction_type": "Like", "date": "2026-10-08"},
                {"type": "like", "reaction_type": "Like", "date": "2026-10-09"},
                {"type": "like", "reaction_type": "Like", "date": "2026-10-10"}
            ]
        }
        save_profile_successful_analysis(slug, hist_payload)

        # Request filtered comments
        self.client.get(f"/api/analysis/latest?subject_id={slug}&activities=comments")

        # Reload raw stored historical record directly from storage
        raw_loaded = load_profile_successful_analysis(slug)
        self.assertIsNotNone(raw_loaded)
        # Original 10 activities must still be completely intact in storage!
        self.assertEqual(len(raw_loaded["activities"]), 10)
        self.assertEqual(raw_loaded["total_activities"], 10)

        clear_profile_successful_analysis(slug)

    # 9. HTTP 999 stops automatic repeated retries
    def test_http_999_stops_automatic_repeated_retries(self):
        slug = "test-rate-limit-slug"
        # Simulate rate-limit trigger
        set_rate_limit(slug, 300.0)

        # Attempt to scrape while cooldown active
        status_out = {}
        res = scrape_linkedin_profile_activities(
            profile_url=f"https://www.linkedin.com/in/{slug}",
            status_out=status_out
        )
        self.assertFalse(res[0])  # False success
        self.assertEqual(status_out.get("status_type"), "RATE_LIMITED_999")
        self.assertIn("temporarily limiting automated requests", status_out.get("status", ""))

    # 10. HTTP 999 is not reported as successful retrieval
    def test_http_999_not_reported_as_successful_retrieval(self):
        slug = "test-rate-limit-not-success"
        set_rate_limit(slug, 300.0)

        resp = self.client.post("/api/analyze", json={
            "subject_url": f"https://www.linkedin.com/in/{slug}",
            "subject_id": slug,
            "subject_name": "Rate Limited Member",
            "activities": ["all"]
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("status_type"), "RATE_LIMITED_999")
        self.assertNotEqual(data.get("status_type"), "SUCCESS_WITH_ACTIVITIES")
        self.assertEqual(data.get("retrieval_completeness"), "UNAVAILABLE")

    # 11. Sneha Patel never receives Pragyanand Sahoo's activities
    def test_sneha_patel_never_receives_pragyanand_sahoo_activities(self):
        resp = self.client.get("/api/analysis/latest?subject_id=sneha-patel&profile_url=https://www.linkedin.com/in/sneha-patel&subject_name=Sneha%20Patel")
        if resp.status_code == 200:
            data = resp.get_json()
            s_name = data.get("subject_name", "")
            s_url = data.get("subject_url", "")
            self.assertNotIn("pragyanand", s_name.lower())
            self.assertNotIn("pragyanand", s_url.lower())
            for act in data.get("activities", []):
                self.assertNotIn("pragyanand", (act.get("target_profile_name") or "").lower())
                self.assertNotIn("pragyanand", (act.get("target_profile_url") or "").lower())

    # 12. Incomplete LinkedIn URLs are rejected
    def test_incomplete_linkedin_urls_are_rejected(self):
        for bad_url in ("https://www.linkedin.com/in/", "https://www.linkedin.com/in", "http://www.linkedin.com/in/"):
            resp = self.client.post("/api/analyze", json={
                "subject_url": bad_url,
                "activities": ["all"]
            })
            self.assertEqual(resp.status_code, 400)
            data = resp.get_json()
            self.assertIn("error", data)

    # 13. Latest analysis is profile-specific
    def test_latest_analysis_is_profile_specific(self):
        resp = self.client.get("/api/analysis/latest?subject_id=completely-nonexistent-user-12345&profile_url=https://www.linkedin.com/in/completely-nonexistent-user-12345")
        self.assertEqual(resp.status_code, 404)
        data = resp.get_json()
        self.assertFalse(data.get("has_data"))

    # 14. Zero matching activities produces honest empty state
    def test_zero_matching_activities_produces_honest_empty_state(self):
        sample_rows = [
            {"type": "post", "text": "Post 1", "date": "2026-10-01"},
            {"type": "post", "text": "Post 2", "date": "2026-10-02"}
        ]
        # Request comments only on dataset with 0 comments
        res = analyze_activities(sample_rows, ["comments"])
        self.assertEqual(res["total_activities"], 0)
        self.assertEqual(res["comments_count"], 0)
        self.assertEqual(len(res["activities"]), 0)
        self.assertIn("No comments activities found", res.get("empty_message", ""))

    # 15. Video URLs are never fabricated
    def test_video_urls_never_fabricated(self):
        sample_rows = [
            {"type": "post", "text": "A text post without video"}
        ]
        res = analyze_activities(sample_rows, ["all"])
        self.assertEqual(len(res["activities"]), 1)
        act = res["activities"][0]
        self.assertIsNone(act["video_url"])
        self.assertIsNone(act["video_thumbnail"])
        self.assertEqual(act["video_status"], "No video")


if __name__ == "__main__":
    unittest.main()
