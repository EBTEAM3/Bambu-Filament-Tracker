from repositories.base import BaseRepository


class FilamentProductRepository(BaseRepository):
    def list(self, conn, brand=None, material=None, color=None):
        where = []
        values = []
        if brand:
            where.append("brand = ?")
            values.append(brand)
        if material:
            where.append("material = ?")
            values.append(material)
        if color:
            where.append("color = ?")
            values.append(color)
        sql = "SELECT * FROM filament_products"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY brand, material, color"
        return conn.execute(sql, values).fetchall()

    def create(self, conn, data: dict, now: str):
        cursor = conn.execute(
            """
            INSERT INTO filament_products (
                brand, material, color, finish_variant, nominal_weight_g,
                filament_diameter_mm, density_g_cm3, manufacturer_sku,
                recommended_print_temp_min_c, recommended_print_temp_max_c,
                recommended_bed_temp_min_c, recommended_bed_temp_max_c,
                default_notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data.get("brand"),
                data.get("material"),
                data.get("color"),
                data.get("finish_variant"),
                int(data.get("nominal_weight_g")),
                float(data.get("filament_diameter_mm")),
                data.get("density_g_cm3"),
                data.get("manufacturer_sku"),
                data.get("recommended_print_temp_min_c"),
                data.get("recommended_print_temp_max_c"),
                data.get("recommended_bed_temp_min_c"),
                data.get("recommended_bed_temp_max_c"),
                data.get("default_notes"),
                now,
                now,
            ),
        )
        return cursor.lastrowid

    def find_for_legacy(self, conn, brand: str, material: str, color: str, finish_variant: str):
        return conn.execute(
            """
            SELECT id FROM filament_products
            WHERE brand = ? AND material = ? AND color = ? AND COALESCE(finish_variant,'') = COALESCE(?, '')
            """,
            (brand, material, color, finish_variant),
        ).fetchone()

    def create_from_legacy(self, conn, row, brand: str, material: str, color: str, finish_variant: str, now: str):
        cursor = conn.execute(
            """
            INSERT INTO filament_products (
                brand, material, color, finish_variant,
                nominal_weight_g, filament_diameter_mm,
                recommended_print_temp_min_c, recommended_print_temp_max_c,
                recommended_bed_temp_min_c, recommended_bed_temp_max_c,
                default_notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                brand,
                material,
                color,
                finish_variant,
                row["spool_weight"] or 1000,
                row["diameter"] or 1.75,
                row["nozzle_temp_min"],
                row["nozzle_temp_max"],
                row["bed_temp"],
                row["bed_temp"],
                "Migrated from legacy spools table",
                now,
                now,
            ),
        )
        return cursor.lastrowid
