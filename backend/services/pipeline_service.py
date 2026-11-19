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
    {"id": 1, "name": "Connecting to Chrome", "description": "Detecting and connecting to active Chrome session via DevTools Protocol", "target_pct": 5},
    {"id": 2, "name": "Validating LinkedIn Session", "description": "Validating LinkedIn authentication state without exposing credentials", "target_pct": 10},
    {"id": 3, "name": "Loading Profile", "description": "Navigating to LinkedIn profile activity feed", "target_pct": 20},
    {"id": 4, "name": "Extracting Activities", "description": "Extracting real activity cards, posts, comments, reactions, and videos", "target_pct": 40},
    {"id": 5, "name": "Classifying Activities", "description": "Categorizing activities by type: post, comment, reaction, repost, video", "target_pct": 55},
    {"id": 6, "name": "Parsing and Validating Dates", "description": "Parsing timestamps, relative badges, and exact snowflake dates", "target_pct": 65},
    {"id": 7, "name": "Retrieving Historical Activities", "description": "Loading preserved historical activities from MongoDB and history storage", "target_pct": 75},
    {"id": 8, "name": "Removing Duplicates", "description": "Deduplicating activities by stable post IDs, URNs, and content hashes", "target_pct": 82},
    {"id": 9, "name": "Saving to Database", "description": "Persisting merged activities into MongoDB with data provenance tags", "target_pct": 90},
    {"id": 10, "name": "Applying Date Filters", "description": "Filtering activities by inclusive date range and activity types", "target_pct": 95},
    {"id": 11, "name": "Preparing Dashboard", "description": "Computing engagement metrics, sentiment, and final dashboard payload", "target_pct": 100},
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
            "target_pct": s["target_pct"],
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


def start_pipeline_run(target_profile: str, run_id: Optional[str] = None, total_profiles: int = 1) -> str:
    """Initializes and registers a new 11-stage pipeline run."""
    actual_run_id = run_id or generate_run_id("run")
    now_iso = datetime.now().isoformat()

    run_obj: Dict[str, Any] = {
        "run_id": actual_run_id,
        "target_profile": target_profile,
        "start_time": now_iso,
        "end_time": None,
        "total_duration_ms": None,
        "status": "running",
        "progress_percentage": 0,
        "current_stage_id": 1,
        "current_stage_name": STAGE_DEFINITIONS[0]["name"],
        "is_indeterminate": False,
        "current_profile_name": target_profile,
        "current_profile_index": 1,
        "total_profiles": max(1, total_profiles),
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


def update_pipeline_profile(run_id: str, profile_name: str, profile_index: int, total_profiles: int) -> None:
    """Updates multi-profile progress indicators."""
    with _LOCK:
        run_obj = _PIPELINE_RUNS.get(run_id)
        if not run_obj:
            return
        run_obj["current_profile_name"] = profile_name
        run_obj["current_profile_index"] = profile_index
        run_obj["total_profiles"] = max(1, total_profiles)
        _recalculate_progress(run_obj)


def _recalculate_progress(run_obj: Dict[str, Any]) -> None:
    """Internal helper to calculate actual completed percentage across stages and profiles."""
    if run_obj.get("status") == "completed":
        run_obj["progress_percentage"] = 100
        run_obj["is_indeterminate"] = False
        return

    if run_obj.get("status") == "failed":
        run_obj["is_indeterminate"] = False
        return

    # Calculate stage completion
    completed_pct = 0
    running_stage = None
    for st in run_obj.get("stages", []):
        if st["status"] == "completed":
            completed_pct = max(completed_pct, st.get("target_pct", 0))
        elif st["status"] == "running":
            running_stage = st

    cur_idx = max(1, run_obj.get("current_profile_index", 1))
    tot_profs = max(1, run_obj.get("total_profiles", 1))

    # Weight by profiles
    if tot_profs > 1:
        base_prof_pct = (cur_idx - 1) * (100.0 / tot_profs)
        stage_slice = (completed_pct / 100.0) * (100.0 / tot_profs)
        overall_pct = int(base_prof_pct + stage_slice)
    else:
        overall_pct = completed_pct

    # Cap running progress at 95% until complete_pipeline_run marks it finished
    overall_pct = min(95, max(0, overall_pct))
    run_obj["progress_percentage"] = overall_pct

    if running_stage:
        run_obj["current_stage_id"] = running_stage["id"]
        run_obj["current_stage_name"] = running_stage["name"]
        # Indeterminate when scrolling or extracting where card count is unknown ahead of time
        run_obj["is_indeterminate"] = running_stage["id"] in (1, 4)
    else:
        run_obj["is_indeterminate"] = False


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
        _recalculate_progress(run_obj)

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
        _recalculate_progress(run_obj)

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
        _recalculate_progress(run_obj)

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
        _recalculate_progress(run_obj)

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

        _recalculate_progress(run_obj)

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
