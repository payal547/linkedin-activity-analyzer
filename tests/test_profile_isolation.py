"""
Comprehensive Automated Tests for Profile-Level Data Isolation and Fix Validation.

Covers:
1. Sneha Patel selection NEVER returns Pragyanand Sahoo's activities.
2. Incomplete profile URLs (https://www.linkedin.com/in/) are rejected with HTTP 400.
3. Canonical slug matching is strict and bounded.
4. Historical analysis is profile-specific and never cross-contaminates.
5. Invalid or corrupted slugs (such as httpswwwlinkedincomin) are handled safely without crashing.
6. Missing activity URLs are distinguished from profile URLs.
7. Video extraction does not fabricate fake URLs.
8. Dashboard /api/analysis/latest verifies profile identity and rejects mismatched profiles.
"""

import os
import sys
import unittest
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app import app
from services.activities_storage import load_saved_activities, save_activity, _get_storage_file as _get_act_file
from services.analysis_storage import (
    save_latest_analysis,
    load_latest_analysis,
    clear_latest_analysis,
    save_profile_successful_analysis,
    load_profile_successful_analysis,
    clear_profile_history,
    extract_slug
)
from services.saved_profiles_storage import extract_slug as extract_saved_slug
from linkedin.playwright_service import _extract_video_info


class TestProfileDataIsolation(unittest.TestCase):

    def setUp(self):
        os.environ["TESTING"] = "1"
        app.config["TESTING"] = True
        self.client = app.test_client()
        clear_latest_analysis()
        clear_profile_history()

        # Clean test activities file
        act_file = _get_act_file()
        if act_file.exists():
            try:
                act_file.unlink()
            except Exception:
                pass

    def tearDown(self):
        clear_latest_analysis()
        clear_profile_history()
        act_file = _get_act_file()
        if act_file.exists():
            try:
                act_file.unlink()
            except Exception:
                pass

    def test_1_sneha_patel_never_returns_pragyanand_sahoo_activities(self):
        """CRITICAL: Selecting Sneha Patel must NEVER return Pragyanand Sahoo's activities."""
        # 1. Seed activities belonging specifically to Pragyanand Sahoo
        pragyanand_activity = {
            "id": "act_pragyanand_001",
            "subject_name": "Pragyanandsaho",
            "subject_id": "pragyanandsaho",
            "subject_url": "https://www.linkedin.com/in/pragyanandsaho",
            "date": "2026-10-19",
            "type": "post",
            "text": "Researcher at SRHU working on activity analysis algorithms.",
            "post_content": "Researcher at SRHU working on activity analysis algorithms.",
            "post_author": "Pragyanand Sahoo",
            "post_url": "https://www.linkedin.com/feed/update/urn:li:activity:999888777",
            "likes": 12,
            "comments": 3
        }
        save_activity(pragyanand_activity)

        # 2. Query activities for Sneha Patel
        sneha_results = load_saved_activities(
            subject_name="Sneha Patel",
            subject_id="conn_imp_1",
            subject_url="https://www.linkedin.com/in/sneha-patel"
        )

        # 3. Assert zero activities returned - Sneha has no activities in storage
        self.assertEqual(len(sneha_results), 0, "Sneha Patel query must NOT return Pragyanand Sahoo's activities!")

        # 4. Query activities for Pragyanand Sahoo - must return exactly his activity
        prag_results = load_saved_activities(
            subject_name="Pragyanandsaho",
            subject_id="pragyanandsaho",
            subject_url="https://www.linkedin.com/in/pragyanandsaho"
        )
        self.assertEqual(len(prag_results), 1)
        self.assertEqual(prag_results[0]["id"], "act_pragyanand_001")

    def test_2_incomplete_profile_urls_are_rejected(self):
        """Incomplete root URLs like https://www.linkedin.com/in/ without a slug must be rejected with HTTP 400."""
        # Test trailing slash root URL
        res_slash = self.client.post("/api/analyze", json={
            "subject_name": "Incomplete User",
            "subject_url": "https://www.linkedin.com/in/",
            "activities": ["all"]
        })
        self.assertEqual(res_slash.status_code, 400)
        self.assertIn("Incomplete", res_slash.get_json().get("error", ""))

        # Test no trailing slash root URL
        res_no_slash = self.client.post("/api/analyze", json={
            "subject_name": "Incomplete User",
            "subject_url": "https://www.linkedin.com/in",
            "activities": ["all"]
        })
        self.assertEqual(res_no_slash.status_code, 400)
        self.assertIn("Incomplete", res_no_slash.get_json().get("error", ""))

        # Test batch profile with incomplete URL
        res_batch = self.client.post("/api/analyze", json={
            "profiles": [
                {"name": "Bad URL Profile", "profile_url": "https://www.linkedin.com/in/"}
            ],
            "activities": ["all"]
        })
        self.assertEqual(res_batch.status_code, 400)

    def test_3_canonical_slug_matching_is_strict(self):
        """Canonical slug extraction must be strict, safe, and reject malformed/root strings."""
        for fn in (extract_slug, extract_saved_slug):
            # Valid URLs
            self.assertEqual(fn("https://www.linkedin.com/in/sneha-patel"), "sneha-patel")
            self.assertEqual(fn("https://www.linkedin.com/in/pragyanandsaho/"), "pragyanandsaho")
            self.assertEqual(fn("http://www.linkedin.com/in/williamhgates"), "williamhgates")
            self.assertEqual(fn("sneha-patel"), "sneha-patel")

            # Incomplete or invalid URLs - MUST return empty string
            self.assertEqual(fn("https://www.linkedin.com/in/"), "")
            self.assertEqual(fn("https://www.linkedin.com/in"), "")
            self.assertEqual(fn("http://www.linkedin.com/in/"), "")
            self.assertEqual(fn("https://www.linkedin.com/"), "")
            self.assertEqual(fn("httpswwwlinkedincomin"), "")
            self.assertEqual(fn("in"), "")
            self.assertEqual(fn(""), "")
            self.assertEqual(fn(None), "")

    def test_4_historical_analysis_is_profile_specific(self):
        """Historical analysis must be retrieved strictly for the matching profile slug."""
        # 1. Save historical analysis for Pragyanand Sahoo
        prag_history = {
            "subject_name": "Pragyanand Sahoo",
            "subject_url": "https://www.linkedin.com/in/pragyanandsaho",
            "subject_id": "pragyanandsaho",
            "total_activities": 19,
            "posts_count": 10,
            "activities": [{"id": "act_1", "text": "Pragyanand's paper presentation"}]
        }
        save_profile_successful_analysis("pragyanandsaho", prag_history)

        # 2. Query history for Sneha Patel
        sneha_hist = load_profile_successful_analysis("sneha-patel")
        self.assertIsNone(sneha_hist, "Sneha Patel must NOT receive Pragyanand Sahoo's historical analysis!")

        # 3. Query history for Pragyanand Sahoo
        prag_hist = load_profile_successful_analysis("pragyanandsaho")
        self.assertIsNotNone(prag_hist)
        self.assertEqual(prag_hist["subject_name"], "Pragyanand Sahoo")

    def test_5_invalid_slugs_handled_safely(self):
        """Querying or saving with corrupted legacy slug strings must not crash or leak data."""
        self.assertIsNone(load_profile_successful_analysis("httpswwwlinkedincomin"))
        self.assertIsNone(load_profile_successful_analysis("https://www.linkedin.com/in/"))
        self.assertIsNone(load_profile_successful_analysis(""))

        # Saving with invalid slug must safely no-op
        save_profile_successful_analysis("https://www.linkedin.com/in/", {"data": 123})
        self.assertIsNone(load_profile_successful_analysis("httpswwwlinkedincomin"))

    def test_6_dashboard_latest_endpoint_profile_verification(self):
        """GET /api/analysis/latest must reject mismatched profile requests and return 404 has_data: False."""
        # 1. Store latest analysis belonging to Pragyanand Sahoo
        prag_analysis = {
            "subject_name": "Pragyanand Sahoo",
            "subject_url": "https://www.linkedin.com/in/pragyanandsaho",
            "subject_id": "pragyanandsaho",
            "total_activities": 19,
            "posts_count": 19,
            "activities": [{"id": "act_prag_01", "text": "Pragyanand post"}]
        }
        save_latest_analysis(prag_analysis)

        # 2. Client requests latest analysis for Sneha Patel
        res_mismatch = self.client.get(
            "/api/analysis/latest?subject_name=Sneha%20Patel&profile_url=https://www.linkedin.com/in/sneha-patel"
        )
        self.assertEqual(res_mismatch.status_code, 404)
        mismatch_json = res_mismatch.get_json()
        self.assertFalse(mismatch_json.get("has_data", True))
        self.assertIn("Sneha Patel", mismatch_json.get("error", ""))

        # 3. Client requests latest analysis for Pragyanand Sahoo
        res_match = self.client.get(
            "/api/analysis/latest?subject_name=Pragyanand%20Sahoo&profile_url=https://www.linkedin.com/in/pragyanandsaho"
        )
        self.assertEqual(res_match.status_code, 200)
        match_json = res_match.get_json()
        self.assertTrue(match_json.get("has_data"))
        self.assertEqual(match_json.get("subject_name"), "Pragyanand Sahoo")

    def test_7_video_extraction_does_not_fabricate_urls(self):
        """Video extraction logic must return honest status and NEVER invent fake URLs."""
        html_without_video = """
        <div class="feed-shared-update-v2">
            <p class="feed-shared-update-v2__description">Just a standard text post with an image.</p>
            <img src="https://media.licdn.com/dms/image/v2/test_img.jpg" class="ivm-view-attr__img--centered" />
        </div>
        """
        has_v, v_url, v_thumb, v_stat = _extract_video_info(html_without_video)
        self.assertFalse(has_v)
        self.assertIsNone(v_url)
        self.assertIsNone(v_thumb)
        self.assertEqual(v_stat, "No video")

        # HTML with legitimate YouTube embed
        html_youtube = """
        <div class="feed-shared-update-v2">
            <a href="https://www.youtube.com/watch?v=dQw4w9WgXcQ" class="app-aware-link">Watch here</a>
        </div>
        """
        has_v2, v_url2, _, v_stat2 = _extract_video_info(html_youtube)
        self.assertTrue(has_v2)
        self.assertEqual(v_url2, "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertEqual(v_stat2, "External video embed")


if __name__ == "__main__":
    unittest.main()

