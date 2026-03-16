import json
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, Optional, Tuple
from uuid import uuid4

from models.enums import SYNTHETIC_ID_PREFIX


class SpoolSyncService:
    """Synchronize AMS payloads into legacy and new spool tables.

    This service intentionally writes both legacy (`spools`, `usage_history`) and
    new-model tables (`spool_instances`, `spool_presence_history`) to preserve
    backward compatibility while the UI/API still consume mixed endpoints.
    """

    def __init__(self, tracker):
        self.tracker = tracker

    def refresh_alerts(self):
        if self.tracker.low_alert_grams <= 0:
            self.tracker._active_alerts = []
            return
        with self.tracker._db_lock:
            conn = self.tracker.db.get_conn()
            try:
                rows = conn.execute(
                    """
                    SELECT tray_uuid, material_type, color_hex, spool_weight, remain_percent,
                           last_ams_unit, last_tray_slot, is_rfid, weight_offset
                    FROM spools WHERE is_active = 1 AND is_rfid = 1
                    """
                ).fetchall()
                alerts = []
                for row in rows:
                    offset = row["weight_offset"] or 0
                    remaining_grams = max(0, int((row["remain_percent"] / 100) * row["spool_weight"]) + offset)
                    if remaining_grams < self.tracker.low_alert_grams:
                        alerts.append(
                            {
                                "tray_uuid": row["tray_uuid"],
                                "material": row["material_type"],
                                "color": row["color_hex"],
                                "remaining_grams": remaining_grams,
                                "spool_weight": row["spool_weight"],
                                "tray_slot": row["last_tray_slot"],
                                "ams_unit": row["last_ams_unit"],
                                "is_rfid": row["is_rfid"],
                            }
                        )
                self.tracker._active_alerts = alerts
            finally:
                conn.close()

    def trigger_low_alert(self, conn, tray_uuid, tray, remaining_grams, spool_weight, ams_id, tray_id):
        """Emit low-stock alert and optionally bridge it to FCM notifications."""
        material = tray.get("tray_type", "Unknown")
        color = tray.get("tray_color", "FFFFFFFF")
        conn.execute("UPDATE spools SET low_alert_sent = 1 WHERE tray_uuid = ?", (tray_uuid,))

        alert = {
            "tray_uuid": tray_uuid,
            "material": material,
            "color": color,
            "remaining_grams": remaining_grams,
            "spool_weight": spool_weight,
            "tray_slot": tray_id,
            "ams_unit": ams_id,
            "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        }
        self.tracker._active_alerts.append(alert)

        if self.tracker.low_alert_fcm and self.tracker.bridge and hasattr(self.tracker.bridge, "send_fcm_notification"):
            try:
                data = {
                    "type": "filament_low",
                    "material": material,
                    "color": color,
                    "remaining_grams": str(remaining_grams),
                    "spool_weight": str(spool_weight),
                    "tray_slot": str(tray_id),
                    "ams_unit": str(ams_id),
                    "timestamp": str(int(time.time())),
                }
                self.tracker.bridge.send_fcm_notification(
                    title="Low Filament",
                    body=f"{material} ({remaining_grams}g remaining) - consider reordering",
                    data=data,
                )
            except Exception as exc:
                self.tracker.logger.error(f"Failed to send low filament FCM: {exc}")

    def _extract_job_name(self) -> str:
        if self.tracker.bridge and hasattr(self.tracker.bridge, "state"):
            return self.tracker.bridge.state.job_name or ""
        return ""

    def _update_current_tray(self, ams_payload: dict):
        tray_now = ams_payload.get("tray_now")
        if tray_now is not None:
            self.tracker._tray_now = int(tray_now)
        if "drying_mode" in ams_payload:
            self.tracker._ams_drying_mode = bool(ams_payload.get("drying_mode"))
        elif "is_drying" in ams_payload:
            self.tracker._ams_drying_mode = bool(ams_payload.get("is_drying"))
        self.tracker._last_ams_update_at = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

    def _update_ams_environment(self, ams_units: Iterable[dict]):
        for unit in ams_units:
            uid = int(unit.get("id", 0))
            trays_list = unit.get("tray", [])
            hum_raw = int(unit.get("humidity_raw", 0) or 0)
            hum_idx = int(unit.get("humidity", 0) or 0)
            self.tracker._ams_info[uid] = {
                "temp": float(unit.get("temp", 0) or 0),
                "humidity": hum_raw if hum_raw else None,
                "humidity_index": hum_idx,
                "tray_count": len(trays_list),
            }

    def _is_valid_tray_reading(self, remain: int, has_rfid: bool, tray_type: str) -> bool:
        if remain == -1 and has_rfid:
            return False
        if remain == -1 and not has_rfid and not tray_type:
            return False
        return True

    def _normalize_tray_identity(self, tray: dict, tray_uuid: str, remain: int, has_rfid: bool) -> Tuple[str, int]:
        if has_rfid:
            return tray_uuid, remain
        # Compatibility decision:
        # Non-RFID spools do not provide stable tray_uuid. We synthesize one from
        # material + RGB color so legacy screens keep a deterministic identity.
        material = tray.get("tray_type", "Unknown") or "Unknown"
        color = tray.get("tray_color", "FFFFFFFF")
        synthetic_uuid = f"{SYNTHETIC_ID_PREFIX}{material}_{color[:6]}"
        return synthetic_uuid, 100

    def _get_existing_legacy_spool(self, conn, tray_uuid: str):
        return conn.execute(
            "SELECT remain_percent, low_alert_sent FROM spools WHERE tray_uuid = ?",
            (tray_uuid,),
        ).fetchone()

    def _insert_usage_history(self, conn, tray_uuid: str, now: str, remain: int, spool_weight: int, job_name: str):
        remaining_grams = int((remain / 100) * spool_weight)
        conn.execute(
            """
            INSERT INTO usage_history (tray_uuid, timestamp, remain_percent, remaining_grams, job_name)
            VALUES (?, ?, ?, ?, ?)
            """,
            (tray_uuid, now, remain, remaining_grams, job_name),
        )

    def _update_existing_legacy_spool(
        self,
        conn,
        tray: dict,
        tray_uuid: str,
        remain: int,
        is_rfid: int,
        ams_id: int,
        tray_id: int,
        now: str,
    ):
        color_hex = tray.get("tray_color", "FFFFFFFF")
        spool_weight = int(tray.get("tray_weight", "250") or "250")
        cols = tray.get("cols", [])
        conn.execute(
            """
            UPDATE spools SET
                material_type = ?, color_hex = ?, color_names = ?,
                spool_weight = ?, remain_percent = ?,
                filament_name = ?, sub_brand = ?,
                diameter = ?, nozzle_temp_min = ?, nozzle_temp_max = ?,
                bed_temp = ?, drying_temp = ?, drying_time = ?,
                last_seen = ?, last_ams_unit = ?, last_tray_slot = ?,
                is_active = 1, tag_uid = ?, is_rfid = ?
            WHERE tray_uuid = ?
            """,
            (
                tray.get("tray_type", "Unknown"),
                color_hex,
                json.dumps(cols) if cols else None,
                spool_weight,
                remain,
                tray.get("tray_id_name", ""),
                tray.get("tray_sub_brands", ""),
                float(tray.get("tray_diameter", "1.75") or "1.75"),
                int(tray.get("nozzle_temp_min", "0") or "0"),
                int(tray.get("nozzle_temp_max", "0") or "0"),
                int(tray.get("bed_temp", "0") or "0"),
                int(tray.get("drying_temp", "0") or "0"),
                int(tray.get("drying_time", "0") or "0"),
                now,
                ams_id,
                tray_id,
                tray.get("tag_uid", ""),
                is_rfid,
                tray_uuid,
            ),
        )
        return spool_weight

    def _create_legacy_spool(
        self,
        conn,
        tray: dict,
        tray_uuid: str,
        remain: int,
        is_rfid: int,
        ams_id: int,
        tray_id: int,
        now: str,
    ):
        color_hex = tray.get("tray_color", "FFFFFFFF")
        spool_weight = int(tray.get("tray_weight", "250") or "250")
        cols = tray.get("cols", [])
        conn.execute(
            """
            INSERT INTO spools (
                tray_uuid, tag_uid, material_type, color_hex, color_names,
                spool_weight, remain_percent, filament_name, sub_brand,
                diameter, nozzle_temp_min, nozzle_temp_max,
                bed_temp, drying_temp, drying_time,
                first_seen, last_seen, last_ams_unit, last_tray_slot,
                is_active, is_rfid
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                tray_uuid,
                tray.get("tag_uid", ""),
                tray.get("tray_type", "Unknown"),
                color_hex,
                json.dumps(cols) if cols else None,
                spool_weight,
                remain,
                tray.get("tray_id_name", ""),
                tray.get("tray_sub_brands", ""),
                float(tray.get("tray_diameter", "1.75") or "1.75"),
                int(tray.get("nozzle_temp_min", "0") or "0"),
                int(tray.get("nozzle_temp_max", "0") or "0"),
                int(tray.get("bed_temp", "0") or "0"),
                int(tray.get("drying_temp", "0") or "0"),
                int(tray.get("drying_time", "0") or "0"),
                now,
                now,
                ams_id,
                tray_id,
                is_rfid,
            ),
        )
        return spool_weight

    def _load_weight_offset(self, conn, tray_uuid: str, existing_row) -> int:
        if not existing_row:
            return 0
        offset_row = conn.execute(
            "SELECT weight_offset FROM spools WHERE tray_uuid = ?",
            (tray_uuid,),
        ).fetchone()
        return (offset_row["weight_offset"] or 0) if offset_row else 0

    def _handle_low_alert_state(
        self,
        conn,
        tray_uuid: str,
        tray: dict,
        remain: int,
        spool_weight: int,
        is_rfid: int,
        ams_id: int,
        tray_id: int,
        existing_row,
    ):
        offset = self._load_weight_offset(conn, tray_uuid, existing_row)
        remaining_grams = max(0, int((remain / 100) * spool_weight) + offset)
        low_alert_sent = existing_row["low_alert_sent"] if existing_row else 0

        if is_rfid and self.tracker.low_alert_grams > 0 and remaining_grams < self.tracker.low_alert_grams:
            if not low_alert_sent:
                self.trigger_low_alert(conn, tray_uuid, tray, remaining_grams, spool_weight, ams_id, tray_id)
            return
        if low_alert_sent:
            conn.execute("UPDATE spools SET low_alert_sent = 0 WHERE tray_uuid = ?", (tray_uuid,))

    def _sync_spool_instance_from_ams(self, conn, tray, tray_uuid, remain, is_rfid, ams_id, tray_id, now):
        """Mirror legacy tray events into `spool_instances` for the new domain model."""
        spool = self.tracker.spool_repo.get_spool_instance_by_legacy_or_rfid(
            conn,
            tray_uuid=tray_uuid,
            tag_uid=tray.get("tag_uid", ""),
        )
        spool_weight = int(tray.get("tray_weight", "250") or "250")
        remaining_weight = int((remain / 100) * spool_weight)

        if spool:
            conn.execute(
                """
                UPDATE spool_instances
                SET tray_uuid = ?,
                    rfid_uid = ?,
                    is_rfid = ?,
                    source = ?,
                    remaining_percent = ?,
                    remaining_weight_g = ?,
                    last_ams_unit = ?,
                    last_tray_slot = ?,
                    archived = 0,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    tray_uuid,
                    tray.get("tag_uid", "") or None,
                    is_rfid,
                    "rfid" if is_rfid else "manual",
                    remain,
                    remaining_weight,
                    ams_id,
                    tray_id,
                    now,
                    spool["id"],
                ),
            )
            conn.execute(
                """
                INSERT INTO spool_presence_history(spool_id, ams_unit, tray_slot, event_type, event_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (spool["id"], ams_id, tray_id, "seen", now),
            )
            return

        conn.execute(
            """
            INSERT INTO spool_instances (
                spool_uuid, legacy_tray_uuid, tray_uuid, rfid_uid,
                is_rfid, source, remaining_weight_g, remaining_percent,
                last_ams_unit, last_tray_slot, archived, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                str(uuid4()),
                tray_uuid,
                tray_uuid,
                tray.get("tag_uid", "") or None,
                is_rfid,
                "rfid" if is_rfid else "manual",
                remaining_weight,
                remain,
                ams_id,
                tray_id,
                now,
                now,
            ),
        )
        spool_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        conn.execute(
            """
            INSERT INTO spool_presence_history(spool_id, ams_unit, tray_slot, event_type, event_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (spool_id, ams_id, tray_id, "created", now),
        )

    def _mark_removed_spools_inactive(self, conn, active_uuids):
        if active_uuids:
            placeholders = ",".join("?" * len(active_uuids))
            conn.execute(
                f"UPDATE spools SET is_active = 0 WHERE is_active = 1 AND tray_uuid NOT IN ({placeholders})",
                list(active_uuids),
            )
            return
        conn.execute("UPDATE spools SET is_active = 0 WHERE is_active = 1")

    def _process_tray(self, conn, tray: dict, ams_id: int, now: str, job_name: str) -> Optional[str]:
        tray_uuid = tray.get("tray_uuid", "")
        remain = tray.get("remain", -1)
        tray_id = int(tray.get("id", 0))
        tray_type = tray.get("tray_type", "")
        has_rfid = bool(tray_uuid and tray_uuid.replace("0", "") != "")

        if not self._is_valid_tray_reading(remain, has_rfid, tray_type):
            return None

        tray_uuid, remain = self._normalize_tray_identity(tray, tray_uuid, remain, has_rfid)
        is_rfid = 1 if has_rfid else 0
        existing = self._get_existing_legacy_spool(conn, tray_uuid)

        if existing:
            old_remain = existing["remain_percent"]
            spool_weight = self._update_existing_legacy_spool(
                conn=conn,
                tray=tray,
                tray_uuid=tray_uuid,
                remain=remain,
                is_rfid=is_rfid,
                ams_id=ams_id,
                tray_id=tray_id,
                now=now,
            )
            if old_remain != remain:
                self._insert_usage_history(conn, tray_uuid, now, remain, spool_weight, job_name)
        else:
            spool_weight = self._create_legacy_spool(
                conn=conn,
                tray=tray,
                tray_uuid=tray_uuid,
                remain=remain,
                is_rfid=is_rfid,
                ams_id=ams_id,
                tray_id=tray_id,
                now=now,
            )
            self._insert_usage_history(conn, tray_uuid, now, remain, spool_weight, job_name)

        self._sync_spool_instance_from_ams(conn, tray, tray_uuid, remain, is_rfid, ams_id, tray_id, now)
        self._handle_low_alert_state(conn, tray_uuid, tray, remain, spool_weight, is_rfid, ams_id, tray_id, existing)
        return tray_uuid

    def update_ams_data(self, ams_payload: dict):
        """Ingest AMS payload and update inventory state.

        Flow summary:
        1) Update current tray and AMS environment cache.
        2) Upsert each tray in legacy + new-model tables.
        3) Mark missing legacy trays inactive.
        4) Recompute active low-stock alerts.
        """
        try:
            ams_units = ams_payload.get("ams", [])
            if not ams_units:
                return

            now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            job_name = self._extract_job_name()
            self._update_current_tray(ams_payload)
            self._update_ams_environment(ams_units)

            active_uuids = set()
            with self.tracker._db_lock:
                conn = self.tracker.db.get_conn()
                try:
                    for unit in ams_units:
                        ams_id = int(unit.get("id", 0))
                        for tray in unit.get("tray", []):
                            active_uuid = self._process_tray(conn, tray, ams_id, now, job_name)
                            if active_uuid:
                                active_uuids.add(active_uuid)
                    self._mark_removed_spools_inactive(conn, active_uuids)
                    conn.commit()
                finally:
                    conn.close()

            self.refresh_alerts()
        except Exception as exc:
            self.tracker.logger.error(f"Error processing AMS data: {exc}", exc_info=True)

    def generate_test_data(self):
        test_spools = [
            {
                "uuid": "A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4",
                "tag": "A1B2C3D4E5F6A1B2",
                "type": "PLA",
                "color": "FF0000FF",
                "weight": 1000,
                "remain": 74,
                "name": "PLA Basic",
                "ams": 0,
                "slot": 0,
                "nmin": "190",
                "nmax": "220",
                "bed": "60",
            },
            {
                "uuid": "B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5",
                "tag": "B2C3D4E5F6A1B2C3",
                "type": "PETG",
                "color": "0077FFFF",
                "weight": 1000,
                "remain": 84,
                "name": "PETG HF",
                "ams": 0,
                "slot": 1,
                "nmin": "230",
                "nmax": "260",
                "bed": "70",
            },
            {
                "uuid": "C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6",
                "tag": "C3D4E5F6A1B2C3D4",
                "type": "ABS",
                "color": "222222FF",
                "weight": 1000,
                "remain": 14,
                "name": "ABS",
                "ams": 0,
                "slot": 2,
                "nmin": "240",
                "nmax": "270",
                "bed": "100",
            },
            {
                "uuid": "NORFID_PLA_9B59B6",
                "tag": "",
                "type": "PLA",
                "color": "9B59B6FF",
                "weight": 1000,
                "remain": 55,
                "name": "PLA",
                "ams": 1,
                "slot": 0,
                "rfid": False,
                "nmin": "190",
                "nmax": "220",
                "bed": "60",
            },
        ]

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with self.tracker._db_lock:
            conn = self.tracker.db.get_conn()
            try:
                conn.execute("DELETE FROM spools")
                conn.execute("DELETE FROM usage_history")

                for spool in test_spools:
                    is_active = spool.get("active", True)
                    first_seen = (now - timedelta(days=random.randint(1, 60))).isoformat()
                    last_seen = now.isoformat() if is_active else (now - timedelta(days=random.randint(1, 14))).isoformat()

                    conn.execute(
                        """
                        INSERT INTO spools (
                            tray_uuid, tag_uid, material_type, color_hex, spool_weight,
                            remain_percent, filament_name, diameter,
                            nozzle_temp_min, nozzle_temp_max, bed_temp,
                            first_seen, last_seen, last_ams_unit, last_tray_slot,
                            is_active, low_alert_sent, is_rfid
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 1.75, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            spool["uuid"],
                            spool["tag"],
                            spool["type"],
                            spool["color"],
                            spool["weight"],
                            spool["remain"],
                            spool["name"],
                            int(spool.get("nmin", "0")),
                            int(spool.get("nmax", "0")),
                            int(spool.get("bed", "0")),
                            first_seen,
                            last_seen,
                            spool["ams"],
                            spool["slot"],
                            1 if is_active else 0,
                            1 if (spool["remain"] / 100 * spool["weight"]) < self.tracker.low_alert_grams else 0,
                            1 if spool.get("rfid", True) else 0,
                        ),
                    )

                    base_remain = min(spool["remain"] + random.randint(10, 40), 100)
                    for idx in range(random.randint(3, 8)):
                        ts = (now - timedelta(hours=random.randint(1, 500))).isoformat()
                        pct = max(spool["remain"], base_remain - idx * random.randint(3, 10))
                        grams = int((pct / 100) * spool["weight"])
                        conn.execute(
                            """
                            INSERT INTO usage_history (tray_uuid, timestamp, remain_percent, remaining_grams, job_name)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (spool["uuid"], ts, pct, grams, f"test_print_{random.randint(1,20)}.gcode"),
                        )

                conn.commit()
            finally:
                conn.close()

        self.refresh_alerts()
        self.tracker._ams_info = {
            0: {"temp": 24.5, "humidity": 23, "humidity_index": 4, "tray_count": 4},
            1: {"temp": 23.0, "humidity": 18, "humidity_index": 5, "tray_count": 4},
            2: {"temp": 25.0, "humidity": 15, "humidity_index": 5, "tray_count": 1},
        }
        self.tracker._tray_now = 2
