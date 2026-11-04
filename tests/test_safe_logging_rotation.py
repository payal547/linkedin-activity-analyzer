"""
Unit tests for Windows-safe log rotation, PyMongo telemetry suppression,
and duplicate handler prevention in the centralized logging system.
"""

import os
import sys
import time
import shutil
import tempfile
import logging
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure backend directory is in sys.path
TEST_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TEST_DIR.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Ensure test mode
os.environ["TESTING"] = "1"

from utils.logger import (
    setup_logging,
    get_logger,
    SafeRotatingFileHandler,
    redact_sensitive_info,
    RunIdContextFilter,
    set_current_run_id,
    clear_current_run_id,
    LOG_FILE
)
from db.mongo import close_mongo_client


class TestSafeLoggingAndTelemetry(unittest.TestCase):

    def setUp(self):
        clear_current_run_id()

    def tearDown(self):
        clear_current_run_id()

    # -----------------------------------------------------------------
    # 1. PyMongo Telemetry Suppression
    # -----------------------------------------------------------------
    def test_pymongo_loggers_configured_to_warning(self):
        """PyMongo loggers must be set to WARNING to eliminate debug telemetry flooding."""
        setup_logging()

        pymongo_loggers = [
            "pymongo",
            "pymongo.command",
            "pymongo.server",
            "pymongo.topology",
            "pymongo.connection",
            "pymongo.serverSelection"
        ]
        for name in pymongo_loggers:
            log_instance = logging.getLogger(name)
            self.assertGreaterEqual(
                log_instance.getEffectiveLevel(),
                logging.WARNING,
                f"Logger {name} effective level ({log_instance.getEffectiveLevel()}) is below WARNING ({logging.WARNING})"
            )
            self.assertFalse(
                log_instance.isEnabledFor(logging.DEBUG),
                f"Logger {name} should NOT be enabled for DEBUG"
            )

    def test_close_mongo_client_does_not_flood_debug_telemetry(self):
        """Closing the mongo client must maintain PyMongo at WARNING and not emit DEBUG logs."""
        setup_logging()
        with patch("db.mongo._mongo_client") as mock_client:
            mock_client.close.return_value = None
            close_mongo_client()

            pymongo_logger = logging.getLogger("pymongo")
            self.assertFalse(pymongo_logger.isEnabledFor(logging.DEBUG))
            self.assertGreaterEqual(pymongo_logger.getEffectiveLevel(), logging.WARNING)

    # -----------------------------------------------------------------
    # 2. Windows Safe Log Rotation & Concurrency Resilience
    # -----------------------------------------------------------------
    def test_safe_rotation_under_windows_file_locking(self):
        """
        Simulate Windows [WinError 32] where another process/thread holds the log file open.
        SafeRotatingFileHandler must safely rotate using copy+truncate fallback without raising
        PermissionError, preserving logs and continuing operation.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_log_path = Path(tmp_dir) / "test_active.log"
            handler = SafeRotatingFileHandler(
                filename=str(test_log_path),
                maxBytes=200,
                backupCount=3,
                encoding="utf-8",
                delay=False
            )
            test_logger = logging.getLogger("test_win_concurrency")
            test_logger.setLevel(logging.INFO)
            test_logger.addHandler(handler)

            try:
                # Write initial entries
                test_logger.info("Initial log message 1 - setup phase")
                test_logger.info("Initial log message 2 - setup phase")

                # Simulate another process holding the file open on Windows
                with open(str(test_log_path), "a", encoding="utf-8") as concurrent_process_handle:
                    # Write enough lines to exceed maxBytes (200 bytes) and trigger rotation
                    for i in range(10):
                        test_logger.info(f"Concurrent burst message {i}: processing workload in progress")

                # If we got here, rotation executed without raising PermissionError [WinError 32]
                self.assertTrue(test_log_path.exists())

                backup_1 = Path(str(test_log_path) + ".1")
                self.assertTrue(backup_1.exists(), "Backup file .1 was not created during safe rollover")
                self.assertGreater(backup_1.stat().st_size, 0, "Backup file .1 is empty")

                # Ensure active log file received new logs after rollover
                self.assertGreater(test_log_path.stat().st_size, 0, "Active log file is empty after rollover")

            finally:
                handler.close()
                test_logger.removeHandler(handler)

    def test_safe_rotation_normal_mode(self):
        """Standard rotation without concurrent lock renames cleanly."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_log_path = Path(tmp_dir) / "test_normal.log"
            handler = SafeRotatingFileHandler(
                filename=str(test_log_path),
                maxBytes=150,
                backupCount=2,
                encoding="utf-8",
                delay=False
            )
            test_logger = logging.getLogger("test_normal_rotation")
            test_logger.setLevel(logging.INFO)
            test_logger.addHandler(handler)

            try:
                for i in range(15):
                    test_logger.info(f"Log entry line {i} to trigger rollover")

                backup_1 = Path(str(test_log_path) + ".1")
                self.assertTrue(test_log_path.exists())
                self.assertTrue(backup_1.exists())
            finally:
                handler.close()
                test_logger.removeHandler(handler)

    # -----------------------------------------------------------------
    # 3. Duplicate Handlers and Idempotency
    # -----------------------------------------------------------------
    def test_setup_logging_idempotent_no_duplicate_handlers(self):
        """Multiple calls to setup_logging must not accumulate duplicate file handlers."""
        setup_logging()
        setup_logging()
        setup_logging()

        root_logger = logging.getLogger()
        rf_handlers = [
            h for h in root_logger.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        self.assertEqual(
            len(rf_handlers), 1,
            f"Expected exactly 1 RotatingFileHandler on root logger, found {len(rf_handlers)}"
        )
        self.assertIsInstance(rf_handlers[0], SafeRotatingFileHandler)

    def test_module_aliasing_shares_singleton(self):
        """Importing via 'utils.logger' and 'backend.utils.logger' must resolve to the same module."""
        import utils.logger as mod1
        try:
            import backend.utils.logger as mod2
        except ImportError:
            import backend
            if hasattr(backend, "utils") and hasattr(backend.utils, "logger"):
                mod2 = backend.utils.logger
            else:
                mod2 = sys.modules.get("backend.utils.logger")
        self.assertIs(mod1, mod2, "utils.logger and backend.utils.logger must be identical module instances")

    # -----------------------------------------------------------------
    # 4. Sensitive Data Redaction Integrity
    # -----------------------------------------------------------------
    def test_sensitive_data_redaction_remains_active(self):
        """Credentials, tokens, and cookies must be scrubbed before persisting to disk."""
        text = "Connecting with li_at=AQEDATWxxxx12345 and password=SuperSecret999! and Bearer eyJhbGciOi..."
        redacted = redact_sensitive_info(text)
        self.assertNotIn("AQEDATWxxxx12345", redacted)
        self.assertNotIn("SuperSecret999!", redacted)
        self.assertIn("***REDACTED***", redacted)


if __name__ == "__main__":
    unittest.main()
