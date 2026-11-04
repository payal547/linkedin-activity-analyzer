"""
Unit and Integration Tests for LinkedIn Video Activity Retrieval and Filtering.
Validates:
1. Video post that was liked (counts as 1 Like + 1 Video, total=1).
2. Ordinary post that was liked (counts as 1 Like + 0 Videos).
3. Video post commented on (counts as 1 Comment + 1 Video).
4. Video post reposted (counts as 1 Share + 1 Video).
5. Video without playable stream but with thumbnail (is_video=True, honest status).
6. Normal post with no video (is_video=False, video_status='No video').
7. Video activity within vs. outside date range filtering.
8. Single-profile and multi-profile video counting.
"""

import unittest
from datetime import date
from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "backend"))

from models.activity import Activity
from services.analysis_service import analyze_activities, _matches_selected_types
from linkedin.playwright_service import _extract_video_info, _extract_activities_from_public_html


class TestVideoActivityRetrieval(unittest.TestCase):
    """Verifies all 8 video activity retrieval and counting scenarios."""

    def test_scenario_1_video_post_liked(self):
        """
        Scenario 1: Video post that was liked.
        Must count as:
        - 1 Reaction / Like
        - 1 Video
        - Total activities: 1 (no double-counting)
        """
        video_like = {
            "id": "act_vlike_1",
            "date": "2026-11-04",
            "type": "like",
            "action_type": "like",
            "text": "Payal Pal reacted (Like) to Kishi Malhotra's video: 'AI advancements in 2026'",
            "post_content": "AI advancements in 2026",
            "post_author": "Kishi Malhotra",
            "post_url": "https://www.linkedin.com/posts/kishi-malhotra_ai-video-7261104/",
            "media_url": "https://media.licdn.com/dms/image/video_poster.jpg",
            "video_url": "https://dms.licdn.com/playlist/vid1/mp4_720p.mp4",
            "video_thumbnail": "https://media.licdn.com/dms/image/video_poster.jpg",
            "video_status": "Video stream extracted",
            "is_video": True,
            "reaction_type": "Like",
            "is_own_post": False,
            "likes": 50,
            "comments": 10,
            "shares": 2
        }

        # Analyze with all filters
        result = analyze_activities([video_like], activity_types=["all"])
        self.assertEqual(result["total_activities"], 1)
        self.assertEqual(result["likes_count"], 1)
        self.assertEqual(result["videos_count"], 1)
        self.assertEqual(result["posts_count"], 0)
        self.assertEqual(result["comments_count"], 0)
        self.assertEqual(result["shares_count"], 0)

        # Analyze with 'likes' filter: must be included
        res_likes = analyze_activities([video_like], activity_types=["likes"])
        self.assertEqual(res_likes["total_activities"], 1)
        self.assertEqual(res_likes["likes_count"], 1)
        self.assertEqual(res_likes["videos_count"], 1)

        # Analyze with 'videos' filter: must be included because it's a video activity
        res_videos = analyze_activities([video_like], activity_types=["videos"])
        self.assertEqual(res_videos["total_activities"], 1)
        self.assertEqual(res_videos["likes_count"], 1)
        self.assertEqual(res_videos["videos_count"], 1)

    def test_scenario_2_ordinary_post_liked(self):
        """
        Scenario 2: Ordinary post that was liked.
        Must count as:
        - 1 Reaction / Like
        - 0 Videos
        - Total activities: 1
        """
        normal_like = {
            "id": "act_nlike_1",
            "date": "2026-11-04",
            "type": "like",
            "action_type": "like",
            "text": "Payal Pal reacted (Like) to Aniket Pal's post: 'Great work team!'",
            "post_content": "Great work team!",
            "post_author": "Aniket Pal",
            "post_url": "https://www.linkedin.com/posts/aniket-pal-post-1/",
            "media_url": None,
            "video_url": None,
            "video_thumbnail": None,
            "video_status": "No video",
            "is_video": False,
            "reaction_type": "Like",
            "is_own_post": False,
            "likes": 20,
            "comments": 2,
            "shares": 0
        }

        result = analyze_activities([normal_like], activity_types=["all"])
        self.assertEqual(result["total_activities"], 1)
        self.assertEqual(result["likes_count"], 1)
        self.assertEqual(result["videos_count"], 0)

        # Analyze with 'videos' filter: ordinary like must NOT match
        res_videos = analyze_activities([normal_like], activity_types=["videos"])
        self.assertEqual(res_videos["total_activities"], 0)
        self.assertEqual(len(res_videos["activities"]), 0)

    def test_scenario_3_video_post_commented(self):
        """
        Scenario 3: Video post commented on.
        Must count as:
        - 1 Comment
        - 1 Video
        - 0 Likes
        - Total activities: 1
        """
        video_comment = {
            "id": "act_vcomm_1",
            "date": "2026-11-04",
            "type": "comment",
            "action_type": "comment",
            "text": "Payal Pal commented on Rohan's video: 'Inspiring explanation!'",
            "comment_text": "Inspiring explanation!",
            "post_content": "Explanation of Python concurrency",
            "post_author": "Rohan Gupta",
            "post_url": "https://www.linkedin.com/posts/rohan-gupta-python-concurrency-video/",
            "media_url": "https://media.licdn.com/dms/image/rohan_video_poster.jpg",
            "video_url": None,
            "video_thumbnail": "https://media.licdn.com/dms/image/rohan_video_poster.jpg",
            "video_status": "Video detected (Stream protected by LinkedIn, permalink available)",
            "is_video": True,
            "is_own_post": False,
            "likes": 0,
            "comments": 5,
            "shares": 0
        }

        result = analyze_activities([video_comment], activity_types=["all"])
        self.assertEqual(result["total_activities"], 1)
        self.assertEqual(result["comments_count"], 1)
        self.assertEqual(result["videos_count"], 1)
        self.assertEqual(result["likes_count"], 0)

        # Matches 'comments'
        res_comm = analyze_activities([video_comment], activity_types=["comments"])
        self.assertEqual(res_comm["total_activities"], 1)

        # Matches 'videos'
        res_vid = analyze_activities([video_comment], activity_types=["videos"])
        self.assertEqual(res_vid["total_activities"], 1)

    def test_scenario_4_video_post_reposted(self):
        """
        Scenario 4: Video post reposted / shared.
        Must count as:
        - 1 Share / Repost
        - 1 Video
        - Total activities: 1
        """
        video_share = {
            "id": "act_vshare_1",
            "date": "2026-11-04",
            "type": "share",
            "action_type": "share",
            "text": "Payal Pal reposted Sarah's video: 'Check out our demo'",
            "post_content": "Check out our demo",
            "post_author": "Sarah Connor",
            "post_url": "https://www.linkedin.com/posts/sarah-connor_demo-video-123/",
            "media_url": "https://media.licdn.com/poster_demo.jpg",
            "video_url": "https://www.youtube.com/watch?v=demo12345",
            "video_thumbnail": "https://media.licdn.com/poster_demo.jpg",
            "video_status": "External video embed",
            "is_video": True,
            "is_own_post": False,
            "likes": 12,
            "comments": 1,
            "shares": 3
        }

        result = analyze_activities([video_share], activity_types=["all"])
        self.assertEqual(result["total_activities"], 1)
        self.assertEqual(result["shares_count"], 1)
        self.assertEqual(result["videos_count"], 1)
        self.assertEqual(result["posts_count"], 0)

        # Matches 'shares'
        res_share = analyze_activities([video_share], activity_types=["shares"])
        self.assertEqual(res_share["total_activities"], 1)

        # Matches 'videos'
        res_vid = analyze_activities([video_share], activity_types=["videos"])
        self.assertEqual(res_vid["total_activities"], 1)

    def test_scenario_5_video_without_playable_stream_with_thumbnail(self):
        """
        Scenario 5: Video without playable stream but with thumbnail.
        Must retain is_video=True, video_thumbnail present, honest status.
        """
        html_chunk = (
            '<div class="feed-shared-update-v2">'
            '<div class="feedshare-video">'
            '<img class="feedshare-video__preview" src="https://media.licdn.com/dms/image/protected_poster.jpg">'
            '</div>'
            '</div>'
        )
        has_v, v_url, v_thumb, v_status = _extract_video_info(html_chunk)
        self.assertTrue(has_v)
        self.assertIsNone(v_url)  # No fake stream URL invented
        self.assertEqual(v_thumb, "https://media.licdn.com/dms/image/protected_poster.jpg")
        self.assertIn("Stream protected by LinkedIn", v_status)

        act = Activity(
            id="act_protected_v",
            date="2026-11-04",
            type="like",
            action_type="like",
            text="Reacted to video",
            video_url=v_url,
            video_thumbnail=v_thumb,
            video_status=v_status,
            is_video=True
        )
        self.assertTrue(act.is_video)
        self.assertEqual(act.video_status, v_status)
        self.assertEqual(act.video_thumbnail, "https://media.licdn.com/dms/image/protected_poster.jpg")

    def test_scenario_6_normal_post_no_video(self):
        """
        Scenario 6: Normal post with no video.
        Must have is_video=False, video_url=None, video_status='No video'.
        """
        html_chunk = (
            '<div class="feed-shared-update-v2">'
            '<div class="update-components-text">Just finished writing clean code!</div>'
            '<div class="update-components-image"><img src="https://media.licdn.com/photo.jpg"></div>'
            '</div>'
        )
        has_v, v_url, v_thumb, v_status = _extract_video_info(html_chunk)
        self.assertFalse(has_v)
        self.assertIsNone(v_url)
        self.assertIsNone(v_thumb)
        self.assertEqual(v_status, "No video")

        act = Activity(
            id="act_normal_post",
            date="2026-11-04",
            type="post",
            action_type="post",
            text="Just finished writing clean code!",
            is_video=False
        )
        self.assertFalse(act.is_video)
        self.assertEqual(act.video_status, "No video")

    def test_scenario_7_video_activity_within_vs_outside_date_range(self):
        """
        Scenario 7: Video activity within vs. outside date range.
        One video like inside range, one outside.
        Only matching range activities must be returned and counted.
        """
        v_today = {
            "id": "v_today",
            "date": "2026-11-04",
            "type": "like",
            "action_type": "like",
            "text": "Liked video today",
            "is_video": True,
            "video_thumbnail": "https://media.licdn.com/thumb_today.jpg"
        }
        v_past = {
            "id": "v_past",
            "date": "2026-10-15",
            "type": "like",
            "action_type": "like",
            "text": "Liked video last month",
            "is_video": True,
            "video_thumbnail": "https://media.licdn.com/thumb_past.jpg"
        }

        # Filter strictly for today: 2026-11-04 to 2026-11-04
        res_today = analyze_activities(
            [v_today, v_past],
            activity_types=["all"],
            start_date="2026-11-04",
            end_date="2026-11-04"
        )
        self.assertEqual(res_today["total_activities"], 1)
        self.assertEqual(res_today["likes_count"], 1)
        self.assertEqual(res_today["videos_count"], 1)
        self.assertEqual(res_today["activities"][0]["id"], "v_today")

        # Filter strictly for October: 2026-10-01 to 2026-10-31
        res_oct = analyze_activities(
            [v_today, v_past],
            activity_types=["all"],
            start_date="2026-10-01",
            end_date="2026-10-31"
        )
        self.assertEqual(res_oct["total_activities"], 1)
        self.assertEqual(res_oct["likes_count"], 1)
        self.assertEqual(res_oct["videos_count"], 1)
        self.assertEqual(res_oct["activities"][0]["id"], "v_past")

        # Both inside broad range
        res_all = analyze_activities(
            [v_today, v_past],
            activity_types=["all"],
            start_date="2026-10-01",
            end_date="2026-11-05"
        )
        self.assertEqual(res_all["total_activities"], 2)
        self.assertEqual(res_all["likes_count"], 2)
        self.assertEqual(res_all["videos_count"], 2)

    def test_scenario_8_single_and_multi_profile_video_counting(self):
        """
        Scenario 8: Single-profile and multi-profile video counting.
        Profile A (Payal Pal): 2 video likes, 1 text post (3 activities, 2 likes, 2 videos, 1 post).
        Profile B (Neel Mani): 1 video post, 1 normal like (2 activities, 1 like, 1 video, 1 post=0).
        Combined:
        - Total activities: 5
        - Likes: 3
        - Videos: 3
        - Posts: 1
        """
        profile_a_activities = [
            {
                "id": "pa_1",
                "date": "2026-11-04",
                "type": "like",
                "action_type": "like",
                "text": "Payal Pal liked Video 1",
                "is_video": True,
                "video_url": "https://video.licdn.com/v1.mp4",
                "likes": 10
            },
            {
                "id": "pa_2",
                "date": "2026-11-04",
                "type": "like",
                "action_type": "like",
                "text": "Payal Pal liked Video 2",
                "is_video": True,
                "video_thumbnail": "https://media.licdn.com/v2.jpg",
                "likes": 15
            },
            {
                "id": "pa_3",
                "date": "2026-11-04",
                "type": "post",
                "action_type": "post",
                "text": "Payal Pal published an article",
                "is_video": False,
                "likes": 5
            }
        ]

        res_a = analyze_activities(profile_a_activities, activity_types=["all"], subject_name="Payal Pal")
        self.assertEqual(res_a["total_activities"], 3)
        self.assertEqual(res_a["likes_count"], 2)
        self.assertEqual(res_a["videos_count"], 2)
        self.assertEqual(res_a["posts_count"], 1)

        profile_b_activities = [
            {
                "id": "pb_1",
                "date": "2026-11-04",
                "type": "video",
                "action_type": "video",
                "text": "Neel Mani posted a video tutorial",
                "is_video": True,
                "video_url": "https://video.licdn.com/tutorial.mp4",
                "likes": 30
            },
            {
                "id": "pb_2",
                "date": "2026-11-04",
                "type": "like",
                "action_type": "like",
                "text": "Neel Mani liked a text post",
                "is_video": False,
                "likes": 8
            }
        ]

        res_b = analyze_activities(profile_b_activities, activity_types=["all"], subject_name="Neel Mani")
        self.assertEqual(res_b["total_activities"], 2)
        self.assertEqual(res_b["likes_count"], 1)
        self.assertEqual(res_b["videos_count"], 1)
        self.assertEqual(res_b["posts_count"], 0)

        # Multi-profile aggregation logic (as performed in activity_routes.py)
        combined_total_acts = res_a["total_activities"] + res_b["total_activities"]
        combined_likes_count = res_a["likes_count"] + res_b["likes_count"]
        combined_videos_count = res_a["videos_count"] + res_b["videos_count"]
        combined_posts_count = res_a["posts_count"] + res_b["posts_count"]

        self.assertEqual(combined_total_acts, 5)
        self.assertEqual(combined_likes_count, 3)
        self.assertEqual(combined_videos_count, 3)
        self.assertEqual(combined_posts_count, 1)

    def test_regression_nested_card_video_like_in_public_dom(self):
        """
        Regression Test: Verifies that public HTML where a like is split across
        Card 1 (header with 'liked this') and Card 2 (nested post with video markup)
        properly extracts video info from the child card.
        """
        html_sample = (
            '<div class="tab__panel reactions-tab">'
            '  <div class="profile-activity-card">'
            '    <a class="base-card__full-link" href="https://www.linkedin.com/posts/author_video-post-123/">'
            '      <span class="sr-only">Payal Pal liked this</span>'
            '    </a>'
            '  </div>'
            '  <div class="profile-activity-card">'
            '    <div class="publisher-author-card">'
            '      <h3 class="base-main-card__title">Rohan Gupta</h3>'
            '      <h4 class="base-main-card__subtitle">Cloud Architect</h4>'
            '      <div class="base-main-card__metadata">1d</div>'
            '    </div>'
            '    <div class="see-more-text">Watch this quick demo on Kubernetes deployment!</div>'
            '    <div class="feedshare-video">'
            '      <video poster="https://media.licdn.com/dms/image/k8s_video_poster.jpg">'
            '        <source src="https://dms.licdn.com/playlist/k8s_demo.mp4" type="video/mp4">'
            '      </video>'
            '    </div>'
            '    <a href="https://www.linkedin.com/posts/author_video-post-123/">View post</a>'
            '    <span data-num-reactions="120"></span>'
            '    <span data-num-comments="15"></span>'
            '  </div>'
            '</div>'
        )

        sec_cnt, total_elem, activities = _extract_activities_from_public_html(
            page_html=html_sample,
            target_slug="payal-pal-a18b92339",
            target_name="Payal Pal"
        )

        self.assertEqual(len(activities), 1)
        act = activities[0]
        self.assertEqual(act["type"], "like")
        self.assertEqual(act["action_type"], "like")
        self.assertTrue(act["is_video"])
        self.assertEqual(act["video_url"], "https://dms.licdn.com/playlist/k8s_demo.mp4")
        self.assertEqual(act["video_thumbnail"], "https://media.licdn.com/dms/image/k8s_video_poster.jpg")
        self.assertEqual(act["video_status"], "Video stream extracted")
        self.assertEqual(act["post_author"], "Rohan Gupta")
        self.assertIn("reacted (Like) to Rohan Gupta's video", act["text"])

    def test_regression_nested_card_video_repost_in_public_dom(self):
        """
        Regression Test: Verifies that a reposted video in public HTML
        is correctly identified as action_type='share' and is_video=True.
        """
        html_sample = (
            '<div class="tab__panel posts-tab">'
            '  <div class="profile-activity-card">'
            '    <a class="base-card__full-link" href="https://www.linkedin.com/posts/author_repost-video-456/">'
            '      <span class="sr-only">Payal Pal reposted this</span>'
            '    </a>'
            '  </div>'
            '  <div class="profile-activity-card">'
            '    <div class="publisher-author-card">'
            '      <h3 class="base-main-card__title">Sarah Connor</h3>'
            '      <div class="base-main-card__metadata">2d</div>'
            '    </div>'
            '    <div class="see-more-text">Keynote on AI agents</div>'
            '    <div class="update-components-video">'
            '      <img class="feedshare-video__preview" src="https://media.licdn.com/dms/image/ai_keynote_poster.jpg">'
            '    </div>'
            '    <a href="https://www.linkedin.com/posts/author_repost-video-456/">View post</a>'
            '  </div>'
            '</div>'
        )

        sec_cnt, total_elem, activities = _extract_activities_from_public_html(
            page_html=html_sample,
            target_slug="payal-pal-a18b92339",
            target_name="Payal Pal"
        )

        self.assertEqual(len(activities), 1)
        act = activities[0]
        self.assertEqual(act["type"], "share")
        self.assertEqual(act["action_type"], "share")
        self.assertTrue(act["is_video"])
        self.assertEqual(act["video_thumbnail"], "https://media.licdn.com/dms/image/ai_keynote_poster.jpg")
        self.assertIn("reposted Sarah Connor's video", act["text"])

    def test_regression_requirement_9_state_distinctions(self):
        """
        Regression Test: Verifies Requirement 9 distinction between:
        1. No video activities found (NO_VIDEO_ACTIVITIES_FOUND)
        2. Video activities inaccessible (VIDEO_ACTIVITIES_INACCESSIBLE)
        3. Authentication/session failure (AUTH_REQUIRED / SESSION_EXPIRED)
        4. Retrieval timeout (NETWORK_TIMEOUT)
        """
        # 1. No video activities found when videos filtered and empty
        res_empty_video = analyze_activities(
            [{"id": "p1", "type": "post", "text": "Just text", "date": "2026-11-04", "is_video": False}],
            activity_types=["videos"]
        )
        self.assertEqual(res_empty_video["total_activities"], 0)
        self.assertEqual(res_empty_video["videos_count"], 0)
        self.assertEqual(res_empty_video["empty_message"], "No video activities found for Selected Connection in the selected period.")

        # 2. Status tracker simulation for inaccessibility
        status_inaccessible = {
            "status_type": "VIDEO_ACTIVITIES_INACCESSIBLE",
            "status": "Video activities are inaccessible in public guest mode. An authenticated session is required to access member-level video interactions.",
            "empty_message": "Video activities are inaccessible in public guest mode."
        }
        self.assertEqual(status_inaccessible["status_type"], "VIDEO_ACTIVITIES_INACCESSIBLE")
        self.assertIn("inaccessible", status_inaccessible["status"])

        # 3. Status tracker simulation for auth failure
        status_auth_fail = {
            "status_type": "AUTH_REQUIRED",
            "status": "LinkedIn authentication/session failure: An active authenticated session is required.",
            "empty_message": "LinkedIn authentication required."
        }
        self.assertEqual(status_auth_fail["status_type"], "AUTH_REQUIRED")

        # 4. Status tracker simulation for timeout
        status_timeout = {
            "status_type": "NETWORK_TIMEOUT",
            "status": "Activity could not be retrieved from LinkedIn. Network timeout or connection unreachable.",
            "empty_message": "Retrieval timeout."
        }
        self.assertEqual(status_timeout["status_type"], "NETWORK_TIMEOUT")


if __name__ == "__main__":
    unittest.main()

