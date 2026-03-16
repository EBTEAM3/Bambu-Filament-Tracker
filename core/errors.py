from flask import jsonify


def error(message: str, status: int):
    return jsonify({"error": message}), status
