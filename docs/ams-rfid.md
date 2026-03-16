# AMS and RFID Behavior

## Source Data and Event Model

The tracker consumes AMS snapshots from the MQTT callback (`on_ams_data`).
Each snapshot can include:

- active tray assignment (`tray_now`)
- tray-level environment (temperature, humidity)
- per-slot spool metadata (`ams[].tray[]`)

`SpoolSyncService.update_ams_data()` is intentionally an orchestrator and delegates slot handling to focused helpers.

## Spool Identity Rules

A spool identity is normalized before persistence:

- RFID spool: use printer-reported tray UUID when available
- non-RFID spool: synthesize stable ID as `NORFID_{material}_{RGB6}` (e.g. `NORFID_PLA_9B59B6`)
- material fallback: `tray_type` -> `"Unknown"`

This makes non-RFID usage trackable across updates while preserving deterministic slot identity.

## Persistence Strategy (Dual Model)

Each tray update writes to two models:

1. Legacy model (`spools`, `usage_history`)
   - keeps existing UI and routes functional
   - updates `last_seen`, `is_active`, metadata and optional offset-adjusted weight

2. Foundation model (`spool_instances`, `spool_presence_history`)
   - supports SKU/spool-instance domain
   - stores `legacy_tray_uuid` bridge for continuity

The write order is designed so legacy behavior remains stable while new domain state is updated in the same callback.

## Weight and Offsets

Reported AMS `remain` is treated as source-of-truth telemetry and converted to grams using tray weight, then adjusted by optional per-spool `weight_offset`:

- effective grams = `max(int((remain / 100) * spool_weight) + offset, 0)`
- offsets are loaded from DB once per tray update path

If tray weight is missing, the current implementation uses a default spool weight fallback (`250g`) for calculations.

## Low-Stock Alerts

When effective weight falls below threshold (`FILAMENT_LOW_ALERT_GRAMS`):

- create/update active alert
- optionally dispatch push notification through attached notification bridge
- close alert state when spool recovers above threshold

Duplicate alert spam is prevented via `spools.low_alert_sent` state in legacy persistence.

## Active/Inactive Semantics

After processing current snapshot, any previously active legacy spool not present in current AMS payload is marked inactive in `spools`.
`spool_instances` are updated on seen trays, but are not globally deactivated by the current removal pass.

## Operational Notes

- Non-RFID weight precision is limited by printer telemetry quality.
- Missing tray payloads are ignored defensively instead of forcing deletions.
- Helper boundaries in `SpoolSyncService` are designed for future unit-level testing without changing callback contract.
