import os
import secrets
from datetime import datetime, timezone

from flask import session
from werkzeug.security import check_password_hash, generate_password_hash

from core.security import generate_csrf_token
from models.enums import ADMIN_ROLE


class AuthService:
    def __init__(self, tracker):
        self.tracker = tracker

    def current_user(self):
        user_id = session.get("user_id")
        if not user_id:
            return None
        with self.tracker._db_lock:
            conn = self.tracker.db.get_conn()
            try:
                row = self.tracker.user_repo.get_by_id(conn, user_id)
                return dict(row) if row else None
            finally:
                conn.close()

    def is_authenticated(self) -> bool:
        if not self.tracker.auth_enabled:
            return True
        return self.current_user() is not None

    def ensure_admin_user(self, conn):
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        admin_username = os.environ.get("ADMIN_USERNAME", "admin").strip() or "admin"
        admin_password = os.environ.get("ADMIN_PASSWORD")
        admin_hash = os.environ.get("ADMIN_PASSWORD_HASH")

        if not admin_hash:
            if admin_password:
                admin_hash = generate_password_hash(admin_password)
            else:
                bootstrap_password = secrets.token_urlsafe(14)
                admin_hash = generate_password_hash(bootstrap_password)
                self.tracker.logger.warning(
                    "ADMIN_PASSWORD/ADMIN_PASSWORD_HASH not configured. Bootstrap credentials created for user '%s' with one-time password: %s",
                    admin_username,
                    bootstrap_password,
                )

        self.tracker.user_repo.create_if_missing(conn, admin_username, admin_hash, ADMIN_ROLE, now)

    def login(self, username: str, password: str):
        with self.tracker._db_lock:
            conn = self.tracker.db.get_conn()
            try:
                row = self.tracker.user_repo.get_by_username(conn, username)
            finally:
                conn.close()
        if not row or not check_password_hash(row["password_hash"], password):
            return None

        session.clear()
        session.permanent = True
        session["user_id"] = row["id"]
        session["role"] = row["role"]
        session["username"] = row["username"]
        csrf = generate_csrf_token(session)
        return {
            "id": row["id"],
            "username": row["username"],
            "role": row["role"],
            "csrf_token": csrf,
        }
