"""
Regression tests for the bugs listed in BUG_REPORT.md (2026-11-25).
"""
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app import app
from linkedin.data_parser import parse_activities_csv
from services.activity_service import get_activities_for_subject
from services.analysis_storage import extract_slug as analysis_extract_slug
from services.pipeline_service import get_pipeline_run
from services.saved_profiles_storage import extract_slug as saved_extract_slug, parse_linkedin_url

CSV = "date,type,text,likes,comments,shares\n2026-10-01,post,My own uploaded post,1,0,0\n"


class TestBugReportFixes(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    # Bug 1: CSV/ZIP upload crashed with NameError (os not imported)
    def test_csv_upload_is_processed(self):
        res = self.client.post(
            "/api/analyze",
            data={"file": (io.BytesIO(CSV.encode()), "activity.csv"), "activities": "all"},
            content_type="multipart/form-data",
        )
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        self.assertEqual(res.get_json().get("total_activities"), 1)

    # Bug 2: /api/analysis/latest crashed with NameError (req_subject_name)
    def test_latest_for_unanalyzed_profile_returns_404(self):
        res = self.client.get(
            "/api/analysis/latest?profile_url=https://www.linkedin.com/in/never-analyzed-xyz-123&subject_name=Nobody"
        )
        self.assertEqual(res.status_code, 404)
        self.assertIn("Nobody", res.get_json()["error"])

    # Bug 3: imported rows were attributed to every analyzed profile
    def test_untagged_import_is_not_attributed_to_another_profile(self):
        rows = [a.to_dict() for a in parse_activities_csv(CSV)]
        with app.app_context():
            other = get_activities_for_subject(
                subject_id="satyanadella", subject_name="Satya Nadella", data_source="sample",
                session_activities=rows, subject_url="https://www.linkedin.com/in/satyanadella/",
            )
            own = get_activities_for_subject(
                subject_id="self", subject_name="My Profile", data_source="sample",
                session_activities=rows, subject_url=None,
            )
        self.assertNotIn("My own uploaded post", [a.get("text") for a in other])
        self.assertEqual([a.get("text") for a in own], ["My own uploaded post"])

    def test_tagged_import_matches_only_its_own_profile(self):
        rows = [dict(a.to_dict(), subject_url="https://www.linkedin.com/in/sneha-patel/", subject_id="sneha-patel")
                for a in parse_activities_csv(CSV)]
        with app.app_context():
            same = get_activities_for_subject(
                subject_id="sneha-patel", subject_name="Sneha", data_source="sample",
                session_activities=rows, subject_url="https://www.linkedin.com/in/sneha-patel",
            )
            prefix = get_activities_for_subject(
                subject_id="sneha", subject_name="Sneha", data_source="sample",
                session_activities=rows, subject_url="https://www.linkedin.com/in/sneha",
            )
        self.assertEqual(len(same), 1)
        self.assertNotIn("My own uploaded post", [a.get("text") for a in prefix])

    # Bug 4: "Videos only" on a profile without videos raised NameError (display_name)
    def test_video_filter_without_videos_reports_no_videos(self):
        import time
        from linkedin.playwright_service import _PROFILE_CACHE, scrape_linkedin_profile_activities
        html = (
            "<!DOCTYPE html><html><head><title>Neel Mani, PhD | LinkedIn</title></head>"
            "<body><h1 class=\"top-card-layout__title\">Neel Mani, PhD</h1>"
            "<div class=\"tab__panel\"><div class=\"profile-activity-card\">"
            "<a class=\"base-card__full-link\" href=\"https://www.linkedin.com/posts/neel-mani_act-1\">"
            "<span class=\"sr-only\">Neel Mani posted this</span></a>"
            "<div class=\"base-main-card__metadata\">1d</div>"
            "<div class=\"see-more-text\">A text-only post.</div>"
            "</div></div></body></html>"
        )
        _PROFILE_CACHE["neel-mani"] = {"html": html, "title": "Neel Mani, PhD | LinkedIn", "status": 200,
                                       "timestamp": time.time(), "reached_end_of_feed": True}
        try:
            res = scrape_linkedin_profile_activities("https://www.linkedin.com/in/neel-mani/", activity_types=["video"])
        finally:
            _PROFILE_CACHE.pop("neel-mani", None)
        self.assertTrue(res.success)
        self.assertEqual(res.status_type, "NO_VIDEO_ACTIVITIES_FOUND")
        self.assertIn("No video activities found", res.message)

    # Bug 5: junk and non-LinkedIn input was squashed into a slug
    def test_slug_functions_reject_junk_consistently(self):
        for value in ("httpswwwlinkedincomin", "google.com/abc", "hello world"):
            self.assertEqual(saved_extract_slug(value), "", value)
            self.assertEqual(analysis_extract_slug(value), "", value)
            self.assertFalse(parse_linkedin_url(value)[0], value)
        self.assertEqual(saved_extract_slug("akanksha-chauhan-5095b7266"), "akanksha-chauhan-5095b7266")

    # Bug 8: validation errors left the pipeline run in the running state
    def test_validation_error_closes_pipeline_run(self):
        res = self.client.post(
            "/api/analyze?run_id=run_test_validation_close",
            json={"subject_url": "https://www.linkedin.com/in/someone/", "startDate": "2026-10-10", "endDate": "2026-10-01"},
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(get_pipeline_run("run_test_validation_close")["status"], "failed")


if __name__ == "__main__":
    unittest.main()
