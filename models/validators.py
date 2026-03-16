from typing import Any, Dict, Iterable, List


def parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "on"}


def require_fields(payload: Dict[str, Any], fields: Iterable[str]) -> List[str]:
    return [field for field in fields if payload.get(field) in (None, "")]
