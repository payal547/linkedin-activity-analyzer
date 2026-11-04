"""
Centralized Persistent Logging System with Rotation, Run ID Tracking,
and Strict Credential/Secret Redaction.
"""

import os
import re
import sys
import uuid
import time
import shutil
import logging
import threading
from logging.handlers import RotatingFileHandler
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

# Ensure canonical module aliasing in sys.modules so both 'utils.logger' and 'backend.utils.logger' share identity
for _mod_alias in ("utils.logger", "backend.utils.logger"):
    if _mod_alias not in sys.modules:
        sys.modules[_mod_alias] = sys.modules[__name__]

try:
    if "backend" in sys.modules:
        _b = sys.modules["backend"]
        if "utils" in sys.modules and not hasattr(_b, "utils"):
            _b.utils = sys.modules["utils"]
            sys.modules["backend.utils"] = sys.modules["utils"]
except Exception:
    pass

# Root project paths
BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOG_DIR / "activity_analyzer.log"

# Thread-local storage for Run ID propagation
_thread_local = threading.local()

# Sensitive data redaction regex patterns
REDACTION_PATTERNS = [
    (re.compile(r'(li_at=)[^;\s&]+', re.IGNORECASE), r'\1***REDACTED***'),
    (re.compile(r'("li_at"\s*:\s*")[^"]+(")', re.IGNORECASE), r'\1***REDACTED***\2'),
    (re.compile(r'(password\s*[:=]\s*["\']?)([^"\'&\s,]+)', re.IGNORECASE), r'\1***REDACTED***'),
    (re.compile(r'(access_token\s*[:=]\s*["\']?)([^"\'&\s,]+)', re.IGNORECASE), r'\1***REDACTED***'),
    (re.compile(r'(Bearer\s+)[A-Za-z0-9_\-\.]+', re.IGNORECASE), r'\1***REDACTED***'),
    (re.compile(r'(client_secret\s*[:=]\s*["\']?)([^"\'&\s,]+)', re.IGNORECASE), r'\1***REDACTED***'),
    (re.compile(r'(session_id\s*[:=]\s*["\']?)([a-zA-Z0-9_\-]{16,})', re.IGNORECASE), r'\1***REDACTED***'),
]


def redact_sensitive_info(text: str) -> str:
    """Scrubs passwords, cookies, tokens, and secrets from any log text."""
    if not isinstance(text, str):
        return text
    scrubbed = text
    for pattern, replacement in REDACTION_PATTERNS:
        scrubbed = pattern.sub(replacement, scrubbed)
    return scrubbed


class RunIdContextFilter(logging.Filter):
    """Injects current active run_id and redacts sensitive data from log records."""
    def filter(self, record: logging.LogRecord) -> bool:
        # 1. Inject run_id
        current_run_id = getattr(_thread_local, "run_id", None)
        record.run_id = current_run_id or getattr(record, "run_id", None) or "system"

        # 2. Scrub sensitive data from message
        if isinstance(record.msg, str):
            record.msg = redact_sensitive_info(record.msg)
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(
                    redact_sensitive_info(arg) if isinstance(arg, str) else arg
                    for arg in record.args
                )
            elif isinstance(record.args, dict):
                record.args = {
                    k: (redact_sensitive_info(v) if isinstance(v, str) else v)
                    for k, v in record.args.items()
                }

        return True


def set_current_run_id(run_id: Optional[str]) -> None:
    """Sets the active run_id for the current execution thread."""
    _thread_local.run_id = run_id


def get_current_run_id() -> Optional[str]:
    """Retrieves the active run_id for the current execution thread."""
    return getattr(_thread_local, "run_id", None)


def clear_current_run_id() -> None:
    """Clears the active run_id for the current execution thread."""
    _thread_local.run_id = None


def generate_run_id(prefix: str = "run") -> str:
    """Generates a timestamped, collision-resistant unique run ID."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    rand = uuid.uuid4().hex[:6]
    return f"{prefix}_{ts}_{rand}"


class SafeRotatingFileHandler(RotatingFileHandler):
    """
    Windows-safe and concurrency-aware RotatingFileHandler.

    Addresses Windows file-locking semantics (WinError 32: The process cannot
    access the file because it is being used by another process):
    1. Multi-threaded safety with re-entrant handler lock.
    2. Concurrent rollover detection: verifies if another process or thread
       already rotated the file and reduced its size before doing redundant work.
    3. Transient file-lock retries with exponential backoff on os.rename / os.remove.
    4. Safe copy + truncate fallback: If Windows prevents os.rename because another
       active process (e.g. concurrent worker, reader, or background test runner)
       holds an open file handle, the handler copies current content to the backup
       file and truncates the active log file in place. This preserves all log data
       without raising PermissionError [WinError 32] and allows all processes to continue.
    5. Guaranteed stream recovery: ensures self.stream is always restored so future
       log emissions succeed without crashing.
    """

    def __init__(
        self,
        filename: str,
        mode: str = "a",
        maxBytes: int = 0,
        backupCount: int = 0,
        encoding: Optional[str] = None,
        delay: bool = False,
        errors: Optional[str] = None
    ):
        super().__init__(
            filename=filename,
            mode=mode,
            maxBytes=maxBytes,
            backupCount=backupCount,
            encoding=encoding,
            delay=delay,
            errors=errors
        )
        self._rollover_lock = threading.RLock()

    def _safe_remove(self, path: str, retries: int = 5, delay: float = 0.05) -> bool:
        """Safely removes a file with retries on Windows, falling back to truncation."""
        for attempt in range(retries):
            try:
                if os.path.exists(path):
                    os.remove(path)
                return True
            except (PermissionError, OSError):
                if attempt < retries - 1:
                    time.sleep(delay * (2 ** attempt))
                else:
                    try:
                        with open(path, "w", encoding=self.encoding or "utf-8", errors="replace") as f:
                            f.truncate(0)
                        return True
                    except Exception:
                        return False
        return False

    def _safe_rename(self, src: str, dst: str, retries: int = 5, delay: float = 0.05) -> bool:
        """Safely renames a file with retries on Windows."""
        for attempt in range(retries):
            try:
                os.rename(src, dst)
                return True
            except (PermissionError, OSError):
                if attempt < retries - 1:
                    time.sleep(delay * (2 ** attempt))
                else:
                    return False
        return False

    def _safe_rotate_or_copy_truncate(self, src: str, dst: str, retries: int = 5, delay: float = 0.05) -> bool:
        """
        Rotates src to dst. If os.rename fails due to Windows file locking (WinError 32),
        falls back to copying src to dst and truncating src, preserving log content
        and preventing application crashes.
        """
        # 1. Attempt standard rename with retries
        for attempt in range(retries):
            try:
                os.rename(src, dst)
                return True
            except (PermissionError, OSError):
                if attempt < retries - 1:
                    time.sleep(delay * (2 ** attempt))

        # 2. Windows fallback: Copy to dst and truncate src in place
        try:
            shutil.copy2(src, dst)
            with open(src, "w", encoding=self.encoding or "utf-8", errors="replace") as f:
                f.truncate(0)
            return True
        except Exception:
            return False

    def doRollover(self) -> None:
        """
        Performs rollover safely on Windows even under concurrent access.
        """
        with self._rollover_lock:
            # 1. Close current stream
            if self.stream:
                try:
                    self.stream.flush()
                except Exception:
                    pass
                try:
                    self.stream.close()
                except Exception:
                    pass
                self.stream = None

            # 2. Check if another thread/process already performed the rollover
            try:
                if not os.path.exists(self.baseFilename) or os.path.getsize(self.baseFilename) < self.maxBytes:
                    if not self.delay:
                        self.stream = self._open()
                    return
            except OSError:
                pass

            # 3. Rotate backup files (.backupCount - 1 down to .1)
            if self.backupCount > 0:
                for i in range(self.backupCount - 1, 0, -1):
                    sfn = self.rotation_filename(f"{self.baseFilename}.{i}")
                    dfn = self.rotation_filename(f"{self.baseFilename}.{i + 1}")
                    if os.path.exists(sfn):
                        if os.path.exists(dfn):
                            self._safe_remove(dfn)
                        self._safe_rename(sfn, dfn)

                dfn = self.rotation_filename(f"{self.baseFilename}.1")
                if os.path.exists(dfn):
                    self._safe_remove(dfn)

                # 4. Rotate baseFilename to baseFilename.1 with copy+truncate fallback
                self._safe_rotate_or_copy_truncate(self.baseFilename, dfn)

            # 5. Always ensure stream is reopened if not in delayed mode
            if not self.delay:
                self.stream = self._open()

    def emit(self, record: logging.LogRecord) -> None:
        """Emits a log record, handling rollover safely without crashing on Windows."""
        try:
            if self.shouldRollover(record):
                self.doRollover()
            super().emit(record)
        except Exception:
            self.handleError(record)


_LOGGING_INITIALIZED = False
_INIT_LOCK = threading.Lock()


def setup_logging(default_level: int = logging.INFO) -> None:
    """
    Initializes root persistent rotating file handler and console handler.
    Thread-safe, Windows-safe, and idempotent.
    """
    global _LOGGING_INITIALIZED
    with _INIT_LOCK:
        if _LOGGING_INITIALIZED:
            return

        LOG_DIR.mkdir(parents=True, exist_ok=True)

        formatter = logging.Formatter(
            fmt="[%(asctime)s] [%(levelname)s] [%(name)s] [RunID: %(run_id)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        context_filter = RunIdContextFilter()

        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)

        # 1. Inspect existing handlers to prevent duplicate or leaked open file handles
        existing_rf_handler = None
        existing_console_handler = None
        abs_log_path = os.path.abspath(str(LOG_FILE))
        for h in list(root_logger.handlers):
            if isinstance(h, RotatingFileHandler):
                h_base = getattr(h, "baseFilename", None)
                if h_base:
                    try:
                        if os.path.samefile(h_base, abs_log_path):
                            existing_rf_handler = h
                    except (FileNotFoundError, OSError, ValueError):
                        if os.path.abspath(h_base) == abs_log_path:
                            existing_rf_handler = h
            elif isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
                existing_console_handler = h

        # If an existing handler is not a SafeRotatingFileHandler, replace it cleanly
        if existing_rf_handler is not None and not isinstance(existing_rf_handler, SafeRotatingFileHandler):
            root_logger.removeHandler(existing_rf_handler)
            try:
                existing_rf_handler.close()
            except Exception:
                pass
            existing_rf_handler = None

        if existing_rf_handler is None:
            file_handler = SafeRotatingFileHandler(
                filename=str(LOG_FILE),
                maxBytes=5 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
                delay=True
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)
            file_handler.addFilter(context_filter)
            root_logger.addHandler(file_handler)

        if existing_console_handler is None:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(default_level)
            console_handler.setFormatter(formatter)
            console_handler.addFilter(context_filter)
            root_logger.addHandler(console_handler)

        # 3. Suppress noisy PyMongo DEBUG telemetry (connection pool, topology, commands)
        for pymongo_logger_name in ("pymongo", "pymongo.command", "pymongo.server", "pymongo.topology", "pymongo.connection", "pymongo.serverSelection"):
            logging.getLogger(pymongo_logger_name).setLevel(logging.WARNING)

        _LOGGING_INITIALIZED = True


def get_logger(name: str) -> logging.Logger:
    """Returns a module logger with RunID filter pre-configured."""
    if not _LOGGING_INITIALIZED:
        setup_logging()
    logger = logging.getLogger(name)
    return logger


LOG_LINE_REGEX = re.compile(
    r"^\[(?P<timestamp>[^\]]+)\]\s+\[(?P<level>[^\]]+)\]\s+\[(?P<module>[^\]]+)\]\s+\[RunID:\s*(?P<run_id>[^\]]+)\]\s+(?P<message>.*)$"
)


def get_recent_logs(
    lines: int = 100,
    level: Optional[str] = None,
    run_id: Optional[str] = None,
    search: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Reads recent log entries from the primary log file with optional filtering.
    Returns newest entries first.
    """
    if not LOG_FILE.exists():
        return []

    level_filter = level.upper().strip() if level and level.upper().strip() != "ALL" else None
    run_id_filter = run_id.strip() if run_id and run_id.strip() != "ALL" else None
    search_filter = search.lower().strip() if search else None

    matching_entries: List[Dict[str, Any]] = []

    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()

        # Iterate backwards (newest lines first)
        for raw_line in reversed(all_lines):
            raw_line = raw_line.rstrip("\r\n")
            if not raw_line:
                continue

            match = LOG_LINE_REGEX.match(raw_line)
            if match:
                entry = match.groupdict()
                entry_level = entry["level"].upper()
                entry_run = entry["run_id"]
                entry_msg = entry["message"]

                if level_filter and entry_level != level_filter:
                    continue
                if run_id_filter and entry_run != run_id_filter:
                    continue
                if search_filter and search_filter not in raw_line.lower():
                    continue

                matching_entries.append({
                    "timestamp": entry["timestamp"],
                    "level": entry_level,
                    "module": entry["module"],
                    "run_id": entry_run,
                    "message": entry_msg,
                    "raw": raw_line
                })
            else:
                # Continuation / stack trace line: attach or include if search matches
                if search_filter and search_filter not in raw_line.lower():
                    continue
                if not level_filter and not run_id_filter:
                    matching_entries.append({
                        "timestamp": "",
                        "level": "TRACE",
                        "module": "",
                        "run_id": "",
                        "message": raw_line,
                        "raw": raw_line
                    })

            if len(matching_entries) >= lines:
                break

    except Exception as e:
        return [{"timestamp": datetime.now().isoformat(), "level": "ERROR", "module": "logger", "run_id": "system", "message": f"Failed to read logs: {e}", "raw": str(e)}]

    return matching_entries


def get_known_runs(limit: int = 20) -> List[Dict[str, Any]]:
    """Scans recent logs to summarize distinct run IDs with activity stats."""
    if not LOG_FILE.exists():
        return []

    runs: Dict[str, Dict[str, Any]] = {}

    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                match = LOG_LINE_REGEX.match(line.rstrip("\r\n"))
                if not match:
                    continue
                d = match.groupdict()
                r_id = d["run_id"]
                if r_id in ("system", "none", ""):
                    continue

                if r_id not in runs:
                    runs[r_id] = {
                        "run_id": r_id,
                        "first_seen": d["timestamp"],
                        "last_seen": d["timestamp"],
                        "log_count": 0,
                        "errors": 0,
                        "warnings": 0,
                        "last_message": d["message"]
                    }
                runs[r_id]["last_seen"] = d["timestamp"]
                runs[r_id]["log_count"] += 1
                runs[r_id]["last_message"] = d["message"]
                if d["level"].upper() == "ERROR":
                    runs[r_id]["errors"] += 1
                elif d["level"].upper() == "WARNING":
                    runs[r_id]["warnings"] += 1

    except Exception:
        pass

    # Sort newest last_seen first
    sorted_runs = sorted(runs.values(), key=lambda r: r["last_seen"], reverse=True)
    return sorted_runs[:limit]
