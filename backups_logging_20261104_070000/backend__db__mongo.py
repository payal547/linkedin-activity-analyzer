"""
Centralized MongoDB Local Connection and Client Management Module.
Supports local MongoDB Community Server exclusively (mongodb://localhost:27017/).
Provides thread-safe connection pooling, fast health checks, idempotent indexing,
and recursive document sanitization to guarantee JSON serializability for Flask responses.
"""

import atexit
import os
import re
import sys
import logging
import threading
from datetime import datetime, date
from typing import Optional, Dict, Any, Tuple, List, Union
from bson import ObjectId
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError, PyMongoError

# Safe settings import supporting both root and backend execution contexts
try:
    from backend.config import settings
except ImportError:
    try:
        from config import settings
    except ImportError:
        import settings

logger = logging.getLogger(__name__)

# Thread-safe client singleton
_client_lock = threading.Lock()
_mongo_client: Optional[MongoClient] = None

DEFAULT_LOCAL_URI = "mongodb://localhost:27017/"
DEFAULT_DB_NAME = "linkedin_activity_analyzer"
DEFAULT_TIMEOUT_MS = 2000  # Fast fail for local health checks


def validate_local_uri(uri: str) -> None:
    """
    Enforces local-only MongoDB connections.
    Rejects MongoDB Atlas (mongodb+srv:// or *.mongodb.net) and remote connection strings.
    """
    if not uri:
        return
    cleaned = uri.strip().lower()
    if cleaned.startswith("mongodb+srv://") or ".mongodb.net" in cleaned:
        raise ValueError(
            "Security Policy Violation: Remote MongoDB / MongoDB Atlas connections are prohibited. "
            "Only Local MongoDB Community Server (e.g. mongodb://localhost:27017/) is permitted."
        )


def get_mongo_client(uri: Optional[str] = None, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> MongoClient:
    """
    Returns a thread-safe singleton MongoClient instance configured for local MongoDB.
    Uses short server selection and connection timeouts to prevent web thread starvation.
    """
    global _mongo_client

    target_uri = uri or getattr(settings, "MONGO_URI", None) or os.getenv("MONGO_URI") or DEFAULT_LOCAL_URI
    validate_local_uri(target_uri)

    if _mongo_client is None:
        with _client_lock:
            if _mongo_client is None:
                _mongo_client = MongoClient(
                    target_uri,
                    serverSelectionTimeoutMS=timeout_ms,
                    connectTimeoutMS=timeout_ms,
                    socketTimeoutMS=5000,
                    maxPoolSize=50,
                    minPoolSize=5,
                    retryWrites=True
                )
    return _mongo_client


def close_mongo_client() -> None:
    """Closes the active MongoClient connection and resets the singleton."""
    global _mongo_client
    with _client_lock:
        if _mongo_client is not None:
            try:
                _mongo_client.close()
            except Exception as e:
                logger.warning("Error closing MongoDB client: %s", e)
            finally:
                _mongo_client = None


atexit.register(close_mongo_client)


def get_db(db_name: Optional[str] = None, client: Optional[MongoClient] = None):
    """
    Returns the target MongoDB database instance.
    Defaults to MONGO_DB_NAME from settings or environment.
    If running under test mode without an explicit db_name, route to a test database.
    """
    mongo_client = client or get_mongo_client()
    target_db_name = db_name or getattr(settings, "MONGO_DB_NAME", None) or os.getenv("MONGO_DB_NAME") or DEFAULT_DB_NAME

    # In testing mode without explicit db_name, route to test database to isolate production data
    is_testing = (
        os.getenv("TESTING") == "1" or
        "unittest" in sys.modules or
        "pytest" in sys.modules
    )
    if not db_name and is_testing and not target_db_name.endswith("_test"):
        target_db_name = f"{target_db_name}_test"

    return mongo_client[target_db_name]


def check_connection(timeout_ms: int = 1500) -> Tuple[bool, str]:
    """
    Performs a lightweight ping against the local MongoDB instance with a short timeout.
    Returns (True, "Connected to MongoDB <version>") or (False, "<error_message>").
    """
    try:
        client = get_mongo_client(timeout_ms=timeout_ms)
        # Fast admin command ping
        client.admin.command("ping")
        server_info = client.server_info()
        version = server_info.get("version", "unknown")
        return True, f"Connected to local MongoDB (v{version})"
    except (ConnectionFailure, ServerSelectionTimeoutError) as e:
        return False, f"MongoDB connection timed out or unreachable: {str(e)}"
    except ValueError as e:
        return False, f"Configuration error: {str(e)}"
    except Exception as e:
        return False, f"Unexpected MongoDB error: {str(e)}"


def init_indexes(db=None) -> Dict[str, List[str]]:
    """
    Idempotently creates required indexes for all target collections.
    Safe to execute repeatedly without duplicating indexes or throwing errors.
    Returns a dictionary of created/verified index names by collection.
    """
    target_db = db if db is not None else get_db()
    results: Dict[str, List[str]] = {}

    # 1. saved_profiles indexes
    saved_profiles = target_db["saved_profiles"]
    idx_sp = []
    idx_sp.append(saved_profiles.create_index([("id", ASCENDING)], unique=True, sparse=True, name="idx_profile_id_unique"))
    idx_sp.append(saved_profiles.create_index([("profile_url", ASCENDING)], unique=True, sparse=True, name="idx_profile_url_unique"))
    idx_sp.append(saved_profiles.create_index([("last_analyzed", DESCENDING)], name="idx_last_analyzed_desc"))
    results["saved_profiles"] = idx_sp

    # 2. activities indexes
    activities = target_db["activities"]
    idx_act = []
    idx_act.append(activities.create_index([("id", ASCENDING)], unique=True, sparse=True, name="idx_activity_id_unique"))
    idx_act.append(activities.create_index([("subject_id", ASCENDING), ("date", DESCENDING)], name="idx_subject_date"))
    idx_act.append(activities.create_index([("subject_url", ASCENDING)], name="idx_subject_url"))
    idx_act.append(activities.create_index([("type", ASCENDING)], name="idx_type"))
    idx_act.append(activities.create_index([("sentiment", ASCENDING)], name="idx_sentiment"))
    results["activities"] = idx_act

    # 3. profile_analysis_history indexes
    history = target_db["profile_analysis_history"]
    idx_hist = []
    idx_hist.append(history.create_index([("slug", ASCENDING)], unique=True, sparse=True, name="idx_slug_unique"))
    idx_hist.append(history.create_index([("historical_analyzed_at", DESCENDING)], name="idx_historical_analyzed_desc"))
    results["profile_analysis_history"] = idx_hist

    # 4. imported_activities indexes (persistent user-imported activity data - NO TTL expiration)
    imported = target_db["imported_activities"]
    idx_imp = []
    # Drop legacy TTL index if it exists to ensure user-imported data is never deleted automatically
    try:
        if "idx_ttl_created_at" in imported.index_information():
            imported.drop_index("idx_ttl_created_at")
    except Exception as e:
        logger.warning("Could not drop legacy TTL index on imported_activities: %s", e)

    idx_imp.append(imported.create_index([("import_id", ASCENDING)], unique=True, sparse=True, name="idx_import_id_unique"))
    idx_imp.append(imported.create_index([("created_at", DESCENDING)], name="idx_created_at_desc"))
    results["imported_activities"] = idx_imp

    # 5. connections indexes
    connections = target_db["connections"]
    idx_conn = []
    idx_conn.append(connections.create_index([("profile_url", ASCENDING)], unique=True, sparse=True, name="idx_conn_profile_url_unique"))
    idx_conn.append(connections.create_index([("id", ASCENDING)], sparse=True, name="idx_conn_id"))
    results["connections"] = idx_conn

    # 6. latest_analysis indexes (singleton active session cache - NO TTL expiration)
    latest_analysis = target_db["latest_analysis"]
    idx_lat = []
    # Primary key _id is auto-indexed uniquely by MongoDB; index timestamp for cache ordering
    idx_lat.append(latest_analysis.create_index([("updated_at", DESCENDING)], name="idx_latest_analysis_updated_desc"))
    results["latest_analysis"] = idx_lat

    return results


def sanitize_doc(doc: Any) -> Any:
    """
    Recursively converts MongoDB BSON types (ObjectId, datetime, date) into JSON-serializable types.
    Ensures that Flask jsonify() or json.dumps() never fails with:
    TypeError: Object of type ObjectId is not JSON serializable.
    """
    if doc is None:
        return None
    if isinstance(doc, ObjectId):
        return str(doc)
    if isinstance(doc, (datetime, date)):
        return doc.isoformat()
    if isinstance(doc, dict):
        sanitized = {}
        for k, v in doc.items():
            sanitized[k] = sanitize_doc(v)
        return sanitized
    if isinstance(doc, list):
        return [sanitize_doc(item) for item in doc]
    if isinstance(doc, tuple):
        return tuple(sanitize_doc(item) for item in doc)
    if isinstance(doc, set):
        return [sanitize_doc(item) for item in doc]
    return doc

