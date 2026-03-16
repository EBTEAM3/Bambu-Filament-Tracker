from repositories.base import BaseRepository


class UserRepository(BaseRepository):
    def get_by_username(self, conn, username: str):
        return conn.execute(
            "SELECT id, username, role, password_hash FROM users WHERE username = ?",
            (username,),
        ).fetchone()

    def get_by_id(self, conn, user_id: int):
        return conn.execute(
            "SELECT id, username, role FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()

    def create_if_missing(self, conn, username: str, password_hash: str, role: str, now: str):
        existing = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if existing:
            return existing["id"]
        cursor = conn.execute(
            "INSERT INTO users(username, password_hash, role, created_at, updated_at) VALUES(?, ?, ?, ?, ?)",
            (username, password_hash, role, now, now),
        )
        return cursor.lastrowid
