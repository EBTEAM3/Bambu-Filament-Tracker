# Data Model

## Overview

The database includes two layers:

- Legacy operational layer used by existing UI and workflows
- New domain layer for inventory and calibration management

Both are actively maintained during transition.

## Legacy Tables

### `spools`

Primary inventory table keyed by `tray_uuid`.
Key fields:

- material/color/weight metadata from AMS
- `remain_percent`, `weight_offset`, low-alert flags
- AMS placement (`last_ams_unit`, `last_tray_slot`)
- status (`is_active`, `is_rfid`)

### `usage_history`

Time-series snapshots per `tray_uuid`:

- timestamp
- remaining percent
- remaining grams
- optional job name

## Domain Tables

### `filament_products`

Canonical SKU-like product record:

- brand/material/color/variant
- nominal spool properties
- recommended print/bed ranges

### `spool_instances`

Physical spool entities:

- optional relation to `filament_products`
- identity fields (`spool_uuid`, `legacy_tray_uuid`, `rfid_uid`, `tray_uuid`)
- lifecycle and inventory fields (opened/drying/remaining/archived)
- AMS placement snapshots

### `calibration_profiles`

Layered calibration settings with scope:

- `scope_type`: `global_material` | `filament_product` | `spool_instance`
- `scope_id`: id/token bound to selected scope
- context qualifiers (printer/nozzle/plate/slicer/layer)
- tuning fields (flow/temp/PA/retraction/fans/etc.)

### `calibration_runs`

Execution/test history:

- references spool and optional product/profile
- test metadata (type/date/status)
- measured vs selected values
- notes and optional artifacts

### `spool_presence_history`

Audit trail of spool sightings in AMS:

- spool id
- AMS unit + slot
- event type (`created`, `seen`)
- event timestamp

## Supporting Tables

### `users`

Local authentication users with password hash and role.

### `schema_migrations`

Applied migration versions and timestamps.

## Important Indices

- `idx_spool_instances_rfid_uid`
- `idx_spool_instances_product`
- `idx_calibration_profiles_scope`
- `idx_calibration_runs_spool_date`
- `idx_presence_spool_event`

These indices are aligned with common API access patterns (by spool, scope, and recency).
