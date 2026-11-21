import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from app import app
from services.activity_service import get_activities_for_subject
from services.analysis_service import analyze_activities


class TestActivities(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def tearDown(self):
        test_file = Path(__file__).resolve().parent.parent / "backend" / "data" / "test_activities.json"
        if test_file.exists():
            try:
                test_file.unlink()
            except Exception:
                pass
        test_profiles = Path(__file__).resolve().parent.parent / "backend" / "data" / "test_saved_target_profiles.json"
        if test_profiles.exists():
            try:
                test_profiles.unlink()
            except Exception:
                pass

    def test_analyze_unauthenticated(self):
        response = self.client.post("/api/analyze", json={"subject_name": "Test", "data_source": "imported"})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data.get("subject_name"), "Test")

    def test_activity_add_rejects_linkedin_page_url_as_video_stream(self):
        response = self.client.post("/api/activities/add", json={
            "subject_name": "Video URL validation test",
            "subject_id": "video-url-validation-test",
            "type": "video",
            "post_url": "https://www.linkedin.com/posts/member_activity-123",
            "video_url": "https://www.linkedin.com/posts/member_activity-123",
            "video_status": "Video stream extracted",
            "video_thumbnail": "https://media.licdn.com/poster.jpg",
        })
        self.assertEqual(response.status_code, 200)
        item = response.get_json()["item"]
        self.assertIsNone(item["video_url"])
        self.assertEqual(item["post_url"], "https://www.linkedin.com/posts/member_activity-123")
        self.assertNotEqual(item["video_status"], "Video stream extracted")

    def test_analyze_primary_json_workflow(self):
        with self.client.session_transaction() as sess:
            sess["linkedin_profile"] = {"name": "Payal Pal", "email": "payal@example.com"}
            sess["linkedin_session_id"] = "session_123"
            sess["linkedin_access_token"] = "token_123"

        # Log an activity first
        add_res = self.client.post("/api/activities/add", json={
            "subject_name": "Payal Pal",
            "type": "post",
            "text": "Excited to share project progress!",
            "likes": 15,
            "comments": 3,
            "shares": 1
        })
        self.assertEqual(add_res.status_code, 200)

        payload = {
            "subject_id": "self",
            "subject_name": "Payal Pal",
            "data_source": "real_linkedin",
            "dateType": "30days",
            "activities": ["posts", "comments", "videos"]
        }

        response = self.client.post("/api/analyze", json=payload)
        self.assertEqual(response.status_code, 200)

        data = response.get_json()
        self.assertEqual(data.get("subject_name"), "Payal Pal")
        self.assertEqual(data.get("data_source"), "real_linkedin")
        self.assertIn("total_activities", data)
        self.assertIn("total_engagement", data)
        self.assertIn("by_type", data)
        self.assertTrue(len(data.get("activities", [])) > 0)

    def test_get_latest_analysis(self):
        with self.client.session_transaction() as sess:
            sess["linkedin_profile"] = {"name": "Payal Pal"}
            sess["latest_analysis"] = {"total_activities": 5, "likes": 10}

        response = self.client.get("/api/analysis/latest")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data.get("has_data"))
        self.assertEqual(data.get("total_activities"), 5)

    def test_activity_service_distinct_sources(self):
        # Sample subject
        sample_acts = get_activities_for_subject("sample_1", "Alex Morgan", "sample")
        self.assertTrue(len(sample_acts) > 0)
        for act in sample_acts:
            self.assertEqual(act.get("source"), "sample")

        # Analysis service filtering
        analysis = analyze_activities(sample_acts, ["post"], subject_name="Alex Morgan", data_source="sample")
        self.assertTrue(analysis["total_activities"] > 0)
        for act in analysis["activities"]:
            self.assertEqual(act["type"], "post")

    def test_validate_profile_url_invalid(self):
        res = self.client.post("/api/profile/validate-url", json={"url": "not-a-linkedin-url"})
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertFalse(data.get("valid"))
        self.assertIn("error", data)

    def test_validate_profile_url_generic(self):
        res = self.client.post("/api/profile/validate-url", json={"url": "https://www.linkedin.com/in/satyanadella/"})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("valid"))
        self.assertEqual(data.get("name"), "Satyanadella")
        self.assertEqual(data.get("profile_url"), "https://www.linkedin.com/in/satyanadella")

    def test_validate_profile_url_known_connection(self):
        res = self.client.post("/api/profile/validate-url", json={"url": "https://www.linkedin.com/in/pragyanandsaho"})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("valid"))
        self.assertIn("Pragyanand Sahoo", data.get("name"))

    def test_validate_profile_url_exact_slug_collision_prevention_saved_profile(self):
        from services.saved_profiles_storage import save_target_profile, _get_storage_file
        # Clean any test profile storage
        test_profiles = _get_storage_file()
        if test_profiles.exists():
            test_profiles.unlink()

        try:
            # Seed a saved profile for Neel Mani with slug 'neel-mani'
            save_target_profile({
                "profile_url": "https://www.linkedin.com/in/neel-mani/",
                "name": "Neel Mani",
                "headline": "LinkedIn Profile",
                "picture": "https://media.licdn.com/dms/image/v2/test/neel_mani_photo.jpg"
            })

            # 1. Partial slug request 'neel' must NOT match 'neel-mani'
            res_partial = self.client.post("/api/profile/validate-url", json={"url": "https://www.linkedin.com/in/neel/"})
            self.assertEqual(res_partial.status_code, 200)
            data_partial = res_partial.get_json()
            self.assertTrue(data_partial.get("valid"))
            # Must derive clean name from slug 'neel', NOT collide with 'Neel Mani'
            self.assertEqual(data_partial.get("name"), "Neel")
            self.assertNotEqual(data_partial.get("name"), "Neel Mani")
            self.assertIsNone(data_partial.get("picture"))

            # 2. Exact slug request 'neel-mani' MUST match the saved profile
            res_exact = self.client.post("/api/profile/validate-url", json={"url": "https://www.linkedin.com/in/neel-mani/"})
            self.assertEqual(res_exact.status_code, 200)
            data_exact = res_exact.get_json()
            self.assertTrue(data_exact.get("valid"))
            self.assertEqual(data_exact.get("name"), "Neel Mani")
            self.assertEqual(data_exact.get("picture"), "https://media.licdn.com/dms/image/v2/test/neel_mani_photo.jpg")

            # 3. Exact slug without trailing slash must also match
            res_exact_noslash = self.client.post("/api/profile/validate-url", json={"url": "https://www.linkedin.com/in/neel-mani"})
            self.assertEqual(res_exact_noslash.status_code, 200)
            data_exact_noslash = res_exact_noslash.get_json()
            self.assertEqual(data_exact_noslash.get("name"), "Neel Mani")
            self.assertEqual(data_exact_noslash.get("picture"), "https://media.licdn.com/dms/image/v2/test/neel_mani_photo.jpg")
        finally:
            if test_profiles.exists():
                test_profiles.unlink()

    def test_validate_profile_url_exact_slug_collision_prevention_connection(self):
        # 1. Partial slug 'pragyanand' must NOT match imported connection 'pragyanandsaho'
        res_partial = self.client.post("/api/profile/validate-url", json={"url": "https://www.linkedin.com/in/pragyanand"})
        self.assertEqual(res_partial.status_code, 200)
        data_partial = res_partial.get_json()
        self.assertTrue(data_partial.get("valid"))
        self.assertEqual(data_partial.get("name"), "Pragyanand")
        self.assertEqual(data_partial.get("source"), "real_linkedin")

        # 2. Exact slug 'pragyanandsaho' MUST match the imported connection
        res_exact = self.client.post("/api/profile/validate-url", json={"url": "https://www.linkedin.com/in/pragyanandsaho"})
        self.assertEqual(res_exact.status_code, 200)
        data_exact = res_exact.get_json()
        self.assertTrue(data_exact.get("valid"))
        self.assertEqual(data_exact.get("name"), "Pragyanand Sahoo")
        self.assertEqual(data_exact.get("source"), "imported")

    def test_selected_profile_session(self):
        with self.client.session_transaction() as sess:
            sess["selected_profile"] = {
                "name": "Jane Doe",
                "profile_url": "https://www.linkedin.com/in/janedoe",
                "headline": "Software Engineer"
            }
        res = self.client.get("/api/profile/selected")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("has_profile"))
        self.assertEqual(data.get("name"), "Jane Doe")

    def test_analyze_multi_profiles(self):
        payload = {
            "profiles": [
                {
                    "profile_url": "https://www.linkedin.com/in/payal-pal-a18b92339/",
                    "name": "Payal Pal",
                    "headline": "Student at Graphic Era",
                    "startDate": "2026-10-01",
                    "endDate": "2026-10-20"
                },
                {
                    "profile_url": "https://www.linkedin.com/in/pragyanandsaho/",
                    "name": "Pragyanand Sahoo",
                    "headline": "AI Researcher",
                    "startDate": "2026-10-05",
                    "endDate": "2026-10-24"
                }
            ],
            "activities": ["all"]
        }
        res = self.client.post("/api/analyze", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("is_multi_profile"))
        self.assertEqual(data.get("profiles_count"), 2)
        self.assertIn("profile_results", data)
        self.assertEqual(len(data["profile_results"]), 2)
        self.assertEqual(data["profile_results"][0]["subject_name"], "Payal Pal")
        self.assertEqual(data["profile_results"][1]["subject_name"], "Pragyanand Sahoo")
        self.assertEqual(data["date_range"]["start"], "2026-10-01")
        self.assertEqual(data["date_range"]["end"], "2026-10-24")

    def test_activity_post_author_fields(self):
        from models.activity import Activity
        act = Activity(
            id="act_test_01",
            type="post",
            date="2026-10-24",
            text="Real post content here",
            post_author="John Author",
            post_author_photo="https://example.com/photo.jpg",
            post_author_headline="VP of Engineering",
            post_url="https://www.linkedin.com/feed/update/urn:li:activity:123456789/"
        )
        d = act.to_dict()
        self.assertEqual(d["post_author_photo"], "https://example.com/photo.jpg")
        self.assertEqual(d["post_author_headline"], "VP of Engineering")
        self.assertEqual(d["post_url"], "https://www.linkedin.com/feed/update/urn:li:activity:123456789/")


if __name__ == "__main__":
    unittest.main()

