SYNTHETIC_ID_PREFIX = "NORFID_"

AUTH_ENABLED_ENV = "AUTH_ENABLED"
ADMIN_ROLE = "admin"

CALIBRATION_SCOPE_TYPES = {
    "global_material",
    "filament_product",
    "spool_instance",
}

CALIBRATION_TEST_TYPES = {
    "flow_ratio",
    "temperature_tower",
    "pressure_advance",
    "max_volumetric_flow",
    "bridging",
    "overhang",
    "stringing",
    "ironing",
    "dimensional_accuracy",
    "drying",
    "adhesion",
    "generic",
}

CALIBRATION_RESULT_STATUS = {"pending", "passed", "failed", "discarded"}
