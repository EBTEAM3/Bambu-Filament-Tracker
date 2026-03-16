from contextlib import contextmanager

from flask import Blueprint, current_app, jsonify, render_template, request

from core.errors import error
from models.dto import serialize_legacy_spool, serialize_spool_instance

bp = Blueprint("legacy_routes", __name__)
SPOOL_NOT_FOUND = "Spool not found"
SPOOL_INSTANCE_ALLOWED_UPDATE_FIELDS = {
    "filament_product_id",
    "rfid_uid",
    "tray_uuid",
    "external_device_slot",
    "batch_code",
    "purchase_date",
    "opened_date",
    "drying_last_date",
    "drying_hours_last",
    "humidity_state",
    "remaining_weight_g",
    "remaining_percent",
    "tare_weight_g",
    "weight_offset_g",
    "custom_name",
    "notes",
    "archived",
    "source",
}
LEGACY_SPOOL_ALLOWED_UPDATE_FIELDS = {"custom_name", "notes", "remain_percent", "weight_offset"}


def tracker():
    return current_app.extensions["tracker"]


@contextmanager
def _db_conn(app_tracker):
    """Provide consistent DB lock/connection lifecycle for route handlers."""
    with app_tracker._db_lock:
        conn = app_tracker.db.get_conn()
        try:
            yield conn
        finally:
            conn.close()


def _is_numeric_spool_reference(tray_uuid: str) -> bool:
    """Legacy compatibility rule: numeric `/api/spools/<id>` targets new model."""
    return tray_uuid.isdigit()


def _load_spool_instance_detail(app_tracker, conn, spool_id: int):
    spool = app_tracker.spool_repo.get_spool_instance(conn, spool_id)
    if not spool:
        return error(SPOOL_NOT_FOUND, 404)

    payload = serialize_spool_instance(spool)
    payload["calibration_effective"] = app_tracker.calibration_service.resolve_effective_calibration(spool_id)
    payload["tests"] = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM calibration_runs WHERE spool_id = ? ORDER BY test_date DESC",
            (spool_id,),
        ).fetchall()
    ]
    return jsonify(payload)


def _load_legacy_spool_detail(app_tracker, conn, tray_uuid: str):
    row = app_tracker.spool_repo.get_legacy_spool(conn, tray_uuid)
    if not row:
        return error(SPOOL_NOT_FOUND, 404)
    spool = serialize_legacy_spool(row, app_tracker.low_alert_grams)
    spool["history"] = [dict(h) for h in app_tracker.spool_repo.get_legacy_history(conn, tray_uuid)]
    return jsonify(spool)


def _extract_updates(data: dict, allowed_fields: set):
    return {k: v for k, v in data.items() if k in allowed_fields}


@bp.route("/", methods=["GET"])
def index():
    app_tracker = tracker()
    return render_template(
        "index.html",
        test_mode=app_tracker.test_mode,
        api_key=app_tracker.api_key,
        auth_enabled=app_tracker.auth_enabled,
    )


@bp.route("/api/spools", methods=["GET"])
def api_spools():
    app_tracker = tracker()
    with _db_conn(app_tracker) as conn:
        rows = app_tracker.spool_repo.list_legacy_spools(conn)
        return jsonify([serialize_legacy_spool(row, app_tracker.low_alert_grams) for row in rows])


@bp.route("/api/spools/active", methods=["GET"])
def api_spools_active():
    app_tracker = tracker()
    with _db_conn(app_tracker) as conn:
        rows = app_tracker.spool_repo.list_legacy_active_spools(conn)
        return jsonify([serialize_legacy_spool(row, app_tracker.low_alert_grams) for row in rows])


@bp.route("/api/spools/<tray_uuid>", methods=["GET"])
def api_spool_detail(tray_uuid):
    app_tracker = tracker()
    with _db_conn(app_tracker) as conn:
        if _is_numeric_spool_reference(tray_uuid):
            return _load_spool_instance_detail(app_tracker, conn, int(tray_uuid))
        return _load_legacy_spool_detail(app_tracker, conn, tray_uuid)


@bp.route("/api/spools/<tray_uuid>/history", methods=["GET"])
def api_spool_history(tray_uuid):
    app_tracker = tracker()
    with _db_conn(app_tracker) as conn:
        if _is_numeric_spool_reference(tray_uuid):
            rows = conn.execute(
                "SELECT * FROM calibration_runs WHERE spool_id = ? ORDER BY test_date DESC",
                (int(tray_uuid),),
            ).fetchall()
        else:
            rows = app_tracker.spool_repo.get_legacy_history(conn, tray_uuid)
        return jsonify([dict(row) for row in rows])


@bp.route("/api/spools/<tray_uuid>", methods=["PATCH"])
def api_spool_update(tray_uuid):
    app_tracker = tracker()
    auth_error = app_tracker.require_write_auth(request)
    if auth_error:
        return auth_error

    data = request.get_json() or {}
    if _is_numeric_spool_reference(tray_uuid):
        updates = _extract_updates(data, SPOOL_INSTANCE_ALLOWED_UPDATE_FIELDS)
        if not updates:
            return error("No valid fields to update", 400)
        updates["updated_at"] = app_tracker.now_iso()
        with _db_conn(app_tracker) as conn:
            app_tracker.spool_repo.update_spool_instance(conn, int(tray_uuid), updates)
            conn.commit()
            row = app_tracker.spool_repo.get_spool_instance(conn, int(tray_uuid))
            if not row:
                return error("Not found", 404)
            return jsonify(serialize_spool_instance(row))

    updates = _extract_updates(data, LEGACY_SPOOL_ALLOWED_UPDATE_FIELDS)
    if not updates:
        return error("No valid fields to update", 400)

    with _db_conn(app_tracker) as conn:
        app_tracker.spool_repo.update_legacy_spool(conn, tray_uuid, updates)
        conn.commit()
        row = app_tracker.spool_repo.get_legacy_spool(conn, tray_uuid)
        if not row:
            return error("Not found", 404)
        return jsonify(serialize_legacy_spool(row, app_tracker.low_alert_grams))


@bp.route("/api/spools/<tray_uuid>", methods=["DELETE"])
def api_spool_delete(tray_uuid):
    app_tracker = tracker()
    auth_error = app_tracker.require_write_auth(request)
    if auth_error:
        return auth_error

    with _db_conn(app_tracker) as conn:
        if _is_numeric_spool_reference(tray_uuid):
            if not app_tracker.spool_repo.get_spool_instance(conn, int(tray_uuid)):
                return error(SPOOL_NOT_FOUND, 404)
            app_tracker.spool_repo.delete_spool_instance(conn, int(tray_uuid))
        else:
            if not app_tracker.spool_repo.get_legacy_spool(conn, tray_uuid):
                return error(SPOOL_NOT_FOUND, 404)
            app_tracker.spool_repo.delete_legacy_spool(conn, tray_uuid)
        conn.commit()
    return jsonify({"ok": True})


@bp.route("/api/status", methods=["GET"])
def api_status():
    return jsonify(tracker().dashboard_service.status_payload())


@bp.route("/api/alerts", methods=["GET"])
def api_alerts():
    return jsonify(tracker()._active_alerts)


@bp.route("/api/alerts/<tray_uuid>", methods=["DELETE"])
def api_alert_dismiss(tray_uuid):
    app_tracker = tracker()
    auth_error = app_tracker.require_write_auth(request)
    if auth_error:
        return auth_error
    with _db_conn(app_tracker) as conn:
        conn.execute("UPDATE spools SET low_alert_sent = 0 WHERE tray_uuid = ?", (tray_uuid,))
        conn.commit()
    app_tracker._active_alerts = [a for a in app_tracker._active_alerts if a.get("tray_uuid") != tray_uuid]
    return jsonify({"ok": True})


@bp.route("/api/settings/alert_threshold", methods=["GET"])
def api_get_threshold():
    return jsonify({"alert_threshold_grams": tracker().low_alert_grams})


@bp.route("/api/settings/alert_threshold", methods=["POST"])
def api_set_threshold():
    app_tracker = tracker()
    auth_error = app_tracker.require_write_auth(request)
    if auth_error:
        return auth_error
    data = request.get_json() or {}
    if "alert_threshold_grams" not in data:
        return error("Missing alert_threshold_grams", 400)
    try:
        val = int(data["alert_threshold_grams"])
    except (TypeError, ValueError):
        return error("Must be an integer", 400)
    app_tracker.low_alert_grams = max(0, val)
    app_tracker.spool_sync_service.refresh_alerts()
    return jsonify({"alert_threshold_grams": app_tracker.low_alert_grams})
