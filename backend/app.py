from flask import Flask, send_from_directory, request, jsonify
from dotenv import load_dotenv
import os
import sys
import time
from pathlib import Path

# Ensure backend directory is in sys.path
backend_dir = str(Path(__file__).resolve().parent)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# Initialize Persistent Logging System with Rotation & RunID tracking
try:
    from utils.logger import setup_logging, get_logger, set_current_run_id, clear_current_run_id
except ImportError:
    try:
        from backend.utils.logger import setup_logging, get_logger, set_current_run_id, clear_current_run_id
    except ImportError:
        setup_logging = None
        get_logger = None

if setup_logging:
    setup_logging()

logger = get_logger("app") if get_logger else Flask(__name__).logger

from routes.auth_routes import auth_bp
from routes.connection_routes import connection_bp
from routes.activity_routes import activity_bp
from routes.dashboard_routes import dashboard_bp

load_dotenv()

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")

app.config["SECRET_KEY"] = os.getenv("SECRET_KEY") or os.urandom(32)
# Session cookie security settings (Phase 3 - Issue 8)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
is_production = os.getenv("APP_ENV", "").lower() == "production" or os.getenv("FLASK_ENV", "").lower() == "production"
app.config["SESSION_COOKIE_SECURE"] = is_production

app.register_blueprint(auth_bp)
app.register_blueprint(connection_bp)
app.register_blueprint(activity_bp)
app.register_blueprint(dashboard_bp)

@app.before_request
def log_request_start():
    request._start_time = time.monotonic()
    req_run_id = request.headers.get("X-Run-ID") or request.args.get("run_id")
    if req_run_id and set_current_run_id:
        set_current_run_id(req_run_id)

@app.after_request
def log_request_end(response):
    start = getattr(request, "_start_time", None)
    duration_ms = round((time.monotonic() - start) * 1000, 1) if start else 0
    if request.path.startswith("/api/"):
        logger.info("%s %s -> %s (%sms)", request.method, request.path, response.status_code, duration_ms)
    if clear_current_run_id:
        clear_current_run_id()
    return response

# Safe MongoDB Local Startup Health Check and Index Initialization
try:
    from db.mongo import check_connection, init_indexes
except ImportError:
    try:
        from backend.db.mongo import check_connection, init_indexes
    except ImportError:
        check_connection = None
        init_indexes = None

if check_connection and init_indexes:
    try:
        is_connected, msg = check_connection()
        if is_connected:
            app.logger.info("MongoDB startup check: %s", msg)
            init_indexes()
            app.logger.info("MongoDB indexes verified successfully.")
        else:
            app.logger.warning("MongoDB unavailable at startup (%s). Using JSON storage fallback.", msg)
    except Exception as e:
        app.logger.warning("MongoDB initialization skipped at startup: %s", e)


# Centralized Error Handlers (Phase 4 - Issue 12)
@app.errorhandler(400)
def handle_400(e):
    if request.path.startswith("/api/") or "application/json" in request.headers.get("Accept", ""):
        desc = getattr(e, "description", "Bad request")
        return jsonify({"error": desc, "status_code": 400}), 400
    return e


@app.errorhandler(404)
def handle_404(e):
    if request.path.startswith("/api/") or "application/json" in request.headers.get("Accept", ""):
        desc = getattr(e, "description", "Resource not found")
        return jsonify({"error": desc, "status_code": 404}), 404
    return e


@app.errorhandler(405)
def handle_405(e):
    if request.path.startswith("/api/") or "application/json" in request.headers.get("Accept", ""):
        desc = getattr(e, "description", "Method not allowed")
        return jsonify({"error": desc, "status_code": 405}), 405
    return e


@app.errorhandler(500)
def handle_500(e):
    if request.path.startswith("/api/") or "application/json" in request.headers.get("Accept", ""):
        desc = getattr(e, "description", "Internal server error")
        return jsonify({"error": desc, "status_code": 500}), 500
    return e


@app.route("/")
def home():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/login")
def login():
    return send_from_directory(FRONTEND_DIR / "pages", "login.html")

@app.route("/connections")
def connections():
    return send_from_directory(FRONTEND_DIR / "pages", "connections.html")

@app.route("/activity-filter")
def activity_filter():
    return send_from_directory(FRONTEND_DIR / "pages", "activity-filter.html")

@app.route("/dashboard")
def dashboard():
    return send_from_directory(FRONTEND_DIR / "pages", "dashboard.html")

@app.route("/friend")
def friend():
    return send_from_directory(FRONTEND_DIR / "pages", "friend.html")

@app.route("/privacy-policy")
def privacy_policy():
    return send_from_directory(FRONTEND_DIR / "pages", "privacy-policy.html")

if __name__ == "__main__":
    app.run(
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", 5000)),
        debug=True,
        reloader_type="stat"
    )
