from flask import Blueprint, current_app, jsonify, redirect, render_template, request, session, url_for

from core.errors import error
from core.security import generate_csrf_token

bp = Blueprint("auth_routes", __name__)


def tracker():
    return current_app.extensions["tracker"]


@bp.route("/login", methods=["GET"])
def login_page():
    if tracker().auth_service.is_authenticated():
        return redirect(url_for("legacy_routes.index"))
    return render_template("login.html")


@bp.route("/logout", methods=["GET"])
def logout_page():
    session.clear()
    return redirect(url_for("auth_routes.login_page"))


@bp.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    data = request.get_json() or {}
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    if not username or not password:
        return error("username and password are required", 400)
    logged = tracker().auth_service.login(username, password)
    if not logged:
        return error("Invalid credentials", 401)
    return jsonify({
        "ok": True,
        "user": {"id": logged["id"], "username": logged["username"], "role": logged["role"]},
        "csrf_token": logged["csrf_token"],
    })


@bp.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    if tracker().auth_enabled:
        if not tracker().auth_service.is_authenticated():
            return error("Authentication required", 401)
        csrf_header = request.headers.get("X-CSRF-Token")
        csrf_session = session.get("csrf_token")
        if not csrf_header or not csrf_session or csrf_header != csrf_session:
            return error("Invalid CSRF token", 403)
    session.clear()
    return jsonify({"ok": True})


@bp.route("/api/auth/me", methods=["GET"])
def api_auth_me():
    if not tracker().auth_enabled:
        return jsonify({"authenticated": True, "auth_enabled": False})
    user = tracker().auth_service.current_user()
    if not user:
        return jsonify({"authenticated": False, "auth_enabled": True})
    csrf = session.get("csrf_token") or generate_csrf_token(session)
    return jsonify(
        {
            "authenticated": True,
            "auth_enabled": True,
            "user": user,
            "csrf_token": csrf,
        }
    )
