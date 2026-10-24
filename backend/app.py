from flask import Flask, send_from_directory
from dotenv import load_dotenv
import os
from pathlib import Path

from routes.auth_routes import auth_bp
from routes.connection_routes import connection_bp
from routes.activity_routes import activity_bp
from routes.dashboard_routes import dashboard_bp

load_dotenv()

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")

app.config["SECRET_KEY"] = os.getenv("SECRET_KEY") or os.urandom(32)

app.register_blueprint(auth_bp)
app.register_blueprint(connection_bp)
app.register_blueprint(activity_bp)
app.register_blueprint(dashboard_bp)


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
        debug=True
    )
