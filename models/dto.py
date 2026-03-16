from typing import Any, Dict


def serialize_row(row) -> Dict[str, Any]:
    return dict(row) if row is not None else {}


def serialize_spool_instance(row, include_product: bool = False) -> Dict[str, Any]:
    payload = dict(row)
    payload["is_rfid"] = bool(payload.get("is_rfid", 0))
    payload["archived"] = bool(payload.get("archived", 0))
    if include_product:
        for key in list(payload.keys()):
            if key.startswith("product_") and payload[key] is None:
                payload.pop(key)
    return payload


def serialize_legacy_spool(row, low_alert_grams: int) -> Dict[str, Any]:
    payload = dict(row)
    weight = payload.get("spool_weight", 250)
    remain = payload.get("remain_percent", 0)
    offset = payload.get("weight_offset", 0) or 0
    payload["remaining_grams"] = max(0, int((remain / 100) * weight) + offset)
    if offset and weight:
        payload["remain_percent"] = max(0, min(100, round(payload["remaining_grams"] / weight * 100)))
    payload["is_low"] = low_alert_grams > 0 and payload["remaining_grams"] < low_alert_grams
    return payload
