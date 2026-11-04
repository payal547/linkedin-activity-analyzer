"""
MongoDB Database package for LinkedIn Activity Analyzer.
"""
try:
    from backend.db.mongo import get_db, get_mongo_client, check_connection, init_indexes, sanitize_doc
except ImportError:
    from db.mongo import get_db, get_mongo_client, check_connection, init_indexes, sanitize_doc

__all__ = ["get_db", "get_mongo_client", "check_connection", "init_indexes", "sanitize_doc"]

