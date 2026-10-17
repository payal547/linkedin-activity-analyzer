from flask import Flask, send_from_directory
from dotenv import load_dotenv
import os

from routes.auth_routes import auth_bp

load_dotenv()

app = Flask(__name__, static_folder="../frontend", static_url_path="")

app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")

app.register_blueprint(auth_bp)


@app.route("/")
def home():
    return send_from_directory("../frontend", "index.html")


@app.route("/login")
def login():
    return send_from_directory("../frontend/pages", "login.html")

@app.route("/connections")
def connections():
    return send_from_directory("../frontend/pages", "connections.html")

@app.route("/activity-filter")
def activity_filter():
    return send_from_directory("../frontend/pages", "activity-filter.html")

@app.route("/dashboard")
def dashboard():
    return send_from_directory("../frontend/pages", "dashboard.html")
@app.route("/privacy-policy")
def privacy_policy():
    return send_from_directory("../frontend/pages", "privacy-policy.html")
if __name__ == "__main__":
    app.run(
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", 5000)),
        debug=True
    )