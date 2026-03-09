#!/usr/bin/env python3
"""
Filament Tracker - SQLite database, Flask web server, AMS data processing.

Tracks all filament spools that have ever been loaded into the AMS.
Provides a local-network web UI for monitoring filament inventory.

Can run standalone (connects directly to Bambu MQTT), or be loaded as a
module by BambuNowBar's notification server to share a single MQTT connection.

Standalone usage:
    python3 filament_tracker.py              # live mode (connects to MQTT)
    python3 filament_tracker.py --test       # test mode (mock data, no MQTT)

As a module (loaded by BambuNowBar):
    from filament_tracker import FilamentTracker
    tracker = FilamentTracker(bridge=mqtt_client, ...)
    mqtt_client.on_ams_data(tracker.update_ams_data)
    tracker.start()
"""

import json
import os
import signal
import socket
import sqlite3
import sys
import time
import logging
import threading
import random
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List

from flask import Flask, render_template, jsonify, request

logger = logging.getLogger(__name__)

_SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.environ.get('FILAMENT_TRACKER_DATA_DIR', _SERVER_DIR)
DB_PATH = os.path.join(_DATA_DIR, 'filament_tracker.db')
TEST_DB_PATH = os.path.join(_DATA_DIR, 'filament_tracker_test.db')

# Prefix used for synthetic IDs (non-RFID spools)
SYNTHETIC_ID_PREFIX = "NORFID_"


class FilamentTracker:
    """Manages filament spool tracking with SQLite persistence and a Flask web UI."""

    def __init__(self, bridge=None, port=5000, host='0.0.0.0',
                 low_alert_grams=150, low_alert_fcm=True, test_mode=False):
        self.bridge = bridge
        self.port = port
        self.host = host
        self.low_alert_grams = low_alert_grams
        self.low_alert_fcm = low_alert_fcm
        self.test_mode = test_mode
        self._active_alerts: List[Dict] = []
        self._db_lock = threading.Lock()
        self.db_path = TEST_DB_PATH if test_mode else DB_PATH

        if not test_mode:
            self._cleanup_test_db()

        self._init_db()
        self._app = self._create_flask_app()

        if self.test_mode:
            self._generate_test_data()

    # =========================================================================
    # Database
    # =========================================================================

    def _init_db(self):
        with self._db_lock:
            conn = sqlite3.connect(self.db_path)
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS spools (
                    tray_uuid       TEXT PRIMARY KEY,
                    tag_uid         TEXT,
                    material_type   TEXT NOT NULL,
                    color_hex       TEXT NOT NULL,
                    color_names     TEXT,
                    spool_weight    INTEGER NOT NULL,
                    remain_percent  INTEGER DEFAULT 0,
                    filament_name   TEXT,
                    sub_brand       TEXT,
                    diameter        REAL DEFAULT 1.75,
                    nozzle_temp_min INTEGER,
                    nozzle_temp_max INTEGER,
                    bed_temp        INTEGER,
                    drying_temp     INTEGER,
                    drying_time     INTEGER,
                    first_seen      TEXT NOT NULL,
                    last_seen       TEXT NOT NULL,
                    last_ams_unit   INTEGER,
                    last_tray_slot  INTEGER,
                    is_active       INTEGER DEFAULT 1,
                    low_alert_sent  INTEGER DEFAULT 0,
                    notes           TEXT,
                    custom_name     TEXT,
                    is_rfid         INTEGER DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS usage_history (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    tray_uuid       TEXT NOT NULL,
                    timestamp       TEXT NOT NULL,
                    remain_percent  INTEGER NOT NULL,
                    remaining_grams INTEGER NOT NULL,
                    job_name        TEXT,
                    FOREIGN KEY (tray_uuid) REFERENCES spools(tray_uuid)
                );
            """)
            # Add columns if upgrading from older schema
            _MIGRATION_COLUMNS = {
                "is_rfid": "INTEGER DEFAULT 1",
                "weight_offset": "INTEGER DEFAULT 0",
            }
            for col, col_type in _MIGRATION_COLUMNS.items():
                try:
                    conn.execute(f"ALTER TABLE spools ADD COLUMN {col} {col_type}")
                except sqlite3.OperationalError:
                    pass  # Column already exists
            conn.close()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _cleanup_test_db(self):
        """Delete the test database file on exit."""
        try:
            if os.path.exists(TEST_DB_PATH):
                os.remove(TEST_DB_PATH)
                logger.info("Filament Tracker: test database cleaned up")
        except Exception as e:
            logger.error(f"Failed to clean up test database: {e}")

    # =========================================================================
    # AMS Data Processing
    # =========================================================================

    def update_ams_data(self, ams_payload: dict):
        """Parse AMS data from MQTT and upsert into the database."""
        try:
            ams_units = ams_payload.get("ams", [])
            if not ams_units:
                return
            now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            active_uuids = set()
            logger.debug(f"AMS data received: {len(ams_units)} unit(s)")

            job_name = ""
            if self.bridge and hasattr(self.bridge, 'state'):
                job_name = self.bridge.state.job_name or ""

            with self._db_lock:
                conn = self._get_conn()
                try:
                    for unit in ams_units:
                        ams_id = int(unit.get("id", 0))
                        trays = unit.get("tray", [])
                        for tray in trays:
                            tray_uuid = tray.get("tray_uuid", "")
                            remain = tray.get("remain", -1)
                            tray_id = int(tray.get("id", 0))
                            tray_type = tray.get("tray_type", "")

                            has_rfid = bool(tray_uuid and tray_uuid.replace("0", "") != "")

                            if remain == -1 and has_rfid:
                                continue
                            if remain == -1 and not has_rfid and not tray_type:
                                continue

                            if not has_rfid:
                                remain = 100
                                material = tray_type or "Unknown"
                                color = tray.get("tray_color", "FFFFFFFF")
                                tray_uuid = f"{SYNTHETIC_ID_PREFIX}{material}_{color}"
                                logger.info(f"Non-RFID spool detected: {material} #{color[:6]} in AMS {ams_id} slot {tray_id}")

                            active_uuids.add(tray_uuid)
                            is_rfid = 1 if has_rfid else 0

                            existing = conn.execute(
                                "SELECT remain_percent, low_alert_sent FROM spools WHERE tray_uuid = ?",
                                (tray_uuid,)
                            ).fetchone()

                            color_hex = tray.get("tray_color", "FFFFFFFF")
                            spool_weight = int(tray.get("tray_weight", "250") or "250")
                            cols = tray.get("cols", [])

                            if existing:
                                old_remain = existing["remain_percent"]
                                conn.execute("""
                                    UPDATE spools SET
                                        material_type = ?, color_hex = ?, color_names = ?,
                                        spool_weight = ?, remain_percent = ?,
                                        filament_name = ?, sub_brand = ?,
                                        diameter = ?, nozzle_temp_min = ?, nozzle_temp_max = ?,
                                        bed_temp = ?, drying_temp = ?, drying_time = ?,
                                        last_seen = ?, last_ams_unit = ?, last_tray_slot = ?,
                                        is_active = 1, tag_uid = ?, is_rfid = ?
                                    WHERE tray_uuid = ?
                                """, (
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
                                    now, ams_id, tray_id,
                                    tray.get("tag_uid", ""),
                                    is_rfid,
                                    tray_uuid
                                ))

                                if old_remain != remain:
                                    remaining_grams = int((remain / 100) * spool_weight)
                                    conn.execute("""
                                        INSERT INTO usage_history (tray_uuid, timestamp, remain_percent, remaining_grams, job_name)
                                        VALUES (?, ?, ?, ?, ?)
                                    """, (tray_uuid, now, remain, remaining_grams, job_name))
                            else:
                                conn.execute("""
                                    INSERT INTO spools (
                                        tray_uuid, tag_uid, material_type, color_hex, color_names,
                                        spool_weight, remain_percent, filament_name, sub_brand,
                                        diameter, nozzle_temp_min, nozzle_temp_max,
                                        bed_temp, drying_temp, drying_time,
                                        first_seen, last_seen, last_ams_unit, last_tray_slot,
                                        is_active, is_rfid
                                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                                """, (
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
                                    now, now, ams_id, tray_id,
                                    is_rfid
                                ))

                                remaining_grams = int((remain / 100) * spool_weight)
                                conn.execute("""
                                    INSERT INTO usage_history (tray_uuid, timestamp, remain_percent, remaining_grams, job_name)
                                    VALUES (?, ?, ?, ?, ?)
                                """, (tray_uuid, now, remain, remaining_grams, job_name))

                            # Low filament alert check (RFID spools only)
                            offset = 0
                            if existing:
                                offset_row = conn.execute(
                                    "SELECT weight_offset FROM spools WHERE tray_uuid = ?", (tray_uuid,)
                                ).fetchone()
                                offset = (offset_row["weight_offset"] or 0) if offset_row else 0
                            remaining_grams = max(0, int((remain / 100) * spool_weight) + offset)
                            low_alert_sent = existing["low_alert_sent"] if existing else 0

                            if is_rfid and self.low_alert_grams > 0 and remaining_grams < self.low_alert_grams:
                                if not low_alert_sent:
                                    self._trigger_low_alert(conn, tray_uuid, tray, remaining_grams, spool_weight, ams_id, tray_id)
                            elif low_alert_sent:
                                conn.execute("UPDATE spools SET low_alert_sent = 0 WHERE tray_uuid = ?", (tray_uuid,))

                    # Mark removed spools as inactive
                    if active_uuids:
                        placeholders = ','.join('?' * len(active_uuids))
                        conn.execute(
                            f"UPDATE spools SET is_active = 0 WHERE is_active = 1 AND tray_uuid NOT IN ({placeholders})",
                            list(active_uuids)
                        )
                    else:
                        conn.execute("UPDATE spools SET is_active = 0 WHERE is_active = 1")

                    conn.commit()
                finally:
                    conn.close()

            self._refresh_alerts()

        except Exception as e:
            logger.error(f"Error processing AMS data: {e}", exc_info=True)

    def _trigger_low_alert(self, conn, tray_uuid, tray, remaining_grams, spool_weight, ams_id, tray_id):
        """Send low filament alert via web UI and optionally FCM."""
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
        self._active_alerts.append(alert)
        logger.warning(f"Low filament alert: {material} ({remaining_grams}g remaining) in AMS {ams_id} slot {tray_id}")

        # FCM notification (only if bridge provides send_fcm_notification)
        if self.low_alert_fcm and self.bridge and hasattr(self.bridge, 'send_fcm_notification'):
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
                self.bridge.send_fcm_notification(
                    title="Low Filament",
                    body=f"{material} ({remaining_grams}g remaining) - consider reordering",
                    data=data
                )
            except Exception as e:
                logger.error(f"Failed to send low filament FCM: {e}")

    def _refresh_alerts(self):
        """Rebuild the active alerts list from the database based on current threshold."""
        if self.low_alert_grams <= 0:
            self._active_alerts = []
            return
        with self._db_lock:
            conn = self._get_conn()
            try:
                rows = conn.execute("""
                    SELECT tray_uuid, material_type, color_hex, spool_weight, remain_percent,
                           last_ams_unit, last_tray_slot, is_rfid, weight_offset
                    FROM spools WHERE is_active = 1 AND is_rfid = 1
                """).fetchall()
                self._active_alerts = []
                for r in rows:
                    offset = r["weight_offset"] or 0
                    remaining_grams = max(0, int((r["remain_percent"] / 100) * r["spool_weight"]) + offset)
                    if remaining_grams < self.low_alert_grams:
                        self._active_alerts.append({
                            "tray_uuid": r["tray_uuid"],
                            "material": r["material_type"],
                            "color": r["color_hex"],
                            "remaining_grams": remaining_grams,
                            "spool_weight": r["spool_weight"],
                            "tray_slot": r["last_tray_slot"],
                            "ams_unit": r["last_ams_unit"],
                            "is_rfid": r["is_rfid"],
                        })
            finally:
                conn.close()

    # =========================================================================
    # Test Mode - generate mock data for previewing the web UI
    # =========================================================================

    def _generate_test_data(self):
        """Populate the database with realistic mock spool data."""
        logger.info("Filament Tracker: generating test data...")
        test_spools = [
            {"uuid": "A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4", "tag": "A1B2C3D4E5F6A1B2",
             "type": "PLA", "color": "FF0000FF", "weight": 1000, "remain": 74,
             "name": "PLA Basic", "ams": 0, "slot": 0, "nmin": "190", "nmax": "220", "bed": "60"},
            {"uuid": "B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5", "tag": "B2C3D4E5F6A1B2C3",
             "type": "PETG", "color": "0077FFFF", "weight": 1000, "remain": 84,
             "name": "PETG HF", "ams": 0, "slot": 1, "nmin": "230", "nmax": "260", "bed": "70"},
            {"uuid": "C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6", "tag": "C3D4E5F6A1B2C3D4",
             "type": "ABS", "color": "222222FF", "weight": 1000, "remain": 14,
             "name": "ABS", "ams": 0, "slot": 2, "nmin": "240", "nmax": "270", "bed": "100"},
            {"uuid": "D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6A1", "tag": "D4E5F6A1B2C3D4E5",
             "type": "TPU", "color": "00FF88FF", "weight": 500, "remain": 92,
             "name": "TPU 95A", "ams": 0, "slot": 3, "nmin": "200", "nmax": "230", "bed": "50"},
            {"uuid": "E5F6A1B2C3D4E5F6A1B2C3D4E5F6A1B2", "tag": "E5F6A1B2C3D4E5F6",
             "type": "PLA", "color": "FFFFFFFF", "weight": 1000, "remain": 45,
             "name": "PLA Basic", "ams": 0, "slot": 0, "active": False, "nmin": "190", "nmax": "220", "bed": "60"},
            {"uuid": "F6A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3", "tag": "F6A1B2C3D4E5F6A1",
             "type": "PLA-CF", "color": "333333FF", "weight": 500, "remain": 62,
             "name": "PLA-CF", "ams": 0, "slot": 1, "active": False, "nmin": "220", "nmax": "250", "bed": "60"},
            {"uuid": "1A2B3C4D5E6F1A2B3C4D5E6F1A2B3C4D", "tag": "1A2B3C4D5E6F1A2B",
             "type": "PETG", "color": "FF8800FF", "weight": 1000, "remain": 8,
             "name": "PETG HF", "ams": 0, "slot": 2, "active": False, "nmin": "230", "nmax": "260", "bed": "70"},
            {"uuid": "NORFID_PLA_9B59B6FF", "tag": "",
             "type": "PLA", "color": "9B59B6FF", "weight": 1000, "remain": 55,
             "name": "PLA", "ams": 1, "slot": 0, "rfid": False, "nmin": "190", "nmax": "220", "bed": "60"},
        ]

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with self._db_lock:
            conn = self._get_conn()
            try:
                conn.execute("DELETE FROM spools")
                conn.execute("DELETE FROM usage_history")

                for s in test_spools:
                    is_active = s.get("active", True)
                    first_seen = (now - timedelta(days=random.randint(1, 60))).isoformat()
                    last_seen = now.isoformat() if is_active else (now - timedelta(days=random.randint(1, 14))).isoformat()

                    conn.execute("""
                        INSERT INTO spools (
                            tray_uuid, tag_uid, material_type, color_hex, spool_weight,
                            remain_percent, filament_name, diameter,
                            nozzle_temp_min, nozzle_temp_max, bed_temp,
                            first_seen, last_seen, last_ams_unit, last_tray_slot,
                            is_active, low_alert_sent, is_rfid
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 1.75, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        s["uuid"], s["tag"], s["type"], s["color"], s["weight"],
                        s["remain"], s["name"],
                        int(s.get("nmin", "0")), int(s.get("nmax", "0")), int(s.get("bed", "0")),
                        first_seen, last_seen, s["ams"], s["slot"],
                        1 if is_active else 0,
                        1 if (s["remain"] / 100 * s["weight"]) < self.low_alert_grams else 0,
                        1 if s.get("rfid", True) else 0
                    ))

                    base_remain = min(s["remain"] + random.randint(10, 40), 100)
                    for i in range(random.randint(3, 8)):
                        ts = (now - timedelta(hours=random.randint(1, 500))).isoformat()
                        pct = max(s["remain"], base_remain - i * random.randint(3, 10))
                        grams = int((pct / 100) * s["weight"])
                        conn.execute("""
                            INSERT INTO usage_history (tray_uuid, timestamp, remain_percent, remaining_grams, job_name)
                            VALUES (?, ?, ?, ?, ?)
                        """, (s["uuid"], ts, pct, grams, f"test_print_{random.randint(1,20)}.gcode"))

                conn.commit()
            finally:
                conn.close()

        self._refresh_alerts()
        logger.info(f"Filament Tracker: {len(test_spools)} test spools generated")

    # =========================================================================
    # Flask Web Server
    # =========================================================================

    def _create_flask_app(self) -> Flask:
        template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
        static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
        app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)
        app.config['JSONIFY_PRETTYPRINT_REGULAR'] = False

        tracker = self

        @app.route('/')
        def index():
            return render_template('index.html', test_mode=tracker.test_mode)

        @app.route('/api/spools')
        def api_spools():
            with tracker._db_lock:
                conn = tracker._get_conn()
                try:
                    rows = conn.execute("SELECT * FROM spools ORDER BY is_active DESC, last_seen DESC").fetchall()
                    return jsonify([tracker._spool_to_dict(r) for r in rows])
                finally:
                    conn.close()

        @app.route('/api/spools/active')
        def api_spools_active():
            with tracker._db_lock:
                conn = tracker._get_conn()
                try:
                    rows = conn.execute("SELECT * FROM spools WHERE is_active = 1 ORDER BY last_ams_unit, last_tray_slot").fetchall()
                    return jsonify([tracker._spool_to_dict(r) for r in rows])
                finally:
                    conn.close()

        @app.route('/api/spools/<tray_uuid>')
        def api_spool_detail(tray_uuid):
            with tracker._db_lock:
                conn = tracker._get_conn()
                try:
                    row = conn.execute("SELECT * FROM spools WHERE tray_uuid = ?", (tray_uuid,)).fetchone()
                    if not row:
                        return jsonify({"error": "Spool not found"}), 404
                    spool = tracker._spool_to_dict(row)
                    history = conn.execute(
                        "SELECT * FROM usage_history WHERE tray_uuid = ? ORDER BY timestamp",
                        (tray_uuid,)
                    ).fetchall()
                    spool["history"] = [dict(h) for h in history]
                    return jsonify(spool)
                finally:
                    conn.close()

        @app.route('/api/spools/<tray_uuid>/history')
        def api_spool_history(tray_uuid):
            with tracker._db_lock:
                conn = tracker._get_conn()
                try:
                    rows = conn.execute(
                        "SELECT * FROM usage_history WHERE tray_uuid = ? ORDER BY timestamp",
                        (tray_uuid,)
                    ).fetchall()
                    return jsonify([dict(r) for r in rows])
                finally:
                    conn.close()

        @app.route('/api/spools/<tray_uuid>', methods=['PATCH'])
        def api_spool_update(tray_uuid):
            data = request.get_json()
            if not data:
                return jsonify({"error": "No data provided"}), 400

            allowed_fields = {'custom_name', 'notes', 'remain_percent', 'weight_offset'}
            updates = {k: v for k, v in data.items() if k in allowed_fields}
            if not updates:
                return jsonify({"error": "No valid fields to update"}), 400

            set_clause = ', '.join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [tray_uuid]

            with tracker._db_lock:
                conn = tracker._get_conn()
                try:
                    conn.execute(f"UPDATE spools SET {set_clause} WHERE tray_uuid = ?", values)
                    conn.commit()
                    row = conn.execute("SELECT * FROM spools WHERE tray_uuid = ?", (tray_uuid,)).fetchone()
                    return jsonify(tracker._spool_to_dict(row)) if row else (jsonify({"error": "Not found"}), 404)
                finally:
                    conn.close()

        @app.route('/api/spools/<tray_uuid>', methods=['DELETE'])
        def api_spool_delete(tray_uuid):
            with tracker._db_lock:
                conn = tracker._get_conn()
                try:
                    row = conn.execute("SELECT tray_uuid FROM spools WHERE tray_uuid = ?", (tray_uuid,)).fetchone()
                    if not row:
                        return jsonify({"error": "Spool not found"}), 404
                    conn.execute("DELETE FROM usage_history WHERE tray_uuid = ?", (tray_uuid,))
                    conn.execute("DELETE FROM spools WHERE tray_uuid = ?", (tray_uuid,))
                    conn.commit()
                    return jsonify({"ok": True})
                finally:
                    conn.close()

        @app.route('/api/status')
        def api_status():
            status = {
                "connected": False,
                "job_name": "",
                "gcode_state": "UNKNOWN",
                "progress": 0,
                "nozzle_temp": 0,
                "bed_temp": 0,
                "test_mode": tracker.test_mode,
            }
            if tracker.test_mode:
                status.update({
                    "connected": True,
                    "job_name": "benchy_v2.gcode",
                    "gcode_state": "RUNNING",
                    "progress": 47,
                    "nozzle_temp": 215,
                    "bed_temp": 60,
                })
            elif tracker.bridge and hasattr(tracker.bridge, 'state'):
                s = tracker.bridge.state
                status.update({
                    "connected": tracker.bridge.mqtt_client is not None,
                    "job_name": s.job_name,
                    "gcode_state": s.gcode_state,
                    "progress": s.progress,
                    "nozzle_temp": s.nozzle_temp,
                    "bed_temp": s.bed_temp,
                })
            return jsonify(status)

        @app.route('/api/alerts')
        def api_alerts():
            return jsonify(tracker._active_alerts)

        @app.route('/api/alerts/<tray_uuid>', methods=['DELETE'])
        def api_alert_dismiss(tray_uuid):
            with tracker._db_lock:
                conn = tracker._get_conn()
                try:
                    conn.execute("UPDATE spools SET low_alert_sent = 0 WHERE tray_uuid = ?", (tray_uuid,))
                    conn.commit()
                finally:
                    conn.close()
            tracker._active_alerts = [a for a in tracker._active_alerts if a.get("tray_uuid") != tray_uuid]
            return jsonify({"ok": True})

        @app.route('/api/settings/alert_threshold', methods=['GET'])
        def api_get_threshold():
            return jsonify({"alert_threshold_grams": tracker.low_alert_grams})

        @app.route('/api/settings/alert_threshold', methods=['POST'])
        def api_set_threshold():
            data = request.get_json()
            if not data or "alert_threshold_grams" not in data:
                return jsonify({"error": "Missing alert_threshold_grams"}), 400
            try:
                val = int(data["alert_threshold_grams"])
            except (ValueError, TypeError):
                return jsonify({"error": "Must be an integer"}), 400
            tracker.low_alert_grams = max(0, val)
            tracker._refresh_alerts()
            return jsonify({"alert_threshold_grams": tracker.low_alert_grams})

        return app

    def _spool_to_dict(self, row) -> dict:
        d = dict(row)
        weight = d.get("spool_weight", 250)
        remain = d.get("remain_percent", 0)
        offset = d.get("weight_offset", 0) or 0
        d["remaining_grams"] = max(0, int((remain / 100) * weight) + offset)
        d["is_low"] = self.low_alert_grams > 0 and d["remaining_grams"] < self.low_alert_grams
        return d

    def start(self):
        """Start the Flask server in a daemon thread."""
        thread = threading.Thread(
            target=self._run_flask,
            name="filament-tracker-web",
            daemon=True
        )
        thread.start()

    def _get_local_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "localhost"

    def _run_flask(self):
        try:
            ip = self._get_local_ip()
            logger.info(f"Filament tracker web server started on http://{ip}:{self.port}")
            self._app.run(host=self.host, port=self.port, debug=False, use_reloader=False)
        except Exception as e:
            logger.error(f"Filament tracker web server failed: {e}")


# =============================================================================
# STANDALONE ENTRY POINT
# =============================================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Filament Tracker")
    parser.add_argument('--test', action='store_true', help='Run in test mode with mock data (no MQTT)')
    parser.add_argument('--port', type=int, default=None, help='Web server port (default: 5000)')
    parser.add_argument('--host', type=str, default=None, help='Web server host (default: 0.0.0.0)')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(os.path.join(_DATA_DIR, 'filament_tracker.log'))
        ]
    )

    if args.test:
        # ----- Test mode: no MQTT, mock data -----
        tracker = FilamentTracker(
            bridge=None,
            port=args.port or 5000,
            host=args.host or '0.0.0.0',
            test_mode=True,
        )

        ip = tracker._get_local_ip()
        print("=" * 50)
        print("  Filament Tracker - TEST MODE")
        print(f"  http://{ip}:{args.port or 5000}")
        print("  Press Ctrl+C to stop")
        print("=" * 50)

        def cleanup_and_exit(sig=None, frame=None):
            tracker._cleanup_test_db()
            raise SystemExit(0)

        signal.signal(signal.SIGINT, cleanup_and_exit)
        signal.signal(signal.SIGTERM, cleanup_and_exit)

        try:
            tracker._app.run(host=args.host or '0.0.0.0', port=args.port or 5000, debug=False, use_reloader=False)
        finally:
            tracker._cleanup_test_db()

    else:
        # ----- Live mode: connect to Bambu MQTT -----
        from bambu_mqtt import BambuMQTTClient

        try:
            import config as _cfg
        except ImportError:
            print("ERROR: config.py not found!")
            print("Copy config.example.py to config.py and fill in your values:")
            print("  cp config.example.py config.py")
            sys.exit(1)

        # MQTT settings (required)
        mqtt_server = getattr(_cfg, 'BAMBU_MQTT_SERVER', 'us.mqtt.bambulab.com')
        mqtt_port = getattr(_cfg, 'BAMBU_MQTT_PORT', 8883)
        user_id = getattr(_cfg, 'BAMBU_USER_ID', '')
        access_token = getattr(_cfg, 'BAMBU_ACCESS_TOKEN', '')
        printer_serial = getattr(_cfg, 'BAMBU_PRINTER_SERIAL', '')

        if not user_id or not access_token or not printer_serial:
            print("ERROR: Missing required config: BAMBU_USER_ID, BAMBU_ACCESS_TOKEN, BAMBU_PRINTER_SERIAL")
            sys.exit(1)

        # Tracker settings
        port = args.port or getattr(_cfg, 'FILAMENT_TRACKER_PORT', 5000)
        host = args.host or getattr(_cfg, 'FILAMENT_TRACKER_HOST', '0.0.0.0')
        low_alert_grams = getattr(_cfg, 'FILAMENT_LOW_ALERT_GRAMS', 150)
        low_alert_fcm = getattr(_cfg, 'FILAMENT_LOW_ALERT_FCM', False)
        enable_notifications = getattr(_cfg, 'ENABLE_NOTIFICATIONS', False)

        # Create shared MQTT client
        mqtt_client = BambuMQTTClient(mqtt_server, mqtt_port, user_id, access_token, printer_serial)

        # Create filament tracker (uses mqtt_client as bridge for .state and .mqtt_client)
        tracker = FilamentTracker(
            bridge=mqtt_client,
            port=port,
            host=host,
            low_alert_grams=low_alert_grams,
            low_alert_fcm=low_alert_fcm,
        )

        # Register AMS callback
        mqtt_client.on_ams_data(tracker.update_ams_data)

        # Optional: load notification service from sibling BambuNowBar folder
        if enable_notifications:
            _nowbar_path = os.path.normpath(os.path.join(_SERVER_DIR, '..', 'BambuNowBar', 'server'))
            if os.path.isdir(_nowbar_path):
                sys.path.insert(0, _nowbar_path)
                try:
                    from bambu_fcm_bridge import BambuFCMBridge
                    notification_bridge = BambuFCMBridge(mqtt_client)
                    # Switch tracker's bridge to the full notification bridge
                    # (provides .send_fcm_notification for low-filament alerts)
                    tracker.bridge = notification_bridge
                    logger.info(f"Notification service loaded from {_nowbar_path}")
                except ImportError as e:
                    logger.error(f"Failed to import notification service: {e}")
                    logger.error("Make sure firebase-admin is installed: pip install firebase-admin")
            else:
                logger.warning(f"ENABLE_NOTIFICATIONS is True but BambuNowBar not found: {_nowbar_path}")

        # Start Flask in daemon thread
        tracker.start()

        # Run MQTT (blocks)
        logger.info("=" * 50)
        logger.info("Filament Tracker Starting")
        logger.info(f"Printer: {printer_serial}")
        logger.info("=" * 50)

        try:
            mqtt_client.run()
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            mqtt_client.disconnect()
        except Exception as e:
            logger.error(f"Fatal error: {e}")
            raise


if __name__ == '__main__':
    main()
