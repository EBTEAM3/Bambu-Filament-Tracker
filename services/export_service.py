class ExportService:
    def __init__(self, tracker):
        self.tracker = tracker

    def export_inventory(self):
        with self.tracker._db_lock:
            conn = self.tracker.db.get_conn()
            try:
                products = [dict(r) for r in conn.execute("SELECT * FROM filament_products ORDER BY id").fetchall()]
                spools = [dict(r) for r in conn.execute("SELECT * FROM spool_instances ORDER BY id").fetchall()]
            finally:
                conn.close()
        return {
            "exported_at": self.tracker.now_iso(),
            "filament_products": products,
            "spool_instances": spools,
        }

    def export_calibrations(self):
        with self.tracker._db_lock:
            conn = self.tracker.db.get_conn()
            try:
                profiles = [dict(r) for r in conn.execute("SELECT * FROM calibration_profiles ORDER BY id").fetchall()]
                runs = [dict(r) for r in conn.execute("SELECT * FROM calibration_runs ORDER BY id").fetchall()]
            finally:
                conn.close()
        return {
            "exported_at": self.tracker.now_iso(),
            "calibration_profiles": profiles,
            "calibration_runs": runs,
        }
