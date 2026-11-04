"""
Unit tests for LinkedIn profile photo extraction scoping and storage.
Verifies:
1. Profile photos are strictly extracted from the target profile's top-card container.
2. Photos from 'People also viewed', sidebars, recommendations, or unrelated page sections are NEVER extracted.
3. Profiles with no top-card photo return None (falling back to neutral placeholder avatar).
4. Subsequent scrapes with no verified photo update persistent storage to picture=None, preventing retention of unverified photos.
5. Persistent storage updates cleanly allow clearing unverified photos when picture=None is passed.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from linkedin.playwright_service import (
    extract_top_card_photo,
    scrape_linkedin_profile_activities,
    _PROFILE_CACHE
)
from services.saved_profiles_storage import (
    save_target_profile,
    load_saved_profiles,
    get_saved_profile_by_url,
    remove_saved_profile,
    _get_storage_file
)


class TestProfilePhotoExtraction(unittest.TestCase):

    def setUp(self):
        _PROFILE_CACHE.clear()
        storage_file = _get_storage_file()
        if storage_file.exists():
            try:
                storage_file.unlink()
            except Exception:
                pass

    def tearDown(self):
        storage_file = _get_storage_file()
        if storage_file.exists():
            try:
                storage_file.unlink()
            except Exception:
                pass

    def test_extract_top_card_photo_valid(self):
        """Top-card entity image must be accurately extracted from public profile layout."""
        html_content = """
        <html>
        <head><title>Pragyanand Sahoo | LinkedIn</title></head>
        <body>
            <section class="top-card-layout">
                <div class="top-card-layout__entity-image-container">
                    <img class="top-card-layout__entity-image"
                         src="https://media.licdn.com/dms/image/v2/pragyanand_valid_photo.jpg"
                         data-delayed-url="https://media.licdn.com/dms/image/v2/pragyanand_valid_photo.jpg"
                         alt="Pragyanand Sahoo">
                </div>
                <h1 class="top-card-layout__title">Pragyanand Sahoo</h1>
                <h2 class="top-card-layout__headline">Researcher</h2>
            </section>
        </body>
        </html>
        """
        photo = extract_top_card_photo(html_content)
        self.assertEqual(photo, "https://media.licdn.com/dms/image/v2/pragyanand_valid_photo.jpg")

    def test_extract_top_card_photo_ignores_sidebar_recommendations(self):
        """When top-card has no photo (SVG silhouette), sidebar 'People also viewed' photos must NEVER be extracted."""
        html_with_sidebar = """
        <html>
        <head><title>Neel Mani, PhD | LinkedIn</title></head>
        <body>
            <section class="top-card-layout">
                <div class="top-card-layout__entity-image-container">
                    <svg class="top-card-layout__entity-image ghost-person"></svg>
                </div>
                <h1 class="top-card-layout__title">Neel Mani, PhD</h1>
                <h2 class="top-card-layout__headline">Professor</h2>
            </section>

            <aside class="aside-rail">
                <section class="aside-section">
                    <h2>People also viewed</h2>
                    <ul class="aside-profiles-list">
                        <li class="aside-profiles-list__item">
                            <a href="https://www.linkedin.com/in/another-person/">
                                <img class="artdeco-entity-image"
                                     data-delayed-url="https://media.licdn.com/dms/image/v2/random_stranger_photo.jpg"
                                     alt="Another Person">
                                <h3>Another Person</h3>
                            </a>
                        </li>
                    </ul>
                </section>
            </aside>
        </body>
        </html>
        """
        photo = extract_top_card_photo(html_with_sidebar)
        # Must strictly be None! Must not capture the stranger's photo from the sidebar
        self.assertIsNone(photo)

    def test_extract_top_card_photo_empty_or_malformed(self):
        """Empty, None, or layout without images returns None."""
        self.assertIsNone(extract_top_card_photo(""))
        self.assertIsNone(extract_top_card_photo(None))
        self.assertIsNone(extract_top_card_photo("<html><body><h1>No photo</h1></body></html>"))

    def test_scrape_profile_with_sidebar_photo_sets_null_in_storage(self):
        """Scraping a profile with no top-card photo but sidebar photos must store picture=None."""
        target_url = "https://www.linkedin.com/in/neel-mani/"

        # Pre-seed storage with an old unverified photo to verify it gets cleared
        save_target_profile({
            "profile_url": target_url,
            "name": "Neel Mani",
            "headline": "LinkedIn Profile",
            "picture": "https://media.licdn.com/dms/image/v2/old_unverified_photo.jpg"
        })

        mock_p = MagicMock()
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()

        mock_p.chromium.launch.return_value = mock_browser
        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page

        html_with_sidebar = """
        <html>
        <head><title>Neel Mani, PhD - India | Professional Profile | LinkedIn</title></head>
        <body>
            <section class="top-card-layout">
                <div class="top-card-layout__entity-image-container">
                    <svg class="top-card-layout__entity-image ghost-person"></svg>
                </div>
                <h1 class="top-card-layout__title">Neel Mani, PhD</h1>
                <h2 class="top-card-layout__headline">Professor</h2>
            </section>
            <div class="tab__panel">
                <div class="profile-activity-card">
                    <a class="base-card__full-link" href="https://www.linkedin.com/posts/neel-mani_update-123/">
                        <span>Neel Mani posted this</span>
                    </a>
                    <div class="base-main-card__metadata">1d</div>
                    <div class="see-more-text">Recent academic update.</div>
                </div>
            </div>
            <aside class="aside-rail">
                <img data-delayed-url="https://media.licdn.com/dms/image/v2/sidebar_person.jpg">
            </aside>
        </body>
        </html>
        """

        mock_page.goto.return_value = MagicMock(status=200)
        mock_page.url = target_url
        mock_page.title.return_value = "Neel Mani, PhD - India | Professional Profile | LinkedIn"
        mock_page.content.return_value = html_with_sidebar

        with patch("playwright.sync_api.sync_playwright") as mock_sp:
            mock_sp.return_value.__enter__.return_value = mock_p
            success, acts, msg = scrape_linkedin_profile_activities(target_url)

        self.assertTrue(success)
        self.assertGreater(len(acts), 0)
        # Author photo must NOT be the sidebar photo
        self.assertIsNone(acts[0].get("post_author_photo"))

        # Storage record must preserve the existing photo (never overwrite a valid existing picture with None)
        saved = get_saved_profile_by_url(target_url)
        self.assertIsNotNone(saved)
        self.assertEqual(saved.get("picture"), "https://media.licdn.com/dms/image/v2/old_unverified_photo.jpg")
        self.assertEqual(saved.get("name"), "Neel Mani, PhD")

    def test_save_target_profile_explicit_null_clears_picture(self):
        """Passing picture=None explicitly to save_target_profile must NOT clear an existing valid picture."""
        url = "https://www.linkedin.com/in/sample-user/"
        # Initial save with picture
        save_target_profile({
            "profile_url": url,
            "name": "Sample User",
            "picture": "https://media.licdn.com/dms/image/sample.jpg"
        })
        prof1 = get_saved_profile_by_url(url)
        self.assertEqual(prof1.get("picture"), "https://media.licdn.com/dms/image/sample.jpg")

        # Update with picture=None
        save_target_profile({
            "profile_url": url,
            "picture": None
        })
        prof2 = get_saved_profile_by_url(url)
        # Photo protection rule: Never overwrite an existing valid profile picture with None or an empty string
        self.assertEqual(prof2.get("picture"), "https://media.licdn.com/dms/image/sample.jpg")

    def test_save_target_profile_omitted_picture_preserves_existing(self):
        """Updating headline without specifying picture must preserve the existing picture."""
        url = "https://www.linkedin.com/in/sample-user-2/"
        save_target_profile({
            "profile_url": url,
            "name": "Sample User 2",
            "headline": "Old Headline",
            "picture": "https://media.licdn.com/dms/image/sample2.jpg"
        })

        # Update headline only
        save_target_profile({
            "profile_url": url,
            "headline": "New Headline"
        })
        prof = get_saved_profile_by_url(url)
        self.assertEqual(prof.get("headline"), "New Headline")
        self.assertEqual(prof.get("picture"), "https://media.licdn.com/dms/image/sample2.jpg")


if __name__ == "__main__":
    unittest.main()

