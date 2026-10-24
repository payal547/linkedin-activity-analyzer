"""
Unit and Integration Tests for Saved Target Profiles Feature.
Verifies:
- Saving target profiles to persistent storage
- Loading saved profiles
- Deduplication of identical profile URLs
- Removing single profiles
- Preserving other profiles when one is removed
- API endpoints for saved profiles
- Automatic persistence during validate-url
- Verification that no passwords/credentials are stored
"""

import json
import unittest
from pathlib import Path
import sys

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app import app
from services.saved_profiles_storage import (
    save_target_profile,
    load_saved_profiles,
    remove_saved_profile,
    _get_storage_file
)


class TestSavedProfiles(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        # Clean test storage file
        test_file = _get_storage_file()
        if test_file.exists():
            test_file.unlink()

    def tearDown(self):
        test_file = _get_storage_file()
        if test_file.exists():
            test_file.unlink()

    def test_save_and_load_profile(self):
        profile = {
            "profile_url": "https://www.linkedin.com/in/payal-pal-a18b92339/",
            "name": "Payal Pal",
            "headline": "Student at Graphic Era",
            "picture": "https://media.licdn.com/test-pic.jpg"
        }
        saved = save_target_profile(profile)
        self.assertIsNotNone(saved)
        self.assertEqual(saved["name"], "Payal Pal")
        self.assertEqual(saved["id"], "payal-pal-a18b92339")
        self.assertIn("last_analyzed", saved)

        loaded = load_saved_profiles()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["name"], "Payal Pal")
        self.assertEqual(loaded[0]["picture"], "https://media.licdn.com/test-pic.jpg")

    def test_no_password_stored(self):
        profile = {
            "profile_url": "https://www.linkedin.com/in/test-user/",
            "name": "Test User",
            "password": "supersecretpassword123",
            "testing_account_password": "aniketpassword"
        }
        saved = save_target_profile(profile)
        self.assertNotIn("password", saved)
        self.assertNotIn("testing_account_password", saved)

        loaded = load_saved_profiles()
        for p in loaded:
            self.assertNotIn("password", p)
            self.assertNotIn("testing_account_password", p)

    def test_deduplication_for_same_url(self):
        url1 = "https://www.linkedin.com/in/payal-pal-a18b92339"
        url2 = "https://www.linkedin.com/in/payal-pal-a18b92339/"

        save_target_profile({"profile_url": url1, "name": "Payal Pal", "headline": "Initial Headline"})
        save_target_profile({"profile_url": url2, "name": "Payal Pal", "headline": "Updated Headline"})

        loaded = load_saved_profiles()
        # Must only be 1 profile, not 2
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["headline"], "Updated Headline")

    def test_multiple_distinct_profiles(self):
        save_target_profile({"profile_url": "https://www.linkedin.com/in/payal-pal-a18b92339/", "name": "Payal Pal"})
        save_target_profile({"profile_url": "https://www.linkedin.com/in/pragyanandsaho/", "name": "Pragyanand Sahoo"})
        save_target_profile({"profile_url": "https://www.linkedin.com/in/satyanadella/", "name": "Satya Nadella"})

        loaded = load_saved_profiles()
        self.assertEqual(len(loaded), 3)
        names = {p["name"] for p in loaded}
        self.assertIn("Payal Pal", names)
        self.assertIn("Pragyanand Sahoo", names)
        self.assertIn("Satya Nadella", names)

    def test_remove_single_profile(self):
        save_target_profile({"profile_url": "https://www.linkedin.com/in/payal-pal-a18b92339/", "name": "Payal Pal"})
        save_target_profile({"profile_url": "https://www.linkedin.com/in/pragyanandsaho/", "name": "Pragyanand Sahoo"})

        loaded = load_saved_profiles()
        self.assertEqual(len(loaded), 2)

        # Remove Payal Pal
        removed = remove_saved_profile("https://www.linkedin.com/in/payal-pal-a18b92339/")
        self.assertTrue(removed)

        # Pragyanand Sahoo must remain
        after_remove = load_saved_profiles()
        self.assertEqual(len(after_remove), 1)
        self.assertEqual(after_remove[0]["name"], "Pragyanand Sahoo")

    def test_api_endpoints_saved_profiles(self):
        # 1. GET /api/profiles/saved (initially empty)
        res = self.client.get("/api/profiles/saved")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["count"], 0)

        # 2. POST /api/profile/validate-url (automatically saves profile)
        res = self.client.post("/api/profile/validate-url", json={
            "url": "https://www.linkedin.com/in/payal-pal-a18b92339/"
        })
        self.assertEqual(res.status_code, 200)
        val_data = res.get_json()
        self.assertTrue(val_data["valid"])

        # 3. GET /api/profiles/saved (now contains Payal Pal)
        res = self.client.get("/api/profiles/saved")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["count"], 1)
        self.assertIn("Payal", data["profiles"][0]["name"])

        # 4. POST /api/profiles/saved (add second profile)
        res = self.client.post("/api/profiles/saved", json={
            "url": "https://www.linkedin.com/in/pragyanandsaho/",
            "name": "Pragyanand Sahoo",
            "headline": "Software Engineer"
        })
        self.assertEqual(res.status_code, 200)

        res = self.client.get("/api/profiles/saved")
        data = res.get_json()
        self.assertEqual(data["count"], 2)

        # 5. DELETE /api/profiles/saved
        res = self.client.delete("/api/profiles/saved", json={
            "url": "https://www.linkedin.com/in/payal-pal-a18b92339/"
        })
        self.assertEqual(res.status_code, 200)

        res = self.client.get("/api/profiles/saved")
        data = res.get_json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["profiles"][0]["name"], "Pragyanand Sahoo")


if __name__ == "__main__":
    unittest.main()

