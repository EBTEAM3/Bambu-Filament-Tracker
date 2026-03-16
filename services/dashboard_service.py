from datetime import datetime, timedelta

from bambu_mqtt import PREPARATION_STAGES, STAGE_CATEGORIES


class DashboardService:
    def __init__(self, tracker):
        self.tracker = tracker

    def _slot_text(self, ams_unit, tray_slot):
        if ams_unit is None or tray_slot is None:
            return None
        try:
            ams_value = int(ams_unit)
            slot_value = int(tray_slot)
        except (TypeError, ValueError):
            return None
        if ams_value < 0 or slot_value < 0:
            return None
        return f"AMS {ams_value + 1} · Slot {slot_value + 1}"

    def _job_spool_context(self, job_row):
        if not job_row:
            return None
        ams_unit = job_row.get("ams_unit")
        tray_slot = job_row.get("tray_slot")
        return {
            "slot_text": self._slot_text(ams_unit, tray_slot),
            "ams_unit": ams_unit,
            "tray_slot": tray_slot,
            "tray_now_raw": job_row.get("tray_now_raw"),
            "spool_tray_uuid": job_row.get("spool_tray_uuid"),
            "spool_material_type": job_row.get("spool_material_type"),
            "spool_color_hex": job_row.get("spool_color_hex"),
            "spool_is_rfid": job_row.get("spool_is_rfid"),
            "association_quality": "observed_snapshot",
        }

    def _error_info(self, error_code, failure_reason):
        code = (error_code or "").strip()
        reason = (failure_reason or "").strip()
        if not code and not reason:
            return None
        summary = None
        if reason:
            summary = reason
        elif code:
            summary = f"Code {code}"
        return {
            "code": code or None,
            "reason": reason or None,
            "summary": summary,
            "is_mapped": bool(reason),
        }

    def _primary_ams_unit(self, ams_units):
        if not ams_units:
            return None
        if "0" in ams_units:
            return ams_units.get("0")
        return next(iter(ams_units.values()), None)

    def _format_eta(self, minutes_value):
        try:
            minutes = int(minutes_value or 0)
        except (TypeError, ValueError):
            return None
        if minutes <= 0:
            return None
        hours = minutes // 60
        mins = minutes % 60
        return f"{hours}h {mins}m" if hours > 0 else f"{mins}m"

    def _estimated_end_at(self, last_update_at, remaining_minutes):
        if not last_update_at:
            return None
        try:
            minutes = int(remaining_minutes or 0)
            base = datetime.fromisoformat(str(last_update_at))
        except (TypeError, ValueError):
            return None
        if minutes <= 0:
            return None
        return (base + timedelta(minutes=minutes)).isoformat()

    def _print_phase(self, gcode_state: str, stage_category: str):
        state = (gcode_state or "").upper()
        if "FAIL" in state or "ERROR" in state:
            return "error"
        if "PAUSE" in state:
            return "paused"
        if "RUN" in state or state == "PRINTING":
            if stage_category in {"prepare", "calibrate", "filament"}:
                return stage_category
            return "printing"
        if "FINISH" in state or "COMPLETED" in state:
            return "finished"
        if "IDLE" in state:
            return "idle"
        return "unknown"

    def status_payload(self):
        status = {
            "connected": False,
            "job_name": "",
            "gcode_state": "UNKNOWN",
            "progress": 0,
            "nozzle_temp": 0,
            "bed_temp": 0,
            "test_mode": self.tracker.test_mode,
            "ams_info": {},
        }
        if self.tracker.test_mode:
            status.update(
                {
                    "connected": True,
                    "job_name": "benchy_v2.gcode",
                    "gcode_state": "RUNNING",
                    "progress": 47,
                    "nozzle_temp": 215,
                    "bed_temp": 60,
                }
            )
        elif self.tracker.bridge and hasattr(self.tracker.bridge, "state"):
            state = self.tracker.bridge.state
            status.update(
                {
                    "connected": self.tracker.bridge.mqtt_client is not None,
                    "job_name": state.job_name,
                    "gcode_state": state.gcode_state,
                    "progress": state.progress,
                    "nozzle_temp": state.nozzle_temp,
                    "bed_temp": state.bed_temp,
                }
            )
            status["layer_num"] = state.layer_num
            status["total_layers"] = state.total_layers
            status["remaining_time_minutes"] = state.remaining_time_minutes
            status["chamber_temp"] = state.chamber_temp
            status["stg_cur"] = state.stg_cur
            status["nozzle_target_temp"] = state.nozzle_target_temp
            status["bed_target_temp"] = state.bed_target_temp
            status["last_error_code"] = getattr(state, "last_error_code", "")
            status["last_failure_reason"] = getattr(state, "last_failure_reason", "")
            status["last_update_at"] = state.last_update_at or self.tracker._last_ams_update_at
        status["ams_info"] = {str(k): v for k, v in self.tracker._ams_info.items()}
        status["tray_now"] = self.tracker._tray_now
        if "layer_num" not in status:
            status["layer_num"] = 0
        if "total_layers" not in status:
            status["total_layers"] = 0
        if "remaining_time_minutes" not in status:
            status["remaining_time_minutes"] = 0
        if "chamber_temp" not in status:
            status["chamber_temp"] = 0
        if "last_update_at" not in status:
            status["last_update_at"] = self.tracker._last_ams_update_at
        if "stg_cur" not in status:
            status["stg_cur"] = -1
        if "nozzle_target_temp" not in status:
            status["nozzle_target_temp"] = 0
        if "bed_target_temp" not in status:
            status["bed_target_temp"] = 0
        if "last_error_code" not in status:
            status["last_error_code"] = ""
        if "last_failure_reason" not in status:
            status["last_failure_reason"] = ""

        stage_id = status.get("stg_cur", -1)
        stage_label = PREPARATION_STAGES.get(stage_id)
        stage_category = STAGE_CATEGORIES.get(stage_id)
        phase = self._print_phase(status.get("gcode_state"), stage_category)

        ams_units = status["ams_info"]
        ams_connected = bool(ams_units)
        primary_unit = self._primary_ams_unit(ams_units)
        status["printer_overview"] = {
            "online": bool(status.get("connected") or status.get("test_mode")),
            "print_status": status.get("gcode_state") or "UNKNOWN",
            "print_phase": phase,
            "job_name": status.get("job_name") or "",
            "progress_percent": status.get("progress", 0),
            "layer_num": status.get("layer_num", 0),
            "total_layers": status.get("total_layers", 0),
            "eta_minutes": status.get("remaining_time_minutes", 0),
            "eta_text": self._format_eta(status.get("remaining_time_minutes", 0)),
            "nozzle_temp": status.get("nozzle_temp", 0),
            "nozzle_target_temp": status.get("nozzle_target_temp", 0),
            "bed_temp": status.get("bed_temp", 0),
            "bed_target_temp": status.get("bed_target_temp", 0),
            "chamber_temp": status.get("chamber_temp", 0),
            "last_update_at": status.get("last_update_at"),
            "estimated_end_at": self._estimated_end_at(
                status.get("last_update_at"),
                status.get("remaining_time_minutes", 0),
            ),
            "stage": {
                "id": stage_id,
                "label": stage_label,
                "category": stage_category,
            },
            "last_error_code": status.get("last_error_code") or None,
            "last_failure_reason": status.get("last_failure_reason") or None,
            "last_error": self._error_info(
                status.get("last_error_code"),
                status.get("last_failure_reason"),
            ),
        }
        status["ams_overview"] = {
            "connected": ams_connected,
            "temp": primary_unit.get("temp") if primary_unit else None,
            "humidity": primary_unit.get("humidity") if primary_unit else None,
            "humidity_index": primary_unit.get("humidity_index") if primary_unit else None,
            "tray_now": status.get("tray_now"),
            "drying_mode": self.tracker._ams_drying_mode,
            "has_tray_data": any((unit.get("tray_count") or 0) > 0 for unit in ams_units.values()),
            "last_update_at": self.tracker._last_ams_update_at,
        }

        active_job = self.tracker.job_history_service.get_active_job(status.get("job_name") or "")
        if active_job:
            status["printer_overview"]["current_job_started_at"] = active_job.get("first_seen_at")
        else:
            status["printer_overview"]["current_job_started_at"] = None
        status["printer_overview"]["current_job_spool"] = self._job_spool_context(active_job)

        last_job = self.tracker.job_history_service.get_last_job()
        recent_jobs = self.tracker.job_history_service.get_recent_jobs(limit=8)
        if last_job:
            last_job["spool_context"] = self._job_spool_context(last_job)
        for job in recent_jobs:
            job["spool_context"] = self._job_spool_context(job)
        status["last_job"] = last_job
        status["recent_jobs"] = recent_jobs
        return status

    def dashboard_payload(self):
        with self.tracker._db_lock:
            conn = self.tracker.db.get_conn()
            try:
                total_spools = conn.execute("SELECT COUNT(*) AS c FROM spool_instances").fetchone()["c"]
                active_ams = conn.execute(
                    "SELECT COUNT(*) AS c FROM spool_instances WHERE archived = 0 AND last_ams_unit IS NOT NULL"
                ).fetchone()["c"]
                missing_sku = conn.execute(
                    "SELECT COUNT(*) AS c FROM spool_instances WHERE archived = 0 AND filament_product_id IS NULL"
                ).fetchone()["c"]
                low_stock = conn.execute(
                    "SELECT COUNT(*) AS c FROM spool_instances WHERE archived = 0 AND remaining_weight_g IS NOT NULL AND remaining_weight_g < ?",
                    (self.tracker.low_alert_grams,),
                ).fetchone()["c"]
                with_override = conn.execute(
                    """
                    SELECT COUNT(DISTINCT si.id) AS c
                    FROM spool_instances si
                    JOIN calibration_profiles cp
                      ON cp.scope_type = 'spool_instance'
                     AND cp.scope_id = CAST(si.id AS TEXT)
                    WHERE si.archived = 0
                    """
                ).fetchone()["c"]
                without_override = conn.execute(
                    """
                    SELECT COUNT(*) AS c
                    FROM spool_instances si
                    WHERE si.archived = 0
                      AND NOT EXISTS (
                        SELECT 1 FROM calibration_profiles cp
                        WHERE cp.scope_type = 'spool_instance'
                          AND cp.scope_id = CAST(si.id AS TEXT)
                      )
                    """
                ).fetchone()["c"]
                latest_runs = [
                    dict(row) for row in conn.execute("SELECT * FROM calibration_runs ORDER BY test_date DESC LIMIT 10").fetchall()
                ]
            finally:
                conn.close()

        return {
            "counts": {
                "total_spools": total_spools,
                "active_in_ams": active_ams,
                "spools_missing_sku": missing_sku,
                "low_stock": low_stock,
                "with_spool_override": with_override,
                "without_spool_override": without_override,
            },
            "latest_runs": latest_runs,
        }
