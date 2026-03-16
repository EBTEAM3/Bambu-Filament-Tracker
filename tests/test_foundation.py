import sqlite3
from pathlib import Path

import pytest

import filament_tracker as tracker_module
from filament_tracker import FilamentTracker


@pytest.fixture()
def tracker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTH_ENABLED", "0")
    monkeypatch.setenv("FILAMENT_TRACKER_DATA_DIR", str(tmp_path))
    t = FilamentTracker(test_mode=True)
    return t


@pytest.fixture()
def client(tracker: FilamentTracker):
    return tracker._app.test_client()


def test_resolve_effective_calibration_priority(tracker: FilamentTracker):
    with tracker._db_lock:
        conn = tracker._get_conn()
        now = tracker._now_iso()
        try:
            product_id = conn.execute(
                """
                INSERT INTO filament_products(brand, material, color, nominal_weight_g, filament_diameter_mm, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("Bambu", "PLA", "White", 1000, 1.75, now, now),
            ).lastrowid
            spool_id = conn.execute(
                """
                INSERT INTO spool_instances(spool_uuid, filament_product_id, is_rfid, source, created_at, updated_at)
                VALUES (?, ?, 1, 'rfid', ?, ?)
                """,
                ("spool-1", product_id, now, now),
            ).lastrowid

            conn.execute(
                "INSERT INTO calibration_profiles(scope_type, scope_id, material, flow_ratio, created_at, updated_at) VALUES ('global_material', 'PLA', 'PLA', 0.95, ?, ?)",
                (now, now),
            )
            conn.execute(
                "INSERT INTO calibration_profiles(scope_type, scope_id, flow_ratio, created_at, updated_at) VALUES ('filament_product', ?, 0.97, ?, ?)",
                (str(product_id), now, now),
            )
            conn.execute(
                "INSERT INTO calibration_profiles(scope_type, scope_id, flow_ratio, created_at, updated_at) VALUES ('spool_instance', ?, 0.99, ?, ?)",
                (str(spool_id), now, now),
            )
            conn.commit()
        finally:
            conn.close()

    resolved = tracker.resolve_effective_calibration(spool_id)
    assert resolved["effective"]["flow_ratio"] == 0.99
    assert [l["source"] for l in resolved["layers"]] == [
        "global_material",
        "filament_product",
        "spool_instance",
    ]


def test_create_rfid_and_non_rfid_spools(client):
    payload_rfid = {
        "source": "rfid",
        "is_rfid": True,
        "rfid_uid": "ABC123",
        "custom_name": "RFID spool",
    }
    resp1 = client.post("/api/spool-instances", json=payload_rfid)
    assert resp1.status_code == 201
    body1 = resp1.get_json()
    assert body1["is_rfid"] is True
    assert body1["spool_uuid"]

    payload_manual = {
        "source": "manual",
        "is_rfid": False,
        "custom_name": "Manual spool",
    }
    resp2 = client.post("/api/spool-instances", json=payload_manual)
    assert resp2.status_code == 201
    body2 = resp2.get_json()
    assert body2["is_rfid"] is False
    assert body2["spool_uuid"] != body1["spool_uuid"]


def test_update_spool_instance(client):
    created = client.post("/api/spool-instances", json={"source": "manual", "custom_name": "Before"})
    assert created.status_code == 201
    spool_id = created.get_json()["id"]

    updated = client.patch(f"/api/spools/{spool_id}", json={"custom_name": "After", "archived": True})
    assert updated.status_code == 200
    body = updated.get_json()
    assert body["custom_name"] == "After"
    assert body["archived"] is True


def test_calibration_run_validation(client):
    created = client.post("/api/spool-instances", json={"source": "manual", "custom_name": "T"})
    spool_id = created.get_json()["id"]

    resp = client.post(
        "/api/calibration-runs",
        json={
            "spool_id": spool_id,
            "printer_model": "X1C",
            "test_type": "invalid_type",
        },
    )
    assert resp.status_code == 400


def test_dashboard_endpoint(client):
    resp = client.get("/api/dashboard")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "counts" in body
    assert "latest_runs" in body


def test_migration_from_legacy_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTH_ENABLED", "0")
    monkeypatch.setenv("FILAMENT_TRACKER_DATA_DIR", str(tmp_path))
    legacy_db = tmp_path / "filament_tracker.db"

    conn = sqlite3.connect(legacy_db)
    conn.executescript(
        """
        CREATE TABLE spools (
            tray_uuid TEXT PRIMARY KEY,
            tag_uid TEXT,
            material_type TEXT NOT NULL,
            color_hex TEXT NOT NULL,
            color_names TEXT,
            spool_weight INTEGER NOT NULL,
            remain_percent INTEGER DEFAULT 0,
            filament_name TEXT,
            sub_brand TEXT,
            diameter REAL DEFAULT 1.75,
            nozzle_temp_min INTEGER,
            nozzle_temp_max INTEGER,
            bed_temp INTEGER,
            drying_temp INTEGER,
            drying_time INTEGER,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            last_ams_unit INTEGER,
            last_tray_slot INTEGER,
            is_active INTEGER DEFAULT 1,
            low_alert_sent INTEGER DEFAULT 0,
            notes TEXT,
            custom_name TEXT,
            is_rfid INTEGER DEFAULT 1,
            weight_offset INTEGER DEFAULT 0
        );
        CREATE TABLE usage_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tray_uuid TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            remain_percent INTEGER NOT NULL,
            remaining_grams INTEGER NOT NULL,
            job_name TEXT
        );
        """
    )
    conn.execute(
        """
        INSERT INTO spools(
            tray_uuid, tag_uid, material_type, color_hex, spool_weight, remain_percent,
            filament_name, sub_brand, diameter, nozzle_temp_min, nozzle_temp_max,
            bed_temp, drying_temp, drying_time, first_seen, last_seen, last_ams_unit,
            last_tray_slot, is_active, is_rfid, weight_offset
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "legacy-1", "tag-1", "PLA", "FFFFFFFF", 1000, 80,
            "PLA Basic", "Bambu", 1.75, 200, 220,
            60, 55, 8, "2026-01-01T00:00:00", "2026-01-02T00:00:00", 0,
            1, 1, 1, 0,
        ),
    )
    conn.commit()
    conn.close()

    tracker = FilamentTracker(test_mode=False)
    with tracker._db_lock:
        conn2 = tracker._get_conn()
        try:
            migrated = conn2.execute("SELECT COUNT(*) AS c FROM spool_instances").fetchone()["c"]
            assert migrated >= 1
        finally:
            conn2.close()
