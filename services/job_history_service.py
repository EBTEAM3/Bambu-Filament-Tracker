class JobHistoryService:
    def __init__(self, tracker):
        self.tracker = tracker

    def _decode_tray_now(self):
        tray_now = getattr(self.tracker, "_tray_now", None)
        if tray_now is None:
            return None, None, None
        try:
            tray_now_int = int(tray_now)
        except (TypeError, ValueError):
            return None, None, None
        if tray_now_int < 0:
            return tray_now_int, None, None
        ams_unit = tray_now_int // 4
        tray_slot = tray_now_int % 4
        return tray_now_int, ams_unit, tray_slot

    def _resolve_spool_snapshot(self, conn, ams_unit, tray_slot):
        if ams_unit is None or tray_slot is None:
            return None
        return conn.execute(
            """
            SELECT tray_uuid, material_type, color_hex, is_rfid
            FROM spools
            WHERE is_active = 1
              AND last_ams_unit = ?
              AND last_tray_slot = ?
            ORDER BY last_seen DESC
            LIMIT 1
            """,
            (ams_unit, tray_slot),
        ).fetchone()

    def _is_terminal_state(self, state_value: str):
        state = (state_value or "").upper()
        if "FAIL" in state or "ERROR" in state:
            return "failed"
        if "CANCEL" in state or "ABORT" in state or "STOP" in state:
            return "cancelled"
        if "FINISH" in state or "COMPLETED" in state:
            return "finished"
        return None

    def record_print_update(self, print_data: dict):
        state = getattr(self.tracker.bridge, "state", None)
        if not state:
            return

        job_name = (state.job_name or print_data.get("subtask_name") or "").strip()
        if not job_name:
            return

        last_update_at = state.last_update_at or self.tracker.now_iso()
        last_state = (state.gcode_state or "UNKNOWN").upper()
        last_progress = int(state.progress or 0)
        last_layer_num = int(state.layer_num or 0)
        last_total_layers = int(state.total_layers or 0)
        terminal_state = self._is_terminal_state(last_state)
        error_code = (getattr(state, "last_error_code", "") or "").strip() or None
        failure_reason = (getattr(state, "last_failure_reason", "") or "").strip() or None
        tray_now_raw, ams_unit, tray_slot = self._decode_tray_now()

        with self.tracker._db_lock:
            conn = self.tracker.db.get_conn()
            try:
                spool_snapshot = self._resolve_spool_snapshot(conn, ams_unit, tray_slot)
                spool_tray_uuid = spool_snapshot["tray_uuid"] if spool_snapshot else None
                spool_material_type = spool_snapshot["material_type"] if spool_snapshot else None
                spool_color_hex = spool_snapshot["color_hex"] if spool_snapshot else None
                spool_is_rfid = spool_snapshot["is_rfid"] if spool_snapshot else None

                latest = conn.execute(
                    """
                    SELECT * FROM print_job_history
                    WHERE job_name = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (job_name,),
                ).fetchone()

                active = conn.execute(
                    """
                    SELECT id FROM print_job_history
                    WHERE job_name = ? AND final_state IS NULL
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (job_name,),
                ).fetchone()

                if active:
                    conn.execute(
                        """
                        UPDATE print_job_history
                        SET last_update_at = ?,
                            last_state = ?,
                            last_progress = ?,
                            last_layer_num = ?,
                            last_total_layers = ?,
                            error_code = COALESCE(?, error_code),
                            failure_reason = COALESCE(?, failure_reason),
                            tray_now_raw = COALESCE(?, tray_now_raw),
                            ams_unit = COALESCE(?, ams_unit),
                            tray_slot = COALESCE(?, tray_slot),
                            spool_tray_uuid = COALESCE(?, spool_tray_uuid),
                            spool_material_type = COALESCE(?, spool_material_type),
                            spool_color_hex = COALESCE(?, spool_color_hex),
                            spool_is_rfid = COALESCE(?, spool_is_rfid)
                        WHERE id = ?
                        """,
                        (
                            last_update_at,
                            last_state,
                            last_progress,
                            last_layer_num,
                            last_total_layers,
                            error_code,
                            failure_reason,
                            tray_now_raw,
                            ams_unit,
                            tray_slot,
                            spool_tray_uuid,
                            spool_material_type,
                            spool_color_hex,
                            spool_is_rfid,
                            active["id"],
                        ),
                    )
                    row_id = active["id"]
                elif latest and terminal_state and latest["final_state"] == terminal_state:
                    conn.execute(
                        """
                        UPDATE print_job_history
                        SET last_update_at = ?,
                            last_state = ?,
                            last_progress = ?,
                            last_layer_num = ?,
                            last_total_layers = ?,
                            error_code = COALESCE(?, error_code),
                            failure_reason = COALESCE(?, failure_reason),
                            tray_now_raw = COALESCE(?, tray_now_raw),
                            ams_unit = COALESCE(?, ams_unit),
                            tray_slot = COALESCE(?, tray_slot),
                            spool_tray_uuid = COALESCE(?, spool_tray_uuid),
                            spool_material_type = COALESCE(?, spool_material_type),
                            spool_color_hex = COALESCE(?, spool_color_hex),
                            spool_is_rfid = COALESCE(?, spool_is_rfid)
                        WHERE id = ?
                        """,
                        (
                            last_update_at,
                            last_state,
                            last_progress,
                            last_layer_num,
                            last_total_layers,
                            error_code,
                            failure_reason,
                            tray_now_raw,
                            ams_unit,
                            tray_slot,
                            spool_tray_uuid,
                            spool_material_type,
                            spool_color_hex,
                            spool_is_rfid,
                            latest["id"],
                        ),
                    )
                    row_id = latest["id"]
                else:
                    cursor = conn.execute(
                        """
                        INSERT INTO print_job_history (
                            job_name,
                            first_seen_at,
                            last_update_at,
                            last_state,
                            last_progress,
                            last_layer_num,
                            last_total_layers,
                            error_code,
                            failure_reason,
                            tray_now_raw,
                            ams_unit,
                            tray_slot,
                            spool_tray_uuid,
                            spool_material_type,
                            spool_color_hex,
                            spool_is_rfid
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            job_name,
                            last_update_at,
                            last_update_at,
                            last_state,
                            last_progress,
                            last_layer_num,
                            last_total_layers,
                            error_code,
                            failure_reason,
                            tray_now_raw,
                            ams_unit,
                            tray_slot,
                            spool_tray_uuid,
                            spool_material_type,
                            spool_color_hex,
                            spool_is_rfid,
                        ),
                    )
                    row_id = cursor.lastrowid

                if terminal_state:
                    conn.execute(
                        """
                        UPDATE print_job_history
                        SET final_state = ?,
                            final_progress = ?
                        WHERE id = ?
                        """,
                        (terminal_state, last_progress, row_id),
                    )

                conn.commit()
            finally:
                conn.close()

    def get_last_job(self):
        with self.tracker._db_lock:
            conn = self.tracker.db.get_conn()
            try:
                row = conn.execute(
                    """
                    SELECT * FROM print_job_history
                    ORDER BY last_update_at DESC
                    LIMIT 1
                    """
                ).fetchone()
            finally:
                conn.close()
        return dict(row) if row else None

    def get_active_job(self, job_name: str = ""):
        with self.tracker._db_lock:
            conn = self.tracker.db.get_conn()
            try:
                if job_name:
                    row = conn.execute(
                        """
                        SELECT * FROM print_job_history
                        WHERE final_state IS NULL AND job_name = ?
                        ORDER BY id DESC
                        LIMIT 1
                        """,
                        (job_name,),
                    ).fetchone()
                    if row:
                        return dict(row)

                row = conn.execute(
                    """
                    SELECT * FROM print_job_history
                    WHERE final_state IS NULL
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
            finally:
                conn.close()
        return dict(row) if row else None

    def get_recent_jobs(self, limit: int = 8):
        safe_limit = max(1, min(int(limit), 25))
        with self.tracker._db_lock:
            conn = self.tracker.db.get_conn()
            try:
                rows = conn.execute(
                    """
                    SELECT * FROM print_job_history
                    ORDER BY last_update_at DESC
                    LIMIT ?
                    """,
                    (safe_limit,),
                ).fetchall()
            finally:
                conn.close()
        return [dict(r) for r in rows]
