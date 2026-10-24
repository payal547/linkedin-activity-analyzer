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

    def test_analyze_unauthenticated(self):
        response = self.client.post("/api/analyze", json={"subject_name": "Test", "data_source": "imported"})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data.get("subject_name"), "Test")

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


if __name__ == "__main__":
    unittest.main()

