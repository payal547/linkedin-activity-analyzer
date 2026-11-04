# LinkedIn Activity Analyzer – Persistent Logging Guide

## 1. Architectural Overview

The application features a centralized, persistent logging architecture designed for high observability, thread safety, and strict credential protection.

Key architectural properties:
- **Persistent Storage**: All log records are appended to `logs/activity_analyzer.log`.
- **Automatic Log Rotation**: Backed by Python's `RotatingFileHandler` configured for **5MB maximum file size** with **5 backup generations** (`activity_analyzer.log.1` through `.5`), preventing unbounded disk growth.
- **Run ID Context Propagation**: Uses thread-local storage (`threading.local`) and a custom `logging.Filter` (`RunIdContextFilter`) so every log line automatically carries the active unique execution identifier.
- **Automatic Credential & Secret Redaction**: All log messages, format arguments, and parameters are filtered through regex scrubbers to ensure passwords, auth cookies, bearer tokens, and client secrets never touch the disk.
- **Dual Destination**: Simultaneous output to persistent rotating files (at `DEBUG` level) and console stdout (at `INFO` level).
- **REST API Endpoints**: Live inspection of logs and execution runs via `/api/logs` and `/api/logs/runs`.

---

## 2. Log Format & Component Breakdown

Every log line follows a standardized, machine-parseable schema:
```text
[TIMESTAMP] [LEVEL] [LOGGER_NAME] [RunID: RUN_ID] MESSAGE
```

### Example Log Lines
```text
[2026-11-03 21:47:15] [INFO] [app] [RunID: run_20261103_214715_eda60a] POST /api/analyze -> 200 (63.4ms)
[2026-11-03 21:47:15] [DEBUG] [linkedin.playwright_service] [RunID: run_20261103_214715_eda60a] Extracted video stream: https://dms.licdn.com/.../mp4
[2026-11-03 21:47:16] [WARNING] [db.mongo] [RunID: system] Primary MongoDB ping delayed (45ms)
[2026-11-03 21:47:18] [ERROR] [scraper] [RunID: run_20261103_214715_eda60a] Target profile returned HTTP 999 rate limit
```

### Component Details
| Component | Description | Example |
| :--- | :--- | :--- |
| `TIMESTAMP` | ISO-formatted local timestamp (`%Y-%m-%d %H:%M:%S`) | `2026-11-03 21:47:15` |
| `LEVEL` | Log severity level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) | `INFO` |
| `LOGGER_NAME` | Subsystem or Python module originating the message | `app`, `playwright_service`, `db.mongo` |
| `RunID` | Unique run identifier for request lifecycle tracing; defaults to `system` for background tasks | `run_20261103_214715_eda60a` |
| `MESSAGE` | Human-readable log narrative with sensitive secrets scrubbed | `Pipeline run finished with status: completed` |

---

## 3. Tracing a Request by Run ID

Every pipeline execution generates a timestamped unique Run ID, such as `run_20261103_214715_eda60a`. To trace the entire journey of a request from intake to delivery:

### Using Shell / PowerShell
```bash
# Linux / macOS / Bash
grep "run_20261103_214715_eda60a" logs/activity_analyzer.log

# Windows PowerShell
Select-String -Path logs/activity_analyzer.log -Pattern "run_20261103_214715_eda60a"
```

### Sample Trace Output
```text
[2026-11-03 21:47:14] [INFO] [app] [RunID: system] POST /api/profile/validate-url -> 200 (21.9ms)
[2026-11-03 21:47:15] [INFO] [services.pipeline_service] [RunID: run_20261103_214715_eda60a] Pipeline run started: run_20261103_214715_eda60a for profile: https://www.linkedin.com/in/payal-pal
[2026-11-03 21:47:15] [DEBUG] [services.pipeline_service] [RunID: run_20261103_214715_eda60a] [Stage 1: Request Received] Started - Validating input URL
[2026-11-03 21:47:15] [DEBUG] [services.pipeline_service] [RunID: run_20261103_214715_eda60a] [Stage 1: Request Received] Completed in 0.4ms
...
[2026-11-03 21:47:15] [INFO] [services.pipeline_service] [RunID: run_20261103_214715_eda60a] Pipeline run finished with status: completed (duration: 62.6ms)
[2026-11-03 21:47:15] [INFO] [app] [RunID: system] POST /api/analyze -> 200 (63.4ms)
```

---

## 4. Credential Scrubber & Security Policy

All log entries are sanitized before being written to disk or console. The regex scrubber automatically redacts:
1. `li_at` session cookies (`li_at=***REDACTED***`).
2. Plaintext passwords (`password=***REDACTED***`).
3. Access tokens (`access_token=***REDACTED***`).
4. OAuth Bearer authorization tokens (`Bearer ***REDACTED***`).
5. Client secrets (`client_secret=***REDACTED***`).
6. Session IDs longer than 16 hex characters (`session_id=***REDACTED***`).

Even if a developer accidentally writes `logger.debug(f"Received session: {session}")`, the scrubber guarantees that secrets remain protected.

---

## 5. Adding Logging to New Code

When authoring new backend modules, follow these conventions:

### Import and Initialize
```python
from utils.logger import get_logger, set_current_run_id, clear_current_run_id

logger = get_logger(__name__)
```

### Logging Conventions
- `logger.debug(...)`: Fine-grained diagnostic information (DOM selector matches, raw element counts, intermediate query timings).
- `logger.info(...)`: High-level operational milestones (request receipt, stage completion, successful data persistence).
- `logger.warning(...)`: Recoverable conditions and degraded states (database timeout falling back to JSON, rate-limit warnings, missing non-critical DOM fields).
- `logger.error(...)`: Recoverable failures affecting a user operation (HTTP 999 rate limit, authentication session expired, unparseable payload).
- `logger.critical(...)`: System-level catastrophic failures (unhandled crash, complete failure of all storage engines).

---

## 6. Log API Endpoints

The system provides REST endpoints for frontend observability and programmatic diagnostics:

### 1. `GET /api/logs`
Returns structured log entries, newest first.

**Query Parameters:**
- `lines` (optional, default: 100, max: 1000): Number of log entries to retrieve.
- `level` (optional, e.g. `INFO`, `ERROR`, `WARNING`): Filter by log severity.
- `run_id` (optional, e.g. `run_20261103_214715_eda60a`): Filter entries by a specific Run ID.
- `search` / `q` (optional): Case-insensitive keyword search across log messages.

**Example Request:**
```bash
curl "http://127.0.0.1:5000/api/logs?level=ERROR&lines=25"
```

**Example Response:**
```json
{
  "count": 1,
  "file": "D:\\likedin_analysis\\linkedin-activity-analyzer\\logs\\activity_analyzer.log",
  "logs": [
    {
      "timestamp": "2026-11-03 21:47:18",
      "level": "ERROR",
      "module": "scraper",
      "run_id": "run_20261103_214715_eda60a",
      "message": "Target profile returned HTTP 999 rate limit",
      "raw": "[2026-11-03 21:47:18] [ERROR] [scraper] [RunID: run_20261103_214715_eda60a] Target profile returned HTTP 999 rate limit"
    }
  ]
}
```

### 2. `GET /api/logs/runs`
Returns all known Run IDs recorded in the log file, ordered from newest to oldest.

**Example Response:**
```json
{
  "count": 2,
  "runs": [
    "run_20261103_220015_77e747",
    "run_20261103_220013_2dfbd4"
  ]
}
```

