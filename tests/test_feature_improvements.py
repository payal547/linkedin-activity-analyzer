import io
import os
import sys
import unittest
import zipfile
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

# Add project root and backend to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from linkedin.data_parser import (
    safe_normalize_date,
    parse_shares_csv,
    parse_reactions_csv,
    parse_comments_csv,
    parse_activities_csv,
    parse_activities_from_file_or_zip
)
from linkedin.playwright_service import (
    parse_relative_time_to_date,
    to_date_obj,
    _extract_activities_from_public_html,
    scrape_linkedin_profile_activities
)
from services.analysis_service import analyze_activities


class TestFeatureImprovements(unittest.TestCase):

    def test_video_detection_no_false_positive_on_commentary_word(self):
        """
        Verify that commentary containing the word 'video' does NOT cause an image
        or text post to be falsely classified as a video.
        Only genuine video media URLs, video file extensions, or DOM video elements
        are classified as video.
        """
        csv_content = (
            "Date,ShareLink,ShareCommentary,MediaUrl\n"
            "2026-10-15,https://www.linkedin.com/posts/neel-mani_update-123,"
            "Check out our new video training guide and webinar slides!,https://media.licdn.com/dms/image/slides.pdf\n"
            "2026-10-16,https://www.linkedin.com/posts/neel-mani_demo-456,"
            "Keynote presentation recording,https://dms.licdn.com/videocdn/sample_recording.mp4\n"
        )
        acts = parse_shares_csv(csv_content)
        self.assertEqual(len(acts), 2)

        # First item has word 'video' in text but media is a PDF/image -> type should be 'post'
        self.assertEqual(acts[0].type, "post")

        # Second item has .mp4 video URL -> type should be 'video'
        self.assertEqual(acts[1].type, "video")

    def test_share_repost_detection_and_no_double_counting(self):
        """
        Verify that reposts / shares are distinguished from original posts,
        original post URLs are preserved, and analysis_service does NOT double-count
        shares inside posts_count.
        """
        csv_content = (
            "Date,ShareLink,ShareCommentary,SharedUrl\n"
            "2026-10-10,https://www.linkedin.com/posts/user_post-1,An original post by the member,\n"
            "2026-10-11,https://www.linkedin.com/posts/user_repost-2,Reposted: Great insights on AI!,https://www.linkedin.com/posts/author_original-999\n"
        )
        acts = parse_shares_csv(csv_content)
        self.assertEqual(len(acts), 2)
        self.assertEqual(acts[0].type, "post")
        self.assertEqual(acts[1].type, "share")
        self.assertEqual(acts[1].post_url, "https://www.linkedin.com/posts/author_original-999")

        # In analysis_service, posts_count must ONLY count original posts
        raw_rows = [a.to_dict() for a in acts]
        analysis = analyze_activities(
            raw_rows,
            activity_types=["all"],
            subject_name="Test User",
            data_source="real_linkedin"
        )
        self.assertEqual(analysis["total_activities"], 2)
        self.assertEqual(analysis["posts_count"], 1)
        self.assertEqual(analysis["shares_count"], 1)

    def test_date_normalization_multi_locale_formats(self):
        """
        Verify date normalization across ISO, US MM/DD/YYYY, EU DD/MM/YYYY,
        verbal month names, and relative date expressions.
        """
        self.assertEqual(safe_normalize_date("2026-10-18"), "2026-10-18")
        self.assertEqual(safe_normalize_date("10/18/2026"), "2026-10-18")
        self.assertEqual(safe_normalize_date("18/10/2026"), "2026-10-18")
        self.assertEqual(safe_normalize_date("October 18, 2026"), "2026-10-18")
        self.assertEqual(safe_normalize_date("18 Oct 2026"), "2026-10-18")

        # Relative units parsing in Playwright service
        two_w_iso, _ = parse_relative_time_to_date("2w")
        self.assertIsNotNone(two_w_iso)
        expected_2w = (date.today() - timedelta(days=14)).isoformat()
        self.assertEqual(two_w_iso, expected_2w)

        one_yr_iso, _ = parse_relative_time_to_date("1 yr")
        self.assertIsNotNone(one_yr_iso)
        expected_1yr = (date.today() - timedelta(days=365)).isoformat()
        self.assertEqual(one_yr_iso, expected_1yr)

    def test_cross_file_zip_deduplication(self):
        """
        Verify that importing a complete ZIP archive with overlapping entries
        between Shares.csv and Posts.csv deduplicates them safely without data loss.
        """
        shares_csv = (
            "Date,ShareLink,ShareCommentary\n"
            "2026-10-12,https://www.linkedin.com/posts/user_act-10?trackingId=abc,Shared article on robotics\n"
            "2026-10-13,https://www.linkedin.com/posts/user_act-11,Unique post in shares\n"
        ).encode("utf-8")

        posts_csv = (
            "Date,ShareLink,ShareCommentary\n"
            "2026-10-12,https://www.linkedin.com/posts/user_act-10?utm_source=li,Shared article on robotics\n"
            "2026-10-14,https://www.linkedin.com/posts/user_act-12,Unique post in posts\n"
        ).encode("utf-8")

        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w") as zf:
            zf.writestr("Shares.csv", shares_csv)
            zf.writestr("Posts.csv", posts_csv)

        activities = parse_activities_from_file_or_zip(zip_buf.getvalue(), "linkedin_export.zip")
        # user_act-10 should be deduplicated despite different URL query params
        self.assertEqual(len(activities), 3)
        urls = [a.post_url for a in activities if a.post_url]
        self.assertEqual(len([u for u in urls if "user_act-10" in u]), 1)

    def test_restricted_profile_activity_feed_status(self):
        """
        Verify that when a profile's activity is marked private or restricted by the member,
        scrape_linkedin_profile_activities returns ACTIVITY_FEED_RESTRICTED with COMPLETE completeness.
        """
        import time
        mock_html = """
        <!DOCTYPE html>
        <html>
        <head><title>This profile is private | LinkedIn</title></head>
        <body>
            <div class="profile-header">
                <h1>Restricted Member</h1>
                <p>This profile is private or the member has limited their visibility.</p>
            </div>
        </body>
        </html>
        """
        cache_data = {
            "restricted-member": {
                "status": 200,
                "html": mock_html,
                "title": "This profile is private | LinkedIn",
                "timestamp": time.time(),
                "reached_end_of_feed": True
            }
        }
        with patch.dict("linkedin.playwright_service._PROFILE_CACHE", cache_data, clear=True):
            status_out = {}
            res = scrape_linkedin_profile_activities(
                profile_url="https://www.linkedin.com/in/restricted-member/",
                status_out=status_out
            )

            self.assertFalse(res.success)
            self.assertEqual(res.status_type, "ACTIVITY_FEED_RESTRICTED")
            self.assertEqual(res.retrieval_completeness, "UNAVAILABLE")
            self.assertIn("private or restricted", res.message.lower())

    def test_activity_section_unavailable_completeness_is_never_complete(self):
        """
        Verify that ACTIVITY_SECTION_UNAVAILABLE is reported with completeness UNAVAILABLE,
        and NEVER as COMPLETE when zero activities were retrieved.
        """
        import time
        mock_html = """
        <!DOCTYPE html>
        <html>
        <head><title>Dr. Neel Mani | LinkedIn</title></head>
        <body>
            <div class="top-card-layout">
                <h1 class="top-card-layout__title">Dr. Neel Mani</h1>
                <h2 class="top-card-layout__headline">Professor of Computer Science</h2>
            </div>
            <section class="about-section">
                <p>Passionate researcher in AI and systems.</p>
            </section>
        </body>
        </html>
        """
        cache_data = {
            "neel-mani": {
                "status": 200,
                "html": mock_html,
                "title": "Dr. Neel Mani | LinkedIn",
                "timestamp": time.time(),
                "reached_end_of_feed": True
            }
        }
        with patch.dict("linkedin.playwright_service._PROFILE_CACHE", cache_data, clear=True):
            status_out = {}
            res = scrape_linkedin_profile_activities(
                profile_url="https://www.linkedin.com/in/neel-mani/",
                status_out=status_out
            )

            self.assertFalse(res.success)
            self.assertEqual(res.status_type, "ACTIVITY_SECTION_UNAVAILABLE")
            self.assertEqual(res.retrieval_completeness, "UNAVAILABLE")
            self.assertNotEqual(res.retrieval_completeness, "COMPLETE")
            self.assertEqual(len(res.activities), 0)

    def test_video_classification_generic_urns_not_video(self):
        """
        Verify that ordinary LinkedIn URNs (such as urn:li:ugcPost or urn:li:digitalmediaasset)
        are NOT falsely classified as videos. Only genuine video files, streaming video hosts,
        or urn:li:video are classified as videos.
        """
        csv_content = (
            "Date,ShareLink,ShareCommentary,MediaUrl\n"
            "2026-10-18,https://www.linkedin.com/feed/update/urn:li:ugcPost:7123456789,"
            "Excited to share our new whitepaper on cloud scaling!,https://media.licdn.com/dms/image/urn:li:digitalmediaasset:D5600A1\n"
            "2026-10-19,https://www.linkedin.com/feed/update/urn:li:video:7987654321,"
            "Watch our conference presentation,https://dms.licdn.com/dms/video/recording.mp4\n"
        )
        acts = parse_shares_csv(csv_content)
        self.assertEqual(len(acts), 2)
        # First item has urn:li:ugcPost and urn:li:digitalmediaasset -> must be 'post', NOT 'video'
        self.assertEqual(acts[0].type, "post")
        # Second item has .mp4 video URL and urn:li:video -> must be 'video'
        self.assertEqual(acts[1].type, "video")

    def test_ambiguous_date_parsing_safety(self):
        """
        Verify that date parsing handles ambiguous DD/MM/YYYY and MM/DD/YYYY formats
        safely without throwing exceptions or corrupting dates.
        """
        # Ambiguous both <= 12: defaults to US MM/DD/YYYY (05 = May, 06 = 6th)
        self.assertEqual(safe_normalize_date("05/06/2026"), "2026-05-06")

        # Unambiguous DD/MM/YYYY (25 > 12)
        self.assertEqual(safe_normalize_date("25/10/2026"), "2026-10-25")

        # Unambiguous MM/DD/YYYY (25 > 12)
        self.assertEqual(safe_normalize_date("10/25/2026"), "2026-10-25")

        # Period / dot and slash ISO variants
        self.assertEqual(safe_normalize_date("2026.10.18"), "2026-10-18")
        self.assertEqual(safe_normalize_date("2026/10/18"), "2026-10-18")

        # Feb 30 is impossible: fallback safely
        res_feb = safe_normalize_date("02/30/2026")
        # Should not crash, and returns either fallback or None
        self.assertTrue(res_feb is None or res_feb.startswith("2026"))

    def test_historical_analysis_preserved_on_failed_attempt(self):
        """
        Verify that a previous successful analysis for a profile is preserved in history
        and attached as historical_successful_analysis when a subsequent attempt fails.
        """
        from services.analysis_storage import (
            save_profile_successful_analysis,
            load_profile_successful_analysis,
            save_latest_analysis,
            load_latest_analysis
        )
        test_slug = "test-historian"
        mock_success = {
            "subject_name": "Test Historian",
            "subject_url": f"https://www.linkedin.com/in/{test_slug}/",
            "total_activities": 3,
            "posts_count": 3,
            "activities": [
                {"id": "act_1", "type": "post", "text": "Post 1", "date": "2026-10-01"},
                {"id": "act_2", "type": "post", "text": "Post 2", "date": "2026-10-02"},
                {"id": "act_3", "type": "post", "text": "Post 3", "date": "2026-10-03"}
            ]
        }
        # 1. Save prior success
        save_profile_successful_analysis(test_slug, mock_success)

        # 2. Simulate subsequent failed attempt
        failed_attempt = {
            "subject_name": "Test Historian",
            "subject_url": f"https://www.linkedin.com/in/{test_slug}/",
            "total_activities": 0,
            "posts_count": 0,
            "activities": [],
            "status_type": "ACTIVITY_SECTION_UNAVAILABLE",
            "retrieval_completeness": "UNAVAILABLE",
            "empty_message": "LinkedIn activity section is not accessible."
        }
        save_latest_analysis(failed_attempt)

        # Verify historical data is attached to the failed attempt
        saved_latest = load_latest_analysis()
        self.assertIsNotNone(saved_latest)
        self.assertIn("historical_successful_analysis", saved_latest)
        hist = saved_latest["historical_successful_analysis"]
        self.assertEqual(hist["total_activities"], 3)
        self.assertEqual(len(hist["activities"]), 3)

        # Verify profile history file still retains the 3 activities
        persisted_hist = load_profile_successful_analysis(test_slug)
        self.assertIsNotNone(persisted_hist)
        self.assertEqual(persisted_hist["total_activities"], 3)

    def test_authenticated_feed_dom_extraction(self):
        """
        Verify that when public tab panels are absent, the parser extracts activities
        from modern / authenticated feed DOM (.feed-shared-update-v2) including
        reposts, videos, and actor-author distinction.
        """
        mock_feed_html = """
        <!DOCTYPE html>
        <html>
        <head><title>Neel Mani | LinkedIn</title></head>
        <body>
            <section class="pv-top-card">
                <h1 class="top-card-layout__title">Dr. Neel Mani</h1>
                <img class="pv-top-card-profile-picture__image" src="https://media.licdn.com/dms/image/v2/neel.jpg" />
            </section>
            
            <div class="feed-shared-update-v2" data-urn="urn:li:activity:700100200">
                <div class="feed-shared-update-v2__context-bar">
                    <span>Neel Mani reposted this</span>
                </div>
                <div class="update-components-actor">
                    <span class="update-components-actor__name">Alice Research</span>
                    <span class="update-components-actor__description">AI Professor</span>
                    <span class="update-components-actor__sub-description">2w • Edited</span>
                </div>
                <div class="feed-shared-update-v2__description">Groundbreaking paper on scalable agents.</div>
                <span class="social-details-social-counts__reactions-count">42</span>
            </div>

            <div class="feed-shared-update-v2" data-urn="urn:li:activity:700100300">
                <div class="update-components-actor">
                    <span class="update-components-actor__name">Dr. Neel Mani</span>
                    <span class="update-components-actor__sub-description">3d</span>
                </div>
                <div class="feed-shared-update-v2__description">Check our live demonstration video!</div>
                <video class="feedshare-video" src="demo.mp4"></video>
                <span class="social-details-social-counts__reactions-count">88</span>
            </div>
        </body>
        </html>
        """
        sec_count, total_count, acts = _extract_activities_from_public_html(
            page_html=mock_feed_html,
            target_slug="neel-mani",
            target_name="Dr. Neel Mani"
        )
        self.assertGreaterEqual(sec_count, 1)
        self.assertEqual(len(acts), 2)

        # First item is a repost / share
        repost = acts[0]
        self.assertEqual(repost["type"], "share")
        self.assertFalse(repost["is_own_post"])
        self.assertEqual(repost["post_author"], "Alice Research")
        self.assertEqual(repost["actor"], "Dr. Neel Mani")
        self.assertIn("reposted Alice Research's post", repost["text"])
        self.assertEqual(repost["likes"], 42)

        # Second item is a video post
        video_act = acts[1]
        self.assertEqual(video_act["type"], "video")
        self.assertTrue(video_act["is_own_post"])
        self.assertEqual(video_act["post_author"], "Dr. Neel Mani")
        self.assertEqual(video_act["likes"], 88)

    def test_comment_actor_vs_post_author_distinction(self):
        """
        Verify that comments in public panels attribute the target profile
        as the actor and accurately capture the original author from the post slug / text.
        """
        public_comments_html = """
        <div class="tab__panel">
            <h3>Comments</h3>
            <div class="comment">
                <span>Neel Mani replied to Satya Nadella's post</span>
                <span class="before:middot">1w</span>
                <div class="text-color-text text-sm font-normal">Congratulations on the great release!</div>
                <a href="https://www.linkedin.com/posts/satya-nadella_azure-ai-announcement-activity-7123456789">Post Link</a>
            </div>
        </div>
        """
        sec_count, total_count, acts = _extract_activities_from_public_html(
            page_html=public_comments_html,
            target_slug="neel-mani",
            target_name="Neel Mani"
        )
        self.assertEqual(len(acts), 1)
        comment_act = acts[0]
        self.assertEqual(comment_act["type"], "comment")
        self.assertEqual(comment_act["actor"], "Neel Mani")
        self.assertEqual(comment_act["post_author"], "Satya Nadella")
        self.assertFalse(comment_act["is_own_post"])
        self.assertIn("Neel Mani commented on Satya Nadella's post", comment_act["text"])

    def test_http_999_stops_immediately_without_retry(self):
        """
        Verify that encountering HTTP 999 rate limit stops immediately and does NOT
        retry requests to LinkedIn, preventing worsening rate limits.
        """
        from linkedin.playwright_service import scrape_linkedin_profile_activities

        with patch("playwright.sync_api.sync_playwright") as mock_pw:
            mock_playwright_instance = MagicMock()
            mock_pw.return_value.__enter__.return_value = mock_playwright_instance

            mock_browser = MagicMock()
            mock_playwright_instance.chromium.launch.return_value = mock_browser

            mock_context = MagicMock()
            mock_browser.new_context.return_value = mock_context

            mock_page = MagicMock()
            mock_context.new_page.return_value = mock_page

            mock_response = MagicMock()
            mock_response.status = 999
            mock_page.goto.return_value = mock_response
            mock_page.url = "https://www.linkedin.com/in/rate-limit-test/"
            mock_page.title.return_value = "999 Rate Limit"

            status_out = {}
            res = scrape_linkedin_profile_activities(
                profile_url="https://www.linkedin.com/in/rate-limit-test/",
                status_out=status_out
            )

            # Must stop immediately: exactly 1 goto call (no second retry goto)
            self.assertEqual(mock_page.goto.call_count, 1)
            self.assertFalse(res.success)
            self.assertEqual(res.status_type, "RATE_LIMITED_999")
            self.assertEqual(res.retrieval_completeness, "BLOCKED")
            self.assertIn("rate limit", res.message.lower())

    def test_failed_attempt_preserves_historical_metrics_and_records(self):
        """
        Verify that an HTTP 999 or blocked attempt preserves prior successful history,
        does not overwrite it, and attaches historical metrics and date range.
        """
        from services.analysis_storage import (
            save_profile_successful_analysis,
            load_profile_successful_analysis,
            save_latest_analysis,
            load_latest_analysis
        )
        test_slug = "dr-neel-mani-test"
        mock_success = {
            "subject_name": "Dr. Neel Mani",
            "subject_url": f"https://www.linkedin.com/in/{test_slug}/",
            "total_activities": 10,
            "posts_count": 4,
            "comments_count": 3,
            "likes_count": 2,
            "shares_count": 1,
            "videos_count": 0,
            "by_type": {"post": 4, "comment": 3, "like": 2, "share": 1},
            "date_range": {"start": "2026-07-16", "end": "2026-10-29"},
            "activities": [
                {"id": f"act_{i}", "type": "post" if i < 4 else ("comment" if i < 7 else ("like" if i < 9 else "share")), "date": "2026-08-01"}
                for i in range(10)
            ]
        }
        # 1. Save prior success
        save_profile_successful_analysis(test_slug, mock_success)

        # 2. Simulate subsequent failed HTTP 999 attempt
        failed_attempt = {
            "subject_name": "Dr. Neel Mani",
            "subject_url": f"https://www.linkedin.com/in/{test_slug}/",
            "total_activities": 0,
            "posts_count": 0,
            "comments_count": 0,
            "likes_count": 0,
            "shares_count": 0,
            "videos_count": 0,
            "by_type": {},
            "date_range": {"start": "2026-10-31", "end": "2026-10-31"},
            "activities": [],
            "status_type": "RATE_LIMITED_999",
            "retrieval_completeness": "BLOCKED",
            "empty_message": "LinkedIn rate limit encountered (HTTP 999)."
        }
        save_latest_analysis(failed_attempt)

        # 3. Verify history is NOT deleted or overwritten
        stored_history = load_profile_successful_analysis(test_slug)
        self.assertIsNotNone(stored_history)
        self.assertEqual(stored_history["total_activities"], 10)
        self.assertEqual(stored_history["posts_count"], 4)
        self.assertEqual(len(stored_history["activities"]), 10)

        # 4. Verify latest analysis carries historical_successful_analysis
        latest = load_latest_analysis()
        self.assertIsNotNone(latest)
        self.assertEqual(latest["status_type"], "RATE_LIMITED_999")
        self.assertEqual(latest["retrieval_completeness"], "BLOCKED")
        self.assertIn("historical_successful_analysis", latest)
        hist = latest["historical_successful_analysis"]
        self.assertEqual(hist["total_activities"], 10)
        self.assertEqual(hist["posts_count"], 4)
        self.assertEqual(hist["comments_count"], 3)
        self.assertEqual(hist["date_range"]["start"], "2026-07-16")
        self.assertEqual(hist["date_range"]["end"], "2026-10-29")

    def test_multi_profile_individual_historical_fallback(self):
        """
        Verify that for multi-profile analysis, historical fallback is applied
        separately to each affected profile that encountered a retrieval failure.
        """
        from services.analysis_storage import (
            save_profile_successful_analysis,
            save_latest_analysis,
            load_latest_analysis
        )
        slug_succ = "profile-live-success"
        slug_fail = "profile-rate-limited"

        # Pre-seed history only for profile-rate-limited
        save_profile_successful_analysis(slug_fail, {
            "subject_name": "Rate Limited Profile",
            "subject_url": f"https://www.linkedin.com/in/{slug_fail}/",
            "total_activities": 5,
            "posts_count": 5,
            "activities": [{"id": f"p2_act_{i}", "type": "post"} for i in range(5)]
        })

        # Multi-profile run: Profile 1 succeeded live, Profile 2 failed live with 0
        multi_payload = {
            "is_multi_profile": True,
            "profiles_count": 2,
            "total_activities": 3,
            "activities": [{"id": f"p1_act_{i}", "type": "post"} for i in range(3)],
            "profile_results": [
                {
                    "subject_name": "Live Success Profile",
                    "subject_url": f"https://www.linkedin.com/in/{slug_succ}/",
                    "total_activities": 3,
                    "activities": [{"id": f"p1_act_{i}", "type": "post"} for i in range(3)]
                },
                {
                    "subject_name": "Rate Limited Profile",
                    "subject_url": f"https://www.linkedin.com/in/{slug_fail}/",
                    "total_activities": 0,
                    "activities": [],
                    "status_type": "RATE_LIMITED_999"
                }
            ]
        }
        save_latest_analysis(multi_payload)

        latest = load_latest_analysis()
        self.assertIsNotNone(latest)
        prof_res = latest.get("profile_results", [])
        self.assertEqual(len(prof_res), 2)
        # Profile 1 has no historical attachment because it succeeded live
        self.assertNotIn("historical_successful_analysis", prof_res[0])
        # Profile 2 has historical attachment because it had 0 activities
        self.assertIn("historical_successful_analysis", prof_res[1])
        self.assertEqual(prof_res[1]["historical_successful_analysis"]["total_activities"], 5)


if __name__ == "__main__":
    unittest.main()
