from repositories.base import BaseRepository


class SpoolRepository(BaseRepository):
    def list_legacy_spools(self, conn):
        return conn.execute("SELECT * FROM spools ORDER BY is_active DESC, last_seen DESC").fetchall()

    def list_legacy_active_spools(self, conn):
        return conn.execute(
            "SELECT * FROM spools WHERE is_active = 1 ORDER BY last_ams_unit, last_tray_slot"
        ).fetchall()

    def get_legacy_spool(self, conn, tray_uuid: str):
        return conn.execute("SELECT * FROM spools WHERE tray_uuid = ?", (tray_uuid,)).fetchone()

    def get_legacy_history(self, conn, tray_uuid: str):
        return conn.execute(
            "SELECT * FROM usage_history WHERE tray_uuid = ? ORDER BY timestamp",
            (tray_uuid,),
        ).fetchall()

    def update_legacy_spool(self, conn, tray_uuid: str, updates: dict):
        set_clause = ", ".join(f"{field} = ?" for field in updates)
        values = list(updates.values()) + [tray_uuid]
        conn.execute(f"UPDATE spools SET {set_clause} WHERE tray_uuid = ?", values)

    def delete_legacy_spool(self, conn, tray_uuid: str):
        conn.execute("DELETE FROM usage_history WHERE tray_uuid = ?", (tray_uuid,))
        conn.execute("DELETE FROM spools WHERE tray_uuid = ?", (tray_uuid,))

    def list_spool_instances(self, conn, archived=None, material=None, has_rfid=None):
        query = (
            "SELECT si.*, fp.brand AS product_brand, fp.material AS product_material, fp.color AS product_color "
            "FROM spool_instances si "
            "LEFT JOIN filament_products fp ON fp.id = si.filament_product_id"
        )
        where = []
        values = []
        if archived is not None:
            where.append("si.archived = ?")
            values.append(1 if archived else 0)
        if material:
            where.append("fp.material = ?")
            values.append(material)
        if has_rfid is not None:
            where.append("si.is_rfid = ?")
            values.append(1 if has_rfid else 0)
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY si.updated_at DESC"
        return conn.execute(query, values).fetchall()

    def create_spool_instance(self, conn, data: dict, now: str):
        cursor = conn.execute(
            """
            INSERT INTO spool_instances (
                filament_product_id, spool_uuid, rfid_uid, tray_uuid,
                external_device_slot, is_rfid, source, batch_code,
                purchase_date, opened_date, drying_last_date, drying_hours_last,
                humidity_state, remaining_weight_g, remaining_percent,
                tare_weight_g, weight_offset_g, custom_name, notes,
                archived, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data.get("filament_product_id"),
                data.get("spool_uuid"),
                data.get("rfid_uid"),
                data.get("tray_uuid"),
                data.get("external_device_slot"),
                1 if data.get("is_rfid") else 0,
                data.get("source"),
                data.get("batch_code"),
                data.get("purchase_date"),
                data.get("opened_date"),
                data.get("drying_last_date"),
                data.get("drying_hours_last"),
                data.get("humidity_state"),
                data.get("remaining_weight_g"),
                data.get("remaining_percent"),
                data.get("tare_weight_g"),
                data.get("weight_offset_g", 0),
                data.get("custom_name"),
                data.get("notes"),
                1 if data.get("archived") else 0,
                now,
                now,
            ),
        )
        return cursor.lastrowid

    def get_spool_instance(self, conn, spool_id: int):
        return conn.execute("SELECT * FROM spool_instances WHERE id = ?", (spool_id,)).fetchone()

    def update_spool_instance(self, conn, spool_id: int, updates: dict):
        set_clause = ", ".join(f"{field} = ?" for field in updates)
        values = list(updates.values()) + [spool_id]
        conn.execute(f"UPDATE spool_instances SET {set_clause} WHERE id = ?", values)

    def delete_spool_instance(self, conn, spool_id: int):
        conn.execute("DELETE FROM calibration_runs WHERE spool_id = ?", (spool_id,))
        conn.execute("DELETE FROM spool_presence_history WHERE spool_id = ?", (spool_id,))
        conn.execute("DELETE FROM spool_instances WHERE id = ?", (spool_id,))

    def get_spool_instance_by_legacy_or_rfid(self, conn, tray_uuid: str, tag_uid: str):
        if tag_uid:
            return conn.execute(
                "SELECT id FROM spool_instances WHERE legacy_tray_uuid = ? OR rfid_uid = ? ORDER BY id LIMIT 1",
                (tray_uuid, tag_uid),
            ).fetchone()
        return conn.execute(
            "SELECT id FROM spool_instances WHERE legacy_tray_uuid = ? ORDER BY id LIMIT 1",
            (tray_uuid,),
        ).fetchone()
