from flask import Blueprint, current_app, jsonify

bp = Blueprint("export_routes", __name__)


def tracker():
    return current_app.extensions["tracker"]


@bp.route("/api/export/inventory", methods=["GET"])
def export_inventory():
    return jsonify(tracker().export_service.export_inventory())


@bp.route("/api/export/calibrations", methods=["GET"])
def export_calibrations():
    return jsonify(tracker().export_service.export_calibrations())
