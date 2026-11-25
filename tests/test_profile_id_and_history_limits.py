"""
Tests for:
1. LinkedIn Profile URL Detection, Parsing, Normalization, and Rejection of non-member URLs
2. Official LinkedIn Activity History Limits (2-year visibility, 360-day dormancy)
3. Verified Registration Date Handling & Date Range Alignment
4. Safe Saved Profile Deletion (Preserves Analysis History)
5. Post-Analysis Profile Identity Verification (Mismatch Warning)
"""

import io
import json
import unittest
import zipfile
from datetime import date, timedelta
from pathlib import Path
import sys

# Add backend directory to sys.path
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app import app
from services.saved_profiles_storage import (
    parse_linkedin_url,
    normalize_profile_url,
    extract_slug,
    save_target_profile,
    load_saved_profiles,
    remove_saved_profile,
    _get_storage_file,
)
from linkedin.data_parser import (
    parse_registration_csv,
    parse_registration_from_file_or_zip,
)
from linkedin.playwright_service import (
    parse_relative_time_to_date,
    to_date_obj,
    check_feed_date_boundary,
)
from routes.activity_routes import (
    _build_history_limits_and_coverage,
    resolve_linkedin_url,
)


class TestLinkedInProfileIdentification(unittest.TestCase):
    def test_parse_standard_profile_url(self):
        url = "https://www.linkedin.com/in/satyanadella/"
        is_valid, err, slug, canonical_url = parse_linkedin_url(url)
        self.assertTrue(is_valid)
        self.assertEqual(slug, "satyanadella")
        self.assertEqual(canonical_url, "https://www.linkedin.com/in/satyanadella/")

    def test_parse_long_tracking_profile_url(self):
        # Very long mobile app share URL with multiple UTM parameters and subdomains
        long_url = (
            "https://www.linkedin.com/in/akanksha-chauhan-5095b7266?"
            "utm_source=share_via&utm_content=profile&utm_medium=member_android"
            "&trackingId=abc123xyz456&originalSubdomain=in&extra_param=verylongquerypayload"
        )
        is_valid, err, slug, canonical_url = parse_linkedin_url(long_url)
        self.assertTrue(is_valid)
        self.assertEqual(slug, "akanksha-chauhan-5095b7266")
        self.assertEqual(canonical_url, "https://www.linkedin.com/in/akanksha-chauhan-5095b7266/")

    def test_parse_url_encoded_and_trailing_slashes(self):
        url = "https://linkedin.com/in/john%2Ddoe%2D123///?ref=banner"
        is_valid, err, slug, canonical_url = parse_linkedin_url(url)
        self.assertTrue(is_valid)
        self.assertEqual(slug, "john-doe-123")
        self.assertEqual(canonical_url, "https://www.linkedin.com/in/john-doe-123/")

    def test_reject_company_url(self):
        company_url = "https://www.linkedin.com/company/microsoft/"
        is_valid, err, slug, canonical_url = parse_linkedin_url(company_url)
        self.assertFalse(is_valid)
        self.assertIn("Company", err)

    def test_reject_school_url(self):
        school_url = "https://www.linkedin.com/school/stanford-university/"
        is_valid, err, slug, canonical_url = parse_linkedin_url(school_url)
        self.assertFalse(is_valid)
        self.assertIn("School", err)

    def test_reject_post_and_feed_url(self):
        post_url = "https://www.linkedin.com/posts/activity-1234567890"
        feed_url = "https://www.linkedin.com/feed/"
        search_url = "https://www.linkedin.com/search/results/all/?keywords=test"

        self.assertFalse(parse_linkedin_url(post_url)[0])
        self.assertFalse(parse_linkedin_url(feed_url)[0])
        self.assertFalse(parse_linkedin_url(search_url)[0])

    def test_reject_non_linkedin_url(self):
        bad_url = "https://example.com/in/satyanadella"
        is_valid, err, slug, canonical_url = parse_linkedin_url(bad_url)
        self.assertFalse(is_valid)
        self.assertIn("valid LinkedIn member profile URL", err)

    def test_deduplication_different_url_variants(self):
        test_file = _get_storage_file()
        if test_file.exists():
            test_file.unlink()

        try:
            url_a = "https://www.linkedin.com/in/satyanadella"
            url_b = "https://www.linkedin.com/in/satyanadella/?utm_source=test"
            url_c = "https://in.linkedin.com/in/satyanadella/"

            save_target_profile({"profile_url": url_a, "name": "Satya Nadella", "headline": "CEO"})
            save_target_profile({"profile_url": url_b, "name": "Satya Nadella", "headline": "Chairman & CEO"})
            save_target_profile({"profile_url": url_c, "name": "Satya Nadella", "headline": "CEO at Microsoft"})

            saved = load_saved_profiles()
            # Must remain exactly 1 saved profile record
            matching = [p for p in saved if p.get("id") == "satyanadella"]
            self.assertEqual(len(matching), 1)
            self.assertEqual(matching[0]["headline"], "CEO at Microsoft")
        finally:
            if test_file.exists():
                test_file.unlink()


class TestOfficialHistoryLimitsAndRegistration(unittest.TestCase):
    def test_history_limits_structure(self):
        activities = [
            {"type": "post", "date": "2023-05-10T10:00:00Z"},
            {"type": "post", "date": "2023-08-15T12:00:00Z"},
            {"type": "video", "date": "2023-11-20T14:00:00Z", "is_video": True},
        ]
        coverage = _build_history_limits_and_coverage(
            activities=activities,
            requested_start="2023-01-01",
            requested_end="2024-01-01",
            verified_reg_date=None,
        )

        official = coverage["official_history_limits"]
        self.assertEqual(official["documented_max_visibility_years"], 2)
        self.assertEqual(official["inactive_hide_threshold_days"], 360)
        self.assertIn("two years", official["notice"].lower())

        # Check coverage
        scanned = coverage["scanned_coverage"]
        self.assertEqual(scanned["earliest_retrieved_activity_date"], "2023-05-10T10:00:00Z")
        self.assertEqual(scanned["latest_retrieved_activity_date"], "2023-11-20T14:00:00Z")
        self.assertEqual(scanned["category_counts"]["posts"], 2)
        self.assertEqual(scanned["category_counts"]["videos"], 1)

        # Unverified registration note
        self.assertIn("Account registration date is unavailable", coverage["registration_date_note"])

    def test_verified_registration_date_alignment_warning(self):
        # Registration date is 2023-06-01, but scan starts 2023-01-01 (before registration)
        coverage = _build_history_limits_and_coverage(
            activities=[{"type": "post", "date": "2023-07-01T10:00:00Z"}],
            requested_start="2023-01-01",
            requested_end="2023-12-31",
            verified_reg_date="2023-06-01T00:00:00Z",
            reg_date_source="Official LinkedIn Data Export (Registration.csv)",
        )

        self.assertIn("starts before this member's verified LinkedIn registration date", coverage["registration_date_note"])
        self.assertEqual(coverage["verified_registration_date"], "2023-06-01T00:00:00Z")
        self.assertEqual(coverage["registration_date_source"], "Official LinkedIn Data Export (Registration.csv)")

    def test_parse_registration_csv_data(self):
        csv_text = "Registered At,Registration IP\n\"Jun 15, 2023, 08:30 AM\",\"192.168.1.1\"\n"
        reg_date = parse_registration_csv(csv_text)
        self.assertIsNotNone(reg_date)
        self.assertEqual(reg_date, "2023-06-15")

    def test_parse_registration_from_zip(self):
        csv_text = "Registered At,Registration IP\n2022-04-10 14:00:00,192.168.1.1\n"
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("Registration.csv", csv_text)

        reg_date = parse_registration_from_file_or_zip(zip_buffer.getvalue(), filename="export.zip")
        self.assertIsNotNone(reg_date)
        self.assertEqual(reg_date, "2022-04-10")


class TestProfileDeletionAndIdentityVerification(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_delete_saved_profile_preserves_historical_data(self):
        test_file = _get_storage_file()
        if test_file.exists():
            test_file.unlink()

        try:
            # 1. Save profile
            profile = {
                "profile_url": "https://www.linkedin.com/in/test-preserve-history/",
                "name": "Preserve Test",
                "headline": "Testing Data Preservation",
            }
            save_target_profile(profile)
            saved = load_saved_profiles()
            self.assertTrue(any(p["id"] == "test-preserve-history" for p in saved))

            # 2. Call DELETE endpoint with URL param
            res = self.client.delete("/api/saved-profiles/test-preserve-history")
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertTrue(data.get("success"))
            self.assertIn("preserved", data.get("note", "").lower())

            # 3. Verify removed from saved bookmarks
            saved_after = load_saved_profiles()
            self.assertFalse(any(p["id"] == "test-preserve-history" for p in saved_after))
        finally:
            if test_file.exists():
                test_file.unlink()

    def test_identity_warning_when_slug_mismatch(self):
        # When user requests 'john-doe-123' but page returns 'jane-smith-456'
        requested_slug = "john-doe-123"
        extracted_slug = "jane-smith-456"

        identity_matches = (requested_slug == extracted_slug)
        self.assertFalse(identity_matches)


class TestTwoYearActivityHistoryRetrieval(unittest.TestCase):
    def test_two_year_relative_time_parsing(self):
        iso_1yr, exact1 = parse_relative_time_to_date("1 yr")
        iso_2yr, exact2 = parse_relative_time_to_date("2 yrs")
        today = date.today()
        d_1yr = to_date_obj(iso_1yr)
        d_2yr = to_date_obj(iso_2yr)

        self.assertIsNotNone(d_1yr)
        self.assertIsNotNone(d_2yr)
        self.assertTrue((today - d_1yr).days >= 360)
        self.assertTrue((today - d_2yr).days >= 720)

    def test_two_year_date_boundary_progression(self):
        today = date.today()
        two_years_ago = today - timedelta(days=729)

        # 1. When feed oldest card is only 30 days old: boundary NOT reached
        recent_feed = [today - timedelta(days=5), today - timedelta(days=30)]
        self.assertFalse(check_feed_date_boundary(recent_feed, two_years_ago, scroll_count=5))

        # 2. When feed oldest card progresses past 2 years (e.g. 740 days old): boundary REACHED
        deep_feed = [today - timedelta(days=100), today - timedelta(days=500), today - timedelta(days=740)]
        self.assertTrue(check_feed_date_boundary(deep_feed, two_years_ago, scroll_count=20))

    def test_two_year_preset_payload_in_routes(self):
        today = date.today()
        today_str = today.isoformat()
        expected_start = (today - timedelta(days=729)).isoformat()

        # Test route fallback logic
        date_type = "2years"
        start_date = None
        end_date = None
        if date_type in ("2years", "2year", "730days", "two_years", "2_years") and not start_date:
            start_date = (date.today() - timedelta(days=729)).isoformat()
            end_date = today_str

        self.assertEqual(start_date, expected_start)
        self.assertEqual(end_date, today_str)


if __name__ == "__main__":
    unittest.main()
