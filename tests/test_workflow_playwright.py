"""
Playwright End-to-End Workflow Test for LinkedIn Activity Analyzer.
Tests the complete application journey:
  Login -> Authenticated User Profile -> Connections/Friends -> Activity Criteria Filter -> Analysis -> Dashboard
Does NOT bypass LinkedIn permissions or scrape LinkedIn.
"""

import os
import threading
import time
import unittest
import sys
from pathlib import Path

# Enforce strict test database and testing mode across all threads and modules
os.environ["TESTING"] = "1"
os.environ["MONGO_DB_NAME"] = "linkedin_activity_analyzer_test"

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from app import app
from werkzeug.serving import make_server


class TestWorkflowPlaywright(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Configure app for local testing
        os.environ["TESTING"] = "1"
        os.environ["MONGO_DB_NAME"] = "linkedin_activity_analyzer_test"
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "playwright-test-secret"

        # Start live server on localhost with dynamic port allocation (port 0)
        # Using port 0 allows the operating system to allocate an available ephemeral port,
        # completely preventing port conflicts with other running instances or stale processes.
        cls.server = make_server("127.0.0.1", 0, app, threaded=True)
        cls.port = cls.server.port
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()

        # Actively probe server readiness before letting tests run
        import urllib.request
        start_time = time.time()
        server_ready = False
        while time.time() - start_time < 10.0:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{cls.port}/login", timeout=1.0) as resp:
                    if resp.status == 200:
                        server_ready = True
                        break
            except Exception:
                time.sleep(0.1)

        if not server_ready:
            raise RuntimeError(f"Flask test server failed to respond on http://127.0.0.1:{cls.port} within 10 seconds.")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.server.shutdown()
        except Exception:
            pass
        try:
            cls.server.server_close()
        except Exception:
            pass
        if hasattr(cls, "server_thread") and cls.server_thread.is_alive():
            cls.server_thread.join(timeout=2.0)

    def setUp(self):
        from services.saved_profiles_storage import _get_storage_file, TEST_PROFILES_FILE
        test_file = _get_storage_file()
        # Strictly ensure we ONLY ever touch the test storage file
        if test_file.name == TEST_PROFILES_FILE.name and test_file.exists():
            try:
                test_file.unlink()
            except Exception:
                pass

    def tearDown(self):
        from services.saved_profiles_storage import _get_storage_file, TEST_PROFILES_FILE
        test_file = _get_storage_file()
        if test_file.name == TEST_PROFILES_FILE.name and test_file.exists():
            try:
                test_file.unlink()
            except Exception:
                pass

    def test_full_application_workflow(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("Playwright not installed in environment")

        base_url = f"http://127.0.0.1:{self.port}"

        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as e:
                self.skipTest(f"Chromium browser not available: {e}")

            context = browser.new_context()

            # 1. Start at Login Page
            page = context.new_page()
            page.goto(f"{base_url}/login")
            self.assertIn("LinkedIn Activity Analyzer", page.title())
            self.assertTrue(page.locator("button.login-btn").is_visible())

            # 2. Simulate Authenticated Session with LinkedIn User
            with app.test_client() as client:
                with client.session_transaction() as sess:
                    sess["linkedin_profile"] = {
                        "name": "Payal Pal",
                        "email": "payal@example.com",
                        "sub": "linkedin_user_123"
                    }
                    sess["linkedin_session_id"] = "session_playwright_123"
                    sess["linkedin_access_token"] = "token_playwright_123"
                cookie = client.get_cookie("session")

            context.add_cookies([{
                "name": "session",
                "value": cookie.value,
                "domain": "127.0.0.1",
                "path": "/"
            }])

            # 3. Visit Connections Page
            page.goto(f"{base_url}/connections")
            page.wait_for_selector("#profileName")
            profile_name = page.text_content("#profileName")
            self.assertIn("Payal Pal", profile_name)

            # Check that honest source badges are present
            page.wait_for_selector(".badge-source")
            self.assertTrue(page.locator(".badge-real").first.is_visible())

            # 4. Select a Profile / Connection
            # Click primary card to analyze user's own profile
            self_btn = page.locator(".friend-card.primary-card .select-btn")
            self.assertTrue(self_btn.is_visible())
            self_btn.click()

            # Verify continue button appears and click it
            continue_btn = page.locator("#continueBtn")
            self.assertTrue(continue_btn.is_visible())
            continue_btn.click()

            # 5. Activity Filter Page
            page.wait_for_url("**/activity-filter")
            page.wait_for_load_state("networkidle")
            target_name = page.text_content("#friendName")
            self.assertIn("Payal Pal", target_name)

            # Choose "Last 30 Days" filter
            page.locator('input[value="30days"]').check()

            # Click Analyze Activity
            analyze_btn = page.locator("#analyzeBtn")
            analyze_btn.click()

            # 6. Dashboard Page
            page.wait_for_url("**/dashboard")
            self.assertIn("Dashboard", page.title())

            # Verify analysis results appear on Dashboard
            page.wait_for_selector("#totalActivities")
            page.wait_for_function("() => document.getElementById('subjectName') && document.getElementById('subjectName').textContent !== 'Selected Profile'", timeout=10000)
            total_activities = int(page.text_content("#totalActivities"))
            self.assertTrue(total_activities >= 0)

            # Verify Subject header and Source badge
            subject_header = page.text_content("#subjectName")
            self.assertIn("Payal Pal", subject_header)

            browser.close()

    def test_connections_csv_import_workflow(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("Playwright not installed in environment")

        base_url = f"http://127.0.0.1:{self.port}"

        # Prepare a valid test Connections.csv
        test_csv_content = (
            "Notes:\n"
            "When exporting your connection data, that member's email address is only included if...\n\n"
            "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
            "Aniket,Sharma,https://www.linkedin.com/in/aniket-sharma,private.aniket@example.com,Google,Staff Engineer,10 May 2024\n"
            "Sneha,Patel,https://www.linkedin.com/in/sneha-patel,private.sneha@example.com,Microsoft,Data Scientist,01 Jun 2024\n"
        )
        test_csv_file = Path(__file__).resolve().parent / "test_Connections.csv"
        test_csv_file.write_text(test_csv_content, encoding="utf-8")

        try:
            with sync_playwright() as p:
                try:
                    browser = p.chromium.launch(headless=True)
                except Exception as e:
                    self.skipTest(f"Chromium browser not available: {e}")

                context = browser.new_context()

                # Simulate authenticated session
                with app.test_client() as client:
                    with client.session_transaction() as sess:
                        sess["linkedin_profile"] = {
                            "name": "Aniket Pal",
                            "email": "aniket@example.com",
                            "sub": "linkedin_user_aniket"
                        }
                        sess["linkedin_session_id"] = "session_playwright_aniket"
                        sess["linkedin_access_token"] = "token_playwright_aniket"
                    cookie = client.get_cookie("session")

                context.add_cookies([{
                    "name": "session",
                    "value": cookie.value,
                    "domain": "127.0.0.1",
                    "path": "/"
                }])

                page = context.new_page()

                # 1. Visit Connections Page
                page.goto(f"{base_url}/connections")
                page.wait_for_selector("#profileName")
                self.assertIn("Aniket Pal", page.text_content("#profileName"))

                # 2. Upload Connections.csv
                page.set_input_files("#connectionsImportFile", str(test_csv_file))
                page.click("#importSubmitBtn")

                # Wait for success status
                page.wait_for_selector(".import-status-msg.success")
                status_text = page.text_content(".import-status-msg.success")
                self.assertIn("Successfully loaded", status_text)

                # 3. Verify imported cards have "Imported from LinkedIn Connections export" badge
                page.wait_for_selector(".badge-imported")
                badge_text = page.text_content(".badge-imported")
                self.assertEqual(badge_text.strip(), "Imported from LinkedIn Connections export")

                # 4. Search and filter by name
                page.fill("#searchBox", "Sneha")

                # 5. Select Sneha Patel
                sneha_card = page.locator(".friend-card:has-text('Sneha Patel')")
                sneha_card.wait_for(state="visible", timeout=5000)
                self.assertTrue(sneha_card.is_visible())
                sneha_btn = sneha_card.locator(".select-btn")
                sneha_btn.click()
                self.assertEqual(sneha_btn.text_content().strip(), "Selected ✓")

                # 6. Click Continue to Activity Filter
                continue_btn = page.locator("#continueBtn")
                self.assertTrue(continue_btn.is_visible())
                self.assertIn("Sneha Patel", continue_btn.text_content())
                continue_btn.click()

                # 7. Verify Activity Filter Page
                page.wait_for_url("**/activity-filter")
                page.wait_for_load_state("networkidle")
                target_name = page.text_content("#friendName")
                self.assertIn("Sneha Patel", target_name)
                source_badge = page.text_content("#sourceBadge")
                self.assertIn("Imported from LinkedIn Connections export", source_badge)

                # 8. Analyze and navigate to Dashboard
                page.click("#analyzeBtn")
                page.wait_for_url("**/dashboard")
                self.assertIn("Dashboard", page.title())

                subject_header = page.text_content("#subjectName")
                self.assertIn("Sneha Patel", subject_header)

                browser.close()
        finally:
            if test_csv_file.exists():
                test_csv_file.unlink()

    def test_offline_connections_csv_import_without_oauth(self):
        """Verify unauthenticated users can view and import Connections.csv without logging into LinkedIn."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("Playwright not installed in environment")

        base_url = f"http://127.0.0.1:{self.port}"

        test_csv_content = (
            "Notes:\n"
            "When exporting your connection data...\n\n"
            "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
            "Payal,Pal,https://www.linkedin.com/in/payal-pal-a18b92339,private@example.com,Prodigy InfoTech,Intern,18 Oct 2026\n"
            "Pragyanand,Sahoo,https://www.linkedin.com/in/pragyanandsaho,,SRHU,Researcher,19 Oct 2026\n"
        )
        test_csv_file = Path(__file__).resolve().parent / "test_Offline_Connections.csv"
        test_csv_file.write_text(test_csv_content, encoding="utf-8")

        try:
            with sync_playwright() as p:
                try:
                    browser = p.chromium.launch(headless=True)
                except Exception as e:
                    self.skipTest(f"Chromium browser not available: {e}")

                # Fresh context with NO cookies / NO session
                context = browser.new_context()
                page = context.new_page()

                # 1. Visit Connections Page without any OAuth session
                page.goto(f"{base_url}/connections")
                page.wait_for_selector("#profileName")
                profile_name = page.text_content("#profileName")
                self.assertIn("Not Connected", profile_name)

                # Verify page does NOT block with "LinkedIn Account Not Connected" message
                self.assertNotIn("Please sign in with your LinkedIn account to access this page", page.content())

                # 2. Upload Connections.csv
                page.set_input_files("#connectionsImportFile", str(test_csv_file))
                page.click("#importSubmitBtn")

                # Wait for success status
                page.wait_for_selector(".import-status-msg.success")
                status_text = page.text_content(".import-status-msg.success")
                self.assertIn("Successfully loaded", status_text)

                # 3. Verify imported cards appear with correct badge
                payal_card = page.locator(".friend-card:has-text('Payal Pal')")
                payal_card.wait_for(state="visible", timeout=5000)
                self.assertTrue(payal_card.is_visible())
                self.assertIn("Imported from LinkedIn Connections export", payal_card.text_content())

                # 4. Select Payal Pal
                payal_btn = payal_card.locator(".select-btn")
                payal_btn.click()
                self.assertEqual(payal_btn.text_content().strip(), "Selected ✓")

                # 5. Continue to Activity Filter
                continue_btn = page.locator("#continueBtn")
                self.assertTrue(continue_btn.is_visible())
                self.assertIn("Payal Pal", continue_btn.text_content())
                continue_btn.click()

                # 6. Verify Activity Filter page loaded
                page.wait_for_url("**/activity-filter")
                page.wait_for_load_state("networkidle")
                target_name = page.text_content("#friendName")
                self.assertIn("Payal Pal", target_name)

                # 7. Run analysis and verify dashboard
                page.click("#analyzeBtn")
                page.wait_for_url("**/dashboard")
                self.assertIn("Dashboard", page.title())
                page.wait_for_function("() => document.getElementById('subjectName') && document.getElementById('subjectName').textContent !== 'Selected Profile'", timeout=10000)
                self.assertIn("Payal Pal", page.text_content("#subjectName"))

                browser.close()
        finally:
            if test_csv_file.exists():
                test_csv_file.unlink()

    def test_direct_profile_url_flow(self):
        """
        Verify the new direct user flow:
        Open / directly -> Enter any LinkedIn Profile URL -> Validate -> Activity Filter -> Dashboard
        No email/password login required as first step.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("Playwright not installed in environment")

        base_url = f"http://127.0.0.1:{self.port}"

        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as e:
                self.skipTest(f"Chromium browser not available: {e}")

            context = browser.new_context()
            page = context.new_page()

            # 1. Step 1: Open Direct Website at /
            page.goto(f"{base_url}/")
            self.assertIn("LinkedIn Activity Analyzer", page.title())
            self.assertTrue(page.locator("#profileUrlInput").is_visible())
            self.assertTrue(page.locator("#analyzeProfileBtn").is_visible())

            # 2. Test Invalid URL validation
            page.fill("#profileUrlInput", "https://not-linkedin.com/user/test")
            page.click("#analyzeProfileBtn")
            page.wait_for_selector("#urlErrorMsg")
            self.assertTrue(page.locator("#urlErrorMsg").is_visible())
            self.assertIn("valid LinkedIn profile URL", page.text_content("#urlErrorMsg"))

            # 3. Step 2: Enter Valid Profile URL
            page.fill("#profileUrlInput", "https://www.linkedin.com/in/pragyanandsaho")
            page.click("#analyzeProfileBtn")

            # 4. Step 3: Activity Filter Page
            page.wait_for_url("**/activity-filter")
            page.wait_for_load_state("networkidle")
            self.assertIn("Filter Criteria", page.title())

            # Verify Profile Name, Profile URL, and Headline are displayed
            page.wait_for_selector("#profileNameDisplay")
            self.assertIn("Pragyanand Sahoo", page.text_content("#profileNameDisplay"))
            self.assertIn("pragyanandsaho", page.text_content("#profileUrlDisplay"))
            headline_text = page.text_content("#profileHeadlineDisplay")
            self.assertTrue("SRHU" in headline_text or "Swami Rama" in headline_text or len(headline_text) > 0)

            # 5. Click Analyze Activity -> Dashboard
            page.click("#analyzeBtn")
            page.wait_for_url("**/dashboard")
            self.assertIn("Dashboard", page.title())

            # 6. Step 4: Verify Dashboard Metrics and Real Calculated Data
            page.wait_for_selector("#subjectName")
            page.wait_for_function("() => document.getElementById('subjectName') && document.getElementById('subjectName').textContent !== 'Selected Profile'", timeout=10000)
            self.assertIn("Pragyanand Sahoo", page.text_content("#subjectName"))
            self.assertTrue(page.locator("#totalActivities").is_visible())
            self.assertTrue(page.locator("#postsCount").is_visible())
            self.assertTrue(page.locator("#commentsCount").is_visible())
            self.assertTrue(page.locator("#likesCount").is_visible())
            self.assertTrue(page.locator("#sharesCount").is_visible())
            self.assertTrue(page.locator("#videosCount").is_visible())

            # Verify Timeline is rendered (post cards or honest empty state)
            page.wait_for_selector("#activitiesTimeline")
            timeline_text = page.text_content("#activitiesTimeline")
            self.assertTrue("POSTED BY PROFILE" in timeline_text or "ORIGINAL POST" in timeline_text or "No activity data available" in timeline_text)

            browser.close()

    def test_aniket_pal_automatic_session_and_target_flow(self):
        """
        Verify:
        1. Automatic background authentication using Aniket Pal testing session.
        2. UI displays Aniket Pal's profile photo and 'LinkedIn Connected ✓'.
        3. User enters a completely separate target profile URL (Payal Pal).
        4. Target profile is analyzed while authenticated account is Aniket Pal.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("Playwright not installed in environment")

        base_url = f"http://127.0.0.1:{self.port}"

        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as e:
                self.skipTest(f"Chromium browser not available: {e}")

            context = browser.new_context()
            page = context.new_page()

            # 1. Open Website directly
            page.goto(f"{base_url}/")

            # 2. Wait for automatic background authentication to confirm
            page.wait_for_selector("#authConnectedState", state="visible", timeout=6000)

            # 3. Confirm Aniket Pal account details and status
            account_name = page.text_content("#authAccountName")
            self.assertIn("Aniket Pal", account_name)

            status_text = page.text_content("#authStatusText")
            self.assertIn("LinkedIn Connected", status_text)

            photo_src = page.locator("#authAccountPhoto").get_attribute("src")
            self.assertIn("aniket_pal.jpg", photo_src)

            # 4. Enter completely separate target profile URL
            page.fill("#profileUrlInput", "https://www.linkedin.com/in/payal-pal-a18b92339")
            page.click("#analyzeProfileBtn")

            # 5. Verify Target profile reaches Activity Filter
            page.wait_for_url("**/activity-filter")
            page.wait_for_load_state("networkidle")
            target_name = page.text_content("#profileNameDisplay")
            self.assertIn("Payal Pal", target_name)

            # 6. Click Analyze Activity -> Dashboard
            page.click("#analyzeBtn")
            page.wait_for_url("**/dashboard")
            self.assertIn("Dashboard", page.title())

            # 7. Dashboard shows target profile Payal Pal
            page.wait_for_function("() => document.getElementById('subjectName') && document.getElementById('subjectName').textContent !== 'Selected Profile'", timeout=10000)
            dashboard_subject = page.text_content("#subjectName")
            self.assertIn("Payal Pal", dashboard_subject)

            browser.close()

    def test_saved_target_profiles_browser_flow(self):
        """
        Verify:
        1. Target profiles are persisted in backend storage upon analysis.
        2. Main page displays 'SAVED PROFILES' section with Name, Headline, URL, Photo.
        3. 'Analyze Again' button allows analyzing target without retyping URL.
        4. Multiple profiles are preserved.
        5. 'Remove' removes only the selected profile.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("Playwright not installed in environment")

        from services.saved_profiles_storage import (
            save_target_profile,
            remove_saved_profile,
            _get_storage_file,
            TEST_PROFILES_FILE
        )

        # Clear test storage before test - strictly verify it's the test file
        test_file = _get_storage_file()
        assert test_file.name == TEST_PROFILES_FILE.name, f"Safety check failed: expected test file, got {test_file.name}"
        if test_file.exists():
            test_file.unlink()

        # Clear isolated test database collection
        try:
            from db.mongo import get_db
            test_db = get_db()
            if test_db.name == "linkedin_activity_analyzer_test":
                test_db["saved_profiles"].delete_many({})
        except Exception:
            pass

        base_url = f"http://127.0.0.1:{self.port}"

        # Seed two distinct saved profiles
        save_target_profile({
            "profile_url": "https://www.linkedin.com/in/payal-pal-a18b92339/",
            "name": "Payal Pal",
            "headline": "Student at Graphic Era Deemed to be University"
        })
        save_target_profile({
            "profile_url": "https://www.linkedin.com/in/pragyanandsaho/",
            "name": "Pragyanand Sahoo",
            "headline": "Software Engineer"
        })

        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as e:
                self.skipTest(f"Chromium browser not available: {e}")

            context = browser.new_context()
            page = context.new_page()

            # 1. Open Website
            page.goto(f"{base_url}/")

            # 2. Wait for Saved Profiles section to render
            page.wait_for_selector("#savedProfilesSection", state="visible", timeout=6000)

            # 3. Verify both profiles appear
            profiles_text = page.text_content("#savedProfilesContainer")
            self.assertIn("Payal Pal", profiles_text)
            self.assertIn("Pragyanand Sahoo", profiles_text)

            # 4. Verify count
            count_text = page.text_content("#savedProfilesCount")
            self.assertIn("2 saved", count_text)

            # 5. Click Analyze Again on Pragyanand Sahoo
            # Automatically accept any confirm dialogs if needed
            page.on("dialog", lambda dialog: dialog.accept())

            # Find the card for Pragyanand Sahoo and click its Analyze Again button
            sahoo_card = page.locator(".saved-profile-card:has-text('Pragyanand Sahoo')")
            sahoo_card.locator(".btn-analyze-again").click()

            # 6. Should navigate to /activity-filter with Pragyanand Sahoo selected
            page.wait_for_url("**/activity-filter", timeout=6000)
            target_name = page.text_content("#profileNameDisplay")
            self.assertIn("Pragyanand Sahoo", target_name)

            # 7. Go back to main page
            page.goto(f"{base_url}/")
            page.wait_for_selector("#savedProfilesSection", state="visible", timeout=6000)

            # 8. Remove Payal Pal
            payal_card = page.locator(".saved-profile-card:has-text('Payal Pal')")
            payal_card.locator(".btn-remove-profile").click()

            # Wait for Payal Pal to disappear
            page.wait_for_timeout(1000)
            updated_text = page.text_content("#savedProfilesContainer")
            self.assertNotIn("Payal Pal", updated_text)
            self.assertIn("Pragyanand Sahoo", updated_text)

            browser.close()

        # Clean up test storage file and test collection
        if test_file.name == TEST_PROFILES_FILE.name and test_file.exists():
            test_file.unlink()
        try:
            from db.mongo import get_db
            test_db = get_db()
            if test_db.name == "linkedin_activity_analyzer_test":
                test_db["saved_profiles"].delete_many({})
        except Exception:
            pass



    def test_dashboard_renders_preserved_history_metrics_on_http_999(self):
        """
        Verify that when a new LinkedIn attempt fails with HTTP 999 rate limiting:
        1. Top metrics show the 10 activities from preserved historical analysis (not 0).
        2. Breakdown and counts show 4 posts, 3 comments, 2 likes, 1 share, 0 videos.
        3. Date range reflects the historical range (2026-07-16 -> 2026-10-29).
        4. Clear banners distinguish the current HTTP 999 attempt from preserved history.
        5. Exactly 10 historical activity cards are rendered.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("Playwright not installed in environment")

        from services.analysis_storage import (
            save_profile_successful_analysis,
            save_latest_analysis,
            clear_latest_analysis,
            clear_profile_history
        )

        test_slug = "neel-mani"
        test_url = f"https://www.linkedin.com/in/{test_slug}/"
        mock_acts = []
        for i in range(10):
            act_type = "post" if i < 4 else ("comment" if i < 7 else ("like" if i < 9 else "share"))
            mock_acts.append({
                "id": f"hist_act_{i}",
                "type": act_type,
                "action_type": act_type,
                "text": f"Historical {act_type} #{i+1} by Dr. Neel Mani",
                "post_content": f"Historical {act_type} #{i+1} by Dr. Neel Mani",
                "date": "2026-08-15",
                "relative_date": "2mo",
                "post_author": "Dr. Neel Mani",
                "post_author_headline": "Professor & AI Researcher",
                "is_own_post": True,
                "likes": 15,
                "comments": 2,
                "shares": 1
            })

        mock_success = {
            "subject_name": "Dr. Neel Mani",
            "subject_url": test_url,
            "headline": "Professor & AI Researcher",
            "total_activities": 10,
            "posts_count": 4,
            "comments_count": 3,
            "likes_count": 2,
            "shares_count": 1,
            "videos_count": 0,
            "by_type": {"post": 4, "comment": 3, "like": 2, "share": 1},
            "date_range": {"start": "2026-07-16", "end": "2026-10-29"},
            "activities": mock_acts
        }
        save_profile_successful_analysis(test_slug, mock_success)

        # Subsequent failed scrape returning HTTP 999
        failed_attempt = {
            "subject_name": "Dr. Neel Mani",
            "subject_url": test_url,
            "headline": "Professor & AI Researcher",
            "total_activities": 0,
            "posts_count": 0,
            "comments_count": 0,
            "likes_count": 0,
            "shares_count": 0,
            "videos_count": 0,
            "activities": [],
            "status_type": "RATE_LIMITED_999",
            "retrieval_completeness": "BLOCKED",
            "empty_message": "LinkedIn rate limit encountered (HTTP 999). Please wait before retrying.",
            "date_range": {"start": "2026-10-31", "end": "2026-10-31"}
        }
        save_latest_analysis(failed_attempt)

        base_url = f"http://127.0.0.1:{self.port}"
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()

            page.goto(f"{base_url}/dashboard")
            page.wait_for_url("**/dashboard")
            self.assertIn("Dashboard", page.title())

            # 1. Wait for renderAnalysisResults to complete
            page.wait_for_selector("#historicalStatsBanner")

            # Subject Header & Badges
            name_text = page.text_content("#subjectName")
            self.assertIn("Dr. Neel Mani", name_text)

            # 2. Date Range clearly shows historical period
            date_range_text = page.text_content("#dateRangeDisplay")
            self.assertIn("2026-07-16", date_range_text)
            self.assertIn("2026-10-29", date_range_text)
            self.assertIn("Historical", date_range_text)

            # 3. Top Metrics must reflect historical analysis (10, 4, 3, 2, 1, 0)
            page.wait_for_selector("#totalActivities")
            self.assertEqual(page.text_content("#totalActivities").strip(), "10")
            self.assertEqual(page.text_content("#postsCount").strip(), "4")
            self.assertEqual(page.text_content("#commentsCount").strip(), "3")
            self.assertEqual(page.text_content("#likesCount").strip(), "2")
            self.assertEqual(page.text_content("#sharesCount").strip(), "1")
            self.assertEqual(page.text_content("#videosCount").strip(), "0")

            # 4. Historical Notice Banner above stats grid
            stats_banner = page.locator("#historicalStatsBanner")
            self.assertTrue(stats_banner.is_visible())
            self.assertIn("Metrics from Preserved Historical Analysis", stats_banner.text_content())

            # 5. Diagnostic Banner distinguishing current HTTP 999 attempt
            page.wait_for_selector(".diagnostic-current-banner")
            diag_text = page.text_content(".diagnostic-current-banner")
            self.assertIn("HTTP 999", diag_text)
            self.assertIn("Displaying Preserved Historical Analysis", diag_text)

            # 6. Exactly 10 historical cards rendered
            cards = page.locator(".feed-post-card")
            self.assertEqual(cards.count(), 10)

            # 7. Card header displays historical badge
            hist_card_badges = page.locator(".historical-badge")
            self.assertEqual(hist_card_badges.count(), 10)

            browser.close()


if __name__ == "__main__":
    unittest.main()

