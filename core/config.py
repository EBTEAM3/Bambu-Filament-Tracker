import os
import secrets
from dataclasses import dataclass

from models.enums import AUTH_ENABLED_ENV


@dataclass
class TrackerConfig:
    db_path: str
    test_db_path: str
    data_dir: str
    auth_enabled: bool
    secret_key: str
    timezone: str
    csrf_header_name: str


def build_tracker_config(server_dir: str, test_mode: bool) -> TrackerConfig:
    data_dir = os.environ.get("FILAMENT_TRACKER_DATA_DIR", server_dir)
    db_path = os.path.join(data_dir, "filament_tracker.db")
    test_db_path = os.path.join(data_dir, "filament_tracker_test.db")
    auth_enabled = os.environ.get(AUTH_ENABLED_ENV, "1").lower() not in {"0", "false", "no"}
    secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
    timezone = os.environ.get("TIMEZONE", "UTC")
    return TrackerConfig(
        db_path=test_db_path if test_mode else db_path,
        test_db_path=test_db_path,
        data_dir=data_dir,
        auth_enabled=auth_enabled,
        secret_key=secret_key,
        timezone=timezone,
        csrf_header_name="X-CSRF-Token",
    )
