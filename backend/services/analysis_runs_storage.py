"""Append-only MongoDB storage for real analysis request summaries.

This collection records requests observed after this feature is deployed. It does
not backfill or synthesize historical runs from profile snapshots.
"""

from datetime import datetime, timezone
import threading
from typing import Any, Dict
from pymongo.errors import DuplicateKeyError

from db.mongo import get_db

_index_lock = threading.Lock()
_indexes_ready = False


def save_analysis_run(run: Dict[str, Any]) -> bool:
    global _indexes_ready
    run_id = str(run.get("run_id") or "").strip()
    if not run_id:
        return False

    doc = dict(run)
    doc.pop("run_id", None)
    doc["_id"] = run_id
    doc.setdefault("completed_at", datetime.now(timezone.utc).isoformat())
    collection = get_db()["analysis_runs"]
    if not _indexes_ready:
        with _index_lock:
            if not _indexes_ready:
                try:
                    collection.create_index([("completed_at", -1)], name="idx_analysis_runs_completed")
                    collection.create_index([("profile_slugs", 1), ("completed_at", -1)], name="idx_analysis_runs_profile_completed")
                    _indexes_ready = True
                except Exception:
                    # Index creation is an additive optimization; saving the real
                    # run remains useful when the MongoDB role cannot manage indexes.
                    pass

    try:
        collection.insert_one(doc)
        return True
    except DuplicateKeyError:
        # Run IDs are request identifiers. An existing ID is an idempotent retry.
        return True
