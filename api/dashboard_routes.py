from flask import Blueprint, current_app, jsonify

bp = Blueprint("dashboard_routes", __name__)


def tracker():
    return current_app.extensions["tracker"]


@bp.route("/api/dashboard", methods=["GET"])
def dashboard():
    return jsonify(tracker().dashboard_service.dashboard_payload())


@bp.route("/api/settings", methods=["GET"])
def settings_get():
    app_tracker = tracker()
    return jsonify(
        {
            "auth_enabled": app_tracker.auth_enabled,
            "timezone": app_tracker.timezone,
            "low_alert_grams": app_tracker.low_alert_grams,
            "api_key_enabled": bool(app_tracker.api_key),
        }
    )
