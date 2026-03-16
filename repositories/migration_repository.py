from datetime import datetime, timezone
from uuid import uuid4

from repositories.base import BaseRepository


class MigrationRepository(BaseRepository):
    """Handle version tracking and data migration from legacy spool rows."""

    def is_applied(self, conn, version: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?",
            (version,),
        ).fetchone()
        return row is not None

    def mark_applied(self, conn, version: str):
        """Register migration execution in `schema_migrations`."""
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        conn.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES(?, ?)",
            (version, now),
        )

    def migrate_domain_foundation(self, conn, filament_product_repo):
        """Backfill domain tables from legacy `spools` data.

        Compatibility decisions:
        - Keep `legacy_tray_uuid` mapped for cross-model lookups.
        - Derive product identity from brand/material/color/finish when no SKU exists.
        - Preserve historical AMS slot metadata where available.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        legacy_rows = conn.execute("SELECT * FROM spools").fetchall()
        for row in legacy_rows:
            tray_uuid = row["tray_uuid"]
            existing = conn.execute(
                "SELECT id FROM spool_instances WHERE legacy_tray_uuid = ?",
                (tray_uuid,),
            ).fetchone()
            if existing:
                continue

            material = row["material_type"] or "Unknown"
            color_hex = row["color_hex"] or "FFFFFFFF"
            brand = row["sub_brand"] or "Unknown"
            finish_variant = row["filament_name"] or None

            product = filament_product_repo.find_for_legacy(
                conn,
                brand=brand,
                material=material,
                color=color_hex,
                finish_variant=finish_variant,
            )
            filament_product_id = product["id"] if product else filament_product_repo.create_from_legacy(
                conn,
                row=row,
                brand=brand,
                material=material,
                color=color_hex,
                finish_variant=finish_variant,
                now=now,
            )

            conn.execute(
                """
                INSERT INTO spool_instances (
                    filament_product_id, spool_uuid, legacy_tray_uuid, rfid_uid, tray_uuid,
                    is_rfid, source, remaining_weight_g, remaining_percent,
                    weight_offset_g, custom_name, notes, archived,
                    last_ams_unit, last_tray_slot, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    filament_product_id,
                    str(uuid4()),
                    tray_uuid,
                    row["tag_uid"] or None,
                    tray_uuid,
                    row["is_rfid"] or 0,
                    "rfid" if row["is_rfid"] else "manual",
                    int(((row["remain_percent"] or 0) / 100) * (row["spool_weight"] or 0)),
                    row["remain_percent"] or 0,
                    row["weight_offset"] or 0,
                    row["custom_name"],
                    row["notes"],
                    0 if row["is_active"] else 1,
                    row["last_ams_unit"],
                    row["last_tray_slot"],
                    row["first_seen"] or now,
                    row["last_seen"] or now,
                ),
            )
