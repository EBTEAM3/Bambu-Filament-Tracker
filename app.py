import argparse
import hmac
import logging
import os
import signal
import socket
import sys
import threading
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, redirect, request, session, url_for

from api.auth_routes import bp as auth_bp
from api.calibration_routes import bp as calibration_bp
from api.dashboard_routes import bp as dashboard_bp
from api.export_routes import bp as export_bp
from api.filament_routes import bp as filament_bp
from api.legacy_routes import bp as legacy_bp
from core.config import build_tracker_config
from core.db import DBManager
from core.security import set_security_headers, validate_csrf_token
from repositories.calibration_repository import CalibrationRepository
from repositories.filament_product_repository import FilamentProductRepository
from repositories.migration_repository import MigrationRepository
from repositories.spool_repository import SpoolRepository
from repositories.user_repository import UserRepository
from services.auth_service import AuthService
from services.calibration_service import CalibrationService
from services.dashboard_service import DashboardService
from services.export_service import ExportService
from services.job_history_service import JobHistoryService
from services.spool_sync_service import SpoolSyncService

logger = logging.getLogger(__name__)


class FilamentTracker:
    def __init__(
        self,
        bridge=None,
        port=5000,
        host="0.0.0.0",
        low_alert_grams=150,
        low_alert_fcm=True,
        test_mode=False,
        api_key="",
    ):
        self.bridge = bridge
        self.port = port
        self.host = host
        self.low_alert_grams = low_alert_grams
        self.low_alert_fcm = low_alert_fcm
        self.test_mode = test_mode
        self.api_key = api_key
        self._active_alerts = []
        self._ams_info = {}
        self._tray_now = -1
        self._ams_drying_mode = None
        self._last_ams_update_at = None
        self._db_lock = threading.Lock()
        self.logger = logger

        self.server_dir = os.path.dirname(os.path.abspath(__file__))
        self.config = build_tracker_config(self.server_dir, test_mode=self.test_mode)
        self.db = DBManager(self.config.db_path)
        self.auth_enabled = self.config.auth_enabled
        self.secret_key = self.config.secret_key
        self.timezone = self.config.timezone
        self._csrf_header_name = self.config.csrf_header_name

        self.user_repo = UserRepository(self.db)
        self.filament_product_repo = FilamentProductRepository(self.db)
        self.spool_repo = SpoolRepository(self.db)
        self.calibration_repo = CalibrationRepository(self.db)
        self.migration_repo = MigrationRepository(self.db)

        self.auth_service = AuthService(self)
        self.spool_sync_service = SpoolSyncService(self)
        self.calibration_service = CalibrationService(self)
        self.dashboard_service = DashboardService(self)
        self.export_service = ExportService(self)
        self.job_history_service = JobHistoryService(self)

        if not self.test_mode:
            self.cleanup_test_db()

        self._init_db()
        self._app = self._create_flask_app()

        if self.test_mode:
            self.spool_sync_service.generate_test_data()

    def now_iso(self) -> str:
        return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

    def _now_iso(self) -> str:
        return self.now_iso()

    def _get_conn(self):
        return self.db.get_conn()

    def cleanup_test_db(self):
        try:
            if os.path.exists(self.config.test_db_path):
                os.remove(self.config.test_db_path)
                self.logger.info("Filament Tracker: test database cleaned up")
        except Exception as exc:
            self.logger.error(f"Failed to clean up test database: {exc}")

    def _cleanup_test_db(self):
        self.cleanup_test_db()

    def _apply_schema_migrations(self, conn):
        migrations = [("20260316_0001_domain_foundation", self._migration_domain_foundation)]
        for version, migration_fn in migrations:
            if self.migration_repo.is_applied(conn, version):
                continue
            migration_fn(conn)
            self.migration_repo.mark_applied(conn, version)
            conn.commit()

    def _migration_domain_foundation(self, conn):
        self.migration_repo.migrate_domain_foundation(conn, self.filament_product_repo)

    def _init_db(self):
        self.db.init_schema()
        with self._db_lock:
            conn = self.db.get_conn()
            try:
                self._apply_schema_migrations(conn)
                self.auth_service.ensure_admin_user(conn)
                conn.commit()
            finally:
                conn.close()

    def _is_public_path(self, path: str) -> bool:
        public_paths = {"/login", "/api/auth/login"}
        if path.startswith("/static/"):
            return True
        if path in public_paths:
            return True
        if path.startswith("/api/auth/"):
            return True
        return False

    def require_write_auth(self, request_obj):
        """Validate write authorization.

        Compatibility behavior:
        - If auth is enabled, session + CSRF are mandatory for browser writes.
        - API key remains accepted for automation/non-browser clients.
        - With auth enabled, valid session is sufficient even without API key.
        """
        provided = request_obj.headers.get("X-API-Key", "")
        if self.api_key and provided and hmac.compare_digest(provided, self.api_key):
            return None

        if self.auth_enabled:
            if not self.auth_service.is_authenticated():
                return jsonify({"error": "Authentication required"}), 401
            token = request_obj.headers.get(self._csrf_header_name, "")
            if not validate_csrf_token(session, token):
                return jsonify({"error": "Invalid or missing CSRF token"}), 403
            return None

        if self.api_key:
            return jsonify({"error": "Invalid or missing API key"}), 403
        return None

    def _create_flask_app(self):
        """Build Flask app and register modular blueprints.

        Security middleware is centralized here to keep route modules focused on
        business behavior and compatibility mappings.
        """
        template_dir = os.path.join(self.server_dir, "templates")
        static_dir = os.path.join(self.server_dir, "static")
        app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)
        app.config["JSONIFY_PRETTYPRINT_REGULAR"] = False
        app.secret_key = self.secret_key
        app.config.update(
            SESSION_COOKIE_HTTPONLY=True,
            SESSION_COOKIE_SAMESITE="Lax",
            SESSION_COOKIE_SECURE=False,
            PERMANENT_SESSION_LIFETIME=timedelta(days=14),
        )
        app.extensions["tracker"] = self

        @app.after_request
        def _after(response):
            return set_security_headers(response)

        @app.before_request
        def _before_request():
            if not self.auth_enabled:
                return None
            path = request.path
            if self._is_public_path(path):
                return None
            if self.auth_service.is_authenticated():
                return None
            if path.startswith("/api/"):
                return jsonify({"error": "Authentication required"}), 401
            return redirect(url_for("auth_routes.login_page"))

        app.register_blueprint(auth_bp)
        app.register_blueprint(legacy_bp)
        app.register_blueprint(filament_bp)
        app.register_blueprint(calibration_bp)
        app.register_blueprint(dashboard_bp)
        app.register_blueprint(export_bp)
        return app

    def resolve_effective_calibration(self, spool_id: int, printer_context=None):
        return self.calibration_service.resolve_effective_calibration(spool_id, printer_context)

    def update_ams_data(self, ams_payload: dict):
        self.spool_sync_service.update_ams_data(ams_payload)

    def update_print_data(self, print_data: dict):
        self.job_history_service.record_print_update(print_data)

    def _refresh_alerts(self):
        self.spool_sync_service.refresh_alerts()

    def _generate_test_data(self):
        self.spool_sync_service.generate_test_data()

    def start(self):
        thread = threading.Thread(target=self._run_flask, name="filament-tracker-web", daemon=True)
        thread.start()

    def _get_local_ip(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            sock.close()
            return ip
        except Exception:
            return "localhost"

    def _run_flask(self):
        try:
            ip = self._get_local_ip()
            self.logger.info(f"Filament tracker web server started on http://{ip}:{self.port}")
            self._app.run(host=self.host, port=self.port, debug=False, use_reloader=False)
        except Exception as exc:
            self.logger.error(f"Filament tracker web server failed: {exc}")


def main():
    args = _parse_args()
    server_dir = os.path.dirname(os.path.abspath(__file__))
    _configure_logging(server_dir)

    if args.test:
        _run_test_mode(args)
        return

    _run_live_mode(args, server_dir)


def _parse_args():
    parser = argparse.ArgumentParser(description="Filament Tracker")
    parser.add_argument("--test", action="store_true", help="Run in test mode with mock data (no MQTT)")
    parser.add_argument("--port", type=int, default=None, help="Web server port (default: 5000)")
    parser.add_argument("--host", type=str, default=None, help="Web server host (default: 0.0.0.0)")
    return parser.parse_args()


def _configure_logging(server_dir: str):
    data_dir = os.environ.get("FILAMENT_TRACKER_DATA_DIR", server_dir)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(os.path.join(data_dir, "filament_tracker.log")),
        ],
    )


def _run_test_mode(args):
    api_key = os.environ.get("FILAMENT_TRACKER_API_KEY", "")
    tracker = FilamentTracker(
        bridge=None,
        port=args.port or 5000,
        host=args.host or "0.0.0.0",
        test_mode=True,
        api_key=api_key,
    )
    ip = tracker._get_local_ip()
    print("=" * 50)
    print("  Filament Tracker - TEST MODE")
    print(f"  http://{ip}:{args.port or 5000}")
    print("  Press Ctrl+C to stop")
    print("=" * 50)

    def cleanup_and_exit(sig=None, frame=None):
        tracker.cleanup_test_db()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, cleanup_and_exit)
    signal.signal(signal.SIGTERM, cleanup_and_exit)
    try:
        tracker._app.run(host=args.host or "0.0.0.0", port=args.port or 5000, debug=False, use_reloader=False)
    finally:
        tracker.cleanup_test_db()


def _resolve_config_module(server_dir: str):
    """Resolve user config.py from mounted config directories.

    Lookup order is preserved for Docker compatibility:
    1) local ./config
    2) mounted /app/config
    """
    config_dirs = [
        os.path.join(server_dir, "config"),
        "/app/config",
    ]
    for config_dir in config_dirs:
        if os.path.isfile(os.path.join(config_dir, "config.py")):
            sys.path.insert(0, config_dir)
            break

    try:
        import config as cfg

        return cfg
    except ImportError:
        print("ERROR: config.py not found!")
        print("Copy config.example.py to config.py and fill in your values:")
        print("  cp config.example.py config.py")
        print("  (Docker users: mount a config directory to /app/config/)")
        sys.exit(1)


def _load_live_runtime_options(cfg, args):
    mqtt_server = getattr(cfg, "BAMBU_MQTT_SERVER", "us.mqtt.bambulab.com")
    mqtt_port = getattr(cfg, "BAMBU_MQTT_PORT", 8883)
    user_id = getattr(cfg, "BAMBU_USER_ID", "")
    access_token = getattr(cfg, "BAMBU_ACCESS_TOKEN", "")
    printer_serial = getattr(cfg, "BAMBU_PRINTER_SERIAL", "")

    if not user_id or not access_token or not printer_serial:
        print("ERROR: Missing required config: BAMBU_USER_ID, BAMBU_ACCESS_TOKEN, BAMBU_PRINTER_SERIAL")
        sys.exit(1)

    return {
        "mqtt_server": mqtt_server,
        "mqtt_port": mqtt_port,
        "user_id": user_id,
        "access_token": access_token,
        "printer_serial": printer_serial,
        "port": args.port or getattr(cfg, "FILAMENT_TRACKER_PORT", 5000),
        "host": args.host or getattr(cfg, "FILAMENT_TRACKER_HOST", "0.0.0.0"),
        "low_alert_grams": getattr(cfg, "FILAMENT_LOW_ALERT_GRAMS", 150),
        "low_alert_fcm": getattr(cfg, "FILAMENT_LOW_ALERT_FCM", False),
        "api_key": os.environ.get("FILAMENT_TRACKER_API_KEY", "")
        or getattr(cfg, "FILAMENT_TRACKER_API_KEY", ""),
        "enable_notifications": getattr(cfg, "ENABLE_NOTIFICATIONS", False),
    }


def _attach_optional_notification_bridge(tracker, mqtt_client, server_dir: str, enable_notifications: bool):
    if not enable_notifications:
        return

    nowbar_path = os.path.normpath(os.path.join(server_dir, "..", "Bambu-Progress-Notification", "server"))
    if not os.path.isdir(nowbar_path):
        logger.warning(f"ENABLE_NOTIFICATIONS is True but Bambu-Progress-Notification not found: {nowbar_path}")
        return

    sys.path.insert(0, nowbar_path)
    try:
        from bambu_fcm_bridge import BambuFCMBridge

        notification_bridge = BambuFCMBridge(mqtt_client)
        tracker.bridge = notification_bridge
        logger.info(f"Notification service loaded from {nowbar_path}")
    except ImportError as exc:
        logger.error(f"Failed to import notification service: {exc}")
        logger.error("Make sure firebase-admin is installed: pip install firebase-admin")


def _run_live_mode(args, server_dir: str):
    from bambu_mqtt import BambuMQTTClient

    cfg = _resolve_config_module(server_dir)
    options = _load_live_runtime_options(cfg, args)

    mqtt_client = BambuMQTTClient(
        options["mqtt_server"],
        options["mqtt_port"],
        options["user_id"],
        options["access_token"],
        options["printer_serial"],
    )
    tracker = FilamentTracker(
        bridge=mqtt_client,
        port=options["port"],
        host=options["host"],
        low_alert_grams=options["low_alert_grams"],
        low_alert_fcm=options["low_alert_fcm"],
        api_key=options["api_key"],
    )
    mqtt_client.on_ams_data(tracker.update_ams_data)
    mqtt_client.on_print_update(tracker.update_print_data)

    _attach_optional_notification_bridge(
        tracker=tracker,
        mqtt_client=mqtt_client,
        server_dir=server_dir,
        enable_notifications=options["enable_notifications"],
    )

    tracker.start()

    logger.info("=" * 50)
    logger.info("Filament Tracker Starting")
    logger.info(f"Printer: {options['printer_serial']}")
    logger.info("=" * 50)

    try:
        mqtt_client.run()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        mqtt_client.disconnect()
    except Exception as exc:
        logger.error(f"Fatal error: {exc}")
        raise


if __name__ == "__main__":
    main()
