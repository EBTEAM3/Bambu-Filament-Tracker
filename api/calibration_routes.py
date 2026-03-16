from contextlib import contextmanager

from flask import Blueprint, current_app, jsonify, request

from core.errors import error
from models.dto import serialize_row
from models.enums import CALIBRATION_RESULT_STATUS, CALIBRATION_SCOPE_TYPES, CALIBRATION_TEST_TYPES

bp = Blueprint("calibration_routes", __name__)
SPOOL_NOT_FOUND = "Spool not found"


def tracker():
    return current_app.extensions["tracker"]


@contextmanager
def _db_conn(app_tracker):
    with app_tracker._db_lock:
        conn = app_tracker.db.get_conn()
        try:
            yield conn
        finally:
            conn.close()


def _calibration_context_from_query():
    return {
        "printer_model": request.args.get("printer_model"),
        "nozzle_diameter_mm": request.args.get("nozzle_diameter_mm", type=float),
        "plate_type": request.args.get("plate_type"),
        "layer_height_mm": request.args.get("layer_height_mm", type=float),
        "slicer_name": request.args.get("slicer_name"),
        "slicer_profile": request.args.get("slicer_profile"),
    }


@bp.route("/api/spools/<int:spool_id>/calibration", methods=["GET"])
def spool_effective_calibration(spool_id):
    """Resolve effective calibration using scope precedence in service layer."""
    context = _calibration_context_from_query()
    resolved = tracker().calibration_service.resolve_effective_calibration(spool_id, context)
    if resolved.get("error") == "spool_not_found":
        return error(SPOOL_NOT_FOUND, 404)
    return jsonify(resolved)


@bp.route("/api/spools/<int:spool_id>/tests", methods=["GET"])
def spool_tests(spool_id):
    app_tracker = tracker()
    with _db_conn(app_tracker) as conn:
        rows = app_tracker.calibration_repo.list_runs(conn, spool_id=spool_id)
        return jsonify([serialize_row(row) for row in rows])


@bp.route("/api/calibration-profiles", methods=["GET"])
def list_calibration_profiles():
    app_tracker = tracker()
    with _db_conn(app_tracker) as conn:
        rows = app_tracker.calibration_repo.list_profiles(
            conn,
            scope_type=request.args.get("scope_type"),
            scope_id=request.args.get("scope_id"),
        )
        return jsonify([serialize_row(row) for row in rows])


@bp.route("/api/calibration-profiles", methods=["POST"])
def create_calibration_profile():
    app_tracker = tracker()
    auth_error = app_tracker.require_write_auth(request)
    if auth_error:
        return auth_error
    data = request.get_json() or {}
    if data.get("scope_type") not in CALIBRATION_SCOPE_TYPES:
        return error("Invalid scope_type", 400)
    if data.get("scope_id") in (None, ""):
        return error("scope_id is required", 400)

    now = app_tracker.now_iso()
    with _db_conn(app_tracker) as conn:
        row_id = app_tracker.calibration_repo.create_profile(conn, data, now)
        conn.commit()
        row = conn.execute("SELECT * FROM calibration_profiles WHERE id = ?", (row_id,)).fetchone()
        return jsonify(serialize_row(row)), 201


@bp.route("/api/calibration-runs", methods=["GET"])
def list_calibration_runs():
    app_tracker = tracker()
    with _db_conn(app_tracker) as conn:
        rows = app_tracker.calibration_repo.list_runs(conn, spool_id=request.args.get("spool_id", type=int))
        return jsonify([serialize_row(row) for row in rows])


@bp.route("/api/calibration-runs", methods=["POST"])
def create_calibration_run():
    app_tracker = tracker()
    auth_error = app_tracker.require_write_auth(request)
    if auth_error:
        return auth_error
    data = request.get_json() or {}
    if not data.get("spool_id") or not data.get("printer_model") or not data.get("test_type"):
        return error("spool_id, printer_model and test_type are required", 400)
    if data.get("test_type") not in CALIBRATION_TEST_TYPES:
        return error("Invalid test_type", 400)
    if data.get("result_status", "pending") not in CALIBRATION_RESULT_STATUS:
        return error("Invalid result_status", 400)

    now = app_tracker.now_iso()
    with _db_conn(app_tracker) as conn:
        exists = app_tracker.spool_repo.get_spool_instance(conn, int(data.get("spool_id")))
        if not exists:
            return error(SPOOL_NOT_FOUND, 404)
        row_id = app_tracker.calibration_repo.create_run(conn, data, now)
        conn.commit()
        row = conn.execute("SELECT * FROM calibration_runs WHERE id = ?", (row_id,)).fetchone()
        return jsonify(serialize_row(row)), 201
