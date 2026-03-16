class CalibrationService:
    """Resolve effective print calibration from layered profile scopes."""

    def __init__(self, tracker):
        self.tracker = tracker

    def resolve_effective_calibration(self, spool_id: int, printer_context=None):
        """Compute the effective calibration for a spool in a print context.

        Precedence order (lowest -> highest):
        1) `global_material`
        2) `filament_product`
        3) `spool_instance`

        Context fields are treated as soft constraints (`field IS NULL OR field = ?`).
        This allows generic profiles to remain applicable when no exact context
        override is present.
        """
        printer_context = printer_context or {}
        with self.tracker._db_lock:
            conn = self.tracker.db.get_conn()
            try:
                spool = conn.execute(
                    """
                    SELECT si.*, fp.material
                    FROM spool_instances si
                    LEFT JOIN filament_products fp ON fp.id = si.filament_product_id
                    WHERE si.id = ?
                    """,
                    (spool_id,),
                ).fetchone()
                if not spool:
                    return {"error": "spool_not_found"}

                filters = []
                params = []
                for field in [
                    "printer_model",
                    "nozzle_diameter_mm",
                    "plate_type",
                    "layer_height_mm",
                    "slicer_name",
                    "slicer_profile",
                ]:
                    value = printer_context.get(field)
                    if value is None:
                        continue
                    filters.append(f"({field} IS NULL OR {field} = ?)")
                    params.append(value)
                where_ctx = " AND ".join(filters) if filters else "1=1"

                fields = [
                    "flow_ratio",
                    "nozzle_temp_c",
                    "bed_temp_c",
                    "chamber_temp_c",
                    "max_volumetric_speed_mm3_s",
                    "pressure_advance",
                    "retraction_distance_mm",
                    "retraction_speed_mm_s",
                    "fan_speed_percent",
                    "bridge_fan_speed_percent",
                    "bridge_flow_ratio",
                    "overhang_speed_percent",
                    "ironing_flow",
                    "ironing_spacing",
                    "ironing_speed_mm_s",
                    "seam_strategy",
                    "drying_recommended_hours",
                    "drying_recommended_temp_c",
                    "quality_score",
                    "notes",
                ]

                def get_profile(scope_type: str, scope_id: str, material: str = None):
                    profile_query = (
                        "SELECT * FROM calibration_profiles "
                        "WHERE scope_type = ? AND scope_id = ? AND "
                        + where_ctx
                        + " ORDER BY updated_at DESC LIMIT 1"
                    )
                    profile = conn.execute(profile_query, [scope_type, scope_id, *params]).fetchone()
                    if profile:
                        return profile
                    if material:
                        # Fallback keeps material defaults usable even when no
                        # scope-specific record exists for the current context.
                        return conn.execute(
                            "SELECT * FROM calibration_profiles WHERE scope_type = 'global_material' AND material = ? ORDER BY updated_at DESC LIMIT 1",
                            (material,),
                        ).fetchone()
                    return None

                spool_profile = get_profile("spool_instance", str(spool_id))
                product_profile = None
                if spool["filament_product_id"]:
                    product_profile = get_profile("filament_product", str(spool["filament_product_id"]))
                material_profile = None
                if spool["material"]:
                    material_profile = conn.execute(
                        "SELECT * FROM calibration_profiles WHERE scope_type = 'global_material' AND material = ? ORDER BY updated_at DESC LIMIT 1",
                        (spool["material"],),
                    ).fetchone()

                merged = {k: None for k in fields}
                layers = []
                for source_name, profile in [
                    ("global_material", material_profile),
                    ("filament_product", product_profile),
                    ("spool_instance", spool_profile),
                ]:
                    if not profile:
                        continue
                    layers.append({"source": source_name, "profile_id": profile["id"]})
                    for field in fields:
                        value = profile[field]
                        if value is not None:
                            merged[field] = value

                return {
                    "spool_id": spool_id,
                    "filament_product_id": spool["filament_product_id"],
                    "material": spool["material"],
                    "context": printer_context,
                    "layers": layers,
                    "effective": merged,
                }
            finally:
                conn.close()
