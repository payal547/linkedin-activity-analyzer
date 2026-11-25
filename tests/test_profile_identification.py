"""
Tests for automatic profile identification from a pasted LinkedIn link:
link classification, identity parsing, live lookup (mocked browser) and the
/api/profile/validate-url integration.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app import app
from linkedin import playwright_service
from linkedin.link_resolver import classify_linkedin_link
from linkedin.playwright_service import identify_linkedin_profile, parse_profile_identity


class FakePage:
    """Minimal Playwright page: url follows goto(), redirects are configurable."""

    def __init__(self, pages, redirects=None, author_href=None):
        self.pages = pages              # url -> (status, title, html, dom)
        self.redirects = redirects or {}
        self.author_href = author_href
        self.url = "about:blank"
        self.visited = []

    def goto(self, url, **kwargs):
        self.visited.append(url)
        self.url = self.redirects.get(url, url)
        status = self.pages.get(self.url, (200, "", "", {}))[0]
        return MagicMock(status=status)

    def title(self):
        return self.pages.get(self.url, (200, "", "", {}))[1]

    def content(self):
        return self.pages.get(self.url, (200, "", "", {}))[2]

    def evaluate(self, script):
        if "update-components-actor" in script:
            return self.author_href
        return self.pages.get(self.url, (200, "", "", {}))[3]

    def wait_for_selector(self, *args, **kwargs):
        return None

    def close(self):
        pass


def fake_playwright(page):
    p = MagicMock()
    p.chromium.launch.return_value.new_context.return_value.new_page.return_value = page
    cm = MagicMock()
    cm.__enter__.return_value = p
    cm.__exit__.return_value = False
    return MagicMock(return_value=cm)


SATYA_URL = "https://www.linkedin.com/in/satyanadella/"
SATYA_PAGE = (200, "(3) Satya Nadella | LinkedIn", "<html><body><main><h1>Satya Nadella</h1></main></body></html>",
              {"name": "Satya Nadella", "headline": "Chairman and CEO at Microsoft",
               "photo": "https://media.licdn.com/dms/image/profile-displayphoto-shrink_200_200/satya.jpg"})


class TestLinkClassification(unittest.TestCase):
    def test_profile_variants_resolve_to_slug(self):
        cases = {
            "https://www.linkedin.com/in/satyanadella/": ("profile", "satyanadella"),
            "linkedin.com/in/Satya-Nadella?utm_source=share": ("profile", "satya-nadella"),
            "Check this out: https://in.linkedin.com/in/payal-pal-a18b92339/ thanks": ("profile", "payal-pal-a18b92339"),
            "https://www.linkedin.com/mwlite/in/satyanadella": ("mobile_profile", "satyanadella"),
            "https://www.linkedin.com/in/satyanadella/details/experience/": ("profile", "satyanadella"),
            "satyanadella": ("username", "satyanadella"),
            "https://www.linkedin.com/posts/satyanadella_ai-activity-7234567890123456789-AbCd": ("post", "satyanadella"),
        }
        for text, (kind, slug) in cases.items():
            r = classify_linkedin_link(text)
            self.assertTrue(r["valid"], text)
            self.assertEqual((r["link_type"], r["slug"]), (kind, slug), text)
            self.assertFalse(r["needs_lookup"], text)

    def test_links_without_author_need_lookup(self):
        for text, kind in {
            "https://www.linkedin.com/feed/update/urn:li:activity:7234567890123456789/": "activity",
            "https://lnkd.in/gAbC123": "short_link",
            "https://www.linkedin.com/pulse/future-of-work-someone": "article",
        }.items():
            r = classify_linkedin_link(text)
            self.assertTrue(r["valid"], text)
            self.assertEqual(r["link_type"], kind)
            self.assertTrue(r["needs_lookup"])
            self.assertIsNone(r["slug"])

    def test_non_person_links_are_rejected_with_a_reason(self):
        for text, word in {
            "https://www.linkedin.com/company/microsoft/": "company",
            "https://www.linkedin.com/school/iit-delhi/": "school",
            "https://www.linkedin.com/jobs/view/123": "job",
            "https://www.linkedin.com/in/": "Incomplete",
            "https://www.linkedin.com/feed/": "home feed",
            "https://google.com/abc": "Invalid URL",
            "hello world": "Invalid URL",
            "https://not-linkedin.com/user/test": "Invalid URL",
            "https://linkedin.com.evil.com/in/satyanadella": "Invalid URL",
            "https://evil.com/linkedin.com/in/satyanadella": "Invalid URL",
        }.items():
            r = classify_linkedin_link(text)
            self.assertFalse(r["valid"], text)
            self.assertIn(word, r["error"], text)


class TestIdentityParsing(unittest.TestCase):
    def test_guest_profile_html(self):
        html = (
            '<html><head><meta property="og:title" content="Satya Nadella - Microsoft | LinkedIn">'
            '<meta property="og:image" content="https://media.licdn.com/dms/image/profile-displayphoto-shrink_800/x.jpg">'
            '<meta property="og:description" content="Chairman and CEO at Microsoft · Experience: Microsoft · Location: Redmond"></head>'
            '<body></body></html>'
        )
        ident = parse_profile_identity(html, "Satya Nadella - Microsoft | LinkedIn", SATYA_URL)
        self.assertEqual(ident["name"], "Satya Nadella")
        self.assertEqual(ident["headline"], "Chairman and CEO at Microsoft")
        self.assertIn("profile-displayphoto", ident["picture"])
        self.assertEqual(ident["slug"], "satyanadella")

    def test_logged_in_title_and_dom_values(self):
        ident = parse_profile_identity("<html></html>", "(12) Payal Pal | LinkedIn",
                                       "https://www.linkedin.com/in/payal-pal-a18b92339/",
                                       {"headline": "Student at Graphic Era"})
        self.assertEqual(ident["name"], "Payal Pal")
        self.assertEqual(ident["headline"], "Student at Graphic Era")
        self.assertIsNone(ident["picture"])

    def test_default_og_image_is_not_used_as_photo(self):
        html = '<meta property="og:image" content="https://static.licdn.com/aero-v1/sc/h/default-share.png">'
        self.assertIsNone(parse_profile_identity(html, "Someone | LinkedIn", SATYA_URL)["picture"])


class TestIdentifyLinkedInProfile(unittest.TestCase):
    def setUp(self):
        playwright_service._IDENTITY_CACHE.clear()
        playwright_service.clear_rate_limit_cooldown()

    def test_identifies_profile_by_slug(self):
        page = FakePage({SATYA_URL: SATYA_PAGE})
        with patch("playwright.sync_api.sync_playwright", fake_playwright(page)):
            res = identify_linkedin_profile(slug="satyanadella")
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "IDENTIFIED")
        self.assertEqual(res["name"], "Satya Nadella")
        self.assertEqual(res["headline"], "Chairman and CEO at Microsoft")
        self.assertEqual(res["profile_url"], SATYA_URL)

    def test_result_is_cached(self):
        page = FakePage({SATYA_URL: SATYA_PAGE})
        with patch("playwright.sync_api.sync_playwright", fake_playwright(page)):
            identify_linkedin_profile(slug="satyanadella")
            identify_linkedin_profile(slug="satyanadella")
        self.assertEqual(page.visited, [SATYA_URL])

    def test_feed_activity_link_finds_author_then_profile(self):
        post_url = "https://www.linkedin.com/feed/update/urn:li:activity:7234567890123456789/"
        page = FakePage({post_url: (200, "Post | LinkedIn", "", {}), SATYA_URL: SATYA_PAGE},
                        author_href="https://www.linkedin.com/in/satyanadella?miniProfileUrn=abc")
        with patch("playwright.sync_api.sync_playwright", fake_playwright(page)):
            res = identify_linkedin_profile(lookup_url=post_url)
        self.assertTrue(res["ok"])
        self.assertEqual(res["slug"], "satyanadella")
        self.assertEqual(page.visited, [post_url, SATYA_URL])

    def test_short_link_redirecting_to_profile(self):
        short = "https://lnkd.in/gAbC123"
        page = FakePage({SATYA_URL: SATYA_PAGE}, redirects={short: SATYA_URL})
        with patch("playwright.sync_api.sync_playwright", fake_playwright(page)):
            res = identify_linkedin_profile(lookup_url=short)
        self.assertTrue(res["ok"])
        self.assertEqual(res["name"], "Satya Nadella")

    def test_author_not_found(self):
        post_url = "https://www.linkedin.com/feed/update/urn:li:activity:1/"
        page = FakePage({post_url: (200, "Post | LinkedIn", "", {})}, author_href=None)
        with patch("playwright.sync_api.sync_playwright", fake_playwright(page)):
            res = identify_linkedin_profile(lookup_url=post_url)
        self.assertFalse(res["ok"])
        self.assertEqual(res["status"], "AUTHOR_NOT_FOUND")

    def test_profile_not_found(self):
        url = "https://www.linkedin.com/in/no-such-person-xyz/"
        page = FakePage({url: (404, "Page not found | LinkedIn", "", {})})
        with patch("playwright.sync_api.sync_playwright", fake_playwright(page)):
            res = identify_linkedin_profile(slug="no-such-person-xyz")
        self.assertEqual(res["status"], "PROFILE_NOT_FOUND")

    def test_http_999_sets_cooldown(self):
        page = FakePage({SATYA_URL: (999, "", "", {})})
        with patch("playwright.sync_api.sync_playwright", fake_playwright(page)):
            res = identify_linkedin_profile(slug="satyanadella")
        self.assertEqual(res["status"], "RATE_LIMITED_999")
        self.assertTrue(playwright_service.is_rate_limited("satyanadella"))

    def test_authwall_is_reported(self):
        page = FakePage({}, redirects={SATYA_URL: "https://www.linkedin.com/authwall?trk=x"})
        with patch("playwright.sync_api.sync_playwright", fake_playwright(page)):
            res = identify_linkedin_profile(slug="satyanadella")
        self.assertEqual(res["status"], "BLOCKED")

    def test_no_session_means_no_browser(self):
        launcher = MagicMock()
        with patch("linkedin.playwright_service.validate_session_state", return_value=("MISSING", False, "none")), \
             patch("playwright.sync_api.sync_playwright", launcher):
            res = identify_linkedin_profile(slug="satyanadella")
        self.assertEqual(res["status"], "AUTH_REQUIRED")
        launcher.assert_not_called()


class TestValidateUrlIdentification(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        playwright_service._IDENTITY_CACHE.clear()
        from services.saved_profiles_storage import remove_saved_profile
        for slug in ("ident-test-person", "ident-test-author"):
            remove_saved_profile(f"https://www.linkedin.com/in/{slug}/")

    def _identified(self, slug, name="Ident Test Person"):
        return {"ok": True, "status": "IDENTIFIED", "message": f"Identified {name} on LinkedIn.", "slug": slug,
                "profile_url": f"https://www.linkedin.com/in/{slug}/", "name": name,
                "headline": "Engineer at Example", "picture": "https://media.licdn.com/dms/image/profile-displayphoto/p.jpg"}

    def test_new_profile_is_identified_live(self):
        with patch("linkedin.playwright_service.identify_linkedin_profile",
                   return_value=self._identified("ident-test-person")) as ident:
            res = self.client.post("/api/profile/validate-url", json={"url": "https://www.linkedin.com/in/ident-test-person"})
        data = res.get_json()
        self.assertEqual(res.status_code, 200)
        ident.assert_called_once_with(slug="ident-test-person")
        self.assertTrue(data["identified"])
        self.assertEqual(data["identity_source"], "linkedin")
        self.assertEqual(data["name"], "Ident Test Person")
        self.assertEqual(data["headline"], "Engineer at Example")
        self.assertTrue(data["picture"].startswith("https://"))
        self.assertEqual(data["saved_profile"]["name"], "Ident Test Person")

    def test_falls_back_to_url_name_without_session(self):
        failed = {"ok": False, "status": "AUTH_REQUIRED", "message": "Open Chrome with LinkedIn logged in.", "slug": "ident-test-person"}
        with patch("linkedin.playwright_service.identify_linkedin_profile", return_value=failed):
            res = self.client.post("/api/profile/validate-url", json={"url": "https://www.linkedin.com/in/ident-test-person"})
        data = res.get_json()
        self.assertEqual(res.status_code, 200)
        self.assertFalse(data["identified"])
        self.assertEqual(data["identity_status"], "AUTH_REQUIRED")
        self.assertEqual(data["name"], "Ident Test Person")

    def test_post_link_is_resolved_to_its_author(self):
        with patch("linkedin.playwright_service.identify_linkedin_profile",
                   return_value=self._identified("ident-test-author", "Ident Author")):
            res = self.client.post("/api/profile/validate-url", json={
                "url": "https://www.linkedin.com/posts/ident-test-author_launch-activity-7234567890123456789-xYz"})
        data = res.get_json()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(data["link_type"], "post")
        self.assertEqual(data["profile_url"], "https://www.linkedin.com/in/ident-test-author/")
        self.assertEqual(data["name"], "Ident Author")

    def test_activity_link_whose_author_cannot_be_found_is_rejected(self):
        failed = {"ok": False, "status": "AUTHOR_NOT_FOUND", "message": "Couldn't find whose post this is.", "slug": None}
        with patch("linkedin.playwright_service.identify_linkedin_profile", return_value=failed):
            res = self.client.post("/api/profile/validate-url", json={
                "url": "https://www.linkedin.com/feed/update/urn:li:activity:7234567890123456789/"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("Post / activity link recognised", res.get_json()["error"])

    def test_company_link_is_rejected(self):
        res = self.client.post("/api/profile/validate-url", json={"url": "https://www.linkedin.com/company/microsoft/"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("company page", res.get_json()["error"])

    def test_known_profile_skips_live_lookup(self):
        from services.saved_profiles_storage import save_target_profile
        save_target_profile({"profile_url": "https://www.linkedin.com/in/ident-test-person/", "name": "Known Person",
                             "headline": "Designer", "picture": "https://media.licdn.com/dms/image/profile-displayphoto/k.jpg"})
        with patch("linkedin.playwright_service.identify_linkedin_profile") as ident:
            res = self.client.post("/api/profile/validate-url", json={"url": "https://www.linkedin.com/in/ident-test-person"})
        ident.assert_not_called()
        self.assertEqual(res.get_json()["name"], "Known Person")
        self.assertEqual(res.get_json()["identity_status"], "KNOWN")


if __name__ == "__main__":
    unittest.main()
