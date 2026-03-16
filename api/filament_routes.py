from contextlib import contextmanager
from uuid import uuid4

from flask import Blueprint, current_app, jsonify, request

from core.errors import error
from models.dto import serialize_row, serialize_spool_instance
from models.validators import parse_bool, require_fields

bp = Blueprint("filament_routes", __name__)
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
VALID_SPOOL_SOURCES = {"rfid", "manual", "imported"}


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


def _extract_spool_instance_updates(data: dict):
    return {k: v for k, v in data.items() if k in SPOOL_INSTANCE_ALLOWED_UPDATE_FIELDS}


def _build_spool_instance_detail_payload(app_tracker, conn, spool_id: int):
    """Compose detail payload preserving current response contract."""
    spool = app_tracker.spool_repo.get_spool_instance(conn, spool_id)
    if not spool:
        return error(SPOOL_NOT_FOUND, 404)

    payload = serialize_spool_instance(spool)
    payload["calibration_effective"] = app_tracker.calibration_service.resolve_effective_calibration(spool_id)
    payload["recent_tests"] = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM calibration_runs WHERE spool_id = ? ORDER BY test_date DESC LIMIT 20",
            (spool_id,),
        ).fetchall()
    ]
    payload["presence_history"] = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM spool_presence_history WHERE spool_id = ? ORDER BY event_at DESC LIMIT 50",
            (spool_id,),
        ).fetchall()
    ]
    return jsonify(payload)


@bp.route("/api/filament-products", methods=["GET"])
def list_filament_products():
    app_tracker = tracker()
    with _db_conn(app_tracker) as conn:
        rows = app_tracker.filament_product_repo.list(
            conn,
            brand=request.args.get("brand"),
            material=request.args.get("material"),
            color=request.args.get("color"),
        )
        return jsonify([serialize_row(row) for row in rows])


@bp.route("/api/filament-products", methods=["POST"])
def create_filament_product():
    app_tracker = tracker()
    auth_error = app_tracker.require_write_auth(request)
    if auth_error:
        return auth_error
    data = request.get_json() or {}
    missing = require_fields(data, ["brand", "material", "color", "nominal_weight_g", "filament_diameter_mm"])
    if missing:
        return error(f"Missing fields: {', '.join(missing)}", 400)

    now = app_tracker.now_iso()
    with _db_conn(app_tracker) as conn:
        row_id = app_tracker.filament_product_repo.create(conn, data, now)
        conn.commit()
        row = conn.execute("SELECT * FROM filament_products WHERE id = ?", (row_id,)).fetchone()
        return jsonify(serialize_row(row)), 201


@bp.route("/api/spool-instances", methods=["GET"])
def list_spool_instances():
    app_tracker = tracker()
    archived = request.args.get("archived")
    has_rfid = request.args.get("is_rfid")

    with _db_conn(app_tracker) as conn:
        rows = app_tracker.spool_repo.list_spool_instances(
            conn,
            archived=parse_bool(archived) if archived is not None else None,
            material=request.args.get("material"),
            has_rfid=parse_bool(has_rfid) if has_rfid is not None else None,
        )
        return jsonify([serialize_spool_instance(row, include_product=True) for row in rows])


@bp.route("/api/spool-instances", methods=["POST"])
def create_spool_instance():
    app_tracker = tracker()
    auth_error = app_tracker.require_write_auth(request)
    if auth_error:
        return auth_error
    data = request.get_json() or {}
    source = data.get("source", "manual")
    if source not in VALID_SPOOL_SOURCES:
        return error("Invalid source", 400)
    data["source"] = source
    data["spool_uuid"] = data.get("spool_uuid") or str(uuid4())

    now = app_tracker.now_iso()
    with _db_conn(app_tracker) as conn:
        row_id = app_tracker.spool_repo.create_spool_instance(conn, data, now)
        conn.commit()
        row = app_tracker.spool_repo.get_spool_instance(conn, row_id)
        return jsonify(serialize_spool_instance(row)), 201


@bp.route("/api/spools/<int:spool_id>", methods=["GET"])
def spool_instance_detail(spool_id):
    app_tracker = tracker()
    with _db_conn(app_tracker) as conn:
        return _build_spool_instance_detail_payload(app_tracker, conn, spool_id)


@bp.route("/api/spools/<int:spool_id>", methods=["PATCH"])
def spool_instance_update(spool_id):
    app_tracker = tracker()
    auth_error = app_tracker.require_write_auth(request)
    if auth_error:
        return auth_error
    data = request.get_json() or {}
    updates = _extract_spool_instance_updates(data)
    if not updates:
        return error("No valid fields to update", 400)
    if "source" in updates and updates["source"] not in VALID_SPOOL_SOURCES:
        return error("Invalid source", 400)
    updates["updated_at"] = app_tracker.now_iso()

    with _db_conn(app_tracker) as conn:
        app_tracker.spool_repo.update_spool_instance(conn, spool_id, updates)
        conn.commit()
        row = app_tracker.spool_repo.get_spool_instance(conn, spool_id)
        if not row:
            return error(SPOOL_NOT_FOUND, 404)
        return jsonify(serialize_spool_instance(row))
