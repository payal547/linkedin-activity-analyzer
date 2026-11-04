"""
Pipeline Monitoring Service for LinkedIn Activity Analyzer.
Tracks the 10 distinct execution stages of the scraping, analysis,
and storage pipeline with per-stage timing, status, and item metrics.
Supports real-time querying via /api/pipeline endpoints.
"""

import time
import threading
from datetime import datetime
from typing import Dict, Any, List, Optional
from pathlib import Path

try:
    from backend.utils.logger import get_logger, generate_run_id
except ImportError:
    from utils.logger import get_logger, generate_run_id

logger = get_logger(__name__)

STAGE_DEFINITIONS = [
    {"id": 1, "name": "Request Received", "description": "Validation and intake of profile request"},
    {"id": 2, "name": "Auth / Session Check", "description": "LinkedIn session and cookie validation"},
    {"id": 3, "name": "Browser Launch", "description": "Playwright headless browser and context setup"},
    {"id": 4, "name": "Profile Navigation", "description": "Navigation to target profile or recent activity page"},
    {"id": 5, "name": "Activity Feed Scroll", "description": "Bounded incremental scrolling and feed expansion"},
    {"id": 6, "name": "DOM Extraction", "description": "Parsing activity cards, text, timestamps, and video tags"},
    {"id": 7, "name": "Data Normalization", "description": "Relative date parsing, deduplication, and model creation"},
    {"id": 8, "name": "Sentiment & Topic Enrichment", "description": "NLP sentiment scoring and topic classification"},
    {"id": 9, "name": "MongoDB / JSON Storage", "description": "Dual-store persistence in MongoDB and JSON fallback"},
    {"id": 10, "name": "Response Delivery", "description": "Packaging payload and returning to client"},
]

_LOCK = threading.RLock()
_PIPELINE_RUNS: Dict[str, Dict[str, Any]] = {}
_RUN_ORDER: List[str] = []
MAX_KEPT_RUNS = 50


def _build_initial_stages() -> List[Dict[str, Any]]:
    return [
        {
            "id": s["id"],
            "name": s["name"],
            "description": s["description"],
            "status": "pending",
            "start_time": None,
            "end_time": None,
            "duration_ms": None,
            "items_count": None,
            "details": None,
            "error": None,
        }
        for s in STAGE_DEFINITIONS
    ]


def start_pipeline_run(target_profile: str, run_id: Optional[str] = None) -> str:
    """Initializes and registers a new 10-stage pipeline run."""
    actual_run_id = run_id or generate_run_id("run")
    now_iso = datetime.now().isoformat()

    run_obj: Dict[str, Any] = {
        "run_id": actual_run_id,
        "target_profile": target_profile,
        "start_time": now_iso,
        "end_time": None,
        "total_duration_ms": None,
        "status": "running",
        "stages": _build_initial_stages(),
        "summary": {
            "cards_found": 0,
            "videos_detected": 0,
            "activities_stored": 0,
            "error": None
        },
        "_start_mono": time.monotonic()
    }

    with _LOCK:
        _PIPELINE_RUNS[actual_run_id] = run_obj
        if actual_run_id not in _RUN_ORDER:
            _RUN_ORDER.append(actual_run_id)
        if len(_RUN_ORDER) > MAX_KEPT_RUNS:
            oldest = _RUN_ORDER.pop(0)
            _PIPELINE_RUNS.pop(oldest, None)

    logger.info("Pipeline run started: %s for profile: %s", actual_run_id, target_profile)
    return actual_run_id


def start_stage(run_id: str, stage_id: int, details: Optional[str] = None) -> None:
    """Marks a specific pipeline stage as running."""
    with _LOCK:
        run_obj = _PIPELINE_RUNS.get(run_id)
        if not run_obj:
            return

        for st in run_obj["stages"]:
            if st["id"] == stage_id:
                st["status"] = "running"
                st["start_time"] = datetime.now().isoformat()
                st["_start_mono"] = time.monotonic()
                if details:
                    st["details"] = details
                break

    stage_name = next((s["name"] for s in STAGE_DEFINITIONS if s["id"] == stage_id), f"Stage {stage_id}")
    logger.debug("[Stage %d: %s] Started - %s", stage_id, stage_name, details or "")


def complete_stage(
    run_id: str,
    stage_id: int,
    items_count: Optional[int] = None,
    details: Optional[str] = None
) -> None:
    """Marks a stage as successfully completed with duration and processed count."""
    with _LOCK:
        run_obj = _PIPELINE_RUNS.get(run_id)
        if not run_obj:
            return

        for st in run_obj["stages"]:
            if st["id"] == stage_id:
                st["status"] = "completed"
                st["end_time"] = datetime.now().isoformat()
                start_mono = st.pop("_start_mono", None)
                if start_mono is not None:
                    st["duration_ms"] = round((time.monotonic() - start_mono) * 1000, 1)
                if items_count is not None:
                    st["items_count"] = items_count
                if details:
                    st["details"] = details
                break

    stage_name = next((s["name"] for s in STAGE_DEFINITIONS if s["id"] == stage_id), f"Stage {stage_id}")
    logger.debug("[Stage %d: %s] Completed (items: %s, details: %s)", stage_id, stage_name, items_count, details or "")


def fail_stage(run_id: str, stage_id: int, error: str) -> None:
    """Marks a stage as failed with error details."""
    with _LOCK:
        run_obj = _PIPELINE_RUNS.get(run_id)
        if not run_obj:
            return

        for st in run_obj["stages"]:
            if st["id"] == stage_id:
                st["status"] = "failed"
                st["end_time"] = datetime.now().isoformat()
                start_mono = st.pop("_start_mono", None)
                if start_mono is not None:
                    st["duration_ms"] = round((time.monotonic() - start_mono) * 1000, 1)
                st["error"] = error
                break

    stage_name = next((s["name"] for s in STAGE_DEFINITIONS if s["id"] == stage_id), f"Stage {stage_id}")
    logger.error("[Stage %d: %s] Failed: %s", stage_id, stage_name, error)


def skip_stage(run_id: str, stage_id: int, reason: Optional[str] = None) -> None:
    """Marks a stage as skipped (e.g. browser launch skipped when using cached data)."""
    with _LOCK:
        run_obj = _PIPELINE_RUNS.get(run_id)
        if not run_obj:
            return

        for st in run_obj["stages"]:
            if st["id"] == stage_id:
                st["status"] = "skipped"
                st["details"] = reason or "Skipped"
                break

    stage_name = next((s["name"] for s in STAGE_DEFINITIONS if s["id"] == stage_id), f"Stage {stage_id}")
    logger.debug("[Stage %d: %s] Skipped (%s)", stage_id, stage_name, reason or "N/A")


def complete_pipeline_run(
    run_id: str,
    status: str = "completed",
    summary: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """Finalizes a pipeline run and calculates total duration."""
    with _LOCK:
        run_obj = _PIPELINE_RUNS.get(run_id)
        if not run_obj:
            return None

        run_obj["status"] = status
        run_obj["end_time"] = datetime.now().isoformat()
        start_mono = run_obj.pop("_start_mono", None)
        if start_mono is not None:
            run_obj["total_duration_ms"] = round((time.monotonic() - start_mono) * 1000, 1)

        if summary:
            run_obj["summary"].update(summary)

        # Mark any remaining 'pending' or 'running' stages cleanly
        for st in run_obj["stages"]:
            if st["status"] == "running":
                st["status"] = "completed" if status == "completed" else "failed"
                st["end_time"] = datetime.now().isoformat()
                s_mono = st.pop("_start_mono", None)
                if s_mono is not None:
                    st["duration_ms"] = round((time.monotonic() - s_mono) * 1000, 1)
            elif st["status"] == "pending" and status in ("completed", "failed"):
                st["status"] = "skipped"

        # Make clean serializable copy
        res = dict(run_obj)
        res["stages"] = [dict(s) for s in run_obj["stages"]]

    logger.info("Pipeline run %s finished with status: %s (duration: %sms)", run_id, status, run_obj.get("total_duration_ms"))
    return res


def get_pipeline_run(run_id: str) -> Optional[Dict[str, Any]]:
    """Returns a copy of a pipeline run by run_id."""
    with _LOCK:
        run_obj = _PIPELINE_RUNS.get(run_id)
        if not run_obj:
            return None
        res = dict(run_obj)
        res["stages"] = [dict(s) for s in run_obj["stages"]]
        return res


def get_latest_pipeline_run() -> Optional[Dict[str, Any]]:
    """Returns the most recent pipeline run."""
    with _LOCK:
        if not _RUN_ORDER:
            return None
        latest_id = _RUN_ORDER[-1]
        return get_pipeline_run(latest_id)


def get_recent_pipeline_runs(limit: int = 10) -> List[Dict[str, Any]]:
    """Returns list of recent pipeline runs (newest first)."""
    with _LOCK:
        recent_ids = list(reversed(_RUN_ORDER[-limit:]))
    runs = []
    for r_id in recent_ids:
        r = get_pipeline_run(r_id)
        if r:
            runs.append(r)
    return runs
