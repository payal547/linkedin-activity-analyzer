"""
Shared test isolation: tests must never reach linkedin.com or the developer's own Chrome.

Without this, scraper tests either stop at AUTH_REQUIRED on a machine with no LinkedIn
session, or, on a machine with Chrome open on the CDP port, drive the developer's real
logged-in LinkedIn account.
"""

import importlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "backend"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# These modules test session detection itself, so they must see the real functions.
_SESSION_TEST_MODULES = ("test_capture_linkedin_session", "test_chrome_cdp_integration")

# Both import paths are used across the suite and load as separate module objects.
_SERVICE_MODULES = ("linkedin.playwright_service", "backend.linkedin.playwright_service")


def _make_guarded_sync_playwright(real_sync_playwright):
    """Block the scraper's browser launch; let UI tests drive the local app normally."""
    def guarded(*args, **kwargs):
        caller = sys._getframe(1).f_globals.get("__name__", "")
        if caller.endswith("playwright_service"):
            raise RuntimeError(
                "Scraper browser launch blocked in tests. Patch playwright.sync_api.sync_playwright "
                "or seed _PROFILE_CACHE with reached_end_of_feed=True."
            )
        return real_sync_playwright(*args, **kwargs)
    return guarded


@pytest.fixture(autouse=True)
def _isolate_from_linkedin(request):
    if request.module.__name__.rsplit(".", 1)[-1] in _SESSION_TEST_MODULES:
        yield
        return

    patches = []
    for name in _SERVICE_MODULES:
        try:
            importlib.import_module(name)
        except Exception:
            continue
        patches += [
            patch(f"{name}.validate_session_state", return_value=("VALID", True, "Test session")),
            patch(f"{name}.is_cdp_available", return_value=False),
            patch(f"{name}.get_active_chrome_cdp_ws_url", return_value=None),
        ]
    # The scraper imports sync_playwright at call time, so this default applies unless a
    # test patches sync_playwright itself.
    import playwright.sync_api as _pw_api
    patches.append(patch("playwright.sync_api.sync_playwright", _make_guarded_sync_playwright(_pw_api.sync_playwright)))

    for p in patches:
        p.start()
    try:
        yield
    finally:
        for p in reversed(patches):
            p.stop()
