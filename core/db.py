import sqlite3


class DBManager:
    """Centralized SQLite access and schema bootstrap.

    The bootstrap keeps both legacy and new-domain tables available so
    incremental migration can happen without breaking existing clients.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    def get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self):
        """Create baseline schema and additive compatibility columns.

        Column additions are idempotent to support upgrades from older databases
        where `spools` did not yet include RFID/offset fields.
        """
        conn = self.get_conn()
        try:
            conn.executescript(
                """
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

                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version         TEXT PRIMARY KEY,
                    applied_at      TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS users (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    username        TEXT NOT NULL UNIQUE,
                    password_hash   TEXT NOT NULL,
                    role            TEXT NOT NULL DEFAULT 'admin',
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS filament_products (
                    id                                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    brand                               TEXT NOT NULL,
                    material                            TEXT NOT NULL,
                    color                               TEXT NOT NULL,
                    finish_variant                      TEXT,
                    nominal_weight_g                    INTEGER NOT NULL,
                    filament_diameter_mm                REAL NOT NULL DEFAULT 1.75,
                    density_g_cm3                       REAL,
                    manufacturer_sku                    TEXT,
                    recommended_print_temp_min_c        INTEGER,
                    recommended_print_temp_max_c        INTEGER,
                    recommended_bed_temp_min_c          INTEGER,
                    recommended_bed_temp_max_c          INTEGER,
                    default_notes                       TEXT,
                    created_at                          TEXT NOT NULL,
                    updated_at                          TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS spool_instances (
                    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
                    filament_product_id         INTEGER,
                    spool_uuid                  TEXT NOT NULL UNIQUE,
                    legacy_tray_uuid            TEXT UNIQUE,
                    rfid_uid                    TEXT,
                    tray_uuid                   TEXT,
                    external_device_slot        TEXT,
                    is_rfid                     INTEGER NOT NULL DEFAULT 0,
                    source                      TEXT NOT NULL,
                    batch_code                  TEXT,
                    purchase_date               TEXT,
                    opened_date                 TEXT,
                    drying_last_date            TEXT,
                    drying_hours_last           REAL,
                    humidity_state              TEXT,
                    remaining_weight_g          INTEGER,
                    remaining_percent           REAL,
                    tare_weight_g               INTEGER,
                    weight_offset_g             INTEGER DEFAULT 0,
                    custom_name                 TEXT,
                    notes                       TEXT,
                    archived                    INTEGER NOT NULL DEFAULT 0,
                    last_ams_unit               INTEGER,
                    last_tray_slot              INTEGER,
                    created_at                  TEXT NOT NULL,
                    updated_at                  TEXT NOT NULL,
                    FOREIGN KEY (filament_product_id) REFERENCES filament_products(id)
                );

                CREATE TABLE IF NOT EXISTS calibration_profiles (
                    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope_type                      TEXT NOT NULL,
                    scope_id                        TEXT NOT NULL,
                    material                        TEXT,
                    printer_model                   TEXT,
                    nozzle_diameter_mm              REAL,
                    nozzle_material                 TEXT,
                    plate_type                      TEXT,
                    layer_height_mm                 REAL,
                    slicer_name                     TEXT,
                    slicer_profile                  TEXT,
                    flow_ratio                      REAL,
                    nozzle_temp_c                   INTEGER,
                    bed_temp_c                      INTEGER,
                    chamber_temp_c                  INTEGER,
                    max_volumetric_speed_mm3_s      REAL,
                    pressure_advance                REAL,
                    retraction_distance_mm          REAL,
                    retraction_speed_mm_s           REAL,
                    fan_speed_percent               REAL,
                    bridge_fan_speed_percent        REAL,
                    bridge_flow_ratio               REAL,
                    overhang_speed_percent          REAL,
                    ironing_flow                    REAL,
                    ironing_spacing                 REAL,
                    ironing_speed_mm_s              REAL,
                    seam_strategy                   TEXT,
                    drying_recommended_hours        REAL,
                    drying_recommended_temp_c       INTEGER,
                    quality_score                   REAL,
                    notes                           TEXT,
                    created_at                      TEXT NOT NULL,
                    updated_at                      TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS calibration_runs (
                    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                    spool_id                INTEGER NOT NULL,
                    filament_product_id     INTEGER,
                    profile_id              INTEGER,
                    printer_model           TEXT NOT NULL,
                    printer_serial          TEXT,
                    ams_slot                TEXT,
                    nozzle_diameter_mm      REAL,
                    nozzle_material         TEXT,
                    plate_type              TEXT,
                    layer_height_mm         REAL,
                    slicer_name             TEXT,
                    slicer_profile          TEXT,
                    test_type               TEXT NOT NULL,
                    test_date               TEXT NOT NULL,
                    result_status           TEXT NOT NULL DEFAULT 'pending',
                    measured_value          REAL,
                    selected_value          REAL,
                    score                   REAL,
                    observations            TEXT,
                    image_path              TEXT,
                    created_at              TEXT NOT NULL,
                    updated_at              TEXT NOT NULL,
                    FOREIGN KEY (spool_id) REFERENCES spool_instances(id),
                    FOREIGN KEY (filament_product_id) REFERENCES filament_products(id),
                    FOREIGN KEY (profile_id) REFERENCES calibration_profiles(id)
                );

                CREATE TABLE IF NOT EXISTS spool_presence_history (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    spool_id            INTEGER NOT NULL,
                    ams_unit            INTEGER,
                    tray_slot           INTEGER,
                    event_type          TEXT NOT NULL,
                    event_at            TEXT NOT NULL,
                    FOREIGN KEY (spool_id) REFERENCES spool_instances(id)
                );

                CREATE TABLE IF NOT EXISTS print_job_history (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_name            TEXT NOT NULL,
                    first_seen_at       TEXT NOT NULL,
                    last_update_at      TEXT NOT NULL,
                    last_state          TEXT,
                    final_state         TEXT,
                    last_progress       INTEGER,
                    final_progress      INTEGER,
                    last_layer_num      INTEGER,
                    last_total_layers   INTEGER,
                    error_code          TEXT,
                    failure_reason      TEXT,
                    tray_now_raw        INTEGER,
                    ams_unit            INTEGER,
                    tray_slot           INTEGER,
                    spool_tray_uuid     TEXT,
                    spool_material_type TEXT,
                    spool_color_hex     TEXT,
                    spool_is_rfid       INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_spool_instances_rfid_uid ON spool_instances(rfid_uid);
                CREATE INDEX IF NOT EXISTS idx_spool_instances_product ON spool_instances(filament_product_id);
                CREATE INDEX IF NOT EXISTS idx_spool_instances_archived ON spool_instances(archived);
                CREATE INDEX IF NOT EXISTS idx_calibration_profiles_scope ON calibration_profiles(scope_type, scope_id);
                CREATE INDEX IF NOT EXISTS idx_calibration_runs_spool_date ON calibration_runs(spool_id, test_date DESC);
                CREATE INDEX IF NOT EXISTS idx_presence_spool_event ON spool_presence_history(spool_id, event_at DESC);
                CREATE INDEX IF NOT EXISTS idx_print_job_history_last_update ON print_job_history(last_update_at DESC);
                """
            )
            for col, col_type in {
                "is_rfid": "INTEGER DEFAULT 1",
                "weight_offset": "INTEGER DEFAULT 0",
            }.items():
                try:
                    conn.execute(f"ALTER TABLE spools ADD COLUMN {col} {col_type}")
                except sqlite3.OperationalError:
                    # Column already exists on upgraded databases.
                    pass

            for col, col_type in {
                "error_code": "TEXT",
                "failure_reason": "TEXT",
                "tray_now_raw": "INTEGER",
                "ams_unit": "INTEGER",
                "tray_slot": "INTEGER",
                "spool_tray_uuid": "TEXT",
                "spool_material_type": "TEXT",
                "spool_color_hex": "TEXT",
                "spool_is_rfid": "INTEGER",
            }.items():
                try:
                    conn.execute(f"ALTER TABLE print_job_history ADD COLUMN {col} {col_type}")
                except sqlite3.OperationalError:
                    pass
            conn.commit()
        finally:
            conn.close()
