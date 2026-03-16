from repositories.base import BaseRepository


class CalibrationRepository(BaseRepository):
    def list_profiles(self, conn, scope_type=None, scope_id=None):
        query = "SELECT * FROM calibration_profiles"
        where = []
        values = []
        if scope_type:
            where.append("scope_type = ?")
            values.append(scope_type)
        if scope_id:
            where.append("scope_id = ?")
            values.append(scope_id)
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY updated_at DESC"
        return conn.execute(query, values).fetchall()

    def create_profile(self, conn, data: dict, now: str):
        cursor = conn.execute(
            """
            INSERT INTO calibration_profiles (
                scope_type, scope_id, material, printer_model, nozzle_diameter_mm,
                nozzle_material, plate_type, layer_height_mm, slicer_name, slicer_profile,
                flow_ratio, nozzle_temp_c, bed_temp_c, chamber_temp_c,
                max_volumetric_speed_mm3_s, pressure_advance, retraction_distance_mm,
                retraction_speed_mm_s, fan_speed_percent, bridge_fan_speed_percent,
                bridge_flow_ratio, overhang_speed_percent, ironing_flow, ironing_spacing,
                ironing_speed_mm_s, seam_strategy, drying_recommended_hours,
                drying_recommended_temp_c, quality_score, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data.get("scope_type"),
                str(data.get("scope_id")),
                data.get("material"),
                data.get("printer_model"),
                data.get("nozzle_diameter_mm"),
                data.get("nozzle_material"),
                data.get("plate_type"),
                data.get("layer_height_mm"),
                data.get("slicer_name"),
                data.get("slicer_profile"),
                data.get("flow_ratio"),
                data.get("nozzle_temp_c"),
                data.get("bed_temp_c"),
                data.get("chamber_temp_c"),
                data.get("max_volumetric_speed_mm3_s"),
                data.get("pressure_advance"),
                data.get("retraction_distance_mm"),
                data.get("retraction_speed_mm_s"),
                data.get("fan_speed_percent"),
                data.get("bridge_fan_speed_percent"),
                data.get("bridge_flow_ratio"),
                data.get("overhang_speed_percent"),
                data.get("ironing_flow"),
                data.get("ironing_spacing"),
                data.get("ironing_speed_mm_s"),
                data.get("seam_strategy"),
                data.get("drying_recommended_hours"),
                data.get("drying_recommended_temp_c"),
                data.get("quality_score"),
                data.get("notes"),
                now,
                now,
            ),
        )
        return cursor.lastrowid

    def list_runs(self, conn, spool_id=None):
        query = "SELECT * FROM calibration_runs"
        values = []
        if spool_id is not None:
            query += " WHERE spool_id = ?"
            values.append(spool_id)
        query += " ORDER BY test_date DESC"
        return conn.execute(query, values).fetchall()

    def create_run(self, conn, data: dict, now: str):
        cursor = conn.execute(
            """
            INSERT INTO calibration_runs (
                spool_id, filament_product_id, profile_id, printer_model, printer_serial,
                ams_slot, nozzle_diameter_mm, nozzle_material, plate_type,
                layer_height_mm, slicer_name, slicer_profile, test_type, test_date,
                result_status, measured_value, selected_value, score,
                observations, image_path, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data.get("spool_id"),
                data.get("filament_product_id"),
                data.get("profile_id"),
                data.get("printer_model"),
                data.get("printer_serial"),
                data.get("ams_slot"),
                data.get("nozzle_diameter_mm"),
                data.get("nozzle_material"),
                data.get("plate_type"),
                data.get("layer_height_mm"),
                data.get("slicer_name"),
                data.get("slicer_profile"),
                data.get("test_type"),
                data.get("test_date") or now,
                data.get("result_status", "pending"),
                data.get("measured_value"),
                data.get("selected_value"),
                data.get("score"),
                data.get("observations"),
                data.get("image_path"),
                now,
                now,
            ),
        )
        return cursor.lastrowid
