from flask import Blueprint, redirect

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/auth/linkedin")
def linkedin_login():
    return redirect("/connections")