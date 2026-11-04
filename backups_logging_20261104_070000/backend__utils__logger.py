"""
Centralized Persistent Logging System with Rotation, Run ID Tracking,
and Strict Credential/Secret Redaction.
"""

import os
import re
import sys
import uuid
import logging
import threading
from logging.handlers import RotatingFileHandler
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

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


_LOGGING_INITIALIZED = False
_INIT_LOCK = threading.Lock()


def setup_logging(default_level: int = logging.INFO) -> None:
    """
    Initializes root persistent rotating file handler and console handler.
    Thread-safe and idempotent.
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

        # 1. Rotating File Handler (5MB max size, 5 backups)
        file_handler = RotatingFileHandler(
            filename=str(LOG_FILE),
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(context_filter)

        # 2. Console Stream Handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(default_level)
        console_handler.setFormatter(formatter)
        console_handler.addFilter(context_filter)

        # Configure root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)
        
        # Avoid duplicate handlers if re-called
        has_file = any(isinstance(h, RotatingFileHandler) for h in root_logger.handlers)
        if not has_file:
            root_logger.addHandler(file_handler)
        has_console = any(isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler) for h in root_logger.handlers)
        if not has_console:
            root_logger.addHandler(console_handler)

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
