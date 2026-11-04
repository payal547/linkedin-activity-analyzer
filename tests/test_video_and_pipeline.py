"""
Unit and Integration Tests for Video Extraction, Logging System, Pipeline Service, and Date Formatting.
Tests adhere to the LinkedIn Activity Analyzer standards without requiring live external network access.
"""

import io
import json
import logging
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure backend directory is in sys.path
import sys
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "backend"))

from models.activity import Activity
import utils.logger as logger_mod
from utils.logger import (
    setup_logging,
    get_logger,
    set_current_run_id,
    get_current_run_id,
    clear_current_run_id,
    redact_sensitive_info,
    RunIdContextFilter,
    get_recent_logs,
    get_known_runs
)
from services.pipeline_service import (
    start_pipeline_run,
    start_stage,
    complete_stage,
    fail_stage,
    skip_stage,
    complete_pipeline_run,
    get_latest_pipeline_run,
    get_pipeline_run,
    get_recent_pipeline_runs,
    STAGE_DEFINITIONS as PIPELINE_STAGES
)
from linkedin.playwright_service import _extract_video_info


class TestVideoExtraction(unittest.TestCase):
    """Test Playwright video info extraction helper across diverse DOM structures."""

    def test_direct_video_tag_with_src(self):
        """Simulate a post containing <video src='https://video.licdn.com/xyz.mp4'>."""
        html_chunk = (
            '<div class="feed-shared-update-v2">'
            '<video src="https://dms.licdn.com/playlist/vid1/mp4_720p.mp4" '
            'poster="https://media.licdn.com/dms/image/poster1.jpg"></video>'
            '</div>'
        )
        has_v, v_url, v_thumb, v_status = _extract_video_info(html_chunk)
        self.assertTrue(has_v)
        self.assertEqual(v_url, "https://dms.licdn.com/playlist/vid1/mp4_720p.mp4")
        self.assertEqual(v_thumb, "https://media.licdn.com/dms/image/poster1.jpg")
        self.assertEqual(v_status, "Video stream extracted")

    def test_video_tag_with_source_child(self):
        """Simulate <video><source src='https://video.licdn.com/source.mp4'></video>."""
        html_chunk = (
            '<div class="feedshare-video">'
            '<video poster="https://media.licdn.com/dms/image/poster_source.jpg">'
            '<source src="https://video.licdn.com/source.mp4" type="video/mp4">'
            '</video>'
            '</div>'
        )
        has_v, v_url, v_thumb, v_status = _extract_video_info(html_chunk)
        self.assertTrue(has_v)
        self.assertEqual(v_url, "https://video.licdn.com/source.mp4")
        self.assertEqual(v_thumb, "https://media.licdn.com/dms/image/poster_source.jpg")
        self.assertEqual(v_status, "Video stream extracted")

    def test_video_tag_with_data_sources_json(self):
        """Simulate LinkedIn VideoJS <video data-sources="[{&quot;src&quot;: &quot;...&quot;}]">."""
        html_chunk = (
            '<div class="video-js">'
            '<video data-sources="[{&quot;src&quot;:&quot;https://stream.licdn.com/v_low.mp4&quot;}]" '
            'poster="https://media.licdn.com/poster_vjs.jpg"></video>'
            '</div>'
        )
        has_v, v_url, v_thumb, v_status = _extract_video_info(html_chunk)
        self.assertTrue(has_v)
        self.assertEqual(v_url, "https://stream.licdn.com/v_low.mp4")
        self.assertEqual(v_thumb, "https://media.licdn.com/poster_vjs.jpg")
        self.assertEqual(v_status, "Video stream extracted")

    def test_video_tag_with_data_stream_url(self):
        """Simulate <video data-stream-url="https://stream.licdn.com/manifest.mpd">."""
        html_chunk = (
            '<div class="feed-shared-linkedin-video">'
            '<video data-stream-url="https://stream.licdn.com/manifest.mpd"></video>'
            '</div>'
        )
        has_v, v_url, v_thumb, v_status = _extract_video_info(html_chunk)
        self.assertTrue(has_v)
        self.assertEqual(v_url, "https://stream.licdn.com/manifest.mpd")
        self.assertEqual(v_status, "Video stream extracted")

    def test_youtube_embed_detection(self):
        """Simulate post with an external YouTube link."""
        html_chunk = (
            '<div class="feed-shared-update-v2">'
            '<a href="https://www.youtube.com/watch?v=dQw4w9WgXcQ">Watch Video on YouTube</a>'
            '</div>'
        )
        has_v, v_url, v_thumb, v_status = _extract_video_info(html_chunk)
        self.assertTrue(has_v)
        self.assertEqual(v_url, "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertEqual(v_status, "External video embed")

    def test_thumbnail_only_video_post(self):
        """Simulate video container where stream is DRM or blob, but thumbnail image exists."""
        html_chunk = (
            '<div class="feed-shared-update-v2 feedshare-video">'
            '<img class="video-thumbnail" src="https://media.licdn.com/thumb.jpg" />'
            '</div>'
        )
        has_v, v_url, v_thumb, v_status = _extract_video_info(html_chunk)
        self.assertTrue(has_v)
        self.assertIsNone(v_url)
        self.assertEqual(v_thumb, "https://media.licdn.com/thumb.jpg")
        self.assertEqual(v_status, "Video detected (Stream protected by LinkedIn, permalink available)")

    def test_non_video_post(self):
        """Simulate standard image or text post with no video elements."""
        html_chunk = (
            '<div class="feed-shared-update-v2">'
            '<p>Just regular text and photo update.</p>'
            '<img src="https://media.licdn.com/photo.jpg" />'
            '</div>'
        )
        has_v, v_url, v_thumb, v_status = _extract_video_info(html_chunk)
        self.assertFalse(has_v)
        self.assertIsNone(v_url)
        self.assertIsNone(v_thumb)
        self.assertEqual(v_status, "No video")


class TestActivityDataclassVideoFields(unittest.TestCase):
    """Test Activity model dataclass with video fields."""

    def test_activity_defaults_for_video(self):
        act = Activity(
            id="act_1",
            date="2026-11-03",
            type="post",
            text="Check out this post"
        )
        self.assertIsNone(act.video_url)
        self.assertIsNone(act.video_thumbnail)
        self.assertEqual(act.video_status, "No video")

    def test_activity_with_explicit_video_info(self):
        act = Activity(
            id="act_video_1",
            date="2026-11-03",
            type="video",
            text="Demo video",
            video_url="https://video.licdn.com/test.mp4",
            video_thumbnail="https://media.licdn.com/poster.jpg",
            video_status="ready_for_play"
        )
        self.assertEqual(act.video_url, "https://video.licdn.com/test.mp4")
        self.assertEqual(act.video_thumbnail, "https://media.licdn.com/poster.jpg")
        self.assertEqual(act.video_status, "ready_for_play")

    def test_activity_video_type_default_status(self):
        act = Activity(
            id="act_video_2",
            date="2026-11-03",
            type="video",
            text="Demo video without explicit status"
        )
        self.assertEqual(act.video_status, "Video detected (LinkedIn auth required for playback)")


class TestLoggingSystem(unittest.TestCase):
    """Test the persistent logging system, context filters, and credential redactions."""

    def test_sensitive_info_redaction(self):
        """Ensure passwords, tokens, cookies, and secrets are redacted from logs."""
        raw_msg = (
            "User login attempt with password=SuperSecretPassword123! and "
            "access_token=AQEDASH821948102948120938120398 and li_at=AQEDASH_cookie_val_12345 "
            "and Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        )
        redacted = redact_sensitive_info(raw_msg)
        self.assertNotIn("SuperSecretPassword123!", redacted)
        self.assertNotIn("AQEDASH821948102948120938120398", redacted)
        self.assertNotIn("AQEDASH_cookie_val_12345", redacted)
        self.assertNotIn("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", redacted)
        self.assertIn("***REDACTED***", redacted)

    def test_run_id_context_filter(self):
        """Ensure thread-local run_id is injected into log records."""
        filter_obj = RunIdContextFilter()
        record = logging.LogRecord("test", logging.INFO, "path", 1, "test msg", (), None)
        
        clear_current_run_id()
        filter_obj.filter(record)
        self.assertEqual(record.run_id, "system")

        set_current_run_id("run_test_456")
        filter_obj.filter(record)
        self.assertEqual(record.run_id, "run_test_456")
        clear_current_run_id()

    def test_rotating_file_handler_creation(self):
        """Verify RotatingFileHandler creates the log directory and file with correct maxBytes."""
        rf_handlers = [h for h in logging.getLogger().handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
        self.assertTrue(len(rf_handlers) > 0)
        self.assertEqual(rf_handlers[0].maxBytes, 5 * 1024 * 1024)
        self.assertEqual(rf_handlers[0].backupCount, 5)

    def test_get_recent_logs_and_filtering(self):
        """Test reading logs with line counts, level filtering, and query searching."""
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "activity_analyzer.log"
            lines = [
                "[2026-11-03 10:00:00] [INFO] [app] [RunID: run_100] User logged in",
                "[2026-11-03 10:00:01] [WARNING] [scraper] [RunID: run_100] Slow response from LinkedIn",
                "[2026-11-03 10:00:02] [ERROR] [scraper] [RunID: run_101] Failed to locate video element",
                "[2026-11-03 10:00:03] [INFO] [db] [RunID: run_101] MongoDB query successful"
            ]
            log_path.write_text("\n".join(lines), encoding="utf-8")

            with patch.object(logger_mod, "LOG_FILE", log_path):
                # Filter by level ERROR
                errors = get_recent_logs(level="ERROR")
                self.assertEqual(len(errors), 1)
                self.assertIn("Failed to locate video", errors[0]["message"])

                # Filter by run_id
                run_100_logs = get_recent_logs(run_id="run_100")
                self.assertEqual(len(run_100_logs), 2)

                # Search by query
                query_logs = get_recent_logs(search="MongoDB")
                self.assertEqual(len(query_logs), 1)
                self.assertEqual(query_logs[0]["module"], "db")


class TestPipelineService(unittest.TestCase):
    """Test 10-stage pipeline orchestration, state transitions, durations, and summaries."""

    def test_pipeline_lifecycle_success(self):
        """Test happy path through stages."""
        run_id = "run_pipe_test_001"
        actual_id = start_pipeline_run(target_profile="Jane Lead", run_id=run_id)
        self.assertEqual(actual_id, run_id)

        pipeline = get_pipeline_run(run_id)
        self.assertEqual(pipeline["status"], "running")
        self.assertEqual(len(pipeline["stages"]), 10)

        # Stage 1: Validate URL
        start_stage(run_id, 1, "Validating input URL")
        complete_stage(run_id, 1, items_count=1)

        # Stage 2: Check Session
        start_stage(run_id, 2, "Checking active session")
        complete_stage(run_id, 2)

        # Complete run
        final_state = complete_pipeline_run(
            run_id,
            status="completed",
            summary={"cards_found": 15, "videos_detected": 3, "activities_stored": 15}
        )
        self.assertEqual(final_state["status"], "completed")
        self.assertEqual(final_state["summary"]["videos_detected"], 3)
        self.assertIsNotNone(final_state["total_duration_ms"])

    def test_pipeline_stage_skip(self):
        """Test skipping stages when using cached/local data."""
        run_id = "run_pipe_skip_002"
        start_pipeline_run(target_profile="Jane Lead", run_id=run_id)
        
        skip_stage(run_id, 3, "Playwright not needed for imported CSV")
        skip_stage(run_id, 4, "Web navigation not needed")
        skip_stage(run_id, 5, "Feed scrolling not needed")

        state = get_pipeline_run(run_id)
        stage_3 = next(s for s in state["stages"] if s["id"] == 3)
        self.assertEqual(stage_3["status"], "skipped")
        self.assertEqual(stage_3["details"], "Playwright not needed for imported CSV")

    def test_pipeline_stage_failure(self):
        """Test failing a stage and setting error message."""
        run_id = "run_pipe_fail_003"
        start_pipeline_run(target_profile="Jane Lead", run_id=run_id)

        start_stage(run_id, 4, "Navigating to LinkedIn profile")
        fail_stage(run_id, 4, "HTTP 999 Request Denied by LinkedIn")

        state = get_pipeline_run(run_id)
        stage_4 = next(s for s in state["stages"] if s["id"] == 4)
        self.assertEqual(stage_4["status"], "failed")
        self.assertIn("HTTP 999", stage_4["error"])


class TestPipelineAndLogApiEndpoints(unittest.TestCase):
    """Test Flask routes for pipeline monitoring and log queries."""

    @classmethod
    def setUpClass(cls):
        from app import app
        app.config["TESTING"] = True
        cls.client = app.test_client()

    @classmethod
    def tearDownClass(cls):
        try:
            from db.mongo import close_mongo_client
            close_mongo_client()
        except Exception:
            pass

    def test_get_pipeline_latest(self):
        """GET /api/pipeline/latest returns 200 with pipeline payload."""
        run_id = "run_api_test_01"
        start_pipeline_run(target_profile="API Subject", run_id=run_id)
        complete_pipeline_run(run_id, status="completed")

        resp = self.client.get("/api/pipeline/latest")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("stages", data)
        self.assertEqual(data["run_id"], run_id)

    def test_get_pipeline_by_run_id(self):
        """GET /api/pipeline/<run_id> returns matching run."""
        run_id = "run_api_specific_02"
        start_pipeline_run(target_profile="Specific Run", run_id=run_id)

        resp = self.client.get(f"/api/pipeline/{run_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["run_id"], run_id)

        # 404 for non-existent run
        resp_404 = self.client.get("/api/pipeline/non_existent_run_999")
        self.assertEqual(resp_404.status_code, 404)

    def test_get_logs_endpoint(self):
        """GET /api/logs returns recent structured logs."""
        resp = self.client.get("/api/logs?lines=20")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("logs", data)
        self.assertIsInstance(data["logs"], list)

    def test_get_logs_runs_endpoint(self):
        """GET /api/logs/runs returns known run IDs."""
        resp = self.client.get("/api/logs/runs")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("runs", data)
        self.assertIsInstance(data["runs"], list)


if __name__ == "__main__":
    unittest.main()
