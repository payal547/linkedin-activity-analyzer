import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from services import dashboard_analytics_service as analytics
from services.analysis_runs_storage import save_analysis_run
from app import app as flask_app


class FakeCursor(list):
    def sort(self, key, direction=1):
        super().sort(key=lambda row: str(row.get(key) or ""), reverse=direction < 0)
        return self

    def skip(self, amount):
        self[:] = self[amount:]
        return self

    def limit(self, amount):
        self[:] = self[:amount]
        return self


class FakeCollection:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.indexes = []

    def find(self, query=None, projection=None):
        query = query or {}
        rows = self.rows
        if "profile_slugs" in query:
            rows = [row for row in rows if query["profile_slugs"] in row.get("profile_slugs", [])]
        if "slug" in query:
            rows = [row for row in rows if row.get("slug") == query["slug"]]
        return FakeCursor(dict(row) for row in rows)

    def find_one(self, query, projection=None):
        return next(iter(self.find(query, projection)), None)

    def count_documents(self, query):
        return len(self.find(query))

    def create_index(self, *args, **kwargs):
        self.indexes.append((args, kwargs))

    def insert_one(self, doc):
        if any(row.get("_id") == doc.get("_id") for row in self.rows):
            error = Exception("duplicate")
            error.__class__.__name__ = "DuplicateKeyError"
            raise error
        self.rows.append(dict(doc))


class FakeDB(dict):
    def __getitem__(self, name):
        return dict.__getitem__(self, name)


def fixture_db():
    return FakeDB({
        "saved_profiles": FakeCollection([
            {"id": "alpha", "profile_url": "https://www.linkedin.com/in/alpha/", "name": "Alpha"},
            {"id": "beta", "profile_url": "https://www.linkedin.com/in/beta/", "name": "Beta"},
        ]),
        "profile_analysis_history": FakeCollection([
            {"slug": "alpha", "subject_name": "Alpha", "subject_url": "https://www.linkedin.com/in/alpha/", "historical_analyzed_at": "2026-11-01T00:00:00", "retrieval_completeness": "PARTIAL_CEILING_REACHED", "date_range": {"start": "2026-01-10", "end": "2026-11-01"}, "activities": [
                {"id": "a1", "type": "post", "date": "2026-10-15", "text": "Hello", "post_url": "https://www.linkedin.com/feed/update/urn:li:activity:1", "subject_url": "https://www.linkedin.com/in/other/"},
                {"id": "a2", "type": "video", "date": "2026-11-01", "is_video": True, "video_url": "https://media.example/v.mp4"},
            ]},
        ]),
        "analysis_runs": FakeCollection([
            {"_id": "run-1", "profile_slugs": ["alpha"], "started_at": "2026-11-01T00:00:00", "completed_at": "2026-11-01T00:01:00", "status": "completed", "total_activities": 2, "posts_count": 1, "videos_count": 1},
        ]),
        "activities": FakeCollection(),
        "imported_activities": FakeCollection(),
    })


class TestDashboardAnalytics(unittest.TestCase):
    def setUp(self):
        self.db = fixture_db()
        self.get_db_patch = patch.object(analytics, "get_db", return_value=self.db)
        self.get_db_patch.start()
        self.addCleanup(self.get_db_patch.stop)

    def test_profile_history_scope_wins_over_stale_embedded_subject(self):
        content = analytics._collect_content(
            self.db["profile_analysis_history"].rows, [], []
        )
        self.assertEqual({row["profile_slug"] for row in content}, {"alpha"})

    def test_duplicate_events_with_run_local_ids_are_counted_once(self):
        content = analytics._collect_content([{
            "slug": "alpha",
            "activities": [
                {"id": "activity_1", "type": "like", "date": "2026-10-15", "text": "Liked a post", "post_url": "https://www.linkedin.com/feed/update/urn:li:activity:1"},
                {"id": "activity_9", "type": "like", "date": "2026-10-15", "text": "Liked a post", "post_url": "https://www.linkedin.com/feed/update/urn:li:activity:1"},
                {"id": "activity_10", "type": "comment", "date": "2026-10-15", "text": "Liked a post", "post_url": "https://www.linkedin.com/feed/update/urn:li:activity:1"},
            ],
        }], [], [])
        self.assertEqual(len(content), 2)

    def test_trend_month_without_records_is_unknown_not_zero(self):
        trends = analytics._trends([
            {"date": "2026-11-01", "type": "video", "is_video": True}
        ], [], count=2)
        self.assertEqual(trends[-1]["activity_count"], 1)
        self.assertEqual(trends[-1]["activity_coverage"], "observed")
        self.assertIsNone(trends[0]["activity_count"])
        self.assertEqual(trends[0]["activity_coverage"], "unknown")

    def test_content_filters_and_profile_isolation(self):
        result = analytics.list_content(profile_slug="alpha", kind="video", page=1, per_page=10)
        self.assertEqual(result["total_items"], 1)
        self.assertTrue(result["items"][0]["is_video"])
        self.assertEqual(result["items"][0]["profile_slug"], "alpha")

    def test_content_library_event_and_media_filters_are_strict(self):
        rows = [
            {"id": "post", "type": "post", "action_type": "post", "date": "2026-11-01T09:30:00", "post_content": "Launch note", "post_url": "https://www.linkedin.com/feed/update/post"},
            {"id": "video", "type": "video", "action_type": "video", "date": "2026-11-02", "is_video": True, "video_url": "https://media.licdn.com/video.mp4", "video_thumbnail": "https://media.licdn.com/poster.jpg"},
            {"id": "photo", "type": "post", "action_type": "post", "date": "2026-11-03", "media_url": "https://media.licdn.com/photo.jpg", "video_status": "No video"},
            {"id": "like", "type": "like", "action_type": "like", "date": "2026-11-04", "reaction_type": "Celebrate", "post_content": "An update", "post_url": "https://www.linkedin.com/feed/update/like"},
            {"id": "comment", "type": "comment", "action_type": "comment", "date": "2026-11-05", "comment_text": "My comment", "post_content": "Original post", "post_url": "https://www.linkedin.com/feed/update/comment"},
            {"id": "share", "type": "share", "action_type": "share", "date": "2026-11-06", "text": "Reposted this", "post_content": "Shared content", "post_url": "https://www.linkedin.com/feed/update/share"},
            {"id": "like-photo", "type": "like", "action_type": "like", "date": "2026-11-07", "media_type": "image", "media_url": "https://media.licdn.com/liked-photo.jpg"},
            {"id": "like-video", "type": "video", "action_type": "like", "date": "2026-11-07", "video_status": "Video detected (LinkedIn auth required for playback)"},
            {"id": "beta-video", "type": "video", "action_type": "video", "date": "2026-11-08", "is_video": True, "video_url": "https://media.licdn.com/beta.mp4"},
        ]
        db = fixture_db()
        db["profile_analysis_history"].rows = [
            {"slug": "alpha", "subject_name": "Alpha", "subject_url": "https://www.linkedin.com/in/alpha/", "activities": rows[:8]},
            {"slug": "beta", "subject_name": "Beta", "subject_url": "https://www.linkedin.com/in/beta/", "activities": rows[8:]},
        ]
        with patch.object(analytics, "get_db", return_value=db):
            expected = {
                "video": {"video", "like-video", "beta-video"},
                "photo": {"photo", "like-photo"},
                "post": {"post", "photo"},
                "like": {"like", "like-photo", "like-video"},
                "comment": {"comment"},
                "repost": {"share"},
            }
            for kind, expected_ids in expected.items():
                with self.subTest(kind=kind):
                    result = analytics.list_content(kind=kind, per_page=50)
                    self.assertEqual({row["id"] for row in result["items"]}, expected_ids)
            self.assertEqual(
                {row["id"] for row in analytics.list_content(profile_slug="beta", kind="video")["items"]},
                {"beta-video"},
            )

    def test_video_stream_only_records_are_classified_and_false_string_is_not_video(self):
        self.assertTrue(analytics._is_video({"video_stream_url": "https://cdn.example/video.mp4"}))
        self.assertFalse(analytics._is_video({"is_video": "false", "video_status": "No video"}))

        db = fixture_db()
        db["profile_analysis_history"].rows = [{
            "slug": "alpha",
            "subject_name": "Alpha",
            "subject_url": "https://www.linkedin.com/in/alpha/",
            "activities": [
                {"id": "stream-only", "type": "post", "date": "2026-11-10", "video_stream_url": "https://cdn.example/video.mp4", "video_mime_type": "video/mp4"},
                {"id": "false-flag", "type": "post", "date": "2026-11-11", "is_video": "false", "video_status": "No video"},
            ],
        }]
        with patch.object(analytics, "get_db", return_value=db):
            result = analytics.list_content(kind="video", per_page=10)
        self.assertEqual([row["id"] for row in result["items"]], ["stream-only"])

    def test_category_pagination_never_leaks_other_event_or_media_categories(self):
        rows = [
            {"id": "photo-post", "type": "post", "date": "2026-11-01", "media_type": "image", "media_url": "https://cdn.example/a.jpg"},
            {"id": "video-like", "type": "like", "date": "2026-11-02", "video_stream_url": "https://cdn.example/b.mp4"},
            {"id": "plain-comment", "type": "comment", "date": "2026-11-03", "comment_text": "Comment"},
            {"id": "share", "type": "share", "date": "2026-11-04", "text": "Shared"},
        ]
        db = fixture_db()
        db["profile_analysis_history"].rows = [{
            "slug": "alpha",
            "subject_name": "Alpha",
            "subject_url": "https://www.linkedin.com/in/alpha/",
            "activities": rows,
        }]
        with patch.object(analytics, "get_db", return_value=db):
            for kind, expected_id, expected_action in (("photo", "photo-post", "post"), ("video", "video-like", "like"), ("post", "photo-post", "post"), ("like", "video-like", "like"), ("comment", "plain-comment", "comment"), ("repost", "share", "share")):
                with self.subTest(kind=kind):
                    first = analytics.list_content(profile_slug="alpha", kind=kind, start_date="2026-11-01", end_date="2026-11-30", page=1, per_page=1)
                    second = analytics.list_content(profile_slug="alpha", kind=kind, start_date="2026-11-01", end_date="2026-11-30", page=2, per_page=1)
                    self.assertEqual([row["id"] for row in first["items"]], [expected_id])
                    self.assertEqual(second["items"], [])
                    self.assertEqual(first["filters"]["profile"], "alpha")
                    self.assertEqual(first["filters"]["from"], "2026-11-01")
                    self.assertEqual(first["items"][0]["activity_kind"], expected_action)

    def test_content_library_combined_filters_search_dates_and_pagination(self):
        rows = [
            {"id": "a1", "type": "post", "action_type": "post", "date": "2026-11-01T09:30:00", "text": "Quarterly Alpha update", "post_url": "https://www.linkedin.com/feed/update/a1"},
            {"id": "a2", "type": "comment", "action_type": "comment", "date": "2026-11-02", "comment_text": "A useful note", "post_url": "https://www.linkedin.com/feed/update/a2"},
            {"id": "a3", "type": "like", "action_type": "like", "date": "2026-11-03", "reaction_type": "Like", "text": "Reacted to an update", "post_url": "https://www.linkedin.com/feed/update/a3"},
            {"id": "b1", "type": "post", "action_type": "post", "date": "2026-11-03", "text": "Quarterly Beta update", "post_url": "https://www.linkedin.com/feed/update/b1"},
        ]
        db = fixture_db()
        db["profile_analysis_history"].rows = [
            {"slug": "alpha", "subject_name": "Alpha", "subject_url": "https://www.linkedin.com/in/alpha/", "activities": rows[:3]},
            {"slug": "beta", "subject_name": "Beta", "subject_url": "https://www.linkedin.com/in/beta/", "activities": rows[3:]},
        ]
        with patch.object(analytics, "get_db", return_value=db):
            result = analytics.list_content(profile_slug="alpha", kind="comment", search="Alpha", start_date="2026-11-01", end_date="2026-11-03")
            self.assertEqual({row["id"] for row in result["items"]}, {"a2"})
            self.assertEqual(result["items"][0]["profile_name"], "Alpha")
            self.assertEqual(result["items"][0]["date"], "2026-11-02")
            first = analytics.list_content(profile_slug="alpha", page=1, per_page=2)
            second = analytics.list_content(profile_slug="alpha", page=2, per_page=2)
            self.assertEqual(first["total_items"], 3)
            self.assertEqual(second["total_items"], 3)
            self.assertTrue(all(row["profile_slug"] == "alpha" for row in first["items"] + second["items"]))
            self.assertEqual(first["items"][0]["date"], "2026-11-03")
            self.assertEqual({row["id"] for row in second["items"]}, {"a1"})

    def test_run_listing_returns_only_profile_run(self):
        result = analytics.list_runs(profile_slug="alpha", page=1, per_page=10)
        self.assertEqual(result["total_items"], 1)
        self.assertEqual(result["items"][0]["run_id"], "run-1")


class TestAnalysisRunStorage(unittest.TestCase):
    def test_save_run_is_append_only_and_creates_additive_indexes(self):
        from services import analysis_runs_storage
        db = FakeDB({"analysis_runs": FakeCollection()})
        with patch.object(analysis_runs_storage, "get_db", return_value=db), patch.object(analysis_runs_storage, "_indexes_ready", False):
            saved = save_analysis_run({"run_id": "run-real-1", "status": "completed", "profile_slugs": ["alpha"]})
        self.assertTrue(saved)
        self.assertEqual(len(db["analysis_runs"].rows), 1)
        self.assertEqual(db["analysis_runs"].rows[0]["_id"], "run-real-1")
        self.assertEqual(len(db["analysis_runs"].indexes), 2)


class TestDashboardEndpoints(unittest.TestCase):
    def setUp(self):
        flask_app.config["TESTING"] = True
        self.client = flask_app.test_client()

    def test_overall_analytics_page_and_read_apis(self):
        page = self.client.get("/analytics")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Overall analytics", page.data)
        page.close()
        for endpoint in (
            "/api/dashboard/overview",
            "/api/dashboard/profiles?per_page=5",
            "/api/dashboard/profile-options",
            "/api/dashboard/runs?per_page=5",
            "/api/dashboard/content?per_page=5",
            "/api/dashboard/trends?months=12",
        ):
            with self.subTest(endpoint=endpoint):
                response = self.client.get(endpoint)
                self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
                self.assertIsInstance(response.get_json(), dict)

    def test_content_library_page_and_filtered_api_use_stored_profile_identity(self):
        page = self.client.get("/content-library")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'data-kind="photo"', page.data)
        self.assertIn(b'data-kind="repost"', page.data)
        page.close()

        db = fixture_db()
        db["profile_analysis_history"].rows = [{
            "slug": "alpha",
            "subject_name": "Alpha Profile",
            "subject_url": "https://www.linkedin.com/in/alpha/",
            "activities": [
                {"id": "alpha-video", "type": "video", "action_type": "video", "date": "2026-11-20", "is_video": True},
                {"id": "alpha-comment", "type": "comment", "action_type": "comment", "date": "2026-11-19", "comment_text": "Alpha note"},
            ],
        }]
        with patch.object(analytics, "get_db", return_value=db):
            response = self.client.get("/api/dashboard/content?profile=alpha&type=video&per_page=1")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["total_items"], 1)
        self.assertEqual(payload["selected_profile"], {"id": "alpha", "name": "Alpha"})
        self.assertEqual(payload["items"][0]["id"], "alpha-video")
        self.assertEqual(payload["items"][0]["profile_slug"], "alpha")


if __name__ == "__main__":
    unittest.main()
